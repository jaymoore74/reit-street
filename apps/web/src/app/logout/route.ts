import { NextResponse, type NextRequest } from "next/server";
import { audit } from "@/lib/audit";
import { env } from "@/lib/env";
import { clientInfo, sameOrigin } from "@/lib/request";
import { endSession } from "@/lib/session";

/** Sign out is a POST from our own pages only, so another site can't sign people out. */
export async function POST(request: NextRequest) {
  const base = env().RS_PUBLIC_BASE_URL;
  if (!sameOrigin(request.headers.get("origin"), base)) return new NextResponse("Forbidden", { status: 403 });
  const user = await endSession();
  if (user) await audit("logout", await clientInfo(), { userId: user.userId, orgId: user.orgId });
  return NextResponse.redirect(new URL("/", base), 303);
}
