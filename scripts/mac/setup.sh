#!/bin/bash
# One-time setup of REIT Street on a Mac with Apple silicon. Safe to run again.
#   cd ~/reit-street && scripts/mac/setup.sh
# Installs MySQL 8.4 LTS and Python 3.12 with Homebrew, creates the database and two
# logins (reit: read/write; reit_ro: read only, for the model), keeps both passwords in
# the login keychain, creates .venv with the pipeline and the MLX packages, writes
# config.local.ini and loads the schema and the REIT universe.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

say() { printf '\n== %s\n' "$*"; }

[ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ] || { echo "This script is for a Mac with Apple silicon."; exit 2; }
command -v brew >/dev/null || { echo "Install Homebrew first (https://brew.sh), then run this again."; exit 2; }

say "Homebrew packages"
brew install mysql@8.4 python@3.12 git
brew services start mysql@8.4
MYSQL="$(brew --prefix mysql@8.4)/bin/mysql"
for _ in $(seq 1 30); do "$MYSQL" -uroot -e "SELECT 1" >/dev/null 2>&1 && break; sleep 1; done

say "Passwords (login keychain)"
pw() {  # existing keychain entry, or a new random one stored there
  local acct="$1" p
  p="$(security find-generic-password -s reitstreet-db -a "$acct" -w 2>/dev/null || true)"
  if [ -z "$p" ]; then
    p="$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-28)"
    security add-generic-password -U -s reitstreet-db -a "$acct" -w "$p"
  fi
  printf '%s' "$p"
}
RW="$(pw reit)"; RO="$(pw reit_ro)"

say "Database and logins"
"$MYSQL" -uroot <<SQL
CREATE DATABASE IF NOT EXISTS reitstreet CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS 'reit'@'localhost' IDENTIFIED BY '${RW}';
ALTER USER 'reit'@'localhost' IDENTIFIED BY '${RW}';
GRANT ALL ON reitstreet.* TO 'reit'@'localhost';
CREATE USER IF NOT EXISTS 'reit_ro'@'localhost' IDENTIFIED BY '${RO}';
ALTER USER 'reit_ro'@'localhost' IDENTIFIED BY '${RO}';
GRANT SELECT, SHOW VIEW ON reitstreet.* TO 'reit_ro'@'localhost';
FLUSH PRIVILEGES;
SQL
unset RW RO

say "Python environment"
[ -d .venv ] || "$(brew --prefix python@3.12)/bin/python3.12" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -c constraints.txt -e '.[dev,mac]'

say "Configuration"
if [ ! -f config.local.ini ]; then
  cp config/config.local.example.ini config.local.ini
  read -r -p "Your name and email for SEC's User-Agent (e.g. Jane Smith jane@firm.com): " UA
  /usr/bin/sed -i '' "s|^user_agent = YOUR NAME your.email@example.com|user_agent = ${UA}|" config.local.ini
fi
export REITSTREET_PROFILE=local
# shellcheck disable=SC1091
source scripts/mac/common.sh
reitstreet config

say "Schema and universe"
reitstreet migrate
reitstreet universe
reitstreet-ai doctor || true

cat <<'MSG'

Done. Next (docs/mac/README.md):
  scripts/mac/first_load.sh            full SEC history (1 to 2 hours)
  scripts/mac/install_launchd.sh       scheduled jobs, like the Windows tasks
  reitstreet-ai models                 which models fit this Mac
MSG
