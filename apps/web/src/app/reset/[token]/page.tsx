import type { Metadata } from "next";
import { SetPasswordForm } from "@/components/forms";
import { findToken } from "@/lib/auth";

export const metadata: Metadata = { title: "Reset password", referrer: "no-referrer" };

export default async function ResetPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const reset = await findToken(token, "password_reset");
  return (
    <main className="narrow">
      <div className="card">
        <h1>Set a new password</h1>
        {reset ? (
          <>
            <p className="muted">
              For <strong>{reset.email}</strong>. Setting it signs out all your other sessions.
            </p>
            <SetPasswordForm token={token} purpose="password_reset" />
          </>
        ) : (
          <p>This link has expired or was already used. Ask your account contact for a new one.</p>
        )}
      </div>
    </main>
  );
}
