/**
 * Entitlements (ADR 0013). Pure functions only, so the rules are unit tested without a database.
 *
 * A grant is a row of app_entitlement on the organization's plan or on the organization itself.
 * What a user may see is the union of the grants valid today. Nothing is visible without a grant,
 * except two limits that have defaults: history (unlimited) and rows per download (1,000,000).
 */
export const DATASETS = ["companies", "metrics", "metrics_history", "filings"] as const;
export const FORMATS = ["csv", "xlsx", "parquet"] as const;
export type Dataset = (typeof DATASETS)[number];
export type Format = (typeof FORMATS)[number];

export const DEFAULT_MAX_ROWS = 1_000_000;

export type GrantRow = {
  kind: string;
  value: string;
  valid_from: string | null; // YYYY-MM-DD
  valid_to: string | null; // last day, inclusive
};

export type OrgTerms = {
  status: string;
  contract_start: string | null;
  contract_end: string | null;
};

export type Entitlements = {
  active: boolean; // false: firm suspended, closed or outside its contract dates
  datasets: Dataset[];
  research: string[];
  formats: Format[];
  coverageAll: boolean;
  sectors: string[];
  tickers: string[];
  historyYears: number | null; // null: no limit
  maxRows: number;
};

export const NO_ACCESS: Entitlements = {
  active: false,
  datasets: [],
  research: [],
  formats: [],
  coverageAll: false,
  sectors: [],
  tickers: [],
  historyYears: null,
  maxRows: 0,
};

const within = (today: string, from: string | null, to: string | null) =>
  (from === null || from <= today) && (to === null || today <= to);

export function resolveEntitlements(grants: GrantRow[], org: OrgTerms, today: string): Entitlements {
  if (org.status !== "active" || !within(today, org.contract_start, org.contract_end)) return NO_ACCESS;
  const live = grants.filter((g) => within(today, g.valid_from, g.valid_to));
  const values = (kind: string) => [...new Set(live.filter((g) => g.kind === kind).map((g) => g.value))].sort();
  const nums = (kind: string) =>
    live
      .filter((g) => g.kind === kind)
      .map((g) => Number(g.value))
      .filter((n) => Number.isInteger(n) && n > 0);

  const history = nums("history_years");
  const rows = nums("max_rows");
  return {
    active: true,
    datasets: values("dataset").filter((d): d is Dataset => (DATASETS as readonly string[]).includes(d)),
    research: values("research"),
    formats: values("format").filter((f): f is Format => (FORMATS as readonly string[]).includes(f)),
    coverageAll: live.some((g) => g.kind === "coverage_all"),
    sectors: values("coverage_sector"),
    tickers: values("coverage_ticker").map((t) => t.toUpperCase()),
    // a grant without history_years means full history; when several apply, the most generous wins
    historyYears: history.length ? Math.max(...history) : null,
    maxRows: rows.length ? Math.max(...rows) : DEFAULT_MAX_ROWS,
  };
}

/** True if the entitlements cover at least one company. */
export function hasCoverage(e: Entitlements): boolean {
  return e.active && (e.coverageAll || e.sectors.length > 0 || e.tickers.length > 0);
}

/**
 * SQL condition limiting rows to the covered companies. `column` is a trusted column name from
 * our own code (never from a request); values are bound parameters.
 */
export function coverageClause(e: Entitlements, column: string): { sql: string; params: string[] } {
  if (!/^[a-z_][a-z0-9_.]*$/.test(column)) throw new Error(`bad column name: ${column}`);
  if (!e.active) return { sql: "1 = 0", params: [] };
  if (e.coverageAll) return { sql: "1 = 1", params: [] };
  const parts: string[] = [];
  const params: string[] = [];
  if (e.tickers.length) {
    parts.push(`${column} IN (${e.tickers.map(() => "?").join(", ")})`);
    params.push(...e.tickers);
  }
  if (e.sectors.length) {
    parts.push(
      `${column} IN (SELECT cov.ticker FROM dl_companies cov WHERE cov.sector IN (${e.sectors.map(() => "?").join(", ")}))`,
    );
    params.push(...e.sectors);
  }
  if (!parts.length) return { sql: "1 = 0", params: [] };
  return { sql: `(${parts.join(" OR ")})`, params };
}

/** First date a user may see for dated rows, or null when history is unlimited. */
export function historyStart(e: Entitlements, today: string): string | null {
  if (e.historyYears === null) return null;
  const [y, m, d] = today.split("-").map(Number) as [number, number, number];
  const start = new Date(Date.UTC(y - e.historyYears, m - 1, d));
  // 29 February minus whole years lands on 1 March in a non-leap year
  return start.toISOString().slice(0, 10);
}

export function todayUtc(now: Date = new Date()): string {
  return now.toISOString().slice(0, 10);
}
