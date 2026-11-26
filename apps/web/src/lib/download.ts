import "server-only";
import { appExec, whQuery } from "./db";
import { DATASET_SPECS } from "./datasets";
import { env } from "./env";
import { hasCoverage, todayUtc, type Entitlements } from "./entitlements";
import { CONTENT_TYPES, render } from "./export";
import type { ExportRow } from "./export/types";
import { buildDownloadQuery } from "./query";
import type { ClientInfo } from "./request";
import type { CurrentUser } from "./session";
import { sha256Hex, ulid } from "./tokens";
import { downloadRequest, type DownloadRequest } from "./validation";

/** XLSX holds at most 1,048,576 rows per sheet. */
const XLSX_MAX_ROWS = 1_000_000;

export type DownloadOutcome =
  | { ok: true; body: Buffer; contentType: string; filename: string; downloadId: string }
  | { ok: false; status: 400 | 403 | 413 | 500; error: string; downloadId: string };

function describe(req: DownloadRequest, effectiveFrom: string | null): string {
  const parts: string[] = [];
  if (req.tickers.length) parts.push(`tickers ${req.tickers.join(" ")}`);
  if (req.metrics.length) parts.push(`metrics ${req.metrics.join(" ")}`);
  if (req.periodTypes.length) parts.push(`period types ${req.periodTypes.join(" ")}`);
  if (req.forms.length) parts.push(`forms ${req.forms.join(" ")}`);
  if (effectiveFrom) parts.push(`from ${effectiveFrom}`);
  if (req.to) parts.push(`to ${req.to}`);
  if (req.dataset === "metrics_history") parts.push(`as of ${req.asOf ?? "today"}`);
  return parts.length ? parts.join("; ") : "none (everything your subscription covers)";
}

async function log(
  id: string,
  user: CurrentUser,
  dataset: string,
  format: string,
  params: unknown,
  status: "ok" | "denied" | "error",
  client: ClientInfo,
  extra: { reason?: string; rows?: number; bytes?: number; sha?: string } = {},
) {
  await appExec(
    `INSERT INTO app_download (download_id, user_id, org_id, dataset, format, params, status, reason,
                               row_count, byte_count, sha256, ip)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      id,
      user.userId,
      user.orgId,
      dataset.slice(0, 48),
      ["csv", "xlsx", "parquet"].includes(format) ? format : "csv",
      JSON.stringify(params ?? {}),
      status,
      extra.reason?.slice(0, 512) ?? null,
      extra.rows ?? null,
      extra.bytes ?? null,
      extra.sha ?? null,
      client.ip,
    ],
  );
}

/**
 * Runs one download end to end. Every attempt, allowed or refused, is written to app_download
 * before anything is returned; if that write fails, no file is sent.
 */
export async function runDownload(
  user: CurrentUser,
  ent: Entitlements,
  input: Record<string, string>,
  client: ClientInfo,
): Promise<DownloadOutcome> {
  const downloadId = ulid();
  const dataset = String(input.dataset ?? "").slice(0, 48);
  const format = String(input.format ?? "").slice(0, 16);
  const parsed = downloadRequest.safeParse(input);
  if (!parsed.success) {
    const error = parsed.error.issues.map((i) => `${i.path.join(".") || "request"}: ${i.message}`).join("; ");
    await log(downloadId, user, dataset || "unknown", format, input, "denied", client, { reason: `invalid: ${error}` });
    return { ok: false, status: 400, error, downloadId };
  }
  const req = parsed.data;

  const refuse = async (reason: string, status: 403 | 413 = 403): Promise<DownloadOutcome> => {
    await log(downloadId, user, req.dataset, req.format, req, "denied", client, { reason });
    return { ok: false, status, error: reason, downloadId };
  };
  if (!ent.active) return refuse("Your firm's subscription is not active.");
  if (!ent.datasets.includes(req.dataset)) return refuse(`Your subscription doesn't include the ${req.dataset} dataset.`);
  if (!ent.formats.includes(req.format)) return refuse(`Your subscription doesn't include ${req.format} downloads.`);
  if (!hasCoverage(ent)) return refuse("Your subscription doesn't cover any companies yet.");

  const cap = Math.min(ent.maxRows, req.format === "xlsx" ? XLSX_MAX_ROWS : Number.MAX_SAFE_INTEGER);
  const today = todayUtc();
  try {
    const q = buildDownloadQuery(req, ent, today, cap + 1);
    const rows = await whQuery<ExportRow>(q.sql, q.params);
    if (rows.length > cap) return refuse(`More than ${cap.toLocaleString("en-US")} rows. Narrow the filters.`, 413);

    const spec = DATASET_SPECS[req.dataset];
    const generatedAt = new Date().toISOString();
    const body = await render(req.format, {
      columns: spec.columns,
      rows,
      meta: {
        downloadId,
        dataset: req.dataset,
        title: spec.title,
        generatedAt,
        filters: describe(req, q.effectiveFrom),
        organization: user.orgName,
        sampleData: env().RS_SAMPLE_DATA,
      },
    });
    const sha = sha256Hex(body);
    await log(downloadId, user, req.dataset, req.format, req, "ok", client, { rows: rows.length, bytes: body.length, sha });
    return {
      ok: true,
      body,
      contentType: CONTENT_TYPES[req.format],
      filename: `reitstreet_${req.dataset}_${today}_${downloadId}.${req.format}`,
      downloadId,
    };
  } catch (err) {
    console.error(JSON.stringify({ msg: "download failed", downloadId, err: String(err) }));
    await log(downloadId, user, req.dataset, req.format, req, "error", client, { reason: "server error" });
    return { ok: false, status: 500, error: "Something went wrong. Quote the download id to support.", downloadId };
  }
}
