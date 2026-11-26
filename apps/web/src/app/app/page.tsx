import type { Metadata } from "next";
import Link from "next/link";
import { dataStatus } from "@/lib/data";
import { hasCoverage } from "@/lib/entitlements";
import { listArticles } from "@/lib/research";
import { entitlementsFor, requireUser } from "@/lib/session";

export const metadata: Metadata = { title: "Dashboard" };

export default async function Dashboard() {
  const user = await requireUser("/app");
  const ent = await entitlementsFor(user.orgId);
  if (!ent.active) {
    return (
      <div className="card">
        <h1>Your firm&apos;s subscription isn&apos;t active</h1>
        <p>Contact us to renew. Your account settings still work.</p>
      </div>
    );
  }
  const [status, articles] = await Promise.all([
    hasCoverage(ent) ? dataStatus(ent) : Promise.resolve({ companies: 0, latestFiling: null }),
    listArticles(ent, 5),
  ]);
  return (
    <>
      <h1>Welcome{user.fullName ? `, ${user.fullName.split(" ")[0]}` : ""}</h1>
      <div className="grid2">
        <div className="card">
          <p className="muted small">Companies in your coverage</p>
          <p className="stat">{status.companies}</p>
          <Link href="/app/companies">Browse companies</Link>
        </div>
        <div className="card">
          <p className="muted small">Latest SEC filing in your coverage</p>
          <p className="stat">{status.latestFiling ?? "None"}</p>
          <Link href="/app/data">Download data</Link>
        </div>
      </div>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Latest research</h2>
        {articles.length ? (
          <ul>
            {articles.map((a) => (
              <li key={a.slug}>
                <Link href={`/app/research/${a.slug}`}>{a.title}</Link>{" "}
                <span className="muted small">{a.published_at.slice(0, 10)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No research published for your subscription yet.</p>
        )}
      </div>
    </>
  );
}
