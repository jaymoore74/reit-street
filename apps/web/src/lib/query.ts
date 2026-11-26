/**
 * Builds the SQL for a download. Pure, so entitlement enforcement is unit tested.
 * Every value is a bound parameter; identifiers come only from DATASET_SPECS.
 */
import { DATASET_SPECS } from "./datasets";
import { coverageClause, historyStart, type Entitlements } from "./entitlements";
import type { DownloadRequest } from "./validation";

export type BuiltQuery = { sql: string; params: (string | number)[]; effectiveFrom: string | null };

const placeholders = (n: number) => Array.from({ length: n }, () => "?").join(", ");

export function buildDownloadQuery(req: DownloadRequest, ent: Entitlements, today: string, limit: number): BuiltQuery {
  const spec = DATASET_SPECS[req.dataset];
  const where: string[] = [...spec.baseWhere];
  const params: (string | number)[] = [];

  const cov = coverageClause(ent, spec.tickerCol);
  where.push(cov.sql);
  params.push(...cov.params);

  if (req.tickers.length) {
    where.push(`${spec.tickerCol} IN (${placeholders(req.tickers.length)})`);
    params.push(...req.tickers);
  }

  let effectiveFrom: string | null = null;
  if (spec.dateCol) {
    const floor = historyStart(ent, today);
    effectiveFrom = [floor, req.from ?? null].filter((d): d is string => d !== null).sort().at(-1) ?? null;
    if (effectiveFrom) {
      where.push(`${spec.dateCol} >= ?`);
      params.push(effectiveFrom);
    }
    if (req.to) {
      where.push(`${spec.dateCol} <= ?`);
      params.push(req.to);
    }
  }

  if (req.dataset === "metrics" || req.dataset === "metrics_history") {
    if (req.metrics.length) {
      where.push(`metric_code IN (${placeholders(req.metrics.length)})`);
      params.push(...req.metrics);
    }
    if (req.periodTypes.length) {
      where.push(`period_type IN (${placeholders(req.periodTypes.length)})`);
      params.push(...req.periodTypes);
    }
  }
  if (req.dataset === "metrics_history") {
    const asOf = req.asOf ?? today;
    where.push("published_date <= ?", "(superseded_date IS NULL OR superseded_date > ?)");
    params.push(asOf, asOf);
  }
  if (req.dataset === "filings" && req.forms.length) {
    where.push(`form_type IN (${placeholders(req.forms.length)})`);
    params.push(...req.forms);
  }

  const cols = spec.columns.map((col) => col.name).join(", ");
  const sql = `SELECT ${cols} FROM ${spec.view} WHERE ${where.join(" AND ")} ORDER BY ${spec.orderBy} LIMIT ?`;
  params.push(limit);
  return { sql, params, effectiveFrom };
}
