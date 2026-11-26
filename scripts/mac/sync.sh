#!/bin/bash
# Incremental SEC sync, then data quality checks and the monitor. launchd: every 30 minutes, weekdays.
source "$(dirname "$0")/common.sh"
reitstreet sec-sync; RC=$?
reitstreet dq
reitstreet monitor
exit $RC
