import "server-only";
import { appExec } from "./db";
import type { ClientInfo } from "./request";

export type AuditType =
  | "login"
  | "login_failed"
  | "logout"
  | "invite_accepted"
  | "password_reset"
  | "password_changed"
  | "sessions_revoked"
  | "newsletter_signup"
  | "newsletter_confirm"
  | "password_reset_request"
  | "newsletter_change"
  | "download_denied"
  | "research_view";

/** Append-only audit log. Failure to write is raised: actions that cannot be audited do not happen. */
export async function audit(
  type: AuditType,
  client: ClientInfo,
  who: { userId?: number | null; orgId?: number | null } = {},
  target: string | null = null,
  detail: Record<string, unknown> | null = null,
): Promise<void> {
  await appExec(
    `INSERT INTO app_audit_event (event_type, user_id, org_id, target, detail, ip, user_agent)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
    [type, who.userId ?? null, who.orgId ?? null, target, detail ? JSON.stringify(detail) : null, client.ip, client.userAgent],
  );
}
