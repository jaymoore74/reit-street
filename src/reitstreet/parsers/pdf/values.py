"""Cell text to numbers, units and scale.

Supplementals state scale once per table ("dollars in thousands, except per share
data"), sometimes per column ("NOI ($000's)", "Square Feet (millions)") and sometimes
only once per page. Per-share, per-square-foot, percent, ratio and count rows are never
scaled. The rules below decide, for each value, what kind of number it is and which
scale applies, and say how sure they are.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

FACTORS = {"thousand": Decimal(1000), "million": Decimal(1_000_000), "billion": Decimal(1_000_000_000)}

_PER_UNIT = re.compile(
    r"per (diluted |basic |common |weighted[- ]average )*(share|unit)|/\s*share|per (occupied |available |leased |rentable )?"  # noqa: E501
    r"(room|key|bed|unit|home|property|resident|site|pod|mw|kw)|\badr\b|revpar|revpor|exppor|average (daily|monthly) (rate|rent)|"  # noqa: E501
    r"(monthly|average) (realized )?rent( per|$)|rent per|dividends? (declared |paid )?per|\beps\b|nav per|book value per",  # noqa: E501
    re.I,
)
_PER_SF = re.compile(
    r"per (rentable |occupied |leased |available )?(sq\.?|square) ?(f(oo|ee)?t|ft)|\bpsf\b|/\s*sf|\$/sf|per sf", re.I
)
_PCT = re.compile(
    r"%|percent|\bpct\b|\brate\b|margin|occupan|leased|yield|growth|change|spread|retention|turnover|\bbps\b|cap rate|"
    r"coupon|return on|\broe\b|\broic\b|payout|share of|renewal|recapture",
    re.I,
)
_RATIO = re.compile(
    r"coverage|\bratio\b|multiple|times|\bx\b|net debt\s*(to|/)\s*\w*ebitda|debt\s*(to|/)\s*ebitda", re.I
)
_COUNT = re.compile(
    r"number of|\bno\. of|#|\bcount\b|^properties$|\bproperties\b(?! (?!held)\w+ (revenue|expense|income|noi))|\bunits\b|"  # noqa: E501
    r"\bhomes\b|\bbeds\b|\bkeys\b|\brooms\b|\bsites\b|\bstores\b|\bbuildings\b|\bcommunities\b|\bfacilities\b|\bhotels\b|"
    r"\bleases\b|\btenants\b|\bemployees\b|\bmw\b|megawatts|\bacres\b|data centers",
    re.I,
)
_SF = re.compile(r"square f(ee|oo)t|sq\.? ?ft|\bgla\b|\bnra\b|\brsf\b|\bsf\b|rentable area|\bmsf\b", re.I)
_DURATION = re.compile(r"\byears?\b|\bmonths\b|\bdays\b|\bterm\b|\bwalt\b|\bwale\b|maturity \(years\)|age", re.I)
_SHARES = re.compile(r"\bshares\b|\bunits outstanding\b|\bop units\b|share count", re.I)
_MONEYWORD = re.compile(
    r"revenue|income|expense|noi|ffo|ebitda|cost|debt|assets|liabilit|equity|cash|capital|rent|"
    r"proceeds|investment|value|price|dividend|distribution|loan|notes|interest|tax|fee|amount|\$",
    re.I,
)

SCALE_PART = re.compile(r"(thousand|million|billion)s?|\b(0{3})'?s\b|\$\s*0{3}s?|\(000'?s?\)|\bmm\b|\$\s*m\b", re.I)


@dataclass(frozen=True)
class Scale:
    money: Decimal | None  # None = not stated
    sf: Decimal | None
    text: str | None


def _factor_of(fragment: str) -> Decimal | None:
    f = fragment.lower()
    if "billion" in f:
        return FACTORS["billion"]
    if "million" in f or re.search(r"\bmm\b|\$\s*m\b", f):
        return FACTORS["million"]
    if "thousand" in f or "000" in f:
        return FACTORS["thousand"]
    return None


def read_scale(text: str | None) -> Scale:
    """'square feet and dollars in thousands, except per share' -> money=1000, sf=1000."""
    if not text:
        return Scale(None, None, None)
    low = text.lower()
    money = sf = None
    for m in re.finditer(
        r"((?:(?:dollars?|\$|usd|amounts?|square feet|sq\.? ?ft|sf|gla|shares?|units)\s*(?:,|and|&)?\s*)*)"
        r"(?:\(?\s*(?:in|\$ in|usd in|shown in)\s+)(thousands|millions|billions)|"
        r"(\$\s*0{3}'?s?|\(000'?s?\)|\$\s*in\s*(?:mm|m)\b|(thousands|millions|billions)\s+of\s+dollars)",
        low,
    ):
        whole = m.group(0)
        subjects = m.group(1) or ""
        f = _factor_of(whole)
        if f is None:
            continue
        mentions_sf = bool(re.search(r"square feet|sq\.? ?ft|\bsf\b|gla", subjects))
        mentions_money = bool(re.search(r"dollars?|\$|usd|amounts?", subjects)) or not subjects.strip()
        if mentions_sf:
            sf = f
        if mentions_money or not mentions_sf:
            money = f
    if money is None and sf is None:
        m2 = re.search(r"(?:^|[(,;:]\s*)(thousands|millions|billions)\b", low)
        if m2:
            money = _factor_of(m2.group(1))
    return Scale(money, sf, text.strip()[:120])


def column_scale(header: str) -> Scale | None:
    """Scale written in a column header: 'NOI ($000s)', 'Square Feet (millions)', '(in thousands)'."""
    m = re.search(r"\(([^)]*)\)", header)
    if not m:
        return None
    inner = m.group(1).lower()
    f = _factor_of(inner) if SCALE_PART.search(inner) else None
    if f is None:
        return None
    if _SF.search(header):
        return Scale(None, f, m.group(0))
    return Scale(f, None, m.group(0))


@dataclass(frozen=True)
class Number:
    value: Decimal | None
    clean: bool  # the text was a plain number (no stray letters)
    pct_sign: bool
    dollar_sign: bool
    multiple: bool  # "5.4x"
    bps: bool


def read_number(text: str) -> Number:
    t = text.strip().replace("−", "-").replace("–", "-").replace("—", "-").replace(" ", " ")
    pct, dollar = "%" in t, "$" in t
    multiple = bool(re.search(r"\d\s*x\)?$", t, re.I))
    bps = bool(re.search(r"\bbps\b|\bbp\b", t, re.I))
    if re.fullmatch(r"[-\s]+|n/?a|nm|n\.m\.|\*+", t, re.I) or not t:
        return Number(None, True, pct, dollar, multiple, bps)
    core = re.sub(r"(?i)\bbps?\b|[$€£%x\s]", "", t)
    neg = (core.startswith("(") and core.endswith(")")) or core.startswith("-") or core.startswith("(-")
    core = core.strip("()").lstrip("-+")
    clean = bool(re.fullmatch(r"\d{1,3}(,\d{3})*(\.\d+)?|\d+(\.\d+)?|\.\d+", core))
    try:
        v = Decimal(core.replace(",", ""))
    except InvalidOperation:
        return Number(None, False, pct, dollar, multiple, bps)
    return Number(-v if neg else v, clean, pct, dollar, multiple, bps)


def unit_kind(label: str, header: str, num: Number, money_context: bool) -> str:
    """money | usd_unit | usd_psf | pct | ratio | count | sf | shares | number."""
    both = f"{label} {header}"
    if num.pct_sign or num.bps:
        return "pct"
    if num.multiple:
        return "ratio"
    if _PER_SF.search(both):
        return "usd_psf"
    if _PER_UNIT.search(both):
        return "usd_unit"
    if _PCT.search(label) and not _MONEYWORD.search(re.sub(r"rate|yield|margin|growth|change", "", label, flags=re.I)):
        return "pct"
    if _RATIO.search(label):
        return "ratio"
    if _SF.search(label) and not num.dollar_sign:
        return "sf"
    if _SHARES.search(label) and not num.dollar_sign:
        return "shares"
    if _COUNT.search(label) and not num.dollar_sign and not _MONEYWORD.search(label):
        return "count"
    if _DURATION.search(label) and not num.dollar_sign and not _MONEYWORD.search(label):
        return "number"
    if num.dollar_sign or money_context:
        return "money"
    # a revenue, income or expense line is money even when the page never says so; with no
    # scale note it then fails the scale check and is quarantined instead of loaded at 1x
    if _MONEYWORD.search(label):
        return "money"
    # header decides when the label is neutral (segment tables: metric names across the top)
    if _SF.search(header):
        return "sf"
    if _PCT.search(header):
        return "pct"
    if _COUNT.search(header):
        return "count"
    return "number"


def apply_scale(v: Decimal, kind: str, scale: Scale) -> tuple[Decimal, bool]:
    """(scaled value, scale_known). Only money and shares follow the money scale; sf its own."""
    if kind in ("money", "shares"):
        return (v * scale.money, True) if scale.money is not None else (v, scale.text is not None)
    if kind == "sf":
        return (v * scale.sf, True) if scale.sf is not None else (v, True)
    return v, True
