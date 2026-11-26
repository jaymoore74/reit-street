/** Display formatting and chart series building. Pure, shared by server and client code. */

export function formatValue(value: number, unit: string, compact = false): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (unit === "USD") {
    if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(compact ? 1 : 2)}B`;
    if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
    return `${sign}$${abs.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
  }
  if (unit.startsWith("USD/")) return `${sign}$${abs.toFixed(2)}`;
  if (unit === "shares") {
    if (abs >= 1e6) return `${sign}${(abs / 1e6).toFixed(1)}M`;
    return `${sign}${abs.toLocaleString("en-US")}`;
  }
  return `${value.toLocaleString("en-US", { maximumFractionDigits: 4 })} ${unit}`;
}

const PERIOD_LABEL: Record<string, string> = {
  Q: "quarterly",
  FY: "fiscal year",
  YTD: "year to date",
  TTM: "trailing 12 months",
  INSTANT: "period end",
};

export type SeriesPoint = { period: string; periodEnd: string; value: number; source: string | null };
export type Series = {
  key: string;
  label: string;
  unit: string;
  mark: "bar" | "line";
  points: SeriesPoint[];
};

export type MetricInput = {
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

export function periodLabel(
  r: Pick<MetricInput, "period_type" | "period_end" | "fiscal_year" | "fiscal_quarter">,
): string {
  if (r.period_type === "Q" && r.fiscal_quarter) return `Q${r.fiscal_quarter} ${r.fiscal_year}`;
  if (r.period_type === "FY") return `FY ${r.fiscal_year}`;
  if (r.period_type === "YTD" && r.fiscal_quarter) return `Q${r.fiscal_quarter} YTD ${r.fiscal_year}`;
  return r.period_end;
}

/** One series per metric and period type. Flows (revenue) are bars; balances (assets) are lines. */
export function buildSeries(rows: MetricInput[]): Series[] {
  const map = new Map<string, Series>();
  for (const r of rows) {
    const key = `${r.metric_code}:${r.period_type}`;
    let s = map.get(key);
    if (!s) {
      s = {
        key,
        label: `${r.metric_name}, ${PERIOD_LABEL[r.period_type] ?? r.period_type}`,
        unit: r.unit,
        mark: r.period_type === "INSTANT" ? "line" : "bar",
        points: [],
      };
      map.set(key, s);
    }
    s.points.push({
      period: periodLabel(r),
      periodEnd: r.period_end,
      value: Number(r.value),
      source: r.source_filing_url,
    });
  }
  for (const s of map.values()) s.points.sort((a, b) => a.periodEnd.localeCompare(b.periodEnd));
  // most useful first: quarterly revenue, then the rest by name, year-to-date last
  const order = (k: string) => (k === "TOTAL_REVENUE:Q" ? 0 : k.endsWith(":YTD") ? 2 : 1);
  return [...map.values()].sort((a, b) => order(a.key) - order(b.key) || a.label.localeCompare(b.label));
}
