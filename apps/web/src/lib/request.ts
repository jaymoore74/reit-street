import "server-only";
import { headers } from "next/headers";
import { env } from "./env";

export type ClientInfo = { ip: string; userAgent: string | null };

/**
 * Client address. Behind an AWS load balancer the last RS_TRUSTED_PROXY_HOPS entries of
 * X-Forwarded-For were added by our own proxies; the entry before them is the client.
 * Entries further left are supplied by the client and are never trusted.
 */
export function clientIpFrom(xff: string | null, hops: number): string {
  if (!xff || hops < 1) return "unknown";
  const parts = xff.split(",").map((s) => s.trim()).filter(Boolean);
  const ip = parts[parts.length - hops];
  return ip && /^[0-9a-fA-F:.]{2,45}$/.test(ip) ? ip : "unknown";
}

export async function clientInfo(): Promise<ClientInfo> {
  const h = await headers();
  return {
    ip: clientIpFrom(h.get("x-forwarded-for"), env().RS_TRUSTED_PROXY_HOPS),
    userAgent: h.get("user-agent")?.slice(0, 512) ?? null,
  };
}

/** For route handlers that change state: the request must come from our own pages. */
export function sameOrigin(originHeader: string | null, base: string): boolean {
  if (!originHeader) return false;
  try {
    return new URL(originHeader).origin === new URL(base).origin;
  } catch {
    return false;
  }
}
