import "server-only";
import { appExec, appQuery } from "./db";
import { audit } from "./audit";
import { newToken, sha256Hex } from "./tokens";
import type { ClientInfo } from "./request";

/**
 * Newsletter sign-ups are stored with the exact consent text shown. No email is sent: a mail
 * provider is a later step that needs Jason's yes (ADR 0013). Public sign-ups stay "pending"
 * until a confirmation email exists (double opt-in); signed-in clients are confirmed directly,
 * because their invitation already proved the address.
 */
export const CONSENT_TEXT =
  "I agree to receive the REIT Street newsletters I selected by email. I can unsubscribe at any time.";
export const PUBLIC_SIGNUPS_PER_IP_PER_HOUR = 5;

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
  const [list] = await appQuery<{ n: number }>(
    "SELECT COUNT(*) AS n FROM app_newsletter_list WHERE list_code = ? AND is_active = 1 AND is_public = 1",
    [listCode],
  );
  if (!Number(list?.n)) return "no_list";
  // the confirmation token is stored hashed and used once a mail provider sends confirmation links
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
    [listCode, email, CONSENT_TEXT, sha256Hex(newToken()), client.ip],
  );
  await audit("newsletter_signup", client, {}, listCode, null);
  return "ok";
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
        [code, user.email, user.userId, CONSENT_TEXT, client.ip],
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
