# Put the landing page live at jwalteranalytics.com

The one-page site in `apps/landing` (plain HTML, no build, no tracking). Today the domain
loads nothing (checked 2026-11-26: no web server answers for `jwalteranalytics.com` or
`www`).

## Options

| | Netlify (recommended) | DreamHost hosting | The research-site EC2 server |
|---|---|---|---|
| Cost | $0 extra; same Netlify account as sci.help | $0 only if you already pay for a DreamHost hosting plan; otherwise a paid plan | $0 extra once the ~$16/month server exists |
| Works today | yes | if a plan exists | no, waits on AWS setup |
| HTTPS | automatic (Let's Encrypt, renews itself) | automatic if enabled in the panel | automatic through Caddy |
| Updating | redeploy from the repo (Claude can do it) | upload files by SFTP | rebuild the server config |
| DNS records changed | root `@` A, `www` CNAME | none if DreamHost hosts it | root `@` A, `www` |

Mail is not affected by any option: MX, SPF/DKIM/DMARC and Google verification TXT
records stay exactly as they are. Only the web records for `@` and `www` change.

## Netlify steps (Windows, browser only)

### 1. Create the site in Netlify

Either let Claude create and deploy it through the Netlify connector (say yes in the chat),
or do it by hand:

1. Sign in at app.netlify.com (same account as sci.help).
2. **Add new project** > **Import an existing project** > **GitHub** > pick
   `jaymoore74/reit-street`. If it isn't listed, click **Configure the Netlify app on
   GitHub** and allow that repo.
3. Branch `main`. **Base directory** `apps/landing`. **Build command** empty.
   **Publish directory** `apps/landing`. **Deploy**.
4. Optional: **Project configuration** > **Change project name** to `jwalteranalytics`, so
   the Netlify address is `jwalteranalytics.netlify.app`. Open it to check the page.

### 2. Add the domain in Netlify

1. Project > **Domain management** > **Add a domain** > `jwalteranalytics.com` > Verify >
   **Add domain**. Netlify adds `www.jwalteranalytics.com` with it.
2. Netlify shows **Pending DNS verification** and the exact records. Use those values if
   they differ from step 3.

### 3. Add two records at DreamHost

DreamHost panel > **Websites** > **Manage Websites** > `jwalteranalytics.com` > **DNS**
(or **Domains** > **Manage Domains** > DNS on older panels).

1. If DreamHost already lists an **A** record for the root (blank or `@`) or for `www`,
   or lists the domain as **Fully Hosted** or **Parked**, change hosting to **DNS only**
   first (or delete only those web A/AAAA/CNAME entries). Do not touch MX or TXT.
2. **Add Record**: Type **A**, Name blank (root), Value `75.2.60.5`. Save.
   (Netlify prefers an ALIAS/ANAME to `apex-loadbalancer.netlify.com`; DreamHost's
   custom records don't offer that, so the A record is Netlify's documented fallback.)
3. **Add Record**: Type **CNAME**, Name `www`, Value `YOUR-PROJECT.netlify.app` (the
   Netlify address from step 1.4). Save.

### 4. Check

PowerShell, after 10 to 30 minutes (Netlify says up to a day):

```powershell
Resolve-DnsName jwalteranalytics.com -Type A
Resolve-DnsName www.jwalteranalytics.com -Type CNAME
Resolve-DnsName jwalteranalytics.com -Type MX
```

The first should show `75.2.60.5`, the second your Netlify address, the third the same
Google mail servers as before. Netlify then issues the certificate by itself (Domain
management > HTTPS). Open https://jwalteranalytics.com.

## Later

- When `research.jwalteranalytics.com` is live, uncomment the **Client login** line in
  `apps/landing/index.html`.
- Wording waiting for review: [landing-copy-review.md](../landing-copy-review.md).
- The script hash in `apps/landing/netlify.toml` (Content-Security-Policy) must match the
  one inline script; `tests/unit/test_landing.py` fails if they drift.
