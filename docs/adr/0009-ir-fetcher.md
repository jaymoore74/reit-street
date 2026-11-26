# ADR 0009: IR website fetcher

Status: accepted, 2026-11-26

## Context
Supplementals are published on each REIT's IR website, often days before (or instead of)
an 8-K exhibit. A probe of 186 results pages on 2026-11-26 found:

| Result | Companies | Meaning |
|---|---:|---|
| works | 60 | static HTML lists supplemental PDFs (1,865 links, 55 of them current to Q3 2026) |
| needs headless | 84 | page is built by JavaScript (78 of 87 Q4 sites) |
| no supplemental | 38 | site does not publish one (mostly smaller and mortgage REITs) |
| error | 4 | ADC, LOAN (client error), HST (cross-host redirect), TRTX (robots.txt unreadable) |
| not probed | 3 | BSRT, CPT (no results URL), TPTS (no IR site) |

Sites move, get redesigned and block crawlers. The fetcher has to keep working with
that and say loudly when it stops.

## Decision
1. **Strategies behind one interface** (`sources/ir/strategies.py`), tried in a fixed
   chain per results page: Q4 JSON feed (only with a configured key), the company's
   profiled strategy, static HTML, then headless Chromium (Playwright, only when the page
   proved script-built and `[ir] headless = true`). SEC 8-K EX-99 exhibits, loaded by
   `sec-sync`, are the fallback and the cross-check.
2. **A versioned profile per company** (`reference/data/ir_profiles.csv`, loaded into
   `ref_ir_profile` with its file hash) records strategy, platform, whether a supplemental
   is expected, and the probe result. Changes go through Git, not edits in production.
   `scripts/dev/build_ir_profiles.py` regenerates it from a probe and keeps hand edits.
3. **Every link is recorded** in `stg_ir_link` (kind, period, where the period came from,
   status), not only the files downloaded. Downloads are newest first, capped per company
   per run; the rest is a backlog for the next run.
4. **Classification is rules on link text, then file name, then nearby headings.** Period
   inference prefers quarter tokens over dates (a date in a file name is often the publish
   date) and records its source. PDF first-page text is the last resort.
5. **Identity is the content hash.** A document is registered once per distinct file
   (`stg_document.source_url = url#sha256=...`), so a reused URL keeps every version.
   `published_date` is the day we first saw it: conservative for point-in-time use.
6. **Polite by construction:** one generic User-Agent with no personal details, robots.txt
   per RFC 9309 (cached per host, Crawl-delay honored up to 30 s, unreachable means
   disallow), per-host rate limit, retries with backoff, conditional GETs (ETag,
   Last-Modified). The magic bytes of every download are checked, so a login or disclaimer
   page is never stored as a supplemental.
7. **Failure is visible:** each check writes `ops_ir_check`. The monitor alerts on a layout
   break (a page that listed supplementals lists none), on N failures in a row, and on
   supplementals overdue against the company's own publication lag or an earnings release
   already out. `ops_ir_coverage` shows works, needs_headless, blocked, broken, no_supp.

## Consequences
- 60 companies are covered today with no browser. The 84 script-built sites need headless
  on the Windows PC or Q4 feed keys. Q4 states its API needs permission, so feed keys are
  not used until that is settled.
- No IR email alert signups happen in code; that is a separate, manual decision.
- Adding a platform is a new strategy class plus profile rows; nothing else changes.
