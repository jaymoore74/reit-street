#!/bin/bash
# EC2 user data for the REIT Street GitHub Actions runner.
# Ubuntu Server 24.04 LTS (arm64). Paste into "Advanced details > User data" when launching
# the instance. It installs tools and the runner software; registering the runner with
# GitHub is a manual step (docs/runbooks/cloud-setup.md) because it needs a one-hour token.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update -y
apt-get install -y python3-venv python3-pip git jq curl unzip ca-certificates unattended-upgrades docker.io

# Docker builds the website image (ops-web-image workflow). A 2 GB swap file keeps the
# Next.js build from running out of memory on a 2 GB instance.
systemctl enable --now docker
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# AWS CLI v2 (the instance role supplies credentials; no keys on disk)
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o /tmp/awscliv2.zip
unzip -q -o /tmp/awscliv2.zip -d /tmp
/tmp/aws/install --update
rm -rf /tmp/aws /tmp/awscliv2.zip

# CA bundle for TLS to Aurora / RDS
mkdir -p /etc/reitstreet
curl -fsSL https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem -o /etc/reitstreet/rds-global-bundle.pem
chmod 644 /etc/reitstreet/rds-global-bundle.pem

# Service account and working data (raw store cache, logs, exports). S3 is the durable copy.
id runner >/dev/null 2>&1 || useradd -m -s /bin/bash runner
usermod -aG docker runner
mkdir -p /srv/reitstreet/data
chown -R runner:runner /srv/reitstreet

# GitHub Actions runner software (latest release; it updates itself afterwards)
ver=$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest | jq -r .tag_name | sed 's/^v//')
mkdir -p /opt/actions-runner
cd /opt/actions-runner
curl -fsSL -o runner.tgz "https://github.com/actions/runner/releases/download/v${ver}/actions-runner-linux-arm64-${ver}.tar.gz"
tar xzf runner.tgz
rm runner.tgz
./bin/installdependencies.sh
chown -R runner:runner /opt/actions-runner

# Shared libraries Chromium needs for the IR fetcher's headless mode. The browser itself is
# downloaded by the workflow into /srv/reitstreet/pw-browsers (owned by runner).
python3 -m venv /tmp/pw && /tmp/pw/bin/pip install -q "playwright==1.65.2" \
  && /tmp/pw/bin/python -m playwright install-deps chromium && rm -rf /tmp/pw
mkdir -p /srv/reitstreet/pw-browsers && chown runner:runner /srv/reitstreet/pw-browsers

echo "reitstreet runner setup done: runner ${ver}" > /etc/reitstreet/setup-done
