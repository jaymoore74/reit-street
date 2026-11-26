# ADR 0012: NAV and AFFO models generated from cited inputs

Status: accepted, 2026-11-26

## Context
The product needs a NAV and an AFFO model for every REIT, kept current each quarter, in the
Excel layout analysts expect (Arial 8, blue hardcodes, black formulas, green cross-sheet
links). Hand-built workbooks drift: a pasted number loses its source, a formula is overwritten
with a value, and a Python number and the Excel number disagree. The first model is Essex
(ESS, 3Q26); other apartment REITs must plug in without new code.

## Decision
1. **Inputs are data.** One TOML file per company and quarter
   (`src/reitstreet/models/inputs/<ticker>_<yyyy>q<n>.toml`). Every reported number is an
   `input` with a source id and page. Every judgment (cap rates, growth, market debt rate,
   haircuts) is an `assumption` with a written rationale. Validation refuses a model with an
   uncited input or an assumption without a rationale.
2. **One formula language, two outputs.** Calculations are written once as Python-syntax
   expressions over cell keys (`formula.py`, parsed with `ast` against a whitelist). The same
   tree is evaluated in Python and rendered as an Excel formula, so the workbook and the
   tests cannot disagree. Supported: `+ - * / **`, comparisons, `SUM`, `SUMPRODUCT`, `MIN`,
   `MAX`, `AVERAGE`, `IF`, `ABS`, and contiguous ranges ("groups") inside `SUM`/`SUMPRODUCT`.
3. **No hardcodes in calc sheets.** Inputs live only on Inputs, assumptions only on
   Assumptions. A calc cell may contain only the constants 0, 1, 4, 12 and 1000 (unit and
   period conversions); anything else fails validation. Cycles fail validation.
4. **Sector builders.** `models/sectors/<sector>.py` turns inputs into sheets; `SECTORS`
   maps the TOML `sector` field to a builder. Apartment is the first. A new sector adds a
   builder and a test; a new apartment REIT adds only a TOML file.
5. **House style in one place.** `excel.py` writes Arial 8 throughout, ticker and title rows,
   dark section bars, total and grand-total borders, gridlines off, frozen panes, and the
   colour rule (blue input or assumption, black formula, green link to another sheet).
6. **Method (apartment).**
   - NAV: annualized quarterly same-store-adjusted NOI by market, grown one year at a market
     growth rate, capped at a market cap rate. Unconsolidated JVs valued the same way at
     share. Management platform at margin times multiple. CIP at cost, other assets at a
     recovery haircut. Fixed-rate debt and preferred marked to market at a market rate
     (closed-form annuity); floating and swapped tranches at par. Implied cap rate solves
     the market's enterprise value against forward NOI.
   - AFFO: reported FFO to Core FFO reconciled to the supplemental (check cells), a Core FFO
     build from NOI, fees, interest, JV and G&A, then recurring capex per unit, JV capex,
     straight-line and non-cash items to AFFO. Three-year forecast with explicit revenue and
     expense growth, acquisitions, interest and JV adjustments. Dividend, payout and coverage.
   - Sensitivity: NAV per share and premium/discount over cap rate and growth shifts.
7. **CLI.** `reitstreet model check <inputs.toml>` validates and prints the summary;
   `reitstreet model build <inputs.toml> [--out file.xlsx]` writes the workbook. Neither
   touches the database or the network.

## Consequences
- Every number in a model traces to a document and page, or to a written assumption.
- Tests assert reconciliation to the supplemental, NAV identities, sensitivity direction
  and, where LibreOffice is installed, that Excel recalculation equals the Python values.
- The DSL is deliberately small. Lookups, text functions and array formulas are out of
  scope; a need for one is a new decision.
- Inputs are typed in by hand for now. Once the supplemental parser loads
  property-level data, the TOML becomes a generated artifact from the warehouse.
