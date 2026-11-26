import ExcelJS from "exceljs";
import { parquetMetadata, parquetReadObjects } from "hyparquet";
import { describe, expect, it } from "vitest";
import { DATASET_SPECS } from "@/lib/datasets";
import { csvCell, toCsv } from "@/lib/export/csv";
import { scaledDecimal, toParquet } from "@/lib/export/parquet";
import type { ExportInput } from "@/lib/export/types";
import { toXlsx } from "@/lib/export/xlsx";

const spec = DATASET_SPECS.metrics;
const row = (ticker: string, value: string, extra: Record<string, string | number | null> = {}) => ({
  ticker,
  cik: 1045609,
  metric_code: "TOTAL_REVENUE",
  metric_name: "Total revenues",
  category: "income",
  period_type: "Q",
  period_start: "2025-01-01",
  period_end: "2025-03-31",
  fiscal_year: 2025,
  fiscal_quarter: 1,
  segment: "Total",
  value,
  unit: "USD",
  source_type: "xbrl",
  version: 1,
  published_date: "2025-04-29",
  source_accession: "0001045609-25-000010",
  source_filing_url: null,
  ...extra,
});
const input: ExportInput = {
  columns: spec.columns,
  rows: [row("PLD", "2134567890.123456"), row("O", "-0.000001", { metric_name: "=HYPERLINK(\"x\")" })],
  meta: {
    downloadId: "01JDOWNLOADID0000000000000",
    dataset: "metrics",
    title: spec.title,
    generatedAt: "2026-11-26T06:00:00.000Z",
    filters: "tickers PLD O",
    organization: "Demo Capital",
    sampleData: true,
  },
};

describe("csv", () => {
  it("quotes and guards against spreadsheet formulas in text only", () => {
    expect(csvCell("=1+1", "string")).toBe("'=1+1");
    expect(csvCell("-5", "decimal")).toBe("-5");
    expect(csvCell('a,"b"', "string")).toBe('"a,""b"""');
    expect(csvCell(null, "string")).toBe("");
  });
  it("writes header and rows with exact decimals", () => {
    const text = toCsv(input).toString("utf8");
    const lines = text.split("\r\n");
    expect(lines[0]).toBe(spec.columns.map((c) => c.name).join(","));
    expect(lines[1]).toContain(",2134567890.123456,");
    expect(lines[2]).toContain(`,"'=HYPERLINK(""x"")",`);
    expect(text.endsWith("\r\n")).toBe(true);
  });
});

describe("parquet", () => {
  it("scales decimals exactly", () => {
    expect(scaledDecimal("123.45")).toBe(123450000n);
    expect(scaledDecimal("-0.000001")).toBe(-1n);
    expect(scaledDecimal("9999999999999999999999.999999")).toBe(9999999999999999999999999999n);
    expect(() => scaledDecimal("1.0000001")).toThrow();
    expect(() => scaledDecimal("1e5")).toThrow();
  });
  it("round-trips values, dates and metadata", async () => {
    const buf = toParquet(input);
    const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;
    const rows = await parquetReadObjects({ file: ab });
    expect(rows).toHaveLength(2);
    expect(rows[0]!.ticker).toBe("PLD");
    expect(Number(rows[0]!.value)).toBeCloseTo(2134567890.123456, 5);
    expect(Number(rows[1]!.value)).toBeCloseTo(-0.000001, 9);
    expect(String(rows[0]!.cik)).toBe("1045609");
    expect(new Date(rows[0]!.period_end as Date).toISOString().slice(0, 10)).toBe("2025-03-31");
    expect(rows[0]!.source_filing_url).toBeNull();
    const meta = parquetMetadata(ab);
    const kv = Object.fromEntries((meta.key_value_metadata ?? []).map((k) => [k.key, k.value]));
    expect(kv["reitstreet.download_id"]).toBe(input.meta.downloadId);
    expect(kv["reitstreet.terms"]).toContain("SAMPLE DATA");
  });
});

describe("xlsx", () => {
  it("has a Data sheet with typed cells and an About sheet", async () => {
    const wb = new ExcelJS.Workbook();
    await wb.xlsx.load((await toXlsx(input)) as unknown as ArrayBuffer);
    const data = wb.getWorksheet("Data")!;
    expect(data.rowCount).toBe(3);
    const valueCol = spec.columns.findIndex((c) => c.name === "value") + 1;
    expect(data.getRow(2).getCell(valueCol).value).toBeCloseTo(2134567890.123456, 4);
    const dateCol = spec.columns.findIndex((c) => c.name === "period_end") + 1;
    expect(data.getRow(2).getCell(dateCol).value).toBeInstanceOf(Date);
    const about = wb.getWorksheet("About")!;
    const text = about.getSheetValues().flat().map(String).join(" ");
    expect(text).toContain(input.meta.downloadId);
    expect(text).toContain("SAMPLE DATA");
    expect(text).toContain("Demo Capital");
  });
});
