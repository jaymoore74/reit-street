# Schema change

All schema changes are Alembic revisions in `src/reitstreet/migrations/versions/`.
Never change a table by hand on a live database.

1. Create the revision:
   ```
   alembic revision -m "add wh_lease_expiry"
   ```
2. Write `upgrade()` and `downgrade()` with `op.execute()` DDL. Every new table and column
   needs a `COMMENT` (a test enforces it) and the layer prefix (`raw_`, `ref_`, `stg_`,
   `wh_`, `dl_`, `ops_`).
3. Prefer additive changes. To rename or drop, add the new column, backfill, switch the
   code, and drop in a later release.
4. `dl_*` views are the client contract. Adding a column is fine; renaming, removing or
   changing the meaning of one is a breaking change: add a new view (`dl_metrics_v2`) and
   announce the old one's retirement.
5. Regenerate the data dictionary and commit it with the migration:
   ```
   reitstreet migrate
   reitstreet db-docs --out docs/data-dictionary.md
   ```
6. CI runs upgrade, downgrade and upgrade against MySQL 8.4 and checks the dictionary.
7. Deploy: back up first ([backup-restore.md](backup-restore.md)), then `reitstreet migrate`.
