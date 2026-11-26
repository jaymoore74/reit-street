"""Property master: pure logic (normalization, matching, ownership spells, list
parsing, geocoder responses, the source map). No database."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from decimal import Decimal

import pytest

from reitstreet.properties import geocode, listparse, match, ownership, sources
from reitstreet.properties import normalize as nz


# ---------------------------------------------------------------- normalize
def test_state_and_zip() -> None:
    assert nz.state_code("Texas") == "TX"
    assert nz.state_code("tx") == "TX"
    assert nz.state_code("New York") == "NY"
    assert nz.state_code("Ontario") == "ON"
    assert nz.state_code("") is None
    assert nz.zip5("75201-1234") == "75201"
    assert nz.zip5("752011234") == "75201"
    assert nz.zip5("ABC") is None


def test_normalize_street() -> None:
    assert nz.normalize_street("1200 North Main Street, Suite 300") == "1200 N MAIN ST"
    assert nz.normalize_street("100-110 Main St.") == "100 MAIN ST"
    assert nz.normalize_street("1200 N. Main St") == nz.normalize_street("1200 north main street")
    assert nz.normalize_street("  ") is None


def test_address_key_needs_a_number_and_a_place() -> None:
    a = nz.address_key("1200 North Main Street", "Dallas", "Texas", "75201")
    b = nz.address_key("1200 N Main St, Suite 4", "DALLAS", "TX", "75201-0001")
    assert a and a == b
    assert nz.address_key("Main Street", "Dallas", "TX", "75201") is None  # no number
    assert nz.address_key("1200 Main St", None, None, None) is None  # no place
    assert nz.address_key("1200 Main St", "Dallas", "TX", None) != a  # city key differs from ZIP key


def test_normalize_name_and_location() -> None:
    assert nz.normalize_name("The Shops at Oak Park") == "SHOPS OAK PARK"
    assert nz.name_tokens("Oak & Elm") == frozenset({"OAK", "ELM"})
    assert nz.parse_location("Dallas, TX") == nz.Location("Dallas", "TX", "US")
    assert nz.parse_location("Dallas, Texas 75201") == nz.Location("Dallas", "TX", "US")
    assert nz.parse_location("Texas") == nz.Location(None, "TX", "US")
    assert nz.parse_location("Toronto, Ontario") == nz.Location("Toronto", "ON", "CA")
    assert nz.parse_location("Various") == nz.Location(None, None)


def test_years_and_aggregates() -> None:
    assert nz.first_year("1998/2004") == 1998
    assert nz.last_year("1998/2004") == 2004
    assert nz.first_year("Various") is None
    assert nz.first_year(1700) is None
    assert nz.aggregate_signal("Other properties (12 properties)") == (True, 12)
    assert nz.aggregate_signal("Various") == (True, None)
    assert nz.aggregate_signal("Oak Park Center") == (False, None)


# ---------------------------------------------------------------- matching
def test_name_similarity() -> None:
    assert match.name_similarity("PLAZA MAIN STREET", "MAIN STREET PLAZA") == pytest.approx(1.0)
    assert match.name_similarity("1000 MAIN", "2000 MAIN") == 0.0
    assert 0.75 <= match.name_similarity("PARK PLACE", "PARK PLAZA") < 0.9
    assert match.name_similarity(None, "X") == 0.0


def _rec(rid: int, snap: str, **kw: object) -> match.Rec:
    base: dict[str, object] = {"company_id": 1, "source_kind": "sec_schedule_iii", "city": "Dallas", "state": "TX"}
    base.update(kw)
    return match.Rec(record_id=rid, snapshot=snap, **base)  # type: ignore[arg-type]


def test_place_matches_across_snapshots_not_within() -> None:
    ix = match.Index()
    d = match.place(
        ix,
        [
            _rec(1, "a@2023", norm_name="OAK PARK CENTER"),
            _rec(2, "a@2023", norm_name="OAK PARK CENTER"),  # same snapshot: never merged
            _rec(3, "a@2024", norm_name="OAK PARK CENTER"),
        ],
    )
    assert d[0].method == "new" and d[1].method == "new"
    assert d[0].new_key != d[1].new_key
    # third record has two equal candidates (the two 2023 rows) -> review, not a guess
    assert d[2].review_status == "needs_review" and len(d[2].candidates) == 2


def test_place_rules_in_order() -> None:
    ix = match.Index()
    ix.add_property(
        match.Prop(10, companies={1}, names={"OAK PARK CENTER"}, city_key="DALLAS|TX", latitude=32.78, longitude=-96.80)
    )
    ix.add_alias(10, "source_ref", "OAKPARK", 1, "sec_schedule_iii")
    ix.add_alias(10, "address", "addr-1", None, None)
    ix.props[10].snapshots.add("old@2022")

    ref, addr, name, geo, new, near_miss = match.place(
        ix,
        [
            _rec(1, "s@2023", source_ref="OAKPARK"),
            _rec(2, "w@2023", source_kind="corporate_website", address_key="addr-1"),
            _rec(3, "x@2023", norm_name="OAK PARK CTR"),
            _rec(
                4, "y@2023", company_id=2, city=None, norm_name="OAK PARK CENTER", latitude=32.7801, longitude=-96.8001
            ),
            _rec(5, "z@2023", norm_name="RIVERSIDE APARTMENTS"),
            _rec(6, "q@2023", norm_name="OAK PARK"),
        ],
    )
    assert (ref.property_id, ref.method) == (10, "source_ref")
    assert (addr.property_id, addr.method) == (10, "address")
    assert name.method in ("name_city", "new")
    assert (geo.property_id, geo.method) == (10, "geo")
    assert new.property_id is None and new.method == "new" and new.review_status == "auto"
    assert near_miss.review_status == "needs_review" and near_miss.candidates[0]["property_id"] == 10


def test_place_new_records_in_one_batch_join_each_other() -> None:
    ix = match.Index()
    d = match.place(ix, [_rec(1, "a@2023", source_ref="P1"), _rec(2, "a@2024", source_ref="P1")])
    assert d[0].new_key is not None and d[1].property_id == d[0].new_key


# ---------------------------------------------------------------- ownership
D = date


def test_spells_sale_and_buyback() -> None:
    s = [
        ownership.Sighting(D(2022, 12, 31), "sec_schedule_iii", 1, D(2023, 2, 15)),
        ownership.Sighting(D(2023, 12, 31), "sec_schedule_iii", 2, D(2024, 2, 15)),
        ownership.Sighting(D(2025, 12, 31), "sec_schedule_iii", 3, D(2026, 2, 15), ownership_pct=Decimal("50")),
    ]
    sizes = {("sec_schedule_iii", d): 100 for d in (D(2022, 12, 31), D(2023, 12, 31), D(2024, 12, 31), D(2025, 12, 31))}
    sp = ownership.compute_spells(s, sizes)
    assert len(sp) == 2
    assert (sp[0].acquired_date, sp[0].acquired_basis) == (D(2022, 12, 31), "first_seen")
    assert (sp[0].disposed_date, sp[0].disposed_basis) == (D(2024, 12, 31), "not_listed")
    assert sp[1].disposed_date is None and sp[1].ownership_pct == Decimal("50")
    assert sp[0].row_hash() != sp[1].row_hash()


def test_spells_small_snapshot_is_not_a_sale() -> None:
    s = [
        ownership.Sighting(D(2022, 12, 31), "supplemental", 1, D(2023, 2, 1)),
        ownership.Sighting(D(2023, 6, 30), "supplemental", 2, D(2023, 8, 1)),
    ]
    sizes = {
        ("supplemental", D(2022, 12, 31)): 100,
        ("supplemental", D(2023, 3, 31)): 10,
        ("supplemental", D(2023, 6, 30)): 100,
    }
    sp = ownership.compute_spells(s, sizes)
    assert len(sp) == 1 and sp[0].disposed_date is None


def test_spells_reported_dates_win() -> None:
    s = [
        ownership.Sighting(D(2023, 12, 31), "supplemental", 1, D(2024, 2, 1), acquired_date=D(2019, 5, 1)),
        ownership.Sighting(D(2024, 3, 31), "supplemental", 2, D(2024, 5, 1), disposed_date=D(2024, 3, 15)),
    ]
    sp = ownership.compute_spells(s, {})
    assert len(sp) == 1
    assert (sp[0].acquired_date, sp[0].acquired_basis) == (D(2019, 5, 1), "reported")
    assert (sp[0].disposed_date, sp[0].disposed_basis) == (D(2024, 3, 15), "reported")
    assert ownership.compute_spells([], {}) == []


# ---------------------------------------------------------------- list parsing
CSV = (
    b"Property Name,Address,City,State,Zip,Square Feet,% Leased,Anchor\n"
    b'Oak Park,1 Main St,Dallas,TX,75201,"120,000",0.95,Kroger\n'
    b",,,,,,,\n"
)


def test_csv_guess_and_map() -> None:
    rows = listparse.read_rows(CSV, "csv")
    mapped, fm = listparse.map_rows(rows, None)
    assert fm["name"] == "Property Name" and fm["rentable_sf"] == "Square Feet" and fm["tenant"] == "Anchor"
    assert len(mapped) == 1  # blank row dropped
    m = mapped[0]
    assert m["rentable_sf"] == Decimal("120000") and m["occupancy_pct"] == Decimal("95.00")
    assert m["attributes"] == {"tenant": "Kroger"}


def test_json_with_path_and_pinned_map() -> None:
    body = json.dumps(
        {"data": {"items": [{"title": "Elm Tower", "loc": {"city": "Austin", "st": "TX"}, "sf": 50000}]}}
    ).encode()
    rows = listparse.read_rows(body, "json", "data.items")
    mapped, _ = listparse.map_rows(rows, {"name": "title", "city": "loc.city", "state": "loc.st", "rentable_sf": "sf"})
    assert (
        mapped[0]["name"] == "Elm Tower" and mapped[0]["state"] == "TX" and mapped[0]["rentable_sf"] == Decimal("50000")
    )


def test_html_picks_largest_table() -> None:
    html = (
        b"<table><tr><td>menu</td></tr></table>"
        b"<table><thead><tr><th>Community</th><th>City</th><th>Units</th></tr></thead>"
        b"<tr><td>Lakeside</td><td>Tampa</td><td>312</td></tr><tr><td>Hillcrest</td><td>Orlando</td><td>1,040</td></tr></table>"
    )
    mapped, fm = listparse.map_rows(listparse.read_rows(html, "html_table"), None)
    assert fm == {"name": "Community", "city": "City", "unit_count": "Units"}
    assert [m["unit_count"] for m in mapped] == [Decimal("312"), Decimal("1040")]


def _xlsx(rows: list[list[str]], sheet: str = "Portfolio") -> bytes:
    def col(i: int) -> str:
        return chr(65 + i)

    shared = [v for r in rows for v in r]
    sheet_xml = "".join(
        f'<row r="{ri + 1}">'
        + "".join(f'<c r="{col(ci)}{ri + 1}" t="s"><v>{shared.index(v)}</v></c>' for ci, v in enumerate(r))
        + "</row>"
        for ri, r in enumerate(rows)
    )
    m = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{m}" xmlns:r="{r}"><sheets><sheet name="Cover" sheetId="1" r:id="rId1"/>'
            f'<sheet name="{sheet}" sheetId="2" r:id="rId2"/></sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>',
        )
        z.writestr(
            "xl/sharedStrings.xml", f'<sst xmlns="{m}">' + "".join(f"<si><t>{v}</t></si>" for v in shared) + "</sst>"
        )
        z.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{m}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>cover</t></is></c></row>'
            "</sheetData></worksheet>",
        )
        z.writestr("xl/worksheets/sheet2.xml", f'<worksheet xmlns="{m}"><sheetData>{sheet_xml}</sheetData></worksheet>')
    return buf.getvalue()


def test_xlsx_named_sheet() -> None:
    body = _xlsx([["Facility", "State", "Beds"], ["Sunrise Manor", "OH", "120"]])
    mapped, fm = listparse.map_rows(listparse.read_rows(body, "xlsx", "portfolio"), None)
    assert fm["name"] == "Facility" and mapped[0]["unit_count"] == Decimal("120") and mapped[0]["state"] == "OH"
    assert listparse.xlsx_grid(body) == [["cover"]]  # default: first sheet


def test_to_decimal_and_bad_format() -> None:
    assert listparse.to_decimal("$(1,250.5)") == Decimal("-1250.5")
    assert listparse.to_decimal("n/a") is None
    with pytest.raises(ValueError):
        listparse.read_rows(b"", "pdf")


# ---------------------------------------------------------------- geocoder
def test_parse_census() -> None:
    one = {
        "result": {
            "addressMatches": [
                {
                    "matchedAddress": "1200 N MAIN ST, DALLAS, TX, 75201",
                    "coordinates": {"x": -96.8011234567, "y": 32.7811234567},
                    "tigerLine": {"tigerLineId": "123"},
                    "addressComponents": {"state": "TX"},
                }
            ]
        }
    }
    g = geocode.parse_census(json.dumps(one).encode())
    assert g["status"] == "match" and g["latitude"] == 32.781123 and g["state"] == "TX" and g["provider_ref"] == "123"
    assert geocode.parse_census(b'{"result": {"addressMatches": []}}')["status"] == "no_match"
    two = {"result": {"addressMatches": [one["result"]["addressMatches"][0]] * 2}}
    assert geocode.parse_census(json.dumps(two).encode())["status"] == "tie"
    assert "benchmark=" in geocode.census_url(geocode.one_line("1 Main St", "Dallas", "TX", "75201"))


# ---------------------------------------------------------------- source map
def test_source_map_file_is_valid() -> None:
    rows = sources.read_sources()
    assert len(rows) > 150
    kinds = {r["source_kind"] for r in rows}
    assert {"sec_schedule_iii", "corporate_website"} <= kinds
    keys = [(r["ticker"], r["source_kind"], r["list_url"]) for r in rows]
    assert len(keys) == len(set(keys))


def test_source_map_rejects_bad_values() -> None:
    head = (
        "ticker,source_kind,lists_properties,status,list_url,list_format,records_path,field_map,detail,"
        "approx_property_count,count_unit,checked_date,notes\n"
    )
    with pytest.raises(ValueError, match="source_kind"):
        sources.read_sources(head + "ABC,blog,yes,active,,csv,,,,,,,\n")
    with pytest.raises(json.JSONDecodeError):
        sources.read_sources(head + 'ABC,corporate_website,yes,active,,csv,,"{bad",,,,,\n')
    ok = sources.read_sources(
        head
        + 'abc,corporate_website,yes,active,https://x.test/p.csv,csv,,"{""name"": ""Property""}",'
        + ",12,properties,2026-11-26,\n"
    )
    assert ok[0]["ticker"] == "ABC" and ok[0]["approx"] == 12 and ok[0]["checked"] == date(2026, 11, 26)
