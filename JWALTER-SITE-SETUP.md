# J. Walter Analytics site: setup steps

Goal: https://jwalteranalytics.com and https://www.jwalteranalytics.com show the one-page
site in `apps/landing`.

Where things stand (checked 2026-11-26):

- The page is built and merged to `main` in this repo.
- Netlify project **jwalteranalytics** exists in your Netlify account, but it is empty and
  not linked to GitHub yet.
- jwalteranalytics.com shows nothing today.

Cost: $0. Time: about 15 minutes of clicks, then up to a few hours for DNS.

Mail is not touched. You change only the web records for the root and `www`. Never edit or
delete MX or TXT records.

---

## Part 0. Write down your mail records (1 minute)

1. Press the Windows key, type `PowerShell`, press Enter.
2. Paste this and press Enter:

```powershell
Resolve-DnsName jwalteranalytics.com -Type MX
```

3. Take a screenshot of the result (Windows key + Shift + S). You will compare against it
   at the end.

---

## Part 1. Netlify: link the project to GitHub (5 minutes)

1. Go to https://app.netlify.com and sign in (same account as sci.help).
2. Click **Projects**, then click **jwalteranalytics**.
3. In the left menu click **Project configuration**, then **Build & deploy**.
4. Under **Continuous deployment**, click **Link repository** (it may say
   **Link to Git provider** or **Link to a repository**).
5. Click **GitHub**. If a GitHub window asks to authorize Netlify, approve it.
6. Pick **jaymoore74/reit-street**.
   - If it is not in the list: click **Configure the Netlify app on GitHub**, choose your
     account, under **Repository access** add `reit-street`, click **Save**, then go back
     to Netlify and pick it.
7. Fill in the build settings exactly:

   | Field | Value |
   |---|---|
   | Branch to deploy | `main` |
   | Base directory | `apps/landing` |
   | Build command | leave empty |
   | Publish directory | `apps/landing` |
   | Functions directory | leave empty |

8. Click **Deploy** (or **Save**, then in the left menu **Deploys** > **Trigger deploy** >
   **Deploy project**).
9. Wait for the deploy to show **Published** (under a minute).
10. Open https://jwalteranalytics.netlify.app . You should see the J. Walter Analytics page.

If the deploy fails, take a screenshot of the deploy log and send it to me.

---

## Part 2. Netlify: add your domain (2 minutes)

1. Still in the **jwalteranalytics** project, click **Domain management** in the left menu.
2. Click **Add a domain**, then **Add a domain you already own**.
3. Type `jwalteranalytics.com` and click **Verify**.
4. Netlify may say the domain is registered elsewhere. Click **Add domain** anyway.
5. Netlify adds both `jwalteranalytics.com` and `www.jwalteranalytics.com` and shows
   **Pending DNS verification** next to them. Leave this tab open.
6. Do not choose **Set up Netlify DNS**. DNS stays at DreamHost.
7. Click **Pending DNS verification** to see the records Netlify wants. They should be the
   same as Part 3. If Netlify shows a different `www` target, use Netlify's value.

---

## Part 3. DreamHost: web records only (5 minutes)

### 3a. Make sure the domain is "DNS Only"

DreamHost will not let you add the root A record or the `www` CNAME while the domain is
"Fully Hosted". DNS Only removes only DreamHost's own web records. It does not touch email
or your custom records, and it does not delete any files.

1. Go to https://panel.dreamhost.com and sign in.
2. Click **Websites**, then **Manage Websites**.
3. Find `jwalteranalytics.com`.
   - If it already says **DNS Only**, skip to 3b.
4. If the domain's **Manage** page shows an SSL certificate from DreamHost, remove that
   certificate first. DreamHost's help says DNS Only can't clear the web records while a
   certificate is on the domain.
5. Click **Manage** on `jwalteranalytics.com`, then the **Settings** tab.
6. Under **Non-Hosting Options**, click **Set to DNS Only**, then the **Set to DNS Only**
   button, then the red **Yes, Set to DNS Only** button.

### 3b. Open the DNS settings

1. Back on **Manage Websites**, click the three vertical dots next to
   `jwalteranalytics.com`.
2. Click **DNS Settings**.
3. Look at the **Custom Records** list.
   - If there is already an **A** or **AAAA** record with an empty host (the root), or any
     record with host `www`, delete it: pencil icon > **Delete Record** > **Remove**.
   - Do not delete anything else. MX and TXT records stay.

### 3c. Add the two records

1. Click **Add Record**. Find **A Record** and click **ADD**.
   - Host: leave empty
   - Points to: `75.2.60.5`
   - Click **Add Record**.
2. Click **Add Record** again. Find **CNAME Record** and click **ADD**.
   - Host: `www`
   - Points to: `jwalteranalytics.netlify.app`
   - Click **Add Record**.

That is everything at DreamHost. Don't add a CNAME on the root; DreamHost won't allow it.

---

## Part 4. Check and turn on HTTPS (after 10 minutes to a few hours)

1. In PowerShell, paste:

```powershell
Resolve-DnsName jwalteranalytics.com -Type A
Resolve-DnsName www.jwalteranalytics.com -Type CNAME
Resolve-DnsName jwalteranalytics.com -Type MX
```

   - First line should show `75.2.60.5`.
   - Second should show `jwalteranalytics.netlify.app`.
   - Third should match your Part 0 screenshot. If it doesn't, stop and tell me.
2. In Netlify, **Domain management**: the "Pending DNS verification" labels go away by
   themselves once DNS is live.
3. Scroll to **HTTPS**. Netlify issues a free Let's Encrypt certificate by itself. If it
   shows a **Verify DNS configuration** or **Provision certificate** button, click it.
4. Open https://jwalteranalytics.com and https://www.jwalteranalytics.com in a private
   window. Both should show the page with a padlock.

Tell me when you've done Part 3 and I'll check the site from my side.

---

## If something goes wrong

| What you see | What to do |
|---|---|
| DreamHost says "You already have a record for this name" | The domain is still hosted, or an old root/`www` record exists. Redo 3a and the delete step in 3b. |
| Netlify page loads at `.netlify.app` but not at your domain after a day | Send me the output of the Part 4 PowerShell lines. |
| Browser warns about the certificate | Wait an hour after DNS goes live, then in Netlify HTTPS click **Verify DNS configuration**. |
| MX result changed | Don't change anything else. Send me both screenshots. |

## Updating the site later

Any change merged to `main` under `apps/landing` redeploys automatically. Other merges
don't trigger a rebuild.

Background on hosting options: `docs/runbooks/landing-site.md`. Page wording under review:
`docs/landing-copy-review.md`.
