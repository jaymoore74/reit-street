"""Rules for SQL a model writes.

A model answering questions may query the warehouse, but only to read the client views.
Three layers keep that true, each enough on its own for its own failure:

1. This check: one SELECT (or WITH ... SELECT) statement, no write or admin keywords, no
   file or lock functions, and every table it names is on the allow list.
2. The session: READ ONLY transaction and a statement time limit (ask.py).
3. The login: `reit_ro`, a MySQL user with SELECT and SHOW VIEW only (scripts/mac/setup.sh), so
   even a query that got past the first two layers cannot change anything.
"""

from __future__ import annotations

import re

# Client contract views (dl_*) plus small reference tables a model needs to resolve names.
ALLOWED_PREFIXES = ("dl_",)
ALLOWED_TABLES = {"ref_company", "ref_metric", "ref_sector", "ref_company_sector", "ref_property_type"}
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|replace|drop|alter|create|truncate|rename|grant|revoke|load|outfile|dumpfile|"
    r"into|call|handler|lock|unlock|set|do|prepare|execute|deallocate|kill|shutdown|flush|reset|purge|"
    r"install|uninstall|import|sleep|benchmark|get_lock|release_lock|load_file|sys_exec|information_schema|"
    r"performance_schema|mysql|sys)\b",
    re.I,
)
_COMMENT = re.compile(r"(--[^\n]*|#[^\n]*|/\*.*?\*/)", re.S)
_STRING = re.compile(r"'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.)*\"")
_FROM_FUNCS = re.compile(r"\b(extract|trim|substring|substr|position|overlay)\s*\([^()]*\)", re.I)
_FROM_LIST = re.compile(
    r"\bfrom\s+(.+?)(?=\bwhere\b|\bgroup\b|\border\b|\blimit\b|\bhaving\b|\bwindow\b|\bunion\b|"
    r"\b(?:inner|left|right|cross|natural|straight_join)\b|\bjoin\b|\)|$)",
    re.I | re.S,
)
_JOIN_REF = re.compile(r"\bjoin\s+([`\w.(]+)", re.I)
_CTE = re.compile(r"(?:\bwith\b|,)\s*(?:recursive\s+)?`?(\w+)`?\s+as\s*\(", re.I)


class UnsafeSql(ValueError):
    """The query breaks a read-only rule; the message says which."""


def check(sql: str, *, max_rows: int = 200) -> str:
    """Return a safe, row-limited version of `sql` or raise UnsafeSql."""
    q = _COMMENT.sub(" ", sql).strip().rstrip(";").strip()
    if not q:
        raise UnsafeSql("empty query")
    bare = _STRING.sub("''", q)  # keywords inside string literals are data, not SQL
    if ";" in bare:
        raise UnsafeSql("one statement only")
    if not re.match(r"^\s*(select|with)\b", bare, re.I):
        raise UnsafeSql("only SELECT or WITH ... SELECT")
    m = FORBIDDEN.search(bare)
    if m:
        raise UnsafeSql(f"keyword not allowed: {m.group(1).upper()}")
    ctes = {c.lower() for c in _CTE.findall(bare)}
    for ref in table_refs(bare):
        name = ref.replace("`", "")
        if not name or name.startswith("("):
            continue
        if "." in name:
            raise UnsafeSql(f"schema-qualified names are not allowed: {name}")
        low = name.lower()
        if low in ctes or low in ALLOWED_TABLES or low.startswith(ALLOWED_PREFIXES):
            continue
        raise UnsafeSql(f"table not allowed: {name} (use the dl_* views)")
    if not re.search(r"\blimit\s+\d+(\s*,\s*\d+)?\s*$", bare, re.I):
        q = f"SELECT * FROM ({q}) AS q LIMIT {int(max_rows)}"
    return q


def table_refs(sql: str) -> list[str]:
    """Names after FROM (including comma lists) and JOIN. Subqueries come back as '('."""
    s = _FROM_FUNCS.sub("f()", sql)
    out: list[str] = []
    for seg in _FROM_LIST.findall(s):
        for part in seg.split(","):
            tok = part.strip().split()
            if tok:
                out.append(tok[0])
    out += _JOIN_REF.findall(s)
    return out
