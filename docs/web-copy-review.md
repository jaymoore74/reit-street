# Website wording waiting for review

Every client-facing sentence the site or the staff commands produce that is a placeholder.
Nothing here is final until Jason approves it. When a line is settled, change it in the file
named, drop the `PLACEHOLDER` marker there, and delete the row here.

## Names

| What | Where | Placeholder today |
|---|---|---|
| Business name on pages and in emails | `RS_SITE_NAME` (default in `apps/web/src/lib/env.ts`), stack parameter `SiteName` | REIT Street |
| Name in the staff commands' emails | `src/reitstreet/web/mail.py` `compose(site_name=...)` | REIT Street |
| Author written into Excel downloads | `apps/web/src/lib/export/xlsx.ts` (`wb.creator`) | REIT Street |
| Site address | stack parameter `DomainName`, `RS_PUBLIC_BASE_URL` | research.jwalteranalytics.com (test site) |

## Public page

| What | Where | Placeholder today |
|---|---|---|
| Headline | `apps/web/src/app/page.tsx` | REIT research and data |
| What we offer | `apps/web/src/app/page.tsx` | Placeholder: company-level and property-level data for US REITs, from SEC filings and company supplementals, with research and downloads for clients. |
| How to become a client | `apps/web/src/app/page.tsx` | Accounts are by invitation. Ask your firm's administrator or contact us. (no contact address yet) |
| Search and link preview text | `apps/web/src/app/layout.tsx` (`description`) | REIT research and data. |

## Consent and legal

These need a lawyer's read before real clients or subscribers.

| What | Where | Placeholder today |
|---|---|---|
| Newsletter consent, shown and stored with each sign-up | `apps/web/src/lib/newsletter.ts` `consentText()` | I agree to receive the {site} newsletters I selected by email. I can unsubscribe at any time. |
| Licence notice inside every download | `apps/web/src/lib/export/types.ts` `NOTICE` | Licensed to the organization named above for its internal use under its subscription agreement. Do not redistribute. |
| Sample-data warning | `apps/web/src/app/layout.tsx`, `export/types.ts` `SAMPLE_NOTICE` | Sample data: figures on this site are synthetic test values, not real company results. (Stays while `RS_SAMPLE_DATA=1`.) |
| Privacy policy, terms of use, unsubscribe footer for issues | none yet | needed before the first newsletter issue or paying client |

Changing the consent text changes what new sign-ups agree to; earlier sign-ups keep the
text they saw (stored per row).

## Emails

| Email | Where | Subject today |
|---|---|---|
| Newsletter confirmation | `apps/web/src/lib/email-templates.ts` | Confirm your {site} newsletter sign-up |
| Password reset (self-service) | `apps/web/src/lib/email-templates.ts` | Reset your {site} password |
| Invitation (staff command) | `src/reitstreet/web/mail.py` | Your {site} account |
| Password reset (staff command) | `src/reitstreet/web/mail.py` | Reset your {site} password |

Bodies are in the same files: two or three plain sentences with the link and its expiry.

## Plans and pricing

Plan codes, names, what each includes, seats and prices are not in the site. Staff create
them with `reitstreet web plan-create` and `grant` ([web admin](runbooks/web-admin.md)); the
examples there (`core`, "Core") are placeholders.
