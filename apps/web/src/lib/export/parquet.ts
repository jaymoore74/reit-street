import { ByteWriter, parquetWrite } from "hyparquet-writer";
import type { ColType } from "../datasets";
import { NOTICE, SAMPLE_NOTICE, type ExportInput, type Cell } from "./types";

/**
 * Parquet with exact types: DECIMAL(28,6) for values (no float rounding), DATE for dates,
 * INT64 for ids. Metadata carries the download id, filters and terms.
 */
const SCALE = 6;
const PRECISION = 28;
const DECIMAL_BYTES = 12; // enough for 28 digits

/** "123.45" -> 123450000n at scale 6. Exact: no floating point. */
export function scaledDecimal(value: string, scale = SCALE): bigint {
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value.trim());
  if (!m) throw new Error(`not a decimal: ${value}`);
  const [, sign, whole, frac = ""] = m;
  if (frac.length > scale && /[^0]/.test(frac.slice(scale))) throw new Error(`more than ${scale} decimals: ${value}`);
  const digits = BigInt(whole! + frac.slice(0, scale).padEnd(scale, "0"));
  return sign === "-" ? -digits : digits;
}

const DAY_MS = 86_400_000;

function convert(values: Cell[], type: ColType): unknown[] {
  switch (type) {
    case "decimal":
      return values.map((v) => (v === null || v === "" ? null : scaledDecimal(String(v))));
    case "date":
      return values.map((v) => (v === null || v === "" ? null : Math.round(Date.parse(`${String(v)}T00:00:00Z`) / DAY_MS)));
    case "int":
      return values.map((v) => (v === null || v === "" ? null : Number(v)));
    case "int64":
      return values.map((v) => (v === null || v === "" ? null : BigInt(String(v))));
    default:
      return values.map((v) => (v === null ? null : String(v)));
  }
}

function schemaElement(name: string, type: ColType) {
  const base = { name, repetition_type: "OPTIONAL" as const };
  switch (type) {
    case "decimal":
      return {
        ...base,
        type: "FIXED_LEN_BYTE_ARRAY" as const,
        type_length: DECIMAL_BYTES,
        converted_type: "DECIMAL" as const,
        scale: SCALE,
        precision: PRECISION,
      };
    case "date":
      return { ...base, type: "INT32" as const, converted_type: "DATE" as const };
    case "int":
      return { ...base, type: "INT32" as const };
    case "int64":
      return { ...base, type: "INT64" as const };
    default:
      return { ...base, type: "BYTE_ARRAY" as const, converted_type: "UTF8" as const };
  }
}

export function toParquet({ columns, rows, meta }: ExportInput): Buffer {
  const writer = new ByteWriter();
  parquetWrite({
    writer,
    columnData: columns.map((c) => ({
      name: c.name,
      data: convert(
        rows.map((r) => r[c.name] ?? null),
        c.type,
      ) as never,
    })),
    schema: [{ name: "root", num_children: columns.length }, ...columns.map((c) => schemaElement(c.name, c.type))],
    kvMetadata: [
      { key: "reitstreet.download_id", value: meta.downloadId },
      { key: "reitstreet.dataset", value: meta.dataset },
      { key: "reitstreet.generated_at", value: meta.generatedAt },
      { key: "reitstreet.organization", value: meta.organization },
      { key: "reitstreet.filters", value: meta.filters },
      { key: "reitstreet.terms", value: meta.sampleData ? `${SAMPLE_NOTICE} ${NOTICE}` : NOTICE },
    ],
    createdBy: "reitstreet-web",
  });
  return Buffer.from(writer.getBuffer());
}
