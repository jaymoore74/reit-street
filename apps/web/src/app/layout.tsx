import type { Metadata } from "next";
import { headers } from "next/headers";
import { siteName } from "@/lib/brand";
import { env } from "@/lib/env";
import "./globals.css";

export async function generateMetadata(): Promise<Metadata> {
  const name = siteName();
  return {
    title: { default: name, template: `%s | ${name}` },
    description: "REIT research and data.", // PLACEHOLDER copy, see docs/web-copy-review.md
    robots: { index: false, follow: false }, // not public yet
  };
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  await headers(); // every page renders per request, so each gets its own CSP nonce
  const sample = env().RS_SAMPLE_DATA;
  return (
    <html lang="en">
      <body>
        {sample && (
          <div className="banner" role="note">
            Sample data: figures on this site are synthetic test values, not real company results.
          </div>
        )}
        {children}
      </body>
    </html>
  );
}
