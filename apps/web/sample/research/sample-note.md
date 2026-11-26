---
slug: sample-note-industrial-revenue
title: Sample note: how a research piece looks on the site
summary: Placeholder research for development. The figures behind it are synthetic test fixtures, not real company data.
research: core
tickers: PLD, O
sectors: Industrial
author: REIT Street (sample)
status: draft
---

**This is sample content for development. It is not research and the numbers are synthetic.**

## What a note can hold

- Headings, lists and tables in Markdown
- Links to company pages, such as [PLD](/app/companies/PLD)
- Tables of figures pulled from the warehouse

| Metric | Source | Notes |
|---|---|---|
| Total revenue | XBRL, 10-Q and 10-K | Q4 derived as FY minus Q1 to Q3 |
| FFO per share | Supplemental | Parser pending |

## How notes are published

A note is a Markdown file with a front matter block. Staff save it with
`reitstreet web article path/to/note.md --status published`. Every save is kept as a
version with its SHA-256, so we can show exactly what a client could read on any date.
