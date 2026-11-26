#!/bin/bash
# Install (or reinstall) the scheduled jobs as launchd agents for this user.
#   scripts/mac/install_launchd.sh            pipeline jobs
#   scripts/mac/install_launchd.sh --model    also keep the model server running
#   scripts/mac/install_launchd.sh --remove   remove them all
# Times are this Mac's local time. A job due while the Mac sleeps runs when it wakes.
# Logs: data/logs/launchd-<job>.log
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
mkdir -p "$AGENTS" "$ROOT/data/logs"
JOBS=(sync ir_fetch weekly monitor backup)
[ "${1:-}" = "--model" ] && JOBS+=(model)

remove() {
  for f in "$AGENTS"/com.reitstreet.*.plist; do
    [ -e "$f" ] || continue
    launchctl bootout "$DOMAIN" "$f" 2>/dev/null || true
    rm -f "$f"
  done
}
remove
[ "${1:-}" = "--remove" ] && { echo "Removed."; exit 0; }

for job in "${JOBS[@]}"; do
  src="$ROOT/scripts/mac/launchd/$job.plist.in"
  dst="$AGENTS/com.reitstreet.$job.plist"
  /usr/bin/sed -e "s|@ROOT@|$ROOT|g" "$src" > "$dst"
  plutil -lint "$dst" >/dev/null
  launchctl bootstrap "$DOMAIN" "$dst"
  echo "installed com.reitstreet.$job"
done
launchctl list | grep com.reitstreet || true
