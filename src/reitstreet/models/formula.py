"""A small formula language shared by the Python evaluator and the Excel writer.

Every calculated cell in a model is written once, as a Python-syntax expression over cell keys:

    "nav_gross - nav_liab"
    "SUMPRODUCT(mkt_fwd_noi / mkt_cap)"
    "IF(dbt_fixed_ub == 1, dbt_cpn_ub * (1 - (1 + rate) ** -n) / rate + (1 + rate) ** -n, 1)"

The same parsed tree is evaluated in Python (for tests, the CLI summary and the stored values) and rendered
as an Excel formula, so the two cannot drift apart. Names are cell keys or, inside SUM / SUMPRODUCT only, group
keys that stand for a contiguous column of cells. Numeric constants in calc cells are limited to
``ALLOWED_CONSTANTS``: anything else is an assumption and belongs on the Assumptions sheet.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Callable
from dataclasses import dataclass, field

ALLOWED_CONSTANTS = frozenset({0, 1, 4, 12, 1000})
FUNCTIONS = frozenset({"SUM", "SUMPRODUCT", "MIN", "MAX", "AVERAGE", "IF", "ABS"})
_VECTOR_FUNCTIONS = frozenset({"SUM", "SUMPRODUCT"})

_BINOPS: dict[type, tuple[str, int]] = {
    ast.Add: ("+", 1),
    ast.Sub: ("-", 1),
    ast.Mult: ("*", 2),
    ast.Div: ("/", 2),
    ast.Pow: ("^", 4),
}
_CMPOPS: dict[type, str] = {ast.Eq: "=", ast.NotEq: "<>", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}

Value = float | list[float]


class FormulaError(ValueError):
    """A formula that cannot be parsed, evaluated or rendered."""


@dataclass(frozen=True)
class Formula:
    text: str
    tree: ast.expr = field(compare=False, repr=False)

    @classmethod
    def parse(cls, text: str) -> Formula:
        try:
            tree = ast.parse(text, mode="eval").body
        except SyntaxError as e:
            raise FormulaError(f"cannot parse {text!r}: {e.msg}") from None
        _check(tree, text)
        return cls(text, tree)

    def names(self) -> set[str]:
        """Cell and group keys the formula reads (function names excluded)."""
        funcs = {id(n.func) for n in ast.walk(self.tree) if isinstance(n, ast.Call)}
        return {n.id for n in ast.walk(self.tree) if isinstance(n, ast.Name) and id(n) not in funcs}

    def constants(self) -> list[float]:
        return [_const(n) for n in ast.walk(self.tree) if isinstance(n, ast.Constant)]

    def evaluate(self, scalar: Callable[[str], float], group: Callable[[str], list[float] | None]) -> float:
        out = _Evaluator(scalar, group).run(self.tree, vector_ok=False)
        if isinstance(out, list):
            raise FormulaError(f"{self.text!r} evaluates to a range, not a number")
        return out

    def render(self, ref: Callable[[str], str]) -> str:
        """Excel formula text (with the leading '='). ``ref`` maps a cell or group key to an address."""
        return "=" + _render(self.tree, ref)


def _const(node: ast.Constant) -> float:
    v = node.value
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise FormulaError(f"non-numeric constant {v!r}")
    return float(v)


def _check(node: ast.AST, text: str) -> None:
    for n in ast.walk(node):
        ok = (
            isinstance(
                n,
                (ast.BinOp, ast.UnaryOp, ast.Name, ast.Constant, ast.Call, ast.Compare, ast.Load, ast.USub, ast.UAdd),
            )
            or type(n) in _BINOPS
            or type(n) in _CMPOPS
        )
        if not ok:
            raise FormulaError(f"{text!r}: {type(n).__name__} is not allowed in a formula")
        if isinstance(n, ast.Constant) and (isinstance(n.value, bool) or not isinstance(n.value, (int, float))):
            raise FormulaError(f"{text!r}: only numeric constants are allowed")
        if isinstance(n, ast.Call) and (not isinstance(n.func, ast.Name) or n.func.id not in FUNCTIONS or n.keywords):
            raise FormulaError(f"{text!r}: unknown function")
        if isinstance(n, ast.Compare) and len(n.ops) != 1:
            raise FormulaError(f"{text!r}: chained comparisons are not allowed")


class _Evaluator:
    def __init__(self, scalar: Callable[[str], float], group: Callable[[str], list[float] | None]) -> None:
        self.scalar = scalar
        self.group = group

    def run(self, node: ast.expr, vector_ok: bool) -> Value:
        if isinstance(node, ast.Constant):
            return _const(node)
        if isinstance(node, ast.Name):
            values = self.group(node.id)
            if values is not None:
                if not vector_ok:
                    raise FormulaError(f"range {node.id!r} is only allowed inside SUM or SUMPRODUCT")
                return values
            return self.scalar(node.id)
        if isinstance(node, ast.UnaryOp):
            v = self.run(node.operand, vector_ok)
            return _map1(v, lambda x: -x if isinstance(node.op, ast.USub) else x)
        if isinstance(node, ast.BinOp):
            a = self.run(node.left, vector_ok)
            b = self.run(node.right, vector_ok)
            return _map2(a, b, _arith(node.op))
        if isinstance(node, ast.Compare):
            a = self.run(node.left, False)
            b = self.run(node.comparators[0], False)
            assert isinstance(a, float) and isinstance(b, float)
            return 1.0 if _compare(node.ops[0], a, b) else 0.0
        if isinstance(node, ast.Call):
            assert isinstance(node.func, ast.Name)
            name = node.func.id
            vec = name in _VECTOR_FUNCTIONS
            args = [self.run(a, vec) for a in node.args]
            return _call(name, args)
        raise FormulaError(f"cannot evaluate {ast.dump(node)}")


def _arith(op: ast.operator) -> Callable[[float, float], float]:
    def f(a: float, b: float) -> float:
        if isinstance(op, ast.Add):
            return a + b
        if isinstance(op, ast.Sub):
            return a - b
        if isinstance(op, ast.Mult):
            return a * b
        if isinstance(op, ast.Div):
            if b == 0:
                raise FormulaError("division by zero")
            return a / b
        return float(a**b)

    return f


def _compare(op: ast.cmpop, a: float, b: float) -> bool:
    if isinstance(op, ast.Eq):
        return math.isclose(a, b, abs_tol=1e-12)
    if isinstance(op, ast.NotEq):
        return not math.isclose(a, b, abs_tol=1e-12)
    if isinstance(op, ast.Lt):
        return a < b
    if isinstance(op, ast.LtE):
        return a <= b
    if isinstance(op, ast.Gt):
        return a > b
    return a >= b


def _map1(v: Value, f: Callable[[float], float]) -> Value:
    return [f(x) for x in v] if isinstance(v, list) else f(v)


def _map2(a: Value, b: Value, f: Callable[[float, float], float]) -> Value:
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            raise FormulaError("ranges of different lengths")
        return [f(x, y) for x, y in zip(a, b)]
    if isinstance(a, list):
        assert isinstance(b, float)
        return [f(x, b) for x in a]
    if isinstance(b, list):
        return [f(a, y) for y in b]
    return f(a, b)


def _flat(args: list[Value]) -> list[float]:
    out: list[float] = []
    for a in args:
        out.extend(a if isinstance(a, list) else [a])
    return out


def _call(name: str, args: list[Value]) -> float:
    if name in ("SUM", "SUMPRODUCT"):
        if name == "SUMPRODUCT" and len(args) > 1:
            prod: Value = args[0]
            for a in args[1:]:
                prod = _map2(prod, a, lambda x, y: x * y)
            return sum(_flat([prod]))
        return sum(_flat(args))
    vals = _flat(args)
    if name == "MIN":
        return min(vals)
    if name == "MAX":
        return max(vals)
    if name == "AVERAGE":
        return sum(vals) / len(vals)
    if name == "ABS":
        return abs(vals[0])
    if name == "IF":
        if len(vals) != 3:
            raise FormulaError("IF takes three arguments")
        return vals[1] if vals[0] != 0 else vals[2]
    raise FormulaError(f"unknown function {name}")


def _num(v: float | int) -> str:
    f = float(v)
    return str(int(f)) if f.is_integer() else repr(f)


def _atomic(node: ast.expr) -> bool:
    return isinstance(node, (ast.Name, ast.Call)) or (isinstance(node, ast.Constant) and _const(node) >= 0)


def _render(node: ast.expr, ref: Callable[[str], str]) -> str:
    if isinstance(node, ast.Constant):
        return _num(_const(node))
    if isinstance(node, ast.Name):
        return ref(node.id)
    if isinstance(node, ast.UnaryOp):
        inner = _render(node.operand, ref)
        if not _atomic(node.operand):
            inner = f"({inner})"
        return f"-{inner}" if isinstance(node.op, ast.USub) else inner
    if isinstance(node, ast.BinOp):
        sym, prec = _BINOPS[type(node.op)]
        left = _render(node.left, ref)
        right = _render(node.right, ref)
        if _needs_parens(node.left, prec, right_side=False, op=node.op):
            left = f"({left})"
        if _needs_parens(node.right, prec, right_side=True, op=node.op):
            right = f"({right})"
        return f"{left}{sym}{right}"
    if isinstance(node, ast.Compare):
        return f"{_render(node.left, ref)}{_CMPOPS[type(node.ops[0])]}{_render(node.comparators[0], ref)}"
    if isinstance(node, ast.Call):
        assert isinstance(node.func, ast.Name)
        return f"{node.func.id}({','.join(_render(a, ref) for a in node.args)})"
    raise FormulaError(f"cannot render {ast.dump(node)}")


def _needs_parens(child: ast.expr, parent_prec: int, right_side: bool, op: ast.operator) -> bool:
    if isinstance(op, ast.Pow):
        # Excel's ^ is left-associative and binds looser than unary minus: wrap anything not atomic.
        return not _atomic(child)
    if isinstance(child, ast.UnaryOp):
        return True
    if isinstance(child, ast.BinOp):
        child_prec = _BINOPS[type(child.op)][1]
        if child_prec < parent_prec:
            return True
        return child_prec == parent_prec and right_side and isinstance(op, (ast.Sub, ast.Div))
    return False
