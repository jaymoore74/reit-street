import { NextResponse } from "next/server";
import { appQuery, whQuery } from "@/lib/db";

export const dynamic = "force-dynamic";

/** Load balancer health check: both schemas reachable. Says nothing else about the system. */
export async function GET() {
  try {
    await Promise.all([whQuery("SELECT 1 AS ok"), appQuery("SELECT 1 AS ok")]);
    return NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ ok: false }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
}
