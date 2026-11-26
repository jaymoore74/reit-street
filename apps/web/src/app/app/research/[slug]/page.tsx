import type { Metadata } from "next";
import { notFound } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { audit } from "@/lib/audit";
import { clientInfo } from "@/lib/request";
import { getArticle } from "@/lib/research";
import { entitlementsFor, requireUser } from "@/lib/session";

export const metadata: Metadata = { title: "Research" };

export default async function ArticlePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const user = await requireUser(`/app/research/${slug}`);
  const article = await getArticle(await entitlementsFor(user.orgId), slug);
  if (!article) notFound(); // also when the subscription doesn't include it: don't reveal that it exists
  await audit("research_view", await clientInfo(), { userId: user.userId, orgId: user.orgId }, article.slug, {
    version: article.version,
  });
  return (
    <article className="card article">
      <h1>{article.title}</h1>
      <p className="muted small">
        {article.published_at.slice(0, 10)}
        {article.author ? ` · ${article.author}` : ""} · version {article.version}
      </p>
      {/* Markdown only: raw HTML in the source is shown as text, never run */}
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
        {article.body_md}
      </ReactMarkdown>
    </article>
  );
}
