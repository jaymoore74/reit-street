import "server-only";
import { appExec, appQuery } from "./db";
import { audit } from "./audit";
import { siteName } from "./brand";
import { emailEnabled, sendInBackground } from "./email";
import { passwordResetEmail } from "./email-templates";
import { env } from "./env";
import { newToken, sha256Hex } from "./tokens";
import type { ClientInfo } from "./request";

/**
 * Self-service "forgot password", only when the site sends email. The answer is the same
 * whether or not the address has an account, and the email goes out in the background, so
 * the form can't be used to find out who is a client. Staff can still issue links by hand
 * (`reitstreet web user-reset`).
 */
export const RESET_MINUTES = 60;
export const REQUESTS_PER_IP_PER_HOUR = 5;
/** At most one self-service link per account in this many minutes. */
export const PER_ACCOUNT_MINUTES = 10;

export async function requestPasswordReset(email: string, client: ClientInfo): Promise<void> {
  if (!emailEnabled()) return;
  if (client.ip !== "unknown") {
    const [r] = await appQuery<{ n: number }>(
      `SELECT COUNT(*) AS n FROM app_audit_event
        WHERE event_type = 'password_reset_request' AND ip = ? AND occurred_at > NOW(6) - INTERVAL 1 HOUR`,
      [client.ip],
    );
    if (Number(r?.n ?? 0) >= REQUESTS_PER_IP_PER_HOUR) return;
  }
  const [user] = await appQuery<{ user_id: number; org_id: number; recent: number }>(
    `SELECT u.user_id, u.org_id,
            EXISTS (SELECT 1 FROM app_user_token t
                     WHERE t.user_id = u.user_id AND t.purpose = 'password_reset'
                       AND t.created_at > NOW(6) - INTERVAL ${PER_ACCOUNT_MINUTES} MINUTE) AS recent
       FROM app_user u JOIN app_organization o ON o.org_id = u.org_id
      WHERE u.email = ? AND u.status = 'active' AND o.status = 'active'`,
    [email],
  );
  // every request is logged, with the account only when there is one
  await audit("password_reset_request", client, { userId: user?.user_id, orgId: user?.org_id }, null, null);
  if (!user || Number(user.recent)) return;
  const token = newToken();
  // a new link replaces any earlier unused one, as with links staff issue
  await appExec(
    "UPDATE app_user_token SET used_at = NOW(6) WHERE user_id = ? AND purpose = 'password_reset' AND used_at IS NULL",
    [user.user_id],
  );
  await appExec(
    `INSERT INTO app_user_token (token_hash, user_id, purpose, created_by, expires_at)
     VALUES (?, ?, 'password_reset', 'self-service', NOW(6) + INTERVAL ${RESET_MINUTES} MINUTE)`,
    [sha256Hex(token), user.user_id],
  );
  const url = `${env().RS_PUBLIC_BASE_URL.replace(/\/$/, "")}/reset/${token}`;
  sendInBackground(passwordResetEmail(siteName(), email, url, RESET_MINUTES));
}
