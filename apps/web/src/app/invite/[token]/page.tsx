import type { Metadata } from "next";
import { SetPasswordForm } from "@/components/forms";
import { findToken } from "@/lib/auth";

export const metadata: Metadata = { title: "Accept invitation", referrer: "no-referrer" };

export default async function InvitePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const invite = await findToken(token, "invite");
  return (
    <main className="narrow">
      <div className="card">
        <h1>Create your account</h1>
        {invite ? (
          <>
            <p className="muted">
              Choose a password for <strong>{invite.email}</strong>.
            </p>
            <SetPasswordForm token={token} purpose="invite" />
          </>
        ) : (
          <p>This invitation has expired or was already used. Ask your account contact for a new one.</p>
        )}
      </div>
    </main>
  );
}
