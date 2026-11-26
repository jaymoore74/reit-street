# ADR 0008: Production on AWS Aurora MySQL, jobs on a self-hosted GitHub Actions runner

Status: accepted, 2026-11-26

## Context
Production needs an always-on database and scheduled jobs that do not depend on a
workstation. The owner already runs an Amazon Aurora MySQL cluster and chose it. Aurora
lives in a VPC and is normally private. Jobs are defined in GitHub. GitHub-hosted runners
have changing IP addresses, a 6-hour job limit, and share their addresses with other
customers, which matters to SEC's fair-access blocking (see sec-403 runbook).

Options weighed:
1. **GitHub-hosted runners, Aurora made public**, security group opened per run by an
   OIDC role. No servers, but the database endpoint is on the internet and every run
   edits a firewall.
2. **AWS CodeBuild-hosted runners in the VPC.** No server to patch, but outbound internet
   from a VPC needs a NAT gateway (about $33 a month plus traffic).
3. **Self-hosted runner on a small EC2 in the VPC** (chosen).

## Decision
- Database: the existing Aurora MySQL 3 cluster (MySQL 8 compatible, ADR 0007), schema
  `reitstreet`, TLS required with certificate and host name verified (`[db] ssl_ca`).
- Three logins with least privilege: `rs_migrator` (schema), `rs_loader` (data),
  `rs_reader` (read). Passwords are generated on the runner and live only in AWS Secrets
  Manager (`reitstreet/db/<role>`). The master password is read once, through a temporary
  policy, to create them.
- Jobs: GitHub Actions workflows on one self-hosted runner (EC2 t4g.small, Ubuntu 24.04
  arm64) in a public subnet of the cluster's VPC, no inbound ports, shell through Session
  Manager. Its instance role is the only AWS credential; GitHub holds no AWS keys or
  database passwords, only three non-secret variables.
- Raw store: content-addressed files on the runner's disk as a working cache, copied to
  S3 after every run (`raw/`, `logs/`, `exports/`). The role has no `s3:DeleteObject` and
  the bucket is versioned, so the raw layer is append-only (ADR 0003).
- Alerts: a failed run opens or updates a GitHub issue; the next good run closes it.

## Consequences
- About $21 a month beyond Aurora; one fixed egress IP for SEC; no job time limit.
- One server to own. Mitigations: unattended security upgrades, runner self-updates,
  disk check in ops-monitor, the box is disposable (rebuild from runbook plus S3).
- The repository must stay private: a self-hosted runner on a public repo would run code
  from anyone's pull request.
- Workstations cannot reach the database directly; use Session Manager port forwarding or
  a VPN when needed, with `rs_reader`.
- Next steps when volume grows: native S3 backend for the raw store (the `Backend`
  protocol in `rawstore.py`) so the runner disk stops growing; reserved instance or
  savings plan for the runner; Secrets Manager rotation.
