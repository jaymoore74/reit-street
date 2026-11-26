#!/bin/bash
# New supplementals from each REIT's IR page, then freshness checks and the monitor. launchd: 06:00 and 15:00 weekdays.
source "$(dirname "$0")/common.sh"
reitstreet fetch-ir; RC=$?
reitstreet dq --only freshness,ops
reitstreet monitor
exit $RC
