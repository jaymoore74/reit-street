import "server-only";
import { appExec, appQuery } from "./db";
import { audit } from "./audit";
import { siteName } from "./brand";
import { emailEnabled, sendInBackground } from "./email";
import { newsletterConfirmEmail } from "./email-templates";
import { env } from "./env";
import { newToken, sha256Hex } from "./tokens";
import type { ClientInfo } from "./request";

/**
 * Newsletter sign-ups are stored with the exact consent text shown. Public sign-ups stay
 * "pending" until the address is confirmed (double opt-in). With email on (RS_EMAIL_PROVIDER)
 * the public form sends a confirmation link; with email off nothing is sent and they stay
 * pending. Signed-in clients are confirmed directly, because their invitation already proved
 * the address. The site does not send newsletter issues yet.
 */
export function consentText(): string {
  // PLACEHOLDER copy (docs/web-copy-review.md)
  return `I agree to receive the ${siteName()} newsletters I selected by email. I can unsubscribe at any time.`;
}
export const PUBLIC_SIGNUPS_PER_IP_PER_HOUR = 5;
export const CONFIRM_DAYS = 7;
/** A second request for the same address and list within this many minutes sends no second email. */
export const RESEND_MINUTES = 10;

export type NewsletterList = { list_code: string; name: string; description: string | null; is_public: number };

export async function lists(includePrivate: boolean): Promise<NewsletterList[]> {
  return appQuery<NewsletterList>(
    `SELECT list_code, name, description, is_public FROM app_newsletter_list
      WHERE is_active = 1 ${includePrivate ? "" : "AND is_public = 1"} ORDER BY is_public DESC, name`,
  );
}

/** Public form. Answers the same way whether or not the address was already on the list. */
export async function publicSignup(email: string, listCode: string, client: ClientInfo): Promise<"ok" | "throttled" | "no_list"> {
  if (client.ip !== "unknown") {
    const [r] = await appQuery<{ n: number }>(
      `SELECT COUNT(*) AS n FROM app_audit_event
        WHERE event_type = 'newsletter_signup' AND ip = ? AND occurred_at > NOW(6) - INTERVAL 1 HOUR`,
      [client.ip],
    );
    if (Number(r?.n ?? 0) >= PUBLIC_SIGNUPS_PER_IP_PER_HOUR) return "throttled";
  }
  const [list] = await appQuery<{ name: string }>(
    "SELECT name FROM app_newsletter_list WHERE list_code = ? AND is_active = 1 AND is_public = 1",
    [listCode],
  );
  if (!list) return "no_list";
  await audit("newsletter_signup", client, {}, listCode, null);
  const [prior] = await appQuery<{ status: string; recent: number }>(
    `SELECT status, requested_at > NOW(6) - INTERVAL ${RESEND_MINUTES} MINUTE AS recent
       FROM app_newsletter_subscription WHERE list_code = ? AND email = ?`,
    [listCode, email],
  );
  // already confirmed: nothing to change or send; a pending request made minutes ago: no second email
  if (prior?.status === "confirmed" || (prior?.status === "pending" && Number(prior.recent))) return "ok";
  const token = newToken();
  await appExec(
    `INSERT INTO app_newsletter_subscription (list_code, email, status, source, consent_text, confirm_token_hash, ip)
     VALUES (?, ?, 'pending', 'public_form', ?, ?, ?)
     ON DUPLICATE KEY UPDATE
       status = IF(status = 'confirmed', status, 'pending'),
       consent_text = IF(status = 'confirmed', consent_text, VALUES(consent_text)),
       confirm_token_hash = IF(status = 'confirmed', confirm_token_hash, VALUES(confirm_token_hash)),
       requested_at = IF(status = 'confirmed', requested_at, NOW(6)),
       unsubscribed_at = IF(status = 'confirmed', unsubscribed_at, NULL),
       ip = VALUES(ip)`,
    [listCode, email, consentText(), sha256Hex(token), client.ip],
  );
  if (emailEnabled()) {
    const url = `${env().RS_PUBLIC_BASE_URL.replace(/\/$/, "")}/newsletter/confirm/${token}`;
    sendInBackground(newsletterConfirmEmail(siteName(), email, list.name, url, CONFIRM_DAYS));
  }
  return "ok";
}

export type Confirmation = { list_code: string; list_name: string; email: string; status: "pending" | "confirmed" };

/**
 * Looks a confirmation link up without using it (the page asks the reader to press Confirm).
 * A used link still shows that the sign-up is confirmed, until it expires.
 */
export async function findConfirmation(token: string): Promise<Confirmation | null> {
  if (!/^[A-Za-z0-9_-]{20,100}$/.test(token)) return null;
  const [row] = await appQuery<Confirmation>(
    `SELECT s.list_code, l.name AS list_name, s.email, s.status
       FROM app_newsletter_subscription s JOIN app_newsletter_list l ON l.list_code = s.list_code
      WHERE s.confirm_token_hash = ? AND s.status IN ('pending', 'confirmed') AND l.is_active = 1
        AND s.requested_at > NOW(6) - INTERVAL ${CONFIRM_DAYS} DAY`,
    [sha256Hex(token)],
  );
  return row ?? null;
}

/** Confirms a pending sign-up. A link confirms once; pressing again changes nothing. */
export async function confirmSubscription(token: string, client: ClientInfo): Promise<boolean> {
  const found = await findConfirmation(token);
  if (!found || found.status !== "pending") return false;
  const res = await appExec(
    `UPDATE app_newsletter_subscription SET status = 'confirmed', confirmed_at = NOW(6), ip = ?
      WHERE confirm_token_hash = ? AND status = 'pending'`,
    [client.ip, sha256Hex(token)],
  );
  if (!res.affectedRows) return false;
  await audit("newsletter_confirm", client, {}, found.list_code, null);
  return true;
}

export type MySubscription = { list_code: string; status: string };

export async function mySubscriptions(email: string): Promise<MySubscription[]> {
  return appQuery<MySubscription>("SELECT list_code, status FROM app_newsletter_subscription WHERE email = ?", [email]);
}

/** Account page: set the user's lists to exactly `selected`. */
export async function setMySubscriptions(
  user: { userId: number; orgId: number; email: string },
  selected: string[],
  client: ClientInfo,
): Promise<void> {
  const available = (await lists(true)).map((l) => l.list_code);
  const want = new Set(selected.filter((s) => available.includes(s)));
  for (const code of available) {
    if (want.has(code)) {
      await appExec(
        `INSERT INTO app_newsletter_subscription (list_code, email, user_id, status, source, consent_text, confirmed_at, ip)
         VALUES (?, ?, ?, 'confirmed', 'account', ?, NOW(6), ?)
         ON DUPLICATE KEY UPDATE user_id = VALUES(user_id),
           confirmed_at = IF(status = 'confirmed', confirmed_at, NOW(6)),
           consent_text = IF(status = 'confirmed', consent_text, VALUES(consent_text)),
           source = IF(status = 'confirmed', source, 'account'),
           unsubscribed_at = NULL, status = 'confirmed'`,
        [code, user.email, user.userId, consentText(), client.ip],
      );
    } else {
      await appExec(
        `UPDATE app_newsletter_subscription SET status = 'unsubscribed', unsubscribed_at = NOW(6)
          WHERE list_code = ? AND email = ? AND status <> 'unsubscribed'`,
        [code, user.email],
      );
    }
  }
  await audit("newsletter_change", client, { userId: user.userId, orgId: user.orgId }, null, { lists: [...want] });
}
