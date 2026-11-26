import "server-only";
import { appQuery } from "./db";
import type { Entitlements } from "./entitlements";

export type ArticleSummary = {
  slug: string;
  title: string;
  summary: string | null;
  tickers: string | null;
  sectors: string | null;
  author: string | null;
  published_at: string;
  version: number;
};
export type Article = ArticleSummary & { body_md: string; research_code: string };

const placeholders = (n: number) => Array.from({ length: n }, () => "?").join(", ");

export async function listArticles(ent: Entitlements, limit = 50): Promise<ArticleSummary[]> {
  if (!ent.active || !ent.research.length) return [];
  return appQuery<ArticleSummary>(
    `SELECT slug, title, summary, tickers, sectors, author, published_at, version
       FROM app_research_article
      WHERE status = 'published' AND research_code IN (${placeholders(ent.research.length)})
      ORDER BY published_at DESC LIMIT ?`,
    [...ent.research, limit],
  );
}

export async function getArticle(ent: Entitlements, slug: string): Promise<Article | null> {
  if (!ent.active || !ent.research.length || !/^[a-z0-9][a-z0-9-]{0,127}$/.test(slug)) return null;
  const rows = await appQuery<Article>(
    `SELECT slug, title, summary, tickers, sectors, author, published_at, version, body_md, research_code
       FROM app_research_article
      WHERE slug = ? AND status = 'published' AND research_code IN (${placeholders(ent.research.length)})`,
    [slug, ...ent.research],
  );
  return rows[0] ?? null;
}
