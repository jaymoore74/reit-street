import "server-only";
import { whQuery } from "./db";
import { coverageClause, historyStart, todayUtc, type Entitlements } from "./entitlements";

/** Read-only queries for the pages. Each enforces coverage and the dataset grant, like downloads. */

export type CompanyRow = {
  ticker: string;
  name: string;
  sector: string | null;
  reit_type: string | null;
  exchange: string | null;
  cik: number;
  fiscal_year_end: string | null;
};

export async function listCompanies(ent: Entitlements): Promise<CompanyRow[]> {
  if (!ent.datasets.includes("companies")) return [];
  const cov = coverageClause(ent, "ticker");
  return whQuery<CompanyRow>(
    `SELECT ticker, name, sector, reit_type, exchange, cik, fiscal_year_end
       FROM dl_companies WHERE in_universe = 1 AND ${cov.sql} ORDER BY ticker`,
    cov.params,
  );
}

export async function getCompany(ent: Entitlements, ticker: string): Promise<CompanyRow | null> {
  if (!ent.datasets.includes("companies") || !/^[A-Z][A-Z0-9.-]{0,15}$/.test(ticker)) return null;
  const cov = coverageClause(ent, "ticker");
  const rows = await whQuery<CompanyRow>(
    `SELECT ticker, name, sector, reit_type, exchange, cik, fiscal_year_end
       FROM dl_companies WHERE ticker = ? AND ${cov.sql}`,
    [ticker, ...cov.params],
  );
  return rows[0] ?? null;
}

export type MetricPoint = {
  metric_code: string;
  metric_name: string;
  period_type: string;
  period_end: string;
  fiscal_year: number;
  fiscal_quarter: number | null;
  value: string;
  unit: string;
  source_filing_url: string | null;
};

/** All preferred values for one company, oldest first, limited by the history grant. */
export async function companyMetrics(ent: Entitlements, ticker: string): Promise<MetricPoint[]> {
  if (!ent.datasets.includes("metrics")) return [];
  const cov = coverageClause(ent, "ticker");
  const from = historyStart(ent, todayUtc());
  return whQuery<MetricPoint>(
    `SELECT metric_code, metric_name, period_type, period_end, fiscal_year, fiscal_quarter, value, unit, source_filing_url
       FROM dl_metrics
      WHERE is_preferred = 1 AND segment = 'Total' AND ticker = ? AND ${cov.sql}
        ${from ? "AND period_end >= ?" : ""}
      ORDER BY metric_code, period_type, period_end`,
    [ticker, ...cov.params, ...(from ? [from] : [])],
  );
}

export type FilingRow = {
  accession_no: string;
  form_type: string;
  filing_date: string;
  report_date: string | null;
  index_url: string;
};

export async function companyFilings(ent: Entitlements, ticker: string, limit = 25): Promise<FilingRow[]> {
  if (!ent.datasets.includes("filings")) return [];
  const cov = coverageClause(ent, "ticker");
  const from = historyStart(ent, todayUtc());
  return whQuery<FilingRow>(
    `SELECT accession_no, form_type, filing_date, report_date, index_url
       FROM dl_filings WHERE ticker = ? AND ${cov.sql} ${from ? "AND filing_date >= ?" : ""}
      ORDER BY filing_date DESC, accession_no DESC LIMIT ?`,
    [ticker, ...cov.params, ...(from ? [from] : []), limit],
  );
}

export type MetricDefinition = {
  metric_code: string;
  metric_name: string;
  category: string;
  unit_type: string;
  period_nature: string;
  is_non_gaap: number;
  is_nareit_defined: number;
  definition: string | null;
};

export async function metricDefinitions(): Promise<MetricDefinition[]> {
  return whQuery<MetricDefinition>(
    `SELECT metric_code, metric_name, category, unit_type, period_nature, is_non_gaap, is_nareit_defined, definition
       FROM dl_metric_definitions ORDER BY category, metric_code`,
  );
}

/** Data freshness for the dashboard. */
export async function dataStatus(ent: Entitlements): Promise<{ companies: number; latestFiling: string | null }> {
  const cov = coverageClause(ent, "ticker");
  const [c] = await whQuery<{ n: number }>(
    `SELECT COUNT(*) AS n FROM dl_companies WHERE in_universe = 1 AND ${cov.sql}`,
    cov.params,
  );
  const [f] = await whQuery<{ d: string | null }>(`SELECT MAX(filing_date) AS d FROM dl_filings WHERE ${cov.sql}`, cov.params);
  return { companies: Number(c?.n ?? 0), latestFiling: f?.d ?? null };
}
