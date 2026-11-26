#!/bin/bash
# Shared by the job scripts: repo root, virtual environment, local profile, logs.
# Exit codes pass through so launchd and the monitor see failures.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT" || exit 2
export REITSTREET_PROFILE="${REITSTREET_PROFILE:-local}"
# launchd starts jobs with a bare PATH; Homebrew lives in /opt/homebrew on Apple silicon
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# secrets come from the login keychain, never from files in the repo (setup.sh stores them)
if [ -z "${REITSTREET_DB_PASSWORD:-}" ]; then
  REITSTREET_DB_PASSWORD="$(security find-generic-password -s reitstreet-db -a reit -w 2>/dev/null || true)"
  export REITSTREET_DB_PASSWORD
fi
# read-only login for model-written queries (reitstreet-ai ask)
if [ -z "${REITSTREET_AI_DB_URL:-}" ]; then
  RO_PW="$(security find-generic-password -s reitstreet-db -a reit_ro -w 2>/dev/null || true)"
  if [ -n "$RO_PW" ]; then
    export REITSTREET_AI_DB_URL="mysql+pymysql://reit_ro:${RO_PW}@127.0.0.1:3306/reitstreet?charset=utf8mb4"
  fi
  unset RO_PW
fi
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate" || exit 2
