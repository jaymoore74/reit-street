import "server-only";
import { cache } from "react";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { appExec, appQuery } from "./db";
import { secureCookies } from "./env";
import { NO_ACCESS, resolveEntitlements, todayUtc, type Entitlements, type GrantRow } from "./entitlements";
import { newToken, sha256Hex } from "./tokens";
import type { ClientInfo } from "./request";

/**
 * Server-side sessions (ADR 0013). The browser holds a random 256-bit token in an HttpOnly,
 * SameSite=Lax cookie; the database holds only its sha256. Sessions end after 7 days, or after
 * 12 hours without use, or when revoked (sign out, password change, firm suspended).
 */
export const SESSION_DAYS = 7;
export const IDLE_HOURS = 12;

export function sessionCookieName(): string {
  return secureCookies() ? "__Host-rs_session" : "rs_session";
}

export type CurrentUser = {
  userId: number;
  orgId: number;
  email: string;
  fullName: string | null;
  role: "member" | "org_admin" | "staff" | "staff_admin";
  orgName: string;
  orgIsInternal: boolean;
  sessionHash: string;
};

export async function createSession(userId: number, client: ClientInfo): Promise<void> {
  const token = newToken();
  await appExec(
    `INSERT INTO app_session (session_hash, user_id, expires_at, ip, user_agent)
     VALUES (?, ?, NOW(6) + INTERVAL ${SESSION_DAYS} DAY, ?, ?)`,
    [sha256Hex(token), userId, client.ip, client.userAgent],
  );
  (await cookies()).set(sessionCookieName(), token, {
    httpOnly: true,
    secure: secureCookies(),
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_DAYS * 24 * 3600,
  });
}

type SessionRow = {
  session_hash: string;
  user_id: number;
  org_id: number;
  email: string;
  full_name: string | null;
  role: CurrentUser["role"];
  org_name: string;
  is_internal: number;
  touch: number;
};

/** The signed-in user for this request, or null. Cached per request. */
export const currentUser = cache(async (): Promise<CurrentUser | null> => {
  const token = (await cookies()).get(sessionCookieName())?.value;
  if (!token || token.length > 100) return null;
  const hash = sha256Hex(token);
  const rows = await appQuery<SessionRow>(
    `SELECT s.session_hash, u.user_id, u.org_id, u.email, u.full_name, u.role, o.name AS org_name, o.is_internal,
            (s.last_seen_at < NOW(6) - INTERVAL 1 MINUTE) AS touch
       FROM app_session s
       JOIN app_user u ON u.user_id = s.user_id
       JOIN app_organization o ON o.org_id = u.org_id
      WHERE s.session_hash = ?
        AND s.revoked_at IS NULL
        AND s.expires_at > NOW(6)
        AND s.last_seen_at > NOW(6) - INTERVAL ${IDLE_HOURS} HOUR
        AND u.status = 'active'
        AND o.status = 'active'`,
    [hash],
  );
  const r = rows[0];
  if (!r) return null;
  if (Number(r.touch)) await appExec("UPDATE app_session SET last_seen_at = NOW(6) WHERE session_hash = ?", [hash]);
  return {
    userId: r.user_id,
    orgId: r.org_id,
    email: r.email,
    fullName: r.full_name,
    role: r.role,
    orgName: r.org_name,
    orgIsInternal: Boolean(Number(r.is_internal)),
    sessionHash: r.session_hash,
  };
});

/** For pages under /app: the signed-in user, or a redirect to the sign-in page. */
export async function requireUser(next = "/app"): Promise<CurrentUser> {
  const user = await currentUser();
  if (!user) redirect(`/login?next=${encodeURIComponent(next)}`);
  return user;
}

/** What the user's firm may see today. Cached per request. */
export const entitlementsFor = cache(async (orgId: number): Promise<Entitlements> => {
  const orgs = await appQuery<{ status: string; contract_start: string | null; contract_end: string | null }>(
    "SELECT status, contract_start, contract_end FROM app_organization WHERE org_id = ?",
    [orgId],
  );
  const org = orgs[0];
  if (!org) return NO_ACCESS;
  const grants = await appQuery<GrantRow>(
    `SELECT e.kind, e.value, e.valid_from, e.valid_to
       FROM app_entitlement e
       JOIN app_organization o ON o.org_id = ?
      WHERE e.org_id = o.org_id OR (e.plan_id IS NOT NULL AND e.plan_id = o.plan_id)`,
    [orgId],
  );
  return resolveEntitlements(grants, org, todayUtc());
});

export async function endSession(): Promise<CurrentUser | null> {
  const user = await currentUser();
  const jar = await cookies();
  const token = jar.get(sessionCookieName())?.value;
  if (token) {
    await appExec("UPDATE app_session SET revoked_at = NOW(6) WHERE session_hash = ? AND revoked_at IS NULL", [
      sha256Hex(token),
    ]);
  }
  jar.delete(sessionCookieName());
  return user;
}

/** Revoke every session of a user, optionally keeping the current one. Returns how many. */
export async function revokeSessions(userId: number, keepHash: string | null = null): Promise<number> {
  const res = await appExec(
    `UPDATE app_session SET revoked_at = NOW(6)
      WHERE user_id = ? AND revoked_at IS NULL AND (? IS NULL OR session_hash <> ?)`,
    [userId, keepHash, keepHash],
  );
  return res.affectedRows;
}
