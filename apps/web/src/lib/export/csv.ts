import type { ExportInput, Cell } from "./types";
import type { ColType } from "../datasets";

/**
 * RFC 4180 CSV, UTF-8, CRLF line ends. Text cells that a spreadsheet would run as a formula
 * (starting with = + - @ tab or CR) get a leading apostrophe; numbers and dates are left as they are.
 */
export function csvCell(value: Cell, type: ColType): string {
  if (value === null || value === undefined) return "";
  let s = String(value);
  if (type === "string" && /^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv({ columns, rows }: ExportInput): Buffer {
  const lines = [columns.map((c) => csvCell(c.name, "string")).join(",")];
  for (const row of rows) lines.push(columns.map((c) => csvCell(row[c.name] ?? null, c.type)).join(","));
  return Buffer.from(lines.join("\r\n") + "\r\n", "utf8");
}
