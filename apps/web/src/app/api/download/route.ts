import { NextResponse, type NextRequest } from "next/server";
import { runDownload } from "@/lib/download";
import { env } from "@/lib/env";
import { clientInfo, sameOrigin } from "@/lib/request";
import { currentUser, entitlementsFor } from "@/lib/session";
import { formToObject } from "@/lib/validation";

export const dynamic = "force-dynamic";

/** POST from the data page's form. The file comes back as an attachment; refusals as plain text. */
export async function POST(request: NextRequest) {
  if (!sameOrigin(request.headers.get("origin"), env().RS_PUBLIC_BASE_URL))
    return new NextResponse("Forbidden", { status: 403 });
  const user = await currentUser();
  if (!user) return new NextResponse("Sign in first.", { status: 401 });

  const form = await request.formData();
  const outcome = await runDownload(user, await entitlementsFor(user.orgId), formToObject(form), await clientInfo());
  const common = { "Cache-Control": "no-store", "X-Download-Id": outcome.downloadId };
  if (!outcome.ok) {
    return new NextResponse(`${outcome.error}\n\nDownload id: ${outcome.downloadId}\n`, {
      status: outcome.status,
      headers: { ...common, "Content-Type": "text/plain; charset=utf-8" },
    });
  }
  return new NextResponse(new Uint8Array(outcome.body), {
    status: 200,
    headers: {
      ...common,
      "Content-Type": outcome.contentType,
      "Content-Length": String(outcome.body.length),
      "Content-Disposition": `attachment; filename="${outcome.filename}"`,
    },
  });
}
