# Dead-letter triage

Anything that cannot be processed (a company, a filing, a document) lands in
`ops_dead_letter` with the error and traceback. Nothing is silently dropped.

```powershell
reitstreet dead-letter list                 # open items, most recent first
reitstreet dead-letter show <id>            # error, traceback, payload, run
```

## Decide

| Cause | Action |
|---|---|
| Transient (timeout, 5xx, connection reset) | Re-run the job with `--resume <run_id>`; then `resolve` |
| SEC format change (parse error on a JSON or index page) | Fix the parser, add a fixture test, `replay`, then `resolve` |
| Document has no usable content (cover page, image-only) | `ignore` with a note |
| Wrong company or period identified on import | Fix the manifest, re-import, `resolve` |

```powershell
reitstreet dead-letter resolve <id> --note "fixed parser for new index layout, replayed"
reitstreet dead-letter ignore <id> --note "image-only PDF, no tables"
```

A note is required, so the audit trail always says why. The monitor alerts on items open
for more than 3 days.

## Documents needing review

Imports that could not identify ticker or period are stored with status `needs_review`
in `stg_document`. Fix them by re-importing with a manifest (see
[add-archive.md](add-archive.md)).
