import { describe, expect, it } from "vitest";
import { buildSeries, formatValue } from "@/lib/format";

describe("formatValue", () => {
  it("formats money, per share and shares", () => {
    expect(formatValue(2_134_567_890, "USD")).toBe("$2.13B");
    expect(formatValue(2_134_567_890, "USD", true)).toBe("$2.1B");
    expect(formatValue(-12_300_000, "USD")).toBe("-$12.3M");
    expect(formatValue(1.234, "USD/shares")).toBe("$1.23");
    expect(formatValue(925_000_000, "shares")).toBe("925.0M");
  });
});

describe("buildSeries", () => {
  const r = (code: string, pt: string, end: string, q: number | null, v: string) => ({
    metric_code: code,
    metric_name: code === "TOTAL_REVENUE" ? "Total revenues" : "Total assets",
    period_type: pt,
    period_end: end,
    fiscal_year: Number(end.slice(0, 4)),
    fiscal_quarter: q,
    value: v,
    unit: "USD",
    source_filing_url: null,
  });
  it("groups by metric and period type, quarterly revenue first, sorted by date", () => {
    const s = buildSeries([
      r("TOTAL_ASSETS", "INSTANT", "2025-03-31", null, "10"),
      r("TOTAL_REVENUE", "Q", "2025-06-30", 2, "2"),
      r("TOTAL_REVENUE", "Q", "2025-03-31", 1, "1"),
    ]);
    expect(s.map((x) => x.key)).toEqual(["TOTAL_REVENUE:Q", "TOTAL_ASSETS:INSTANT"]);
    expect(s[0]!.mark).toBe("bar");
    expect(s[1]!.mark).toBe("line");
    expect(s[0]!.points.map((p) => p.period)).toEqual(["Q1 2025", "Q2 2025"]);
  });
});
