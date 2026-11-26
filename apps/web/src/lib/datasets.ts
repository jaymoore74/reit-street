/**
 * The datasets clients can download. Each reads one dl_* delivery view (the client contract,
 * ADR 0004), never a wh_* table. Column lists are fixed here, so a new warehouse column is
 * never published by accident.
 */
import type { Dataset } from "./entitlements";

export type ColType = "string" | "int" | "int64" | "decimal" | "date";
export type Column = { name: string; type: ColType; description: string };

export type DatasetSpec = {
  id: Dataset;
  title: string;
  description: string;
  view: string;
  baseWhere: string[]; // fixed conditions, no parameters
  tickerCol: string;
  dateCol: string | null; // limited by history entitlement and from/to filters
  orderBy: string;
  columns: Column[];
};

const c = (name: string, type: ColType, description: string): Column => ({ name, type, description });

const METRIC_KEY = [
  c("ticker", "string", "Exchange ticker"),
  c("cik", "int64", "SEC Central Index Key"),
  c("metric_code", "string", "Metric code, see the metric definitions"),
];
const PERIOD = [
  c("period_type", "string", "Q, YTD, FY, TTM or INSTANT"),
  c("period_start", "date", "First day of the period (empty for INSTANT)"),
  c("period_end", "date", "Last day of the period, or the balance sheet date"),
  c("fiscal_year", "int", "Fiscal year"),
  c("fiscal_quarter", "int", "Fiscal quarter, empty for full-year values"),
  c("segment", "string", "Segment, or Total"),
  c("value", "decimal", "Value, exact to 6 decimal places"),
  c("unit", "string", "Unit, such as USD or USD/share"),
  c("source_type", "string", "xbrl, derived, supplemental or manual"),
  c("version", "int", "Version of this value; restatements add versions"),
  c("published_date", "date", "Date this version became known"),
];

export const DATASET_SPECS: Record<Dataset, DatasetSpec> = {
  companies: {
    id: "companies",
    title: "Companies",
    description: "The REITs we cover, with identifiers and classification.",
    view: "dl_companies",
    baseWhere: ["in_universe = 1"],
    tickerCol: "ticker",
    dateCol: null,
    orderBy: "ticker",
    columns: [
      c("cik", "int64", "SEC Central Index Key"),
      c("ticker", "string", "Exchange ticker"),
      c("name", "string", "Company name"),
      c("exchange", "string", "Listing exchange"),
      c("sector", "string", "Property sector"),
      c("reit_type", "string", "Equity or mortgage REIT"),
      c("fiscal_year_end", "string", "Fiscal year end as MMDD"),
    ],
  },
  metrics: {
    id: "metrics",
    title: "Financial metrics",
    description: "Latest preferred value of each metric for each period.",
    view: "dl_metrics",
    baseWhere: ["is_preferred = 1"],
    tickerCol: "ticker",
    dateCol: "period_end",
    orderBy: "ticker, metric_code, period_type, period_end, segment",
    columns: [
      ...METRIC_KEY,
      c("metric_name", "string", "Metric name"),
      c("category", "string", "Metric category"),
      ...PERIOD,
      c("source_accession", "string", "SEC accession number of the source filing"),
      c("source_filing_url", "string", "Link to the source filing on SEC EDGAR"),
    ],
  },
  metrics_history: {
    id: "metrics_history",
    title: "Metrics as known on a date",
    description: "Point in time: every value as it was known on the as-of date, before later restatements.",
    view: "dl_metrics_history",
    baseWhere: [],
    tickerCol: "ticker",
    dateCol: "period_end",
    orderBy: "ticker, metric_code, period_type, period_end, segment, version",
    columns: [
      ...METRIC_KEY,
      ...PERIOD,
      c("superseded_date", "date", "Date a later version replaced this one (empty if current)"),
      c("source_accession", "string", "SEC accession number of the source filing"),
      c("value_id", "int64", "Stable id of this value, for support questions"),
    ],
  },
  filings: {
    id: "filings",
    title: "SEC filings",
    description: "Filings by the companies we cover, with links to SEC EDGAR.",
    view: "dl_filings",
    baseWhere: [],
    tickerCol: "ticker",
    dateCol: "filing_date",
    orderBy: "ticker, filing_date, accession_no",
    columns: [
      c("ticker", "string", "Exchange ticker"),
      c("cik", "int64", "SEC Central Index Key"),
      c("accession_no", "string", "SEC accession number"),
      c("form_type", "string", "Form type, such as 10-K or 8-K"),
      c("filing_date", "date", "Date filed"),
      c("report_date", "date", "Period the filing reports on"),
      c("items", "string", "8-K item numbers"),
      c("index_url", "string", "Filing index on SEC EDGAR"),
      c("primary_doc_url", "string", "Main document on SEC EDGAR"),
    ],
  },
};
