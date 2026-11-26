"""NAV and AFFO models: formula engine, house rules, Essex numbers, Excel output."""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

import openpyxl
import pytest

from reitstreet.cli import main as cli_main
from reitstreet.models import INPUTS_DIR, SECTORS, build_model, load_inputs, write_xlsx
from reitstreet.models.formula import Formula, FormulaError
from reitstreet.models.workbook import Book, Cell, calc

ESS = INPUTS_DIR / "ess_2026q3.toml"


# ------------------------------------------------------------------ formula engine
def _eval(expr: str, env: dict[str, float], groups: dict[str, list[float]] | None = None) -> float:
    groups = groups or {}
    return Formula.parse(expr).evaluate(lambda k: env[k], lambda g: groups.get(g))


def _render(expr: str) -> str:
    return Formula.parse(expr).render(lambda k: k.upper())


@pytest.mark.parametrize(
    ("expr", "excel"),
    [
        ("a + b * c", "=A+B*C"),
        ("(a + b) * c", "=(A+B)*C"),
        ("a - (b - c)", "=A-(B-C)"),
        ("a / (b * c)", "=A/(B*C)"),
        ("(1 + r) ** -n", "=(1+R)^(-N)"),
        ("-a ** 2", "=-(A^2)"),
        ("a ** b ** c", "=A^(B^C)"),
        ("IF(x == 1, a, 0)", "=IF(X=1,A,0)"),
        ("SUMPRODUCT(g * (1 + h) / k)", "=SUMPRODUCT(G*(1+H)/K)"),
        ("-(a + b)", "=-(A+B)"),
    ],
)
def test_render(expr: str, excel: str) -> None:
    assert _render(expr) == excel


def test_render_matches_python_precedence() -> None:
    env = {"a": 2.0, "b": 3.0, "c": 4.0, "r": 0.05, "n": 6.9}
    assert _eval("-a ** 2", env) == -4.0
    assert _eval("a ** b ** c", env) == 2.0 ** (3.0**4.0)
    assert math.isclose(_eval("(1 + r) ** -n", env), 1.05**-6.9)
    assert _eval("a - (b - c)", env) == 3.0


def test_vectors_only_inside_sum() -> None:
    groups = {"g": [1.0, 2.0, 3.0], "h": [0.5, 0.5, 0.5]}
    assert _eval("SUMPRODUCT(g * (1 + h))", {}, groups) == 9.0
    assert _eval("SUMPRODUCT(g, h)", {}, groups) == 3.0
    assert _eval("SUM(g) + 1", {}, groups) == 7.0
    with pytest.raises(FormulaError):
        _eval("g + 1", {}, groups)


def test_rejects_unsafe_syntax() -> None:
    for bad in ("__import__('os')", "a.b", "[a]", "a if b else c", "EVAL(a)", "'x'", "a < b < c", "True"):
        with pytest.raises(FormulaError):
            Formula.parse(bad)


def test_names_exclude_functions() -> None:
    assert Formula.parse("SUM(g) + IF(a == 1, b, c)").names() == {"g", "a", "b", "c"}


# ------------------------------------------------------------------ house rules
def _tiny(expr: str) -> Book:
    b = Book("TST", "Test")
    inp = b.sheet("Inputs", "Inputs")
    inp.line("x", Cell("input", key="x", value=2.0, source="s", page="p1"))
    calc_sheet = b.sheet("NAV", "NAV")
    calc_sheet.line("y", calc("y", expr))
    return b


def test_validate_flags_hardcodes_and_missing_citations() -> None:
    assert _tiny("x * 4").validate({"s"}) == []
    assert any("hard-codes" in p for p in _tiny("x * 1.05").validate({"s"}))
    assert any("unknown key" in p for p in _tiny("x * z").validate({"s"}))
    b = _tiny("x")
    b.sheets[0].rows[0].cells[0].page = ""  # type: ignore[union-attr]
    assert any("no source and page" in p for p in b.validate({"s"}))


def test_validate_flags_cycles() -> None:
    b = Book("TST", "Test")
    s = b.sheet("NAV", "NAV")
    s.line("a", calc("a", "b + 1"))
    s.line("b", calc("b", "a + 1"))
    assert any("circular" in p for p in b.validate())


# ------------------------------------------------------------------ Essex model
@pytest.fixture(scope="module")
def ess() -> tuple[Book, dict[str, float]]:
    mi = load_inputs(ESS)
    book = build_model(mi)
    assert book.validate(set(mi.sources)) == []
    return book, book.evaluate()


def test_sector_registry() -> None:
    assert "apartment" in SECTORS


def test_every_input_cited_and_every_assumption_explained() -> None:
    mi = load_inputs(ESS)
    for it in mi.inputs:
        assert it.source in mi.sources and it.page, it.key
    for a in mi.assumptions:
        assert a.rationale, a.key


def test_essex_reconciles_to_reported(ess: tuple[Book, dict[str, float]]) -> None:
    _, v = ess
    assert v["ck_ffo_q"] == 0 and v["ck_ffo_ytd"] == 0  # FFO and Core FFO rebuild to S-3
    assert abs(v["ck_guide"]) < 0.01  # 2026E Core FFO/share = guidance midpoint $16.16
    assert abs(v["ck_pct"]) < 1e-9 and abs(v["ck_jvpct"]) < 1e-9
    assert math.isclose(v["nav_adj_noi"], 1_356_956)  # Adjusted Total NOI, S-17.4
    assert math.isclose(v["a_core_ps_q"], 4.00, abs_tol=0.005)
    assert math.isclose(v["a_core_ps_ytd"], 12.15, abs_tol=0.005)


def test_essex_debt_mark_matches_10q_fair_value(ess: tuple[Book, dict[str, float]]) -> None:
    _, v = ess
    fixed = v["d_fv_ub"] + v["d_fv_tlf"] + v["d_fv_sf"]
    assert 5_600_000 < fixed < 5_800_000  # 10-Q: fixed and swapped debt fair value about $5.7B


def test_essex_nav_identities(ess: tuple[Book, dict[str, float]]) -> None:
    _, v = ess
    assert math.isclose(v["nav_ps"], v["nav_nav"] / v["nav_shares"])
    assert math.isclose(v["sn_3_3"], v["nav_ps"])  # centre of the grid is the base case
    assert v["sn_1_1"] > v["sn_3_3"] > v["sn_5_5"]  # lower cap, higher growth = higher NAV
    implied = (v["nav_fwd_noi"] + v["mt_jvfwd"]) / v["nav_imp_re"]
    assert math.isclose(v["nav_imp_cap"], implied)
    assert 0.04 < v["mt_cap"] < 0.06 and 0.04 < v["nav_imp_cap"] < 0.08


def test_essex_affo_bridge(ess: tuple[Book, dict[str, float]]) -> None:
    _, v = ess
    for y in ("2026", "2027", "2028"):
        parts = sum(v[f"a_{p}_{y}"] for p in ("core", "capex", "jvcapex", "sc", "fa", "sl"))
        assert math.isclose(v[f"a_affo_{y}"], parts)
        assert v[f"a_affops_{y}"] < v[f"a_coreps_{y}"]


def test_missing_key_is_a_clear_error(tmp_path: Path) -> None:
    text = ESS.read_text(encoding="utf-8").replace('key = "gd_core_ps"', 'key = "gd_core_ps_x"')
    p = tmp_path / "bad.toml"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="gd_core_ps"):
        build_model(load_inputs(p))


# ------------------------------------------------------------------ Excel output
def test_xlsx_house_style(tmp_path: Path, ess: tuple[Book, dict[str, float]]) -> None:
    book, _ = ess
    path = write_xlsx(book, tmp_path / "ess.xlsx")
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames[:3] == ["Summary", "NAV", "Markets"]
    idx = book.index()
    for key, (addr, cell) in idx.items():
        xc = wb[addr.sheet][addr.a1]
        assert xc.font.name == "Arial" and xc.font.sz == 8, key
        if cell.kind in ("input", "assumption"):
            assert xc.font.color.rgb == "FF0000FF", key
            assert not str(xc.value).startswith("=")
        elif cell.kind == "calc":
            assert str(xc.value).startswith("="), key
            assert xc.font.color.rgb in ("FF000000", "FF008000"), key
    for ws in wb.worksheets:
        assert ws.sheet_view.showGridLines is False
        for row in ws.iter_rows():
            for c in row:
                if c.value is not None:
                    assert c.font.sz is None or c.font.sz <= 8, (ws.title, c.coordinate)


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_xlsx_formulas_match_python(tmp_path: Path, ess: tuple[Book, dict[str, float]]) -> None:
    """Recalculate the workbook in LibreOffice and compare every formula cell with the Python evaluation."""
    book, v = ess
    src = write_xlsx(book, tmp_path / "ess.xlsx")
    out = tmp_path / "lo"
    soffice = shutil.which("soffice")
    assert soffice is not None
    subprocess.run(  # noqa: S603  (fixed argument list, no user input)
        [soffice, "--headless", "--calc", "--convert-to", "xlsx", "--outdir", str(out), str(src)],
        check=True,
        capture_output=True,
        timeout=180,
    )
    wb = openpyxl.load_workbook(out / "ess.xlsx", data_only=True)
    for key, (addr, cell) in book.index().items():
        if cell.kind != "calc":
            continue
        got = wb[addr.sheet][addr.a1].value
        assert isinstance(got, (int, float)), (key, got)
        assert math.isclose(got, v[key], rel_tol=1e-9, abs_tol=1e-9), (key, got, v[key])


def test_cli_build(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "m.xlsx"
    assert cli_main(["model", "build", str(ESS), "--out", str(out)]) == 0
    assert out.exists()
    assert "NAV/share" in capsys.readouterr().out
