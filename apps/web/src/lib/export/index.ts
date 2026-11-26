import type { Format } from "../entitlements";
import { toCsv } from "./csv";
import { toParquet } from "./parquet";
import { toXlsx } from "./xlsx";
import type { ExportInput } from "./types";

export const CONTENT_TYPES: Record<Format, string> = {
  csv: "text/csv; charset=utf-8",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  parquet: "application/vnd.apache.parquet",
};

export async function render(format: Format, input: ExportInput): Promise<Buffer> {
  if (format === "csv") return toCsv(input);
  if (format === "xlsx") return toXlsx(input);
  return toParquet(input);
}
