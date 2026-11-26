import type { Metadata } from "next";
import { headers } from "next/headers";
import { env } from "@/lib/env";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "REIT Street", template: "%s | REIT Street" },
  description: "REIT research and data.",
  robots: { index: false, follow: false }, // not public yet
};

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
