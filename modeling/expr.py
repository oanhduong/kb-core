"""Ngôn ngữ công thức của mô hình (Rule.kind="formula") — cú pháp kiểu Excel để người dùng nghiệp vụ đọc/viết được:

    DAYS([First IMV Date], [First Enrollment Date])
    [Total DOS planned (90 days)] - [SDV DOS Backlog+90-day Growth]
    IF([Open Queries] > 50, "High", "Normal")
    ROUND([Backlog] / 7, 1)

[Tên cột] tham chiếu 1 cột đầu ra khác cùng dòng (theo nhãn hoặc tên SQL). AS_OF()/TODAY() = ngày báo cáo của mô hình.
Parse -> AST (dataclass bất biến) -> in lại chuẩn hoá (to_text) hoặc biên dịch SQL (to_sql) chạy được trên cả Postgres
lẫn DuckDB. Chỉ hàm trong whitelist _FUNCS; mọi thứ khác là lỗi cú pháp — không có đường chèn SQL tự do.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Union


class ExprError(ValueError):
    pass


# ===================================================================================== AST
@dataclass(frozen=True)
class Num:
    v: float


@dataclass(frozen=True)
class Str:
    v: str


@dataclass(frozen=True)
class Bool:
    v: bool


@dataclass(frozen=True)
class Col:
    name: str


@dataclass(frozen=True)
class Call:
    fn: str
    args: tuple["Node", ...]


@dataclass(frozen=True)
class Bin:
    op: str
    left: "Node"
    right: "Node"


@dataclass(frozen=True)
class Un:
    op: str
    x: "Node"


Node = Union[Num, Str, Bool, Col, Call, Bin, Un]

# hàm -> (số tham số tối thiểu, tối đa | None = không giới hạn)
_FUNCS: dict[str, tuple[int, int | None]] = {
    "AS_OF": (0, 0), "TODAY": (0, 0), "DAYS": (2, 2), "DATEDIF": (2, 3), "ROUND": (1, 2), "ABS": (1, 1),
    "COALESCE": (1, None), "IFNULL": (2, 2), "IFERROR": (2, 2), "IF": (2, 3), "AND": (1, None), "OR": (1, None),
    "NOT": (1, 1), "MIN": (1, None), "MAX": (1, None), "SUM": (1, None), "YEAR": (1, 1), "MONTH": (1, 1), "DAY": (1, 1),
    "CONCAT": (1, None), "LEN": (1, 1), "UPPER": (1, 1), "LOWER": (1, 1), "TRIM": (1, 1), "ISBLANK": (1, 1),
    "DATE": (3, 3), "INT": (1, 1), "DIGITS": (1, 1), "VALUE": (1, 1),
}
_PREC = {"OR": 1, "AND": 2, "=": 3, "<>": 3, "<": 3, ">": 3, "<=": 3, ">=": 3, "&": 4, "+": 5, "-": 5, "*": 6, "/": 6, "^": 7}

_TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<col>\[[^\]]+\])
  | (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<op><>|<=|>=|[-+*/^&=<>(),%])
  | (?P<name>[A-Za-z_][A-Za-z0-9_.]*)
""", re.VERBOSE)


def _tokens(text: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise ExprError(f"Ký tự không hợp lệ ở vị trí {pos + 1}: {text[pos:pos + 10]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind != "ws":
            out.append((kind, m.group()))
    return out


class _Parser:
    def __init__(self, text: str) -> None:
        self.toks = _tokens(text)
        self.i = 0

    def peek(self) -> tuple[str, str] | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self) -> tuple[str, str]:
        t = self.peek()
        if t is None:
            raise ExprError("Công thức kết thúc đột ngột")
        self.i += 1
        return t

    def expect(self, value: str) -> None:
        t = self.take()
        if t[1] != value:
            raise ExprError(f"Cần '{value}' nhưng gặp '{t[1]}'")

    def parse(self) -> Node:
        node = self.expr(0)
        if self.peek() is not None:
            raise ExprError(f"Thừa ký tự: '{self.peek()[1]}'")
        return node

    def expr(self, min_prec: int) -> Node:
        left = self.unary()
        while True:
            t = self.peek()
            if t is None or t[0] not in ("op", "name"):
                return left
            op = t[1].upper() if t[0] == "name" else t[1]
            prec = _PREC.get(op)
            if prec is None or prec < min_prec:
                return left
            self.take()
            right = self.expr(prec + (0 if op == "^" else 1))
            left = Bin(op, left, right)

    def unary(self) -> Node:
        t = self.peek()
        if t and t[1] in ("-", "+"):
            self.take()
            x = self.unary()
            return x if t[1] == "+" else (Num(-x.v) if isinstance(x, Num) else Un("-", x))
        node = self.atom()
        if self.peek() and self.peek()[1] == "%":
            self.take()
            node = Bin("/", node, Num(100))
        return node

    def atom(self) -> Node:
        kind, val = self.take()
        if kind == "num":
            return Num(float(val) if any(c in val for c in ".eE") else int(val))
        if kind == "str":
            return Str(val[1:-1].replace('""', '"'))
        if kind == "col":
            name = val[1:-1].strip()
            if not name:
                raise ExprError("Tham chiếu cột rỗng []")
            return Col(name)
        if kind == "op" and val == "(":
            node = self.expr(0)
            self.expect(")")
            return node
        if kind == "name":
            up = val.upper()
            if up in ("TRUE", "FALSE") and not (self.peek() and self.peek()[1] == "("):
                return Bool(up == "TRUE")
            if not (self.peek() and self.peek()[1] == "("):
                raise ExprError(f"'{val}' không phải tên hàm hợp lệ (tên cột phải đặt trong [ ])")
            self.take()
            args: list[Node] = []
            if self.peek() and self.peek()[1] == ")":
                self.take()
            else:
                while True:
                    args.append(self.expr(0))
                    t = self.take()
                    if t[1] == ")":
                        break
                    if t[1] != ",":
                        raise ExprError(f"Cần ',' hoặc ')' trong {up}(...)")
            if up not in _FUNCS:
                raise ExprError(f"Hàm {up} chưa được hỗ trợ")
            lo, hi = _FUNCS[up]
            if len(args) < lo or (hi is not None and len(args) > hi):
                raise ExprError(f"Hàm {up} nhận {lo}{'' if hi == lo else f'–{hi}' if hi else '+'} tham số")
            return Call(up, tuple(args))
        raise ExprError(f"Không hiểu '{val}'")


def parse(text: str) -> Node:
    if not text or not text.strip():
        raise ExprError("Công thức rỗng")
    return _Parser(text.lstrip("=").strip()).parse()


def columns_used(node: Node) -> list[str]:
    out: list[str] = []

    def walk(n: Node) -> None:
        if isinstance(n, Col):
            if n.name not in out:
                out.append(n.name)
        elif isinstance(n, Call):
            for a in n.args:
                walk(a)
        elif isinstance(n, Bin):
            walk(n.left)
            walk(n.right)
        elif isinstance(n, Un):
            walk(n.x)

    walk(node)
    return out


def uses_as_of(node: Node) -> bool:
    if isinstance(node, Call):
        return node.fn in ("AS_OF", "TODAY") or any(uses_as_of(a) for a in node.args)
    if isinstance(node, Bin):
        return uses_as_of(node.left) or uses_as_of(node.right)
    if isinstance(node, Un):
        return uses_as_of(node.x)
    return False


# ===================================================================================== in lại
def to_text(node: Node, parent_prec: int = 0) -> str:
    if isinstance(node, Num):
        v = node.v
        return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)
    if isinstance(node, Str):
        return '"' + node.v.replace('"', '""') + '"'
    if isinstance(node, Bool):
        return "TRUE" if node.v else "FALSE"
    if isinstance(node, Col):
        return f"[{node.name}]"
    if isinstance(node, Call):
        return f"{node.fn}({', '.join(to_text(a) for a in node.args)})"
    if isinstance(node, Un):
        return f"-{to_text(node.x, 8)}"
    prec = _PREC[node.op]
    op = f" {node.op} " if node.op not in ("^",) else "^"
    s = f"{to_text(node.left, prec)}{op}{to_text(node.right, prec + 1)}"
    return f"({s})" if prec < parent_prec else s


# ===================================================================================== SQL
class SqlCol:
    """Kết quả phân giải 1 tham chiếu cột: biểu thức SQL + kiểu (integer|double|date|timestamp|text|boolean...)."""

    def __init__(self, sql: str, type_: str) -> None:
        self.sql = sql
        self.type = type_


def _lit(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


def infer_type(node: Node, resolve: Callable[[str], SqlCol]) -> str:
    if isinstance(node, Num):
        return "integer" if isinstance(node.v, int) else "double"
    if isinstance(node, Str):
        return "text"
    if isinstance(node, Bool):
        return "boolean"
    if isinstance(node, Col):
        t = resolve(node.name).type
        return "date" if t == "timestamp" else t
    if isinstance(node, Un):
        return infer_type(node.x, resolve)
    if isinstance(node, Call):
        fn = node.fn
        if fn in ("AS_OF", "TODAY", "DATE"):
            return "date"
        if fn in ("DAYS", "DATEDIF", "YEAR", "MONTH", "DAY", "LEN", "INT"):
            return "integer"
        if fn in ("AND", "OR", "NOT", "ISBLANK"):
            return "boolean"
        if fn in ("CONCAT", "UPPER", "LOWER", "TRIM", "DIGITS"):
            return "text"
        if fn in ("IF",):
            return infer_type(node.args[1], resolve)
        if fn in ("COALESCE", "IFNULL", "IFERROR", "MIN", "MAX", "ABS"):
            return infer_type(node.args[0], resolve)
        return "double"
    op = node.op
    if op in ("=", "<>", "<", ">", "<=", ">=", "AND", "OR"):
        return "boolean"
    if op == "&":
        return "text"
    lt, rt = infer_type(node.left, resolve), infer_type(node.right, resolve)
    if op == "-" and lt == "date" and rt == "date":
        return "integer"
    if op in ("+", "-") and (lt == "date") != (rt == "date"):
        return "date"
    if op == "/" or "double" in (lt, rt):
        return "double"
    return "integer" if lt == rt == "integer" else "double"


def to_sql(node: Node, resolve: Callable[[str], SqlCol], as_of_sql: str) -> str:
    """Biên dịch AST -> SQL (tập con chung Postgres + DuckDB)."""

    def date_of(n: Node) -> str:
        s = rec(n)
        return f"CAST({s} AS DATE)"

    def rec(n: Node) -> str:
        if isinstance(n, Num):
            return repr(n.v) if isinstance(n.v, float) else str(n.v)
        if isinstance(n, Str):
            return _lit(n.v)
        if isinstance(n, Bool):
            return "TRUE" if n.v else "FALSE"
        if isinstance(n, Col):
            c = resolve(n.name)
            return f"CAST({c.sql} AS DATE)" if c.type == "timestamp" else c.sql
        if isinstance(n, Un):
            return f"(-{rec(n.x)})"
        if isinstance(n, Call):
            a = n.args
            fn = n.fn
            if fn in ("AS_OF", "TODAY"):
                return as_of_sql
            if fn == "DAYS":
                return f"({date_of(a[0])} - {date_of(a[1])})"
            if fn == "DATEDIF":  # Excel DATEDIF(start, end, "d")
                return f"({date_of(a[1])} - {date_of(a[0])})"
            if fn == "ROUND":
                digits = int(a[1].v) if len(a) > 1 and isinstance(a[1], Num) else 0
                return f"ROUND(CAST({rec(a[0])} AS DECIMAL(38,10)), {digits})"
            if fn == "INT":
                return f"FLOOR({rec(a[0])})"
            if fn in ("COALESCE", "IFNULL", "IFERROR"):
                return f"COALESCE({', '.join(rec(x) for x in a)})"
            if fn == "IF":
                other = rec(a[2]) if len(a) > 2 else "NULL"
                return f"(CASE WHEN {rec(a[0])} THEN {rec(a[1])} ELSE {other} END)"
            if fn in ("AND", "OR"):
                return "(" + f" {fn} ".join(rec(x) for x in a) + ")"
            if fn == "NOT":
                return f"(NOT {rec(a[0])})"
            if fn == "MIN":
                return rec(a[0]) if len(a) == 1 else f"LEAST({', '.join(rec(x) for x in a)})"
            if fn == "MAX":
                return rec(a[0]) if len(a) == 1 else f"GREATEST({', '.join(rec(x) for x in a)})"
            if fn == "SUM":
                return "(" + " + ".join(f"COALESCE({rec(x)}, 0)" for x in a) + ")"
            if fn in ("YEAR", "MONTH", "DAY"):
                return f"CAST(EXTRACT({fn} FROM {date_of(a[0])}) AS INTEGER)"
            if fn == "ABS":
                return f"ABS({rec(a[0])})"
            if fn == "CONCAT":
                return "(" + " || ".join(f"COALESCE(CAST({rec(x)} AS VARCHAR), '')" for x in a) + ")"
            if fn == "LEN":
                return f"LENGTH(CAST({rec(a[0])} AS VARCHAR))"
            if fn in ("UPPER", "LOWER", "TRIM"):
                return f"{fn}(CAST({rec(a[0])} AS VARCHAR))"
            if fn == "ISBLANK":
                return f"({rec(a[0])} IS NULL)"
            if fn == "DIGITS":
                return f"NULLIF(REGEXP_REPLACE(CAST({rec(a[0])} AS VARCHAR), '[^0-9]', '', 'g'), '')"
            if fn == "VALUE":
                return f"CAST(NULLIF(TRIM(CAST({rec(a[0])} AS VARCHAR)), '') AS FLOAT8)"
            if fn == "DATE":
                return f"MAKE_DATE(CAST({rec(a[0])} AS INTEGER), CAST({rec(a[1])} AS INTEGER), CAST({rec(a[2])} AS INTEGER))"
            raise ExprError(f"Hàm {fn} chưa hỗ trợ")
        op = n.op
        if op == "&":
            return f"(COALESCE(CAST({rec(n.left)} AS VARCHAR), '') || COALESCE(CAST({rec(n.right)} AS VARCHAR), ''))"
        if op == "/":
            return f"(CAST({rec(n.left)} AS FLOAT8) / NULLIF({rec(n.right)}, 0))"
        if op == "^":
            return f"POWER({rec(n.left)}, {rec(n.right)})"
        if op in ("AND", "OR"):
            return f"({rec(n.left)} {op} {rec(n.right)})"
        lt, rt = infer_type(n.left, resolve), infer_type(n.right, resolve)
        if op == "-" and lt == "date" and rt == "date":
            return f"({date_of(n.left)} - {date_of(n.right)})"
        if op in ("+", "-") and lt == "date" and rt != "date":
            return f"({date_of(n.left)} {op} CAST({rec(n.right)} AS INTEGER))"
        if op == "+" and rt == "date" and lt != "date":
            return f"({date_of(n.right)} + CAST({rec(n.left)} AS INTEGER))"
        return f"({rec(n.left)} {op} {rec(n.right)})"

    return rec(node)
