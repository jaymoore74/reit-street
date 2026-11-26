// End-to-end smoke test against a running site and the seeded development database
// (scripts/dev/seed_web.py). Uses only fetch: forms are posted the way a browser without
// JavaScript posts them, so this also proves the pages work without JavaScript.
//
//   node tests/e2e/smoke.mjs http://localhost:3000 path/to/links.json
//
// Exits non-zero on the first failed check.
import { readFileSync } from "node:fs";

const base = process.argv[2] ?? "http://localhost:3000";
const links = JSON.parse(readFileSync(process.argv[3] ?? "links.json", "utf8"));
const origin = new URL(base).origin;
let failures = 0;
let checks = 0;

function check(name, ok, extra = "") {
  checks++;
  if (ok) console.log(`ok   ${name}`);
  else {
    failures++;
    console.log(`FAIL ${name} ${extra}`);
  }
}

class Browser {
  jar = new Map();
  cookieHeader() {
    return [...this.jar].map(([k, v]) => `${k}=${v}`).join("; ");
  }
  store(res) {
    for (const c of res.headers.getSetCookie()) {
      const [pair] = c.split(";");
      const i = pair.indexOf("=");
      const k = pair.slice(0, i);
      const v = pair.slice(i + 1);
      if (!v || /max-age=0|expires=thu, 01 jan 1970/i.test(c)) this.jar.delete(k);
      else this.jar.set(k, v);
    }
  }
  async get(path) {
    const res = await fetch(new URL(path, base), { headers: { cookie: this.cookieHeader() }, redirect: "manual" });
    this.store(res);
    return res;
  }
  async post(path, body, headers = {}) {
    const res = await fetch(new URL(path, base), {
      method: "POST",
      body,
      headers: { cookie: this.cookieHeader(), origin, ...headers },
      redirect: "manual",
    });
    this.store(res);
    return res;
  }
  /** Fill the first server-action form on the page: keep its hidden fields, set ours, post it like a browser. */
  async submitForm(path, fields) {
    const page = await this.get(path);
    const html = await page.text();
    const form = [...html.matchAll(/<form[^>]*>([\s\S]*?)<\/form>/g)].find((m) => m[1].includes("$ACTION_"));
    if (!form) throw new Error(`no form on ${path}`);
    const data = new FormData();
    for (const m of form[1].matchAll(/<input type="hidden" name="([^"]+)"(?: value="([^"]*)")?\/?>/g)) {
      data.append(m[1], (m[2] ?? "").replace(/&quot;/g, '"').replace(/&amp;/g, "&"));
    }
    for (const [k, v] of Object.entries(fields)) data.set(k, v);
    return this.post(path, data);
  }
}

const decode = (s) => s.replace(/&amp;/g, "&").replace(/&#x27;/g, "'");
const download = (b, fields) => {
  const data = new FormData();
  for (const [k, v] of Object.entries(fields)) data.set(k, v);
  return b.post("/api/download", data);
};

const client = new Browser();
const inviteUrl = new URL(links["client@demo.invalid"]);
const password = "correct horse battery staple 42";

// public pages
check("home page 200", (await client.get("/")).status === 200);
check("app redirects to sign-in", (await client.get("/app")).status === 307);
check("download needs sign-in", (await download(client, { dataset: "companies", format: "csv" })).status === 401);

// invitation: weak password refused, then accepted
let r = await client.submitForm(inviteUrl.pathname, { password: "short", confirm: "short" });
check("short password refused", r.status === 200 && (await r.text()).includes("at least 12"));
r = await client.submitForm(inviteUrl.pathname, { password, confirm: password });
check("invite accepted and signed in", r.status === 303 && client.jar.has("rs_session"), `status ${r.status}`);
r = await client.get(inviteUrl.pathname);
check("invite link is single use", (await r.text()).includes("expired or was already used"));

// dashboard and coverage: the demo plan covers 6 of the 12 sample companies
r = await client.get("/app");
let html = decode(await r.text());
check("dashboard 200", r.status === 200);
check("dashboard shows 6 companies", /Companies in your coverage<\/p><p class="stat">6</.test(html));
html = decode(await (await client.get("/app/companies")).text());
check("companies lists PLD", html.includes(">PLD<"));
check("companies hides PSA (not covered)", !html.includes(">PSA<"));
check("covered company page 200", (await client.get("/app/companies/PLD")).status === 200);
check("uncovered company page 404", (await client.get("/app/companies/PSA")).status === 404);

// research
html = decode(await (await client.get("/app/research")).text());
check("research list shows the sample note", html.includes("/app/research/sample-note-industrial-revenue"));
check("research note 200", (await client.get("/app/research/sample-note-industrial-revenue")).status === 200);

// downloads
r = await download(client, { dataset: "metrics", format: "csv", tickers: "PLD", metrics: "TOTAL_REVENUE", periodTypes: "Q" });
const csv = await r.text();
check("metrics CSV 200", r.status === 200, `status ${r.status} ${csv.slice(0, 200)}`);
check("CSV has header and rows", csv.startsWith("ticker,cik,metric_code") && csv.trim().split("\r\n").length > 1);
check("CSV only PLD", csv.trim().split("\r\n").slice(1).every((l) => l.startsWith("PLD,")));
check("download id header", /^[0-9A-Z]{26}$/.test(r.headers.get("x-download-id") ?? ""));
r = await download(client, { dataset: "metrics", format: "csv", tickers: "PSA" });
check("uncovered ticker gives header only", (await r.text()).trim().split("\r\n").length === 1);
r = await download(client, { dataset: "companies", format: "xlsx" });
check("companies XLSX 200", r.status === 200 && (r.headers.get("content-type") ?? "").includes("spreadsheetml"));
r = await download(client, { dataset: "companies", format: "parquet" });
check("parquet refused (not in demo plan)", r.status === 403);
r = await download(client, { dataset: "metrics_history", format: "csv" });
check("metrics_history refused (not in demo plan)", r.status === 403);
r = await download(client, { dataset: "metrics", format: "csv", tickers: "PLD'--" });
check("bad input refused", r.status === 400);
r = await download(client, { dataset: "companies", format: "csv" }).then(() =>
  fetch(new URL("/api/download", base), {
    method: "POST",
    body: new URLSearchParams({ dataset: "companies", format: "csv" }),
    headers: { cookie: client.cookieHeader(), origin: "https://attacker.example" },
  }),
);
check("cross-site download refused", r.status === 403);

// account: newsletters
r = await client.submitForm("/app/account", { list: "weekly" });
check("newsletter choice saved", r.status === 200 && (await r.text()).includes("Newsletter choices saved"));

// sign out
r = await client.post("/logout", new FormData());
check("sign out redirects", r.status === 303);
check("signed out: app redirects", (await client.get("/app")).status === 307);

// sign in again, then a wrong password
const again = new Browser();
r = await again.submitForm("/login", { email: "client@demo.invalid", password });
check("sign in works", r.status === 303 && again.jar.has("rs_session"));
const stranger = new Browser();
r = await stranger.submitForm("/login", { email: "client@demo.invalid", password: "wrong password 123" });
check("wrong password refused", r.status === 200 && (await r.text()).includes("don&#x27;t match"));

// public newsletter form
r = await stranger.submitForm("/", { email: "reader@demo.invalid", consent: "yes" });
check("public newsletter sign-up saved", r.status === 200 && (await r.text()).includes("saved your sign-up"));

console.log(`\n${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
