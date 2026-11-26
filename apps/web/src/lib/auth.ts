import "server-only";
import { appExec, appQuery } from "./db";
import { audit } from "./audit";
import { hashPassword, needsRehash, passwordProblem, verifyPassword } from "./password";
import { createSession, revokeSessions } from "./session";
import { sha256Hex } from "./tokens";
import type { ClientInfo } from "./request";

/** Sign-in throttling (ADR 0013). */
export const MAX_ACCOUNT_FAILURES = 5;
export const LOCK_MINUTES = 15;
export const MAX_IP_FAILURES = 30;
export const IP_WINDOW_MINUTES = 15;

export const GENERIC_LOGIN_ERROR =
  "That email and password don't match an active account, or the account is locked for 15 minutes after repeated failures.";

type UserRow = {
  user_id: number;
  org_id: number;
  email: string;
  status: string;
  password_hash: string | null;
  locked: number;
  org_status: string;
};

async function recordAttempt(ip: string, email: string, ok: boolean) {
  await appExec("INSERT INTO app_login_attempt (ip, email, succeeded) VALUES (?, ?, ?)", [ip, email.slice(0, 254), ok ? 1 : 0]);
}

export function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}

export async function signIn(
  emailRaw: string,
  password: string,
  client: ClientInfo,
): Promise<{ ok: true } | { ok: false; error: string }> {
  const email = normalizeEmail(emailRaw);
  const ipFails = await appQuery<{ n: number }>(
    `SELECT COUNT(*) AS n FROM app_login_attempt
      WHERE ip = ? AND succeeded = 0 AND attempted_at > NOW(6) - INTERVAL ${IP_WINDOW_MINUTES} MINUTE`,
    [client.ip],
  );
  if (client.ip !== "unknown" && Number(ipFails[0]?.n ?? 0) >= MAX_IP_FAILURES) {
    await recordAttempt(client.ip, email, false);
    await audit("login_failed", client, {}, email, { reason: "ip_throttled" });
    return { ok: false, error: "Too many sign-in attempts from your network. Try again in 15 minutes." };
  }

  const rows = await appQuery<UserRow>(
    `SELECT u.user_id, u.org_id, u.email, u.status, u.password_hash,
            (u.locked_until IS NOT NULL AND u.locked_until > NOW(6)) AS locked, o.status AS org_status
       FROM app_user u JOIN app_organization o ON o.org_id = u.org_id
      WHERE u.email = ?`,
    [email],
  );
  const u = rows[0];
  const usable = u && u.status === "active" && u.org_status === "active" && !Number(u.locked);
  // always run the hash, so timing does not reveal which emails have accounts
  const good = await verifyPassword(password, usable ? u.password_hash : null);

  if (!u || !usable || !good) {
    await recordAttempt(client.ip, email, false);
    if (u && usable && !good) {
      await appExec(
        `UPDATE app_user
            SET failed_logins = failed_logins + 1,
                locked_until = IF(failed_logins + 1 >= ${MAX_ACCOUNT_FAILURES}, NOW(6) + INTERVAL ${LOCK_MINUTES} MINUTE, locked_until)
          WHERE user_id = ?`,
        [u.user_id],
      );
    }
    const reason = !u ? "no_account" : !usable ? (Number(u.locked) ? "locked" : "inactive") : "bad_password";
    await audit("login_failed", client, u ? { userId: u.user_id, orgId: u.org_id } : {}, email, { reason });
    return { ok: false, error: GENERIC_LOGIN_ERROR };
  }

  await recordAttempt(client.ip, email, true);
  const rehash = needsRehash(u.password_hash!) ? await hashPassword(password) : null;
  await appExec(
    `UPDATE app_user SET failed_logins = 0, locked_until = NULL, last_login_at = NOW(6),
            password_hash = COALESCE(?, password_hash)
      WHERE user_id = ?`,
    [rehash, u.user_id],
  );
  await createSession(u.user_id, client);
  await audit("login", client, { userId: u.user_id, orgId: u.org_id });
  return { ok: true };
}

export type TokenPurpose = "invite" | "password_reset";

type TokenRow = { user_id: number; org_id: number; email: string; full_name: string | null; status: string };

/** Looks a token up without using it. */
export async function findToken(token: string, purpose: TokenPurpose): Promise<TokenRow | null> {
  if (!/^[A-Za-z0-9_-]{20,100}$/.test(token)) return null;
  const rows = await appQuery<TokenRow>(
    `SELECT u.user_id, u.org_id, u.email, u.full_name, u.status
       FROM app_user_token t JOIN app_user u ON u.user_id = t.user_id
       JOIN app_organization o ON o.org_id = u.org_id
      WHERE t.token_hash = ? AND t.purpose = ? AND t.used_at IS NULL AND t.expires_at > NOW(6)
        AND o.status = 'active'
        AND u.status = ?`,
    [sha256Hex(token), purpose, purpose === "invite" ? "invited" : "active"],
  );
  return rows[0] ?? null;
}

/** Sets a password from an invitation or reset link, uses the token, and signs the user in. */
export async function setPasswordWithToken(
  token: string,
  purpose: TokenPurpose,
  password: string,
  client: ClientInfo,
): Promise<{ ok: true } | { ok: false; error: string }> {
  const row = await findToken(token, purpose);
  if (!row) return { ok: false, error: "This link has expired or was already used. Ask us for a new one." };
  const problem = passwordProblem(password, row.email);
  if (problem) return { ok: false, error: problem };

  // claim the token first, so two submissions cannot both use it
  const claimed = await appExec(
    "UPDATE app_user_token SET used_at = NOW(6) WHERE token_hash = ? AND used_at IS NULL AND expires_at > NOW(6)",
    [sha256Hex(token)],
  );
  if (claimed.affectedRows !== 1) return { ok: false, error: "This link has expired or was already used." };

  await appExec(
    `UPDATE app_user SET password_hash = ?, password_changed_at = NOW(6), status = 'active',
            failed_logins = 0, locked_until = NULL
      WHERE user_id = ?`,
    [await hashPassword(password), row.user_id],
  );
  const revoked = await revokeSessions(row.user_id);
  await createSession(row.user_id, client);
  await audit(purpose === "invite" ? "invite_accepted" : "password_reset", client, {
    userId: row.user_id,
    orgId: row.org_id,
  }, null, { sessions_revoked: revoked });
  return { ok: true };
}

export async function changePassword(
  user: { userId: number; orgId: number; email: string; sessionHash: string },
  current: string,
  next: string,
  client: ClientInfo,
): Promise<{ ok: true; message: string } | { ok: false; error: string }> {
  const rows = await appQuery<{ password_hash: string | null }>("SELECT password_hash FROM app_user WHERE user_id = ?", [
    user.userId,
  ]);
  if (!(await verifyPassword(current, rows[0]?.password_hash ?? null)))
    return { ok: false, error: "Your current password is wrong." };
  const problem = passwordProblem(next, user.email);
  if (problem) return { ok: false, error: problem };
  await appExec("UPDATE app_user SET password_hash = ?, password_changed_at = NOW(6) WHERE user_id = ?", [
    await hashPassword(next),
    user.userId,
  ]);
  const revoked = await revokeSessions(user.userId, user.sessionHash);
  await audit("password_changed", client, { userId: user.userId, orgId: user.orgId }, null, { sessions_revoked: revoked });
  return { ok: true, message: "Password changed. Other devices were signed out." };
}
