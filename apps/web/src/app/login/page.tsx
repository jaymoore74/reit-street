import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { LoginForm } from "@/components/forms";
import { currentUser } from "@/lib/session";
import { safeNext } from "../actions";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const next = await safeNext((await searchParams).next ?? "/app");
  if (await currentUser()) redirect(next);
  return (
    <main className="narrow">
      <p>
        <Link href="/" className="brand">
          REIT Street
        </Link>
      </p>
      <div className="card">
        <h1>Client sign in</h1>
        <LoginForm next={next} />
        <p className="muted small" style={{ marginTop: 12 }}>
          Forgot your password? Ask your account contact for a reset link.
        </p>
      </div>
    </main>
  );
}
