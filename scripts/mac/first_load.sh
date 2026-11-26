#!/bin/bash
# First load after setup.sh: full SEC history, IR supplementals, parse, checks, spot check.
source "$(dirname "$0")/common.sh"
reitstreet sec-sync --full-history; RC=$?
reitstreet fetch-ir --backfill
reitstreet parse-docs
reitstreet load-properties
reitstreet build-properties
reitstreet dq
reitstreet spotcheck --tickers PLD,O,SPG
reitstreet status
exit $RC
