#!/bin/bash
# Weekly full refresh, then a fresh training dataset. launchd: Sunday 06:00.
source "$(dirname "$0")/common.sh"
reitstreet universe || exit $?
reitstreet sec-sync --force; RC=$?
reitstreet fetch-ir
reitstreet parse-docs
reitstreet dq
reitstreet export
reitstreet monitor
# keep the model's training data current; training itself is started by hand (docs/mac/README.md)
GOLD=(); [ -f "$ROOT/eval/gold.jsonl" ] && GOLD=(--gold "$ROOT/eval/gold.jsonl")
reitstreet-ai build-dataset --name "weekly-$(date +%Y%m%d)" "${GOLD[@]}" || true
exit $RC
