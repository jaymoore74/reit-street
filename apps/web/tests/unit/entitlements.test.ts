import { describe, expect, it } from "vitest";
import {
  DEFAULT_MAX_ROWS,
  coverageClause,
  hasCoverage,
  historyStart,
  resolveEntitlements,
  type GrantRow,
} from "@/lib/entitlements";

const g = (kind: string, value = "", valid_from: string | null = null, valid_to: string | null = null): GrantRow => ({
  kind,
  value,
  valid_from,
  valid_to,
});
const ACTIVE = { status: "active", contract_start: null, contract_end: null };
const TODAY = "2026-11-26";

describe("resolveEntitlements", () => {
  it("unions plan and org grants and removes duplicates", () => {
    const e = resolveEntitlements(
      [g("dataset", "metrics"), g("dataset", "metrics"), g("dataset", "filings"), g("format", "csv"), g("coverage_ticker", "pld")],
      ACTIVE,
      TODAY,
    );
    expect(e.datasets).toEqual(["filings", "metrics"]);
    expect(e.formats).toEqual(["csv"]);
    expect(e.tickers).toEqual(["PLD"]);
    expect(e.coverageAll).toBe(false);
    expect(e.historyYears).toBeNull();
    expect(e.maxRows).toBe(DEFAULT_MAX_ROWS);
  });

  it("ignores grants outside their dates; valid_to is the last day", () => {
    const e = resolveEntitlements(
      [
        g("dataset", "metrics", "2026-12-01"),
        g("dataset", "filings", null, "2026-11-25"),
        g("dataset", "companies", null, TODAY),
      ],
      ACTIVE,
      TODAY,
    );
    expect(e.datasets).toEqual(["companies"]);
  });

  it("gives nothing when the firm is suspended or outside its contract", () => {
    const grants = [g("dataset", "metrics"), g("coverage_all")];
    expect(resolveEntitlements(grants, { ...ACTIVE, status: "suspended" }, TODAY).active).toBe(false);
    expect(resolveEntitlements(grants, { ...ACTIVE, contract_end: "2026-11-25" }, TODAY).datasets).toEqual([]);
    expect(resolveEntitlements(grants, { ...ACTIVE, contract_start: "2026-12-01" }, TODAY).active).toBe(false);
    expect(resolveEntitlements(grants, { ...ACTIVE, contract_end: TODAY }, TODAY).active).toBe(true);
  });

  it("takes the most generous numeric limit and drops junk values and unknown kinds", () => {
    const e = resolveEntitlements(
      [g("history_years", "3"), g("history_years", "10"), g("max_rows", "abc"), g("max_rows", "5000"), g("dataset", "secret")],
      ACTIVE,
      TODAY,
    );
    expect(e.historyYears).toBe(10);
    expect(e.maxRows).toBe(5000);
    expect(e.datasets).toEqual([]);
  });
});

describe("coverageClause", () => {
  const base = resolveEntitlements([], ACTIVE, TODAY);

  it("sees nothing without a coverage grant", () => {
    expect(coverageClause(base, "ticker")).toEqual({ sql: "1 = 0", params: [] });
    expect(hasCoverage(base)).toBe(false);
  });

  it("sees everything with coverage_all", () => {
    expect(coverageClause({ ...base, coverageAll: true }, "ticker").sql).toBe("1 = 1");
  });

  it("binds tickers and sectors as parameters", () => {
    const c = coverageClause({ ...base, tickers: ["PLD", "O"], sectors: ["Office"] }, "ticker");
    expect(c.sql).toBe(
      "(ticker IN (?, ?) OR ticker IN (SELECT cov.ticker FROM dl_companies cov WHERE cov.sector IN (?)))",
    );
    expect(c.params).toEqual(["PLD", "O", "Office"]);
  });

  it("refuses an unsafe column name", () => {
    expect(() => coverageClause(base, "ticker; DROP")).toThrow();
  });

  it("gives nothing for an inactive firm even with grants", () => {
    expect(coverageClause({ ...base, active: false, coverageAll: true }, "ticker").sql).toBe("1 = 0");
  });
});

describe("historyStart", () => {
  it("is null without a limit and N years back with one", () => {
    expect(historyStart({ ...resolveEntitlements([], ACTIVE, TODAY) }, TODAY)).toBeNull();
    expect(historyStart(resolveEntitlements([g("history_years", "5")], ACTIVE, TODAY), TODAY)).toBe("2021-11-26");
  });
  it("handles 29 February", () => {
    expect(historyStart(resolveEntitlements([g("history_years", "1")], ACTIVE, "2028-02-29"), "2028-02-29")).toBe(
      "2027-03-01",
    );
  });
});
