# Cloud setup on AWS (Aurora MySQL)

How production runs: the database is the existing **Amazon Aurora MySQL** cluster. Jobs run
as GitHub Actions on a small **self-hosted runner** (EC2) inside the cluster's VPC. Raw
files, logs and exports are copied to an **S3 bucket**. Database logins live in **AWS
Secrets Manager**. Nothing secret is stored in GitHub, typed into chat or printed in logs.
Why this design: [ADR 0008](../adr/0008-aws-aurora-self-hosted-runner.md).

```
GitHub Actions (schedules, buttons)
        |  runner polls GitHub over HTTPS, no inbound ports
        v
EC2 reitstreet-runner (t4g.small, Ubuntu 24.04, instance role)
        |-- port 3306, TLS --> Aurora MySQL cluster, schema `reitstreet`
        |-- Secrets Manager reitstreet/db/{migrator,loader,reader}
        |-- S3 bucket: raw/ logs/ exports/ (append only)
        '-- HTTPS out --> SEC EDGAR, REIT IR sites (fixed public IP)
```

About an hour, all in the AWS console and GitHub in a browser. Use the **same region** as
the cluster for everything. Names below are suggestions; if you change one, change it
everywhere.

## 1. Check the cluster (RDS console)

RDS > Databases > click the **cluster** (the row with role "Regional cluster").

1. Top right of the console: note the **region** code, e.g. `us-west-2`.
2. **Configuration** tab:
   - **Engine version** must start with `8.0.mysql_aurora.3`. That is Aurora MySQL 3,
     MySQL 8 compatible. If it starts with `5.7` (Aurora MySQL 2), stop: upgrade first.
   - **Master credentials ARN**: if it shows "Managed in AWS Secrets Manager", copy the
     ARN (starts `arn:aws:secretsmanager:`). If not, see 1a below.
3. **Connectivity & security** tab:
   - Copy the **Writer** endpoint (`...cluster-....rds.amazonaws.com`, type Writer).
   - Note the **VPC** and the **VPC security group** name.
4. **Maintenance & backups** tab: **Backup retention period** should be at least 14 days.
   If it is less: Modify > Backup > Backup retention period 14 days (or 35) > Continue >
   Apply immediately. Point-in-time restore covers any second inside that window.
5. While in Modify: under Additional configuration, tick **Enable deletion protection**.

**1a. Master password not in Secrets Manager.** Either switch it (Modify > Credentials
management > Managed in AWS Secrets Manager, which sets a new master password) or create a
secret yourself: Secrets Manager > Store a new secret > Credentials for Amazon RDS database >
enter the master user name and password > choose the cluster > name it
`reitstreet/master` > Store. Copy its ARN.

## 2. S3 bucket (S3 console)

Create bucket:
- Name: `reitstreet-data-` plus something unique, e.g. `reitstreet-data-jwalter`.
- Region: the cluster's region.
- Block all public access: **on** (default).
- Bucket Versioning: **Enable**.
- Default encryption: SSE-S3 (default).

Create. Note the bucket name.

## 3. IAM role for the runner (IAM console)

1. Roles > Create role > Trusted entity: **AWS service**, Use case: **EC2** > Next.
2. Search and tick **AmazonSSMManagedInstanceCore** (lets you open a shell from the
   browser) > Next > Role name `reitstreet-runner` > Create role.
3. Open the role > Add permissions > **Create inline policy** > JSON. Paste
   [deploy/aws/iam/runner-policy.json](../../deploy/aws/iam/runner-policy.json), replace
   `YOUR-BUCKET-NAME` (2 places) > Next > name `reitstreet-runner-access` > Create.
4. Add permissions > Create inline policy > JSON again. Paste
   [deploy/aws/iam/bootstrap-policy.json](../../deploy/aws/iam/bootstrap-policy.json), replace
   `YOUR-MASTER-SECRET-ARN` with the ARN from step 1 > name `reitstreet-bootstrap-temp` >
   Create. This one is deleted again in step 8.

If the master secret is encrypted with your own KMS key (Secrets Manager > the secret >
Encryption key is not `aws/secretsmanager`), add `kms:Decrypt` on that key to the
temporary policy.

## 4. Security groups (EC2 console > Security Groups)

1. Create security group: name `reitstreet-runner-sg`, VPC = **the cluster's VPC**.
   Inbound rules: **none**. Outbound: leave the default (all). Create.
2. Open the **cluster's** security group (name from step 1.3) > Inbound rules > Edit >
   Add rule: Type **MYSQL/Aurora** (3306), Source **Custom** > pick `reitstreet-runner-sg` >
   Save. Only the runner can reach the database; nothing changes for the internet.

## 5. Launch the runner (EC2 console > Instances > Launch instances)

- Name: `reitstreet-runner`
- AMI: **Ubuntu Server 24.04 LTS**, Architecture **64-bit (Arm)**
- Instance type: **t4g.small**
- Key pair: **Proceed without a key pair** (shell access is through Session Manager)
- Network settings > Edit:
  - VPC: the cluster's VPC
  - Subnet: a **public** subnet (in the VPC console its route table has a route to an
    `igw-`; every subnet of a default VPC is public). If the VPC has no public subnet, stop
    and ask: it then needs a NAT gateway (about $33 a month more) or a different layout.
  - Auto-assign public IP: **Enable**
  - Firewall: Select existing security group > `reitstreet-runner-sg`
- Configure storage: **30 GiB gp3**
- Advanced details:
  - IAM instance profile: `reitstreet-runner`
  - Metadata version: **V2 only (token required)**
  - User data: paste all of [deploy/aws/runner-setup.sh](../../deploy/aws/runner-setup.sh)

Launch. Setup takes about 5 minutes after the instance shows Running.

## 6. Register the runner with GitHub

1. GitHub > `jaymoore74/reit-street` > Settings > Actions > Runners > **New self-hosted
   runner** > Linux, ARM64. In the "Configure" box, copy only the token after `--token`
   (valid one hour). Do not paste it anywhere but the shell below.
2. EC2 > Instances > tick `reitstreet-runner` > **Connect** > **Session Manager** tab >
   Connect. A shell opens in the browser. Run, pasting the token where shown:

```bash
cat /etc/reitstreet/setup-done          # should print "reitstreet runner setup done"
cd /opt/actions-runner
sudo -u runner ./config.sh --unattended --url https://github.com/jaymoore74/reit-street \
  --name reitstreet-aws-1 --labels reitstreet-aws --token PASTE_TOKEN_HERE
sudo ./svc.sh install runner
sudo ./svc.sh start
```

Back on GitHub's Runners page, `reitstreet-aws-1` shows **Idle**.

## 7. GitHub repository variables

GitHub > repo > Settings > Secrets and variables > Actions > **Variables** tab > New
repository variable, twice:

| Name | Value |
|---|---|
| `AWS_REGION` | region code from step 1, e.g. `us-west-2` |
| `REITSTREET_S3_BUCKET` | bucket name from step 2 |

No GitHub **secrets** are needed. Optional variables:

- `REITSTREET_SCHEDULES` = `off` pauses all scheduled runs (the kill switch); delete it or
  set `on` to resume.
- `REITSTREET_SEC_USER_AGENT` overrides the contact sent to SEC EDGAR with every request.
  SEC requires a real name and email; the jobs already send `Jason Moore reits@jwalteranalytics.com`
  (set in `.github/actions/reitstreet-env/action.yml`), so only add this to change it.

## 8. Create the schema and logins

GitHub > Actions > **ops-bootstrap-db** > Run workflow. Paste the master secret ARN and the
writer endpoint > Run. In about a minute the log shows four logins created and
"login checked over TLS". It creates the `reitstreet` schema, the `reitstreet_app` schema
for the client website (ADR 0013), and:

| Login | Secret | Can |
|---|---|---|
| `rs_migrator` | `reitstreet/db/migrator` | change the `reitstreet` schema (migrations) |
| `rs_loader` | `reitstreet/db/loader` | read and write data, no schema changes |
| `rs_reader` | `reitstreet/db/reader` | read only on `reitstreet`, for analysis tools; no access to client accounts |
| `rs_web` | `reitstreet/db/web` | the client website: read `reitstreet`, read and write `reitstreet_app`, never delete |

Passwords are random, generated on the runner, stored only in Secrets Manager and never
shown. All four require TLS and can only touch the two schemas. `rs_migrator` changes both;
`rs_loader` also writes `reitstreet_app` for the `reitstreet web` admin commands.

Already ran this before the website existed? Run it again (it only adds what is missing:
the second schema, `rs_web` and the new grants), with the temporary policy attached as in
step 3.4, then run **ops-migrate** with target `all`, and remove the policy again.

Then **delete the temporary policy**: IAM > Roles > `reitstreet-runner` > tick
`reitstreet-bootstrap-temp` > Remove. The runner can no longer read the master password.

## 9. First load

GitHub > Actions > **ops-first-load** > Run workflow. Migrates the schema, imports the
universe from `universe/reits.csv`, loads full SEC history (1 to 3 hours), then runs the
checks, a spot check (PLD, O, SPG) and `status`. To resume an interrupted load, run it
again with the `sec-sync` run_id from the log.

From then on the schedules run by themselves. Until the first load finishes they skip
quietly.

## Schedules

| Workflow | When (UTC) | Does |
|---|---|---|
| ops-sync | weekdays every 30 min, 11:15 to 02:45 | `sec-sync`, `dq`, `monitor`, copy to S3 |
| ops-ir-fetch | weekdays 14:00 and 23:00 | `fetch-ir`, `dq --only freshness,ops`, `monitor`, `ir-coverage`, copy to S3 |
| ops-weekly | Sunday 13:30 | `universe`, `sec-sync --force`, `fetch-ir`, `parse-docs`, `dq`, `export`, `monitor`, copy to S3 |
| ops-monitor | hourly at :05 | `monitor`, runner disk check |
| ops-migrate | button | schema upgrade after a release with a migration |
| ops-first-load | button | the initial load, or a resumed one |
| ops-bootstrap-db | button | schema and logins; safe to re-run |

A failed run opens a GitHub issue labelled `ops-failure` (one per workflow; later
failures comment on it) and GitHub emails the repo owner. The next good run closes it.

## Operations

- **Shell on the runner**: EC2 > instance > Connect > Session Manager.
- **Runner logs**: `sudo journalctl -u 'actions.runner.*' -n 200`.
- **Pipeline logs**: S3 `logs/`, and `/srv/reitstreet/data/logs` on the runner.
- **Disk**: ops-monitor fails at 85% full. Grow it: EC2 > Volumes > the runner's volume >
  Modify > larger size, then in the shell `sudo growpart /dev/nvme0n1 1 && sudo resize2fs
  /dev/nvme0n1p1`.
- **Rebuild the runner**: launch a new one (steps 5 and 6), then restore the raw cache with
  `sudo -u runner aws s3 sync s3://BUCKET/raw/ /srv/reitstreet/data/raw/`. S3 is the
  durable copy; the runner's disk is a cache.
- **Connect from your PC** (MySQL Workbench, Excel, Python): not possible while the
  cluster is private. Options when needed: Session Manager port forwarding through the
  runner, or a VPN. Use the `rs_reader` login.
- **Rotate a password**: edit the password in Secrets Manager > `reitstreet/db/<role>`,
  re-add the temporary bootstrap policy (step 3.4), run ops-bootstrap-db so MySQL matches,
  then remove the policy again.
- **Backups**: Aurora continuous backup and point-in-time restore over the retention
  window (step 1.4). Restores create a new cluster; point the logins at it by updating
  `host` in the three secrets. Raw files: S3 with versioning; the runner role cannot delete.

## Monthly cost on top of the Aurora cluster

us-east-1. EC2 and IPv4 rates checked 2026-11-26; the other lines are AWS list prices not
rechecked.

| Item | $/month |
|---|---|
| EC2 t4g.small on demand ($0.0168/hr) | 12.26 |
| Public IPv4 address ($0.005/hr) | 3.65 |
| EBS 30 GiB gp3 | about 2.40 |
| Secrets Manager, 3 or 4 secrets at $0.40 | 1.20 to 1.60 |
| S3 Standard, first 50 GB | about 1 |
| **Total** | **about 21** |

GitHub Actions: self-hosted runner minutes are free today; GitHub announced a
$0.002/minute charge for March 2026 and postponed it with no new date. If it lands, the
schedules above use roughly 3,000 to 5,000 minutes a month ($6 to $10). Aurora charges
(instance or ACUs, storage, and I/O on the Standard configuration) are separate and
depend on the cluster; check Cost Explorer after the first load.
