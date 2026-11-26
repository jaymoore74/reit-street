import Link from "next/link";
import { lists } from "@/lib/newsletter";
import { currentUser } from "@/lib/session";
import { NewsletterForm } from "@/components/forms";

export default async function Home() {
  const [user, publicLists] = await Promise.all([currentUser(), lists(false)]);
  return (
    <>
      <header className="topbar">
        <Link href="/" className="brand">
          REIT Street
        </Link>
        <span className="spacer" />
        <Link href={user ? "/app" : "/login"} className="button">
          {user ? "Go to your dashboard" : "Client sign in"}
        </Link>
      </header>
      <main>
        <div className="card">
          <h1>REIT research and data</h1>
          {/* Placeholder copy. Positioning, pricing and marketing text come from the business plan. */}
          <p className="muted">
            Placeholder: company-level and property-level data for US REITs, from SEC filings and company
            supplementals, with research and downloads for clients.
          </p>
        </div>
        <div className="grid2">
          <div className="card">
            <h2>Newsletter</h2>
            {publicLists.length ? (
              <NewsletterForm lists={publicLists.map((l) => ({ code: l.list_code, name: l.name }))} />
            ) : (
              <p className="muted">No newsletters yet.</p>
            )}
          </div>
          <div className="card">
            <h2>Clients</h2>
            <p className="muted">Accounts are by invitation. Ask your firm&apos;s administrator or contact us.</p>
            <Link href="/login">Sign in</Link>
          </div>
        </div>
      </main>
    </>
  );
}
