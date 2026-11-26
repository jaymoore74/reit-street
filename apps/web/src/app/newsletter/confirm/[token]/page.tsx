import type { Metadata } from "next";
import Link from "next/link";
import { ConfirmNewsletterForm } from "@/components/forms";
import { siteName } from "@/lib/brand";
import { findConfirmation } from "@/lib/newsletter";

export const metadata: Metadata = { title: "Confirm newsletter", referrer: "no-referrer" };

/**
 * Confirming takes a button press, not just opening the link: mail filters open links to
 * scan them, and that must not sign anyone up.
 */
export default async function ConfirmPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const found = await findConfirmation(token);
  return (
    <main className="narrow">
      <p>
        <Link href="/" className="brand">
          {siteName()}
        </Link>
      </p>
      <div className="card">
        <h1>Confirm your sign-up</h1>
        {found?.status === "pending" ? (
          <>
            <p className="muted">
              Send <strong>{found.list_name}</strong> to <strong>{found.email}</strong>?
            </p>
            <ConfirmNewsletterForm token={token} />
          </>
        ) : found?.status === "confirmed" ? (
          <p role="status">
            Confirmed. You&apos;re on the list for <strong>{found.list_name}</strong>.
          </p>
        ) : (
          <p>This link has expired or was already used.</p>
        )}
      </div>
    </main>
  );
}
