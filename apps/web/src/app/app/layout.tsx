import Link from "next/link";
import { requireUser } from "@/lib/session";

/** Every page under /app needs a signed-in user; each page also checks entitlements for its own data. */
export default async function ClientLayout({ children }: { children: React.ReactNode }) {
  const user = await requireUser();
  const staff = user.role === "staff" || user.role === "staff_admin";
  return (
    <>
      <header className="topbar">
        <Link href="/app" className="brand">
          REIT Street
        </Link>
        <nav className="nav" aria-label="Main">
          <Link href="/app/research">Research</Link>
          <Link href="/app/companies">Companies</Link>
          <Link href="/app/data">Data downloads</Link>
          <Link href="/app/account">Account</Link>
        </nav>
        <span className="spacer" />
        <span className="muted small">
          {user.fullName ?? user.email} · {user.orgName}
          {staff ? " (staff)" : ""}
        </span>
        <form action="/logout" method="post">
          <button type="submit" className="secondary">
            Sign out
          </button>
        </form>
      </header>
      <main>{children}</main>
    </>
  );
}
