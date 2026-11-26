# ADR 0003: Content-addressed, immutable raw store

Status: accepted, 2026-11-26

## Context
Parsers will have bugs and sources will change format. Re-downloading years of history
is slow, may be impossible (documents disappear from IR sites), and would not reproduce
what we saw at the time.

## Decision
Every response body is stored once, gzipped, at `raw/sha256/ab/cd/<sha256>.gz`, keyed by
the SHA-256 of the uncompressed bytes. `raw_object` records it; `raw_fetch` records every
attempt (URL, status, headers, timing, run) including failures. Nothing in the pipeline
updates or deletes raw data. Reads verify the hash.

## Consequences
- `replay` rebuilds staging and the warehouse from raw with no network, optionally as of a
  past time, so parser fixes apply to all history.
- Duplicate documents (the same PDF from SEC, the IR site and an archive) are detected by hash.
- Storage grows monotonically; at REIT scale this is tens of GB. The raw folder must be
  backed up with the database.
