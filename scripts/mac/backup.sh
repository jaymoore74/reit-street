#!/bin/bash
# Nightly MySQL dump to data/backups (kept 14 days). The raw store (data/raw) is backed up by
# Time Machine; together they rebuild everything. launchd: daily 02:30.
source "$(dirname "$0")/common.sh"
OUT="$ROOT/data/backups"; mkdir -p "$OUT"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
MYSQL_PWD="$REITSTREET_DB_PASSWORD" mysqldump --single-transaction --routines --triggers --set-gtid-purged=OFF \
  -h 127.0.0.1 -u reit reitstreet | gzip > "$OUT/reitstreet-$STAMP.sql.gz" || exit 1
find "$OUT" -name 'reitstreet-*.sql.gz' -mtime +14 -delete
