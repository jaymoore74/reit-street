# Host the client website on AWS

Puts the site (apps/web) online in your AWS account, us-east-2, next to Aurora (ADR 0013).
Nothing here has been created yet; every step is yours to start. The test site is
`research.jwalteranalytics.com` (`EnvName` = `staging`); the business site gets its own
domain later as a second stack (`EnvName` = `production`).

Two ways, same image, same settings. **Option B is the chosen one** (Jason, 2026-11-26); A is
the upgrade path later and needs no change to the site:

| | A: Fargate + load balancer (later) | B: one EC2 instance + Caddy (chosen) |
|---|---|---|
| Template | [deploy/aws/web/web-fargate.yaml](../../deploy/aws/web/web-fargate.yaml) | [deploy/aws/web/web-ec2.yaml](../../deploy/aws/web/web-ec2.yaml) |
| About $/month | 40 to 48 | 16 |
| Certificate | AWS (ACM), renews itself | Let's Encrypt through Caddy, renews itself |
| Servers to patch | none | one (security updates install automatically) |
| Deploys | no downtime with 2 tasks | a few seconds down |
| DNS record | CNAME to the load balancer | A record to an Elastic IP |

Prices are us-east-1 list prices read 2026-11-26 (Ohio is usually the same); ADR 0013 has
the breakdown. Option B's Caddy registers with Let's Encrypt, which means accepting the
Let's Encrypt subscriber agreement for the domain.

## Before you start

1. [cloud-setup](cloud-setup.md) is done: Aurora reachable, the runner registered.
2. The web login exists. Re-run **ops-bootstrap-db** (Actions > ops-bootstrap-db > Run
   workflow). It is safe to re-run and only adds what is missing; it makes the
   `rs_web` login and the secret `reitstreet/db/web`. It needs the temporary
   `reitstreet-bootstrap-temp` policy from cloud-setup step 3.4 again; delete it after.
3. The web schema is current: Actions > **ops-migrate** > target `app`.
4. Note from the consoles:
   - VPC id of the cluster, and its **public** subnets (option A needs two in different
     Availability Zones; option B one)
   - the cluster's security group id (`sg-...`)
   - Secrets Manager > `reitstreet/db/web` > **Secret ARN** (the full one, ending in `-xxxxxx`)

## Where the domain's DNS lives

`jwalteranalytics.com`'s DNS is at **DreamHost**, and its mail is Google Workspace. The site
needs one new record there, added after the stack exists (its value is the server's IP):

1. DreamHost panel > **Websites** > **Manage Websites** > find `jwalteranalytics.com` >
   **DNS** (under the three-dot menu or the site's settings, depending on the panel version).
2. **Add Record**: Type **A**, Name/Host **research**, Value the Elastic IP from the
   stack's `DnsRecord` output, TTL default > Save.
3. Check from Windows PowerShell after a few minutes:

   ```powershell
   Resolve-DnsName research.jwalteranalytics.com -Type A
   ```

**Do not change** the root (`@`) records, `www`, MX, or the root TXT records (SPF, Google
site verification). Google Workspace mail keeps working because the site only adds new
names under `research` (and `_domainkey` if SES is turned on). If DreamHost already lists
`research.jwalteranalytics.com` as a hosted site or subdomain, remove that first, or the
panel refuses a custom record with the same name.

## Option B: one instance with Caddy

1. CloudFormation > Create stack > Upload `deploy/aws/web/web-ec2.yaml`.
2. Stack name `reitstreet-web-staging`. Parameters: VpcId, PublicSubnetId (one),
   DbSecurityGroupId, DbSecretArn. Leave the rest. Acknowledge IAM > Submit.
3. Stack > **Outputs** > `DnsRecord` shows the Elastic IP. Add it at DreamHost as above
   (type A, name `research`).
4. Build the image: see [Build an image](#build-an-image).
5. EC2 > Instances > `reitstreet-web-staging` > Connect > **Session Manager** > Connect:

   ```bash
   sudo /opt/reitstreet-web/deploy.sh THE-TAG
   ```

   Caddy gets the certificate within a minute of the DNS record resolving. Then open
   https://research.jwalteranalytics.com.

Deploy a new version: build an image, then run the same command with the new tag.

## Option A: Fargate (later, if wanted)

1. CloudFormation console (us-east-2) > **Create stack** > With new resources > Upload a
   template file > `deploy/aws/web/web-fargate.yaml` > Next.
2. Stack name `reitstreet-web-staging`. Parameters: VpcId, PublicSubnetIds (two),
   DbSecurityGroupId, DbSecretArn from above. Leave the rest at their defaults
   (`DesiredCount` 0, `EmailProvider` none, `SampleData` 1). Next > tick "I acknowledge
   that AWS CloudFormation might create IAM resources" > Submit.
3. The stack stops at **Certificate** (CREATE_IN_PROGRESS). Open Certificate Manager >
   the certificate for `research.jwalteranalytics.com` > Domains > copy the CNAME name and
   value. At DreamHost add (Add Record):

   | Type | Name | Value |
   |---|---|---|
   | CNAME | `_xxxxxxxx.research` (the part before `.jwalteranalytics.com`) | `_yyyyyyyy.xxxxxx.acm-validations.aws.` |

   Leave this record in place: it is how the certificate renews. The stack finishes a
   few minutes after the record appears.
4. Stack > **Outputs** > `DnsRecord`. At DreamHost add (Add Record):

   | Type | Name | Value |
   |---|---|---|
   | CNAME | `research` | the load balancer name from `DnsRecord` (`reitstreet-web-staging-....us-east-2.elb.amazonaws.com`) |

5. Build the image: see [Build an image](#build-an-image) below.
6. Stack > **Update** > Use existing template > `ImageTag` = the tag from step 5,
   `DesiredCount` = 1 > Submit. When it finishes, open https://research.jwalteranalytics.com.

Deploy a new version: build an image, then update the stack's `ImageTag`. A failed
version rolls back by itself (deployment circuit breaker).

## Build an image

1. Once only: IAM > Roles > `reitstreet-runner` > Add permissions > Create inline policy >
   JSON > paste [deploy/aws/iam/web-image-policy.json](../../deploy/aws/iam/web-image-policy.json)
   > name `reitstreet-web-image` > Create. It can push only to `reitstreet-web-*`
   repositories.
2. The runner needs Docker. [runner-setup.sh](../../deploy/aws/runner-setup.sh) installs it
   on a new runner. On a runner launched before that, in a Session Manager shell:

   ```bash
   sudo apt-get install -y docker.io && sudo usermod -aG docker runner
   sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   sudo systemctl restart actions.runner.*
   ```

   Members of the `docker` group can act as root on that instance; the runner is a
   single-purpose machine, so that is acceptable.
3. Actions > **ops-web-image** > Run workflow > env `staging`. The run summary prints the
   tag (the commit SHA). Images are immutable; the repository keeps the last 30.

## Check it

- https://research.jwalteranalytics.com/api/health returns `{"ok":true}` (it reads both schemas).
- http:// redirects to https://.
- Every page shows the sample-data banner until `SampleData` is set to 0.
- Logs: CloudWatch > Log groups > `/reitstreet/web/staging`.
- First users: [web admin](web-admin.md). Set `REITSTREET_WEB_BASE_URL` (or `[web] base_url`)
  to the site's address wherever the staff commands run, so invitation links point at it.

## Email (optional, Amazon SES)

Off by default: staff send invitation and reset links by hand, and the public newsletter
form records unconfirmed sign-ups. To turn it on:

1. SES console (us-east-2) > Identities > Create identity > Domain >
   `jwalteranalytics.com` > Easy DKIM, RSA 2048 > Create. Add the **three CNAME records**
   it shows (`xxxx._domainkey`). They do not touch Google Workspace's DKIM (`google._domainkey`),
   MX or SPF.
2. SES > Account dashboard > **Request production access** (new accounts can only send to
   verified addresses). Describe it as invitations, password resets and newsletter
   confirmations to people who asked for them. AWS answers in about a day.
3. Pick a sender, e.g. `research@jwalteranalytics.com`, and make it a real mailbox or
   alias in Google Workspace so replies arrive.
4. Update the stack: `EmailProvider` = `ses`, `EmailFrom` = that address. The site's role
   can then send only from that address.
5. For the staff commands, set `REITSTREET_WEB_EMAIL_PROVIDER=ses` and
   `REITSTREET_WEB_EMAIL_FROM` where they run, add `ses:SendEmail` for that address to the
   runner role, and use `--email-link` ([web admin](web-admin.md)).

SES costs $0.10 per 1,000 emails.

## Turn it off

- Pause: update the stack with `DesiredCount` 0 (A) or stop the instance (B). The load
  balancer (A) and the Elastic IP keep charging while the stack exists.
- Remove: delete the stack. The image repository is kept (delete it in ECR if wanted);
  the database rule the stack added is removed with it. Remove the DNS records.
