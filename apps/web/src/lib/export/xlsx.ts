import ExcelJS from "exceljs";
import { NOTICE, SAMPLE_NOTICE, type ExportInput } from "./types";

/** Excel workbook: a Data sheet, plus an About sheet with the download id, filters and column definitions. */
export async function toXlsx({ columns, rows, meta }: ExportInput): Promise<Buffer> {
  const wb = new ExcelJS.Workbook();
  wb.creator = "REIT Street";
  wb.created = new Date(meta.generatedAt);

  const data = wb.addWorksheet("Data", { views: [{ state: "frozen", ySplit: 1 }] });
  data.columns = columns.map((c) => ({
    header: c.name,
    key: c.name,
    width: Math.min(60, Math.max(10, c.name.length + 2)),
    style:
      c.type === "date"
        ? { numFmt: "yyyy-mm-dd" }
        : c.type === "decimal"
          ? { numFmt: "#,##0.00####" }
          : c.type === "int64"
            ? { numFmt: "0" }
            : {},
  }));
  data.getRow(1).font = { bold: true };
  for (const r of rows) {
    const out: Record<string, unknown> = {};
    for (const c of columns) {
      const v = r[c.name];
      if (v === null || v === undefined || v === "") out[c.name] = null;
      else if (c.type === "date") out[c.name] = new Date(`${String(v)}T00:00:00Z`);
      // Excel stores numbers as 64-bit floats; the exact decimal is in the CSV and Parquet files
      else if (c.type === "decimal" || c.type === "int" || c.type === "int64") out[c.name] = Number(v);
      else out[c.name] = String(v);
    }
    data.addRow(out);
  }
  if (rows.length) data.autoFilter = { from: { row: 1, column: 1 }, to: { row: 1, column: columns.length } };

  const about = wb.addWorksheet("About");
  about.columns = [
    { header: "Field", key: "k", width: 22 },
    { header: "Value", key: "v", width: 90 },
  ];
  about.getRow(1).font = { bold: true };
  const info: [string, string][] = [
    ["Dataset", `${meta.title} (${meta.dataset})`],
    ["Download id", meta.downloadId],
    ["Generated (UTC)", meta.generatedAt],
    ["Organization", meta.organization],
    ["Filters", meta.filters],
    ["Rows", String(rows.length)],
    ["Terms", NOTICE],
  ];
  if (meta.sampleData) info.unshift(["WARNING", SAMPLE_NOTICE]);
  for (const [k, v] of info) about.addRow({ k, v });
  about.addRow({});
  about.addRow({ k: "Column", v: "Definition" }).font = { bold: true };
  for (const c of columns) about.addRow({ k: c.name, v: c.description });

  return Buffer.from(await wb.xlsx.writeBuffer());
}
