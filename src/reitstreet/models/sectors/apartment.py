"""Apartment REIT NAV and AFFO model.

Generic for any apartment REIT whose inputs file provides the keys in ``REQUIRED_INPUTS`` and
``REQUIRED_ASSUMPTIONS`` (plus the per-year keys listed in ``required_keys``). Method: see
docs/adr/0012-nav-affo-models.md.
"""

from __future__ import annotations

from ..inputs import ModelInputs
from ..workbook import Book, Cell, Sheet, calc, text

REQUIRED_INPUTS = [
    "price", "div_q",
    "sh_common", "sh_units", "sh_options", "sh_wtd_q", "sh_wtd_ytd", "sh_guide",
    "noi_q", "noi_q1", "noi_q2", "noi_q3", "rev_q", "rev_q1", "rev_q2", "rev_q3",
    "noi_adj_pf", "noi_adj_other", "sl_q",
    "jv_noi_q", "jv_debt", "jv_debt_rate", "jv_debt_years", "jv_cash",
    "cip", "pref_eq", "notes_rec", "mkt_sec", "cash", "cash_restr", "other_assets",
    "other_liab", "redeem_nci",
    "ffo_rep_q", "ffo_rep_ytd", "core_rep_q", "core_rep_ytd",
    "stock_comp_ytd", "fin_amort_ytd", "sl_rent_ytd", "ytd_months",
    "capex_q", "capex_t4q", "capex_unit_t4q", "avg_units_t4q",
    "gd_noi", "gd_fees", "gd_int", "gd_other", "gd_jvffo", "gd_ga", "gd_corp", "gd_nci", "gd_core_ps",
]  # fmt: skip

REQUIRED_ASSUMPTIONS = [
    "as_jv_cap_spread", "as_rate_unsec", "as_rate_sec",
    "as_other_assets_rec", "as_other_liab_pct", "as_cip_pct", "as_pref_pct", "as_notes_pct",
    "as_fee_margin", "as_fee_mult",
    "as_fee_g", "as_other_g", "as_ga_g", "as_share_g", "as_capex_g", "as_div_g",
    "as_refi_rate", "as_refi_timing", "as_acq_yield", "as_acq_debt_pct", "as_acq_timing", "as_jv_units_share",
]  # fmt: skip

SENS_STEPS = 5


def required_keys(mi: ModelInputs) -> tuple[list[str], list[str]]:
    years = mi.forecast_years
    inputs = list(REQUIRED_INPUTS)
    for y in years:
        inputs += [f"mat_{y}", f"mrate_{y}"]
    assumptions = list(REQUIRED_ASSUMPTIONS)
    for y in years[1:]:
        assumptions += [f"as_rev_g_{y}", f"as_exp_g_{y}", f"as_acq_amt_{y}", f"as_int_adj_{y}", f"as_jvffo_adj_{y}"]
    for i in range(1, SENS_STEPS + 1):
        assumptions += [f"as_cs_{i}", f"as_gs_{i}"]
    return inputs, assumptions


def _in(mi: ModelInputs, key: str) -> Cell:
    for it in mi.inputs:
        if it.key == key:
            return Cell("input", key=it.key, value=it.value, fmt=it.fmt, source=it.source, page=it.page, note=it.note)
    raise KeyError(key)


def build(mi: ModelInputs) -> Book:
    need_in, need_as = required_keys(mi)
    missing = [k for k in need_in if k not in mi.input_keys()] + [k for k in need_as if k not in mi.assumption_keys()]
    if missing:
        raise ValueError(f"{mi.path}: missing keys for the apartment model: {', '.join(missing)}")
    if len(mi.forecast_years) < 2:
        raise ValueError("apartment model needs at least two forecast years (guidance year plus one)")

    book = Book(mi.ticker, mi.company, meta={"period": mi.period, "as_of": mi.as_of, "price_date": mi.price_date})
    summary = book.sheet("Summary", "Summary", col_widths=[11, 11, 11, 11])
    nav = book.sheet("NAV", "Net Asset Value", headers=[mi.period, "Per share"], col_widths=[12, 10])
    markets = book.sheet(
        "Markets",
        "NAV by Market",
        headers=[
            "Units", "% NOI", "Cash NOI", "Growth", "Fwd NOI", "Cap rate", "Value", "$/unit",
            "JV units", "JV % NOI", "JV NOI", "JV fwd NOI", "JV cap", "JV value",
        ],
        label_width=24,
        col_widths=[8, 7, 10, 7, 10, 7, 11, 9, 8, 7, 9, 9, 7, 10],
    )  # fmt: skip
    debt = book.sheet(
        "Debt",
        "Debt Mark-to-Market",
        headers=["Principal", "Rate", "Years", "Fixed", "Secured", "Mkt rate", "MTM factor", "Fair value", "MTM"],
        label_width=34,
        col_widths=[11, 7, 7, 6, 7, 7, 9, 11, 10],
    )
    years = mi.forecast_years
    affo = book.sheet(
        "AFFO",
        "FFO, Core FFO and AFFO",
        headers=[mi.quarter_label, mi.ytd_label, f"{mi.quarter_label} Ann.", *[f"{y}E" for y in years]],
        col_widths=[10, 10, 10, 10, 10, 10],
    )
    sens = book.sheet("Sensitivity", "NAV Sensitivity", unit_note="(per share)", label_width=30)
    inputs = book.sheet(
        "Inputs",
        "Inputs (sourced)",
        headers=["Value", "Unit", "Source", "Page", "Note", "Key"],
        unit_note="(as reported; $ thousands unless noted)",
        label_width=46,
        col_widths=[11, 7, 22, 12, 60, 22],
    )
    assumptions = book.sheet(
        "Assumptions",
        "Assumptions (analyst estimates)",
        headers=["Value", "Unit", "Rationale", "Key"],
        unit_note="(change these; every calc reads them)",
        label_width=40,
        col_widths=[9, 6, 100, 18],
    )
    sources = book.sheet(
        "Sources",
        "Sources",
        headers=["Document", "Date", "Link"],
        unit_note="",
        label_width=14,
        col_widths=[60, 11, 80],
    )

    _inputs_sheet(inputs, mi)
    _assumptions_sheet(assumptions, mi)
    _markets_sheet(book, markets, mi)
    _debt_sheet(debt, mi)
    _nav_sheet(nav, mi)
    _affo_sheet(affo, mi)
    _sens_sheet(sens)
    _summary_sheet(summary, mi)
    for s in mi.sources.values():
        sources.line(s.id, text(s.title), text(s.date), text(s.url))
    sources.blank()
    sources.note("Market data, filings and transcripts retrieved through Bigdata.com (https://bigdata.com).")
    sources.note("Blue = sourced input or analyst assumption. Black = formula. Green = formula reading another sheet.")
    return book


# ---------------------------------------------------------------------------- inputs and assumptions
def _inputs_sheet(sh: Sheet, mi: ModelInputs) -> None:
    section = None
    for it in mi.inputs:
        if it.section != section:
            if section is not None:
                sh.blank()
            sh.section(it.section or "Other")
            section = it.section
        src = mi.sources[it.source].title if it.source in mi.sources else it.source
        sh.line(it.label, _in(mi, it.key), text(it.unit), text(src), text(it.page), text(it.note), text(it.key))


def _assumptions_sheet(sh: Sheet, mi: ModelInputs) -> None:
    section = None
    for it in mi.assumptions:
        if it.section != section:
            if section is not None:
                sh.blank()
            sh.section(it.section or "Other")
            section = it.section
        cell = Cell("assumption", key=it.key, value=it.value, fmt=it.fmt, note=it.rationale)
        sh.line(it.label, cell, text(it.unit), text(it.rationale), text(it.key))


# ---------------------------------------------------------------------------- markets
_MKT_COLS = [
    ("units", "num"), ("pct", "pct1"), ("noi", "num"), ("g", "pct1"), ("fwd", "num"), ("cap", "pct"),
    ("val", "num"), ("unitval", "dollar"), ("jvunits", "num"), ("jvpct", "pct1"), ("jvnoi", "num"),
    ("jvfwd", "num"), ("jvcap", "pct"), ("jvval", "num"),
]  # fmt: skip


def _markets_sheet(book: Book, sh: Sheet, mi: ModelInputs) -> None:
    sh.section("Consolidated and co-investment portfolio, forward NOI capitalized by market")
    for col, _ in _MKT_COLS:
        book.groups[f"mkt_{col}"] = []
    for m in mi.markets:
        k = m.key
        exprs = {
            "units": f"mk_units_{k}",
            "pct": f"mk_noi_pct_{k}",
            "noi": f"nav_cash_noi * m_pct_{k}",
            "g": f"as_g_{k}",
            "fwd": f"m_noi_{k} * (1 + m_g_{k})",
            "cap": f"as_cap_{k}",
            "val": f"m_fwd_{k} / m_cap_{k}",
            "unitval": f"m_val_{k} * 1000 / m_units_{k}",
            "jvunits": f"mk_jv_units_{k}",
            "jvpct": f"mk_jv_noi_pct_{k}",
            "jvnoi": f"nav_jv_noi * m_jvpct_{k}",
            "jvfwd": f"m_jvnoi_{k} * (1 + m_g_{k})",
            "jvcap": f"m_cap_{k} + as_jv_cap_spread",
            "jvval": f"IF(m_jvfwd_{k} == 0, 0, m_jvfwd_{k} / m_jvcap_{k})",
        }
        cells: list[Cell | None] = []
        for col, fmt in _MKT_COLS:
            if col == "unitval":
                exprs[col] = f"IF(m_units_{k} == 0, 0, m_val_{k} * 1000 / m_units_{k})"
            cells.append(calc(f"m_{col}_{k}", exprs[col], fmt))
            book.groups[f"mkt_{col}"].append(f"m_{col}_{k}")
        sh.line(m.name, *cells)
    sh.line(
        "Total / weighted",
        calc("mt_units", "SUM(mkt_units)"),
        calc("mt_pct", "SUM(mkt_pct)", "pct1"),
        calc("mt_noi", "SUM(mkt_noi)"),
        calc("mt_g", "mt_fwd / mt_noi - 1", "pct1"),
        calc("mt_fwd", "SUM(mkt_fwd)"),
        calc("mt_cap", "mt_fwd / mt_val", "pct"),
        calc("mt_val", "SUM(mkt_val)"),
        calc("mt_unitval", "mt_val * 1000 / mt_units", "dollar"),
        calc("mt_jvunits", "SUM(mkt_jvunits)"),
        calc("mt_jvpct", "SUM(mkt_jvpct)", "pct1"),
        calc("mt_jvnoi", "SUM(mkt_jvnoi)"),
        calc("mt_jvfwd", "SUM(mkt_jvfwd)"),
        calc("mt_jvcap", "mt_jvfwd / mt_jvval", "pct"),
        calc("mt_jvval", "SUM(mkt_jvval)"),
        style="grand",
    )
    sh.blank()
    sh.line("Check: market NOI shares sum to 100%", None, calc("ck_pct", "mt_pct - 1", "pct1"), style="note")
    sh.line(
        "Check: co-investment shares sum to 100%", *[None] * 9, calc("ck_jvpct", "mt_jvpct - 1", "pct1"), style="note"
    )
    sh.note("Cash NOI = run-rate cash NOI (NAV sheet) x the market's share of NOI. Fwd NOI = Cash NOI x (1 + growth).")
    sh.note("Value = Fwd NOI / cap rate. Co-investments use the same growth and cap rate plus the JV cap spread.")


# ---------------------------------------------------------------------------- debt
def _debt_sheet(sh: Sheet, mi: ModelInputs) -> None:
    sh.section("Consolidated debt")
    prin, fvs = [], []
    for t in mi.tranches:
        k = t.key
        sh.line(
            t.name,
            calc(f"d_prin_{k}", f"db_principal_{k}"),
            calc(f"d_cpn_{k}", f"db_coupon_{k}", "pct"),
            calc(f"d_yrs_{k}", f"db_years_{k}", "dec1"),
            calc(f"d_fix_{k}", f"db_fixed_{k}", "int"),
            calc(f"d_sec_{k}", f"db_secured_{k}", "int"),
            calc(f"d_mrate_{k}", f"IF(d_sec_{k} == 1, as_rate_sec, as_rate_unsec)", "pct"),
            calc(
                f"d_factor_{k}",
                f"IF(d_fix_{k} == 1, d_cpn_{k} * (1 - (1 + d_mrate_{k}) ** -d_yrs_{k}) / d_mrate_{k}"
                f" + (1 + d_mrate_{k}) ** -d_yrs_{k}, 1)",
                "dec3",
            ),
            calc(f"d_fv_{k}", f"d_prin_{k} * d_factor_{k}"),
            calc(f"d_mtm_{k}", f"d_fv_{k} - d_prin_{k}"),
        )
        prin.append(f"d_prin_{k}")
        fvs.append(f"d_fv_{k}")
    wcpn = " + ".join(f"d_prin_{t.key} * d_cpn_{t.key}" for t in mi.tranches)
    sh.line(
        "Total consolidated debt",
        calc("d_prin_total", " + ".join(prin)),
        calc("d_cpn_total", f"({wcpn}) / d_prin_total", "pct"),
        None,
        None,
        None,
        None,
        calc("d_factor_total", "d_fv_total / d_prin_total", "dec3"),
        calc("d_fv_total", " + ".join(fvs)),
        calc("d_mtm_total", "d_fv_total - d_prin_total"),
        style="grand",
    )
    sh.blank()
    sh.section("Co-investment debt at share")
    sh.line(
        "Co-investment debt at pro rata share",
        calc("d_prin_jv", "jv_debt"),
        calc("d_cpn_jv", "jv_debt_rate", "pct"),
        calc("d_yrs_jv", "jv_debt_years", "dec1"),
        None,
        None,
        calc("d_mrate_jv", "as_rate_sec", "pct"),
        calc(
            "d_factor_jv",
            "d_cpn_jv * (1 - (1 + d_mrate_jv) ** -d_yrs_jv) / d_mrate_jv + (1 + d_mrate_jv) ** -d_yrs_jv",
            "dec3",
        ),
        calc("d_fv_jv", "d_prin_jv * d_factor_jv"),
        calc("d_mtm_jv", "d_fv_jv - d_prin_jv"),
        style="total",
    )
    sh.blank()
    sh.section("Scheduled maturities (consolidated)")
    sh.colhead("", ["Amount", "Rate"])
    for y in mi.forecast_years:
        sh.line(str(y), calc(f"d_mat_{y}", f"mat_{y}"), calc(f"d_matrate_{y}", f"mrate_{y}", "pct"))
    sh.blank()
    sh.note("MTM factor = price of an annual-pay bond: rate x (1 - (1 + mkt)^-years) / mkt + (1 + mkt)^-years.")
    sh.note("Floating and swapped-to-fixed tranches are held at par; the swap's value sits in other assets.")


# ---------------------------------------------------------------------------- NAV
def _ps(key: str) -> Cell:
    return calc(f"{key}_ps", f"{key} / nav_shares", "ps")


def _nav_sheet(sh: Sheet, mi: ModelInputs) -> None:
    sh.section("Net operating income")
    sh.line(f"{mi.quarter_label} NOI annualized", calc("nav_noi_ann", "noi_q * 4"), indent=1)
    sh.line("Pro forma and other adjustments", calc("nav_noi_adj", "noi_adj_pf + noi_adj_other"), indent=1)
    sh.line("Adjusted NOI", calc("nav_adj_noi", "nav_noi_ann + nav_noi_adj"), style="subtotal")
    sh.line("Remove straight-line concessions", calc("nav_sl", "-sl_q * 4"), indent=1)
    sh.line("Cash NOI, run-rate", calc("nav_cash_noi", "nav_adj_noi + nav_sl"), style="total")
    sh.line("Forward 12-month NOI (Markets)", calc("nav_fwd_noi", "mt_fwd"), indent=1)
    sh.line("Implied forward NOI growth", calc("nav_fwd_g", "nav_fwd_noi / nav_cash_noi - 1", "pct1"), indent=1)
    sh.blank()
    sh.section("Gross asset value")
    sh.line("Operating real estate (Markets)", calc("nav_op_val", "mt_val"), _ps("nav_op_val"), indent=1)
    sh.line("Nominal cap rate on forward NOI", calc("nav_nom_cap", "nav_fwd_noi / nav_op_val", "pct"), indent=2)
    sh.line("Value per consolidated unit", calc("nav_unit_val", "nav_op_val * 1000 / mt_units", "dollar"), indent=2)
    sh.line(f"Co-investment NOI at share, {mi.quarter_label} annualized", calc("nav_jv_noi", "jv_noi_q * 4"), indent=1)
    sh.line("Co-investments at share (Markets)", calc("nav_jv_val", "mt_jvval"), _ps("nav_jv_val"), indent=1)
    sh.line("Nominal cap rate on forward JV NOI", calc("nav_jv_cap", "mt_jvfwd / nav_jv_val", "pct"), indent=2)
    sh.line(
        "Management platform",
        calc("nav_platform", "gd_fees * as_fee_margin * as_fee_mult"),
        _ps("nav_platform"),
        indent=1,
    )
    sh.line("Development and land, at cost", calc("nav_cip", "cip * as_cip_pct"), _ps("nav_cip"), indent=1)
    sh.line("Preferred equity investments", calc("nav_pref", "pref_eq * as_pref_pct"), _ps("nav_pref"), indent=1)
    sh.line("Notes and other receivables", calc("nav_notes", "notes_rec * as_notes_pct"), _ps("nav_notes"), indent=1)
    sh.line("Marketable securities", calc("nav_sec", "mkt_sec"), _ps("nav_sec"), indent=1)
    sh.line(
        "Cash, restricted cash and JV cash at share",
        calc("nav_cash", "cash + cash_restr + jv_cash"),
        _ps("nav_cash"),
        indent=1,
    )
    sh.line("Prepaid and other assets", calc("nav_oth", "other_assets * as_other_assets_rec"), _ps("nav_oth"), indent=1)
    sh.line(
        "Gross asset value",
        calc(
            "nav_gav",
            "nav_op_val + nav_jv_val + nav_platform + nav_cip + nav_pref + nav_notes + nav_sec + nav_cash + nav_oth",
        ),
        _ps("nav_gav"),
        style="total",
    )
    sh.blank()
    sh.section("Liabilities")
    sh.line("Consolidated debt at market (Debt)", calc("nav_debt", "d_fv_total"), _ps("nav_debt"), indent=1)
    sh.line("Co-investment debt at share, at market", calc("nav_jvdebt", "d_fv_jv"), _ps("nav_jvdebt"), indent=1)
    sh.line("Other liabilities", calc("nav_ol", "other_liab * as_other_liab_pct"), _ps("nav_ol"), indent=1)
    sh.line("Redeemable noncontrolling interest", calc("nav_nci", "redeem_nci"), _ps("nav_nci"), indent=1)
    sh.line(
        "Total liabilities",
        calc("nav_liab", "nav_debt + nav_jvdebt + nav_ol + nav_nci"),
        _ps("nav_liab"),
        style="total",
    )
    sh.blank()
    sh.section("Net asset value")
    sh.line("Net asset value", calc("nav_nav", "nav_gav - nav_liab"), style="grand")
    sh.line("Shares, OP units and options (000)", calc("nav_shares", "sh_common + sh_units + sh_options"), indent=1)
    sh.line("NAV per share", calc("nav_ps", "nav_nav / nav_shares", "ps"), style="grand")
    sh.line(f"Share price ({mi.price_date})", calc("nav_price", "price", "ps"), indent=1)
    sh.line("Premium / (discount) to NAV", calc("nav_prem", "nav_price / nav_ps - 1", "pct1"), style="bold")
    sh.blank()
    sh.section("Implied valuation at the share price")
    sh.line("Equity market value", calc("nav_mktcap", "nav_price * nav_shares"), indent=1)
    sh.line("Plus total liabilities", calc("nav_imp_liab", "nav_liab"), indent=1)
    sh.line(
        "Less non-operating assets and platform",
        calc("nav_imp_nonop", "-(nav_platform + nav_cip + nav_pref + nav_notes + nav_sec + nav_cash + nav_oth)"),
        indent=1,
    )
    sh.line(
        "Implied value of operating real estate",
        calc("nav_imp_re", "nav_mktcap + nav_imp_liab + nav_imp_nonop"),
        style="total",
    )
    sh.line(
        "Implied nominal cap rate (forward NOI incl. JV)",
        calc("nav_imp_cap", "(nav_fwd_noi + mt_jvfwd) / nav_imp_re", "pct"),
        style="bold",
    )
    sh.line(
        "Implied value per unit (incl. JV units at share)",
        calc("nav_imp_unit", "nav_imp_re * 1000 / (mt_units + as_jv_units_share)", "dollar"),
        indent=1,
    )
    sh.blank()
    sh.section("Memo")
    sh.line(
        "Debt incl. JV at share / gross asset value",
        calc("nav_lev", "(nav_debt + nav_jvdebt) / nav_gav", "pct1"),
        indent=1,
    )
    sh.line(
        "NAV excluding operating real estate (for Sensitivity)",
        calc("nav_other_net", "nav_nav - nav_op_val - nav_jv_val"),
        indent=1,
    )


# ---------------------------------------------------------------------------- AFFO
def _affo_sheet(sh: Sheet, mi: ModelInputs) -> None:
    years = mi.forecast_years
    y0 = years[0]
    pad = [None] * 3  # Q, YTD, run-rate columns before the forecast years

    sh.section("FFO reconciliation (as reported)")
    ffo = [i for i in mi.ffo_items if i.kind == "ffo"]
    core = [i for i in mi.ffo_items if i.kind == "core"]
    for it in ffo:
        sh.line(
            it.label, calc(f"a_{it.key}_q", f"fr_{it.key}_q"), calc(f"a_{it.key}_ytd", f"fr_{it.key}_ytd"), indent=1
        )
    sh.line(
        "FFO",
        calc("a_ffo_q", " + ".join(f"a_{i.key}_q" for i in ffo)),
        calc("a_ffo_ytd", " + ".join(f"a_{i.key}_ytd" for i in ffo)),
        style="subtotal",
    )
    for it in core:
        sh.line(
            it.label, calc(f"a_{it.key}_q", f"fr_{it.key}_q"), calc(f"a_{it.key}_ytd", f"fr_{it.key}_ytd"), indent=1
        )
    sh.line(
        "Core FFO",
        calc("a_core_q", "a_ffo_q + " + " + ".join(f"a_{i.key}_q" for i in core)),
        calc("a_core_ytd", "a_ffo_ytd + " + " + ".join(f"a_{i.key}_ytd" for i in core)),
        style="total",
    )
    sh.line(
        "Core FFO per diluted share",
        calc("a_core_ps_q", "a_core_q / sh_wtd_q", "ps"),
        calc("a_core_ps_ytd", "a_core_ytd / sh_wtd_ytd", "ps"),
        style="bold",
    )
    sh.line(
        "Check: FFO and Core FFO vs reported",
        calc("ck_ffo_q", "a_ffo_q - ffo_rep_q + a_core_q - core_rep_q"),
        calc("ck_ffo_ytd", "a_ffo_ytd - ffo_rep_ytd + a_core_ytd - core_rep_ytd"),
        style="note",
    )
    sh.blank()

    sh.section("Forecast drivers")
    sh.line(
        "Operating margin, trailing four quarters",
        *pad,
        calc("a_margin", "(noi_q + noi_q1 + noi_q2 + noi_q3) / (rev_q + rev_q1 + rev_q2 + rev_q3)", "pct1"),
    )
    rev, exp, noig = [], [], []
    for y in years[1:]:
        rev.append(calc(f"a_rev_g_{y}", f"as_rev_g_{y}", "pct1"))
        exp.append(calc(f"a_exp_g_{y}", f"as_exp_g_{y}", "pct1"))
        noig.append(calc(f"a_noi_g_{y}", f"(a_rev_g_{y} - a_exp_g_{y} * (1 - a_margin)) / a_margin", "pct1"))
    sh.line("Revenue growth", *pad, None, *rev, indent=1)
    sh.line("Expense growth", *pad, None, *exp, indent=1)
    sh.line("NOI growth (operating leverage on margin)", *pad, None, *noig, indent=1)
    acq = [calc(f"a_acq_{y}", f"as_acq_amt_{y}") for y in years[1:]]
    sh.line("Net acquisitions", *pad, None, *acq, indent=1)
    sh.blank()

    sh.section("Core FFO build")
    rows: dict[str, list[Cell | None]] = {k: [] for k in ("noi", "fees", "int", "oth", "jv", "ga", "corp", "nci")}
    gd = {"noi": "gd_noi", "fees": "gd_fees", "int": "gd_int", "oth": "gd_other", "jv": "gd_jvffo", "ga": "gd_ga",
          "corp": "gd_corp", "nci": "gd_nci"}  # fmt: skip
    pre_names = ["noi", "fees", "int", "oth", "jv", "ga", "corp"]
    for i, y in enumerate(years):
        if i == 0:
            for k, src in gd.items():
                rows[k].append(calc(f"a_{k}_{y}", src))
            continue
        p = years[i - 1]
        acq_noi = f"a_acq_{y} * as_acq_yield * as_acq_timing"
        if i >= 2:
            acq_noi += f" + a_acq_{p} * as_acq_yield * (1 - as_acq_timing)"
        rows["noi"].append(calc(f"a_noi_{y}", f"a_noi_{p} * (1 + a_noi_g_{y}) + {acq_noi}"))
        rows["fees"].append(calc(f"a_fees_{y}", f"a_fees_{p} * (1 + as_fee_g)"))
        refi = (
            f"d_mat_{p} * (as_refi_rate - d_matrate_{p}) * (1 - as_refi_timing)"
            f" + d_mat_{y} * (as_refi_rate - d_matrate_{y}) * as_refi_timing"
            f" + a_acq_{y} * as_acq_debt_pct * as_refi_rate * as_acq_timing"
        )
        if i >= 2:
            refi += f" + a_acq_{p} * as_acq_debt_pct * as_refi_rate * (1 - as_acq_timing)"
        rows["int"].append(calc(f"a_int_{y}", f"a_int_{p} - ({refi}) - as_int_adj_{y}"))
        rows["oth"].append(calc(f"a_oth_{y}", f"a_oth_{p} * (1 + as_other_g)"))
        rows["jv"].append(calc(f"a_jv_{y}", f"a_jv_{p} * (1 + a_noi_g_{y}) + as_jvffo_adj_{y}"))
        rows["ga"].append(calc(f"a_ga_{y}", f"a_ga_{p} * (1 + as_ga_g)"))
        rows["corp"].append(calc(f"a_corp_{y}", f"a_corp_{p} * (1 + as_ga_g)"))
        pre = " + ".join(f"a_{k}_{y}" for k in pre_names)
        rows["nci"].append(calc(f"a_nci_{y}", f"a_ncishare * ({pre})"))
    labels = {
        "noi": "Total NOI",
        "fees": "Management and other fees",
        "int": "Interest expense, net",
        "oth": "Other income",
        "jv": "FFO from co-investments",
        "ga": "General and administrative",
        "corp": "Corporate-level property management",
        "nci": "Noncontrolling interest",
    }
    for k, label in labels.items():
        sh.line(label, *pad, *rows[k], indent=1)
    sh.line(
        "Core FFO",
        *pad,
        *[calc(f"a_cffo_{y}", " + ".join(f"a_{k}_{y}" for k in labels)) for y in years],
        style="total",
    )
    pre0 = " + ".join(f"a_{k}_{y0}" for k in pre_names)
    sh.line(
        "NCI share of pre-NCI Core FFO (held at guidance)",
        *pad,
        calc("a_ncishare", f"a_nci_{y0} / ({pre0})", "pct1"),
        indent=1,
    )
    sh.blank()

    sh.section("AFFO")
    sh.line(
        "Core FFO",
        None,
        None,
        calc("a_core_rr", "a_core_q * 4"),
        *[calc(f"a_core_{y}", f"a_cffo_{y}") for y in years],
    )
    capu = [calc(f"a_capu_{y0}", "capex_unit_t4q * (1 + as_capex_g)", "dollar")]  # T4Q ends mid-year
    capu += [calc(f"a_capu_{y}", f"a_capu_{years[i]} * (1 + as_capex_g)", "dollar") for i, y in enumerate(years[1:])]
    sh.line(
        "Recurring capex per unit ($)",
        calc("a_capu_q", "capex_q * 1000 / avg_units_t4q", "dollar"),
        None,
        calc("a_capu_rr", "capex_unit_t4q", "dollar"),
        *capu,
        indent=1,
    )
    sh.line(
        "Less recurring capex, consolidated",
        None,
        None,
        calc("a_capex_rr", "-capex_t4q"),
        *[calc(f"a_capex_{y}", f"-avg_units_t4q * a_capu_{y} / 1000") for y in years],
        indent=1,
    )
    sh.line(
        "Less recurring capex, co-investments at share",
        None,
        None,
        calc("a_jvcapex_rr", "-as_jv_units_share * a_capu_rr / 1000"),
        *[calc(f"a_jvcapex_{y}", f"-as_jv_units_share * a_capu_{y} / 1000") for y in years],
        indent=1,
    )
    ann = "* 12 / ytd_months"
    sc = [calc(f"a_sc_{y0}", f"stock_comp_ytd {ann}")]
    sc += [calc(f"a_sc_{y}", f"a_sc_{years[i]} * (1 + as_ga_g)") for i, y in enumerate(years[1:])]
    sh.line(
        "Plus stock-based compensation",
        None,
        calc("a_sc_ytd", "stock_comp_ytd"),
        calc("a_sc_rr", f"stock_comp_ytd {ann}"),
        *sc,
        indent=1,
    )
    sh.line(
        "Plus amortization of debt costs and discounts",
        None,
        calc("a_fa_ytd", "fin_amort_ytd"),
        calc("a_fa_rr", f"fin_amort_ytd {ann}"),
        *[calc(f"a_fa_{y}", f"fin_amort_ytd {ann}") for y in years],
        indent=1,
    )
    sh.line(
        "Straight-line rent adjustment (cash less GAAP)",
        None,
        calc("a_sl_ytd", "sl_rent_ytd"),
        calc("a_sl_rr", f"sl_rent_ytd {ann}"),
        *[calc(f"a_sl_{y}", f"sl_rent_ytd {ann}") for y in years],
        indent=1,
    )
    cols = ["rr", *[str(y) for y in years]]
    sh.line(
        "AFFO",
        None,
        None,
        *[
            calc(f"a_affo_{c}", f"a_core_{c} + a_capex_{c} + a_jvcapex_{c} + a_sc_{c} + a_fa_{c} + a_sl_{c}")
            for c in cols
        ],
        style="grand",
    )
    sh.blank()
    sh.section("Per share")
    shares = [calc(f"a_sh_{y0}", "sh_guide")]
    shares += [calc(f"a_sh_{y}", f"a_sh_{years[i]} * (1 + as_share_g)") for i, y in enumerate(years[1:])]
    sh.line("Diluted shares and units (000)", None, None, calc("a_sh_rr", "sh_wtd_q"), *shares, indent=1)
    sh.line(
        "Core FFO per share",
        None,
        None,
        *[calc(f"a_coreps_{c}", f"a_core_{c} / a_sh_{c}", "ps") for c in cols],
        style="bold",
    )
    sh.line(
        "AFFO per share",
        None,
        None,
        *[calc(f"a_affops_{c}", f"a_affo_{c} / a_sh_{c}", "ps") for c in cols],
        style="grand",
    )
    growth = [None] + [
        calc(f"a_affog_{y}", f"a_affops_{y} / a_affops_{years[i]} - 1", "pct1") for i, y in enumerate(years[1:])
    ]
    sh.line("AFFO per share growth", None, None, None, *growth, indent=1)
    div = [calc(f"a_div_{y0}", "div_q * 4", "ps")]
    div += [calc(f"a_div_{y}", f"a_div_{years[i]} * (1 + as_div_g)", "ps") for i, y in enumerate(years[1:])]
    sh.line(
        "Dividend per share", calc("a_div_q", "div_q", "ps"), None, calc("a_div_rr", "div_q * 4", "ps"), *div, indent=1
    )
    sh.line(
        "Payout ratio (dividend / AFFO)",
        None,
        None,
        *[calc(f"a_payout_{c}", f"a_div_{c} / a_affops_{c}", "pct1") for c in cols],
        indent=1,
    )
    sh.line(
        "Dividend coverage (AFFO / dividend)",
        None,
        None,
        *[calc(f"a_cover_{c}", f"a_affops_{c} / a_div_{c}", "mult") for c in cols],
        indent=1,
    )
    sh.line(
        f"Check: {y0}E Core FFO per share less guidance midpoint",
        *pad,
        calc("ck_guide", f"a_coreps_{y0} - gd_core_ps", "ps"),
        style="note",
    )
    sh.blank()
    sh.note(f"{y0}E is built from company guidance midpoints; later years roll forward with the drivers above.")
    sh.note(
        "Run-rate column: latest quarter Core FFO x 4, trailing-four-quarter capex, "
        "year-to-date non-cash items annualized."
    )


# ---------------------------------------------------------------------------- sensitivity
def _sens_sheet(sh: Sheet) -> None:
    def grid(title: str, fn: str, fmt: str, prefix: str) -> None:
        sh.section(title)
        sh.line(
            "Cap rate shift (down) vs NOI growth shift (across)",
            text("Cap shift", bold=True),
            *[calc(f"{prefix}_gh_{j}", f"as_gs_{j}", "pct1") for j in range(1, SENS_STEPS + 1)],
            style="colhead",
        )
        for i in range(1, SENS_STEPS + 1):
            cells: list[Cell | None] = [calc(f"{prefix}_ch_{i}", f"as_cs_{i}", "pct")]
            for j in range(1, SENS_STEPS + 1):
                cells.append(calc(f"{prefix}_{i}_{j}", fn.format(i=i, j=j, p=prefix), fmt))
            sh.line("", *cells)
        sh.blank()

    nav_expr = (
        "(SUMPRODUCT(mkt_noi * (1 + mkt_g + {p}_gh_{j}) / (mkt_cap + {p}_ch_{i}))"
        " + SUMPRODUCT(mkt_jvnoi * (1 + mkt_g + {p}_gh_{j}) / (mkt_jvcap + {p}_ch_{i}))"
        " + nav_other_net) / nav_shares"
    )
    grid("NAV per share", nav_expr, "ps", "sn")
    grid("Premium / (discount) to NAV at the current share price", "nav_price / sn_{i}_{j} - 1", "pct1", "sp")
    sh.note("Shifts are added to every market's cap rate and forward NOI growth, consolidated and co-investment.")
    sh.note("The centre cell (no shift) equals NAV per share on the NAV sheet.")


# ---------------------------------------------------------------------------- summary
def _summary_sheet(sh: Sheet, mi: ModelInputs) -> None:
    years = mi.forecast_years
    sh.section(f"Valuation ({mi.period}, price {mi.price_date})")
    sh.line("Share price", calc("s_price", "nav_price", "ps"))
    sh.line("NAV per share", calc("s_nav", "nav_ps", "ps"), style="grand")
    sh.line("Premium / (discount) to NAV", calc("s_prem", "nav_prem", "pct1"), style="bold")
    sh.line("Implied nominal cap rate at the share price", calc("s_impcap", "nav_imp_cap", "pct"), style="bold")
    sh.line("NAV nominal cap rate (consolidated)", calc("s_navcap", "nav_nom_cap", "pct"), indent=1)
    sh.line("Implied value per unit", calc("s_impunit", "nav_imp_unit", "dollar"), indent=1)
    sh.line("NAV value per unit", calc("s_navunit", "nav_unit_val", "dollar"), indent=1)
    sh.line("Debt / gross asset value", calc("s_lev", "nav_lev", "pct1"), indent=1)
    sh.blank()
    sh.section("Earnings")
    cols = ["rr", *[str(y) for y in years]]
    sh.colhead("", [f"{mi.quarter_label} Ann.", *[f"{y}E" for y in years]])
    sh.line("Core FFO per share", *[calc(f"s_core_{c}", f"a_coreps_{c}", "ps") for c in cols])
    sh.line("AFFO per share", *[calc(f"s_affo_{c}", f"a_affops_{c}", "ps") for c in cols], style="grand")
    sh.line("Dividend per share", *[calc(f"s_div_{c}", f"a_div_{c}", "ps") for c in cols], indent=1)
    sh.line("Payout ratio (dividend / AFFO)", *[calc(f"s_pay_{c}", f"a_payout_{c}", "pct1") for c in cols], indent=1)
    sh.line("Price / AFFO", *[calc(f"s_pa_{c}", f"nav_price / a_affops_{c}", "mult") for c in cols], indent=1)
    sh.blank()
    sh.section("Key assumptions (Assumptions sheet)")
    sh.line("Weighted cap rate, consolidated", calc("s_a_cap", "mt_cap", "pct"), indent=1)
    sh.line("Weighted forward NOI growth", calc("s_a_g", "mt_g", "pct1"), indent=1)
    sh.line("Market rate, unsecured debt", calc("s_a_ru", "as_rate_unsec", "pct"), indent=1)
    sh.line("Market rate, secured and JV debt", calc("s_a_rs", "as_rate_sec", "pct"), indent=1)
    for y in years[1:]:
        sh.line(f"{y}E same-property NOI growth", calc(f"s_a_noig_{y}", f"a_noi_g_{y}", "pct1"), indent=1)
    sh.line("Refinancing rate", calc("s_a_refi", "as_refi_rate", "pct"), indent=1)
