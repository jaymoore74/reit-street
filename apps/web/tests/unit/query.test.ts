import { describe, expect, it } from "vitest";
import { resolveEntitlements } from "@/lib/entitlements";
import { buildDownloadQuery } from "@/lib/query";
import { downloadRequest } from "@/lib/validation";

const TODAY = "2026-11-26";
const ent = resolveEntitlements(
  [
    { kind: "coverage_ticker", value: "PLD", valid_from: null, valid_to: null },
    { kind: "history_years", value: "5", valid_from: null, valid_to: null },
  ],
  { status: "active", contract_start: null, contract_end: null },
  TODAY,
);
const req = (o: Record<string, string>) => downloadRequest.parse({ format: "csv", ...o });

describe("buildDownloadQuery", () => {
  it("always applies coverage and the history floor", () => {
    const q = buildDownloadQuery(req({ dataset: "metrics" }), ent, TODAY, 101);
    expect(q.sql).toContain("FROM dl_metrics WHERE is_preferred = 1 AND (ticker IN (?)) AND period_end >= ?");
    expect(q.params).toEqual(["PLD", "2021-11-26", 101]);
    expect(q.effectiveFrom).toBe("2021-11-26");
  });

  it("can't reach before the history floor with an earlier from date", () => {
    const q = buildDownloadQuery(req({ dataset: "filings", from: "1990-01-01" }), ent, TODAY, 10);
    expect(q.effectiveFrom).toBe("2021-11-26");
    expect(q.params).toContain("2021-11-26");
    expect(q.params).not.toContain("1990-01-01");
  });

  it("a later from date wins over the floor", () => {
    expect(buildDownloadQuery(req({ dataset: "filings", from: "2024-01-01" }), ent, TODAY, 10).effectiveFrom).toBe(
      "2024-01-01",
    );
  });

  it("requested tickers narrow coverage, never widen it", () => {
    const q = buildDownloadQuery(req({ dataset: "metrics", tickers: "psa pld" }), ent, TODAY, 10);
    expect(q.sql).toMatch(/\(ticker IN \(\?\)\) AND ticker IN \(\?, \?\)/);
    expect(q.params.slice(0, 1)).toEqual(["PLD"]);
  });

  it("metrics_history filters to what was known on the as-of date", () => {
    const q = buildDownloadQuery(req({ dataset: "metrics_history", asOf: "2025-03-31" }), ent, TODAY, 10);
    expect(q.sql).toContain("published_date <= ? AND (superseded_date IS NULL OR superseded_date > ?)");
    expect(q.params).toEqual(["PLD", "2021-11-26", "2025-03-31", "2025-03-31", 10]);
  });

  it("companies has no date filter", () => {
    const q = buildDownloadQuery(req({ dataset: "companies", from: "2020-01-01" }), ent, TODAY, 10);
    expect(q.sql).not.toContain(">=");
    expect(q.effectiveFrom).toBeNull();
  });
});

describe("downloadRequest", () => {
  it("normalizes lists", () => {
    const r = req({ dataset: "metrics", tickers: " pld, o;spg  PLD", periodTypes: "q fy" });
    expect(r.tickers).toEqual(["PLD", "O", "SPG"]);
    expect(r.periodTypes).toEqual(["Q", "FY"]);
  });
  it("rejects bad values", () => {
    for (const bad of [
      { dataset: "users" },
      { dataset: "metrics", format: "pdf" },
      { dataset: "metrics", tickers: "PLD'--" },
      { dataset: "metrics", from: "2025-02-30" },
      { dataset: "metrics", from: "2025-03-01", to: "2025-01-01" },
      { dataset: "metrics", periodTypes: "WEEK" },
    ]) {
      expect(downloadRequest.safeParse({ format: "csv", ...bad }).success, JSON.stringify(bad)).toBe(false);
    }
  });
  it("treats blank dates as absent", () => {
    expect(req({ dataset: "metrics", from: "", to: "" }).from).toBeUndefined();
  });
});
