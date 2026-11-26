import type { Metadata } from "next";
import Link from "next/link";
import { listArticles } from "@/lib/research";
import { entitlementsFor, requireUser } from "@/lib/session";

export const metadata: Metadata = { title: "Research" };

export default async function ResearchList() {
  const user = await requireUser("/app/research");
  const articles = await listArticles(await entitlementsFor(user.orgId));
  return (
    <>
      <h1>Research</h1>
      {articles.length === 0 && <p className="muted">No research published for your subscription yet.</p>}
      {articles.map((a) => (
        <div className="card" key={a.slug}>
          <h2 style={{ marginTop: 0 }}>
            <Link href={`/app/research/${a.slug}`}>{a.title}</Link>
          </h2>
          <p className="muted small">
            {a.published_at.slice(0, 10)}
            {a.author ? ` · ${a.author}` : ""}
          </p>
          {a.summary && <p>{a.summary}</p>}
          {a.tickers && (
            <div className="tags">
              {a.tickers.split(",").map((t) => (
                <span className="tag" key={t}>
                  {t.trim()}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
    </>
  );
}
