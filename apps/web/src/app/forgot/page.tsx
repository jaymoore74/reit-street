import type { Metadata } from "next";
import Link from "next/link";
import { ForgotPasswordForm } from "@/components/forms";
import { siteName } from "@/lib/brand";
import { emailEnabled } from "@/lib/email";

export const metadata: Metadata = { title: "Forgot password" };

export default function ForgotPage() {
  return (
    <main className="narrow">
      <p>
        <Link href="/" className="brand">
          {siteName()}
        </Link>
      </p>
      <div className="card">
        <h1>Forgot your password?</h1>
        {emailEnabled() ? (
          <>
            <p className="muted">Enter the email you sign in with and we&apos;ll send a link to choose a new password.</p>
            <ForgotPasswordForm />
          </>
        ) : (
          <p>Ask your account contact for a reset link.</p>
        )}
        <p className="small" style={{ marginTop: 12 }}>
          <Link href="/login">Back to sign in</Link>
        </p>
      </div>
    </main>
  );
}
