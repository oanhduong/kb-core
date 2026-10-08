"""Hiểu công thức Excel: tokenize (openpyxl) -> AST -> dịch sang quy tắc của mô hình.

Dịch được (toàn bộ công thức, có thể bọc trong IFERROR/IFNA/ROUND):
- tính trong dòng: tham chiếu ô cùng dòng (=TODAY()-M3, =AB3-AA3, =IF(F3>0, ...)) -> Rule formula
- tra cứu: VLOOKUP / INDEX(MATCH) / XLOOKUP sang sheet khác -> Rule lookup + khoá nối
- gộp có điều kiện: COUNTIF(S) / SUMIF(S) / AVERAGEIF(S) / MAXIFS / MINIFS -> Rule aggregate + khoá nối + bộ lọc
- hằng số (=TRUE(), =0, ="N/A") -> Rule constant
Không dịch được -> Translation.error (công thức gốc vẫn được giữ làm bằng chứng để người dùng/AI xử lý tiếp).
Tham chiếu tới ô tuyệt đối ngoài vùng bảng (ô tham số kiểu $B$1) được thay bằng giá trị hiện tại của ô đó.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from openpyxl.formula.tokenizer import Token, Tokenizer
from openpyxl.utils.cell import column_index_from_string, get_column_letter

from core.i18n import L
from modeling import expr as E
from modeling.spec import Condition, Rule


@dataclass(frozen=True)
class Ref:
    sheet: str | None
    c1: int | None      # cột đầu (1-based) | None = cả dòng
    r1: int | None      # dòng đầu | None = cả cột
    c2: int | None
    r2: int | None
    abs_r1: bool = False

    @property
    def is_cell(self) -> bool:
        return self.c1 is not None and self.r1 is not None and self.c1 == self.c2 and self.r1 == self.r2


_CELL_RE = re.compile(r"^(\$?)([A-Za-z]{1,3})?(\$?)(\d+)?$")


def parse_ref(text: str) -> Ref | None:
    sheet = None
    if "!" in text:
        sheet, text = text.rsplit("!", 1)
        sheet = sheet.strip("'").replace("''", "'")
    parts = text.split(":")
    if len(parts) > 2 or "[" in text:
        return None
    cells = []
    for p in parts:
        m = _CELL_RE.match(p)
        if not m or (not m.group(2) and not m.group(4)):
            return None
        col = column_index_from_string(m.group(2).upper()) if m.group(2) else None
        row = int(m.group(4)) if m.group(4) else None
        cells.append((col, row, bool(m.group(3))))
    (c1, r1, a1), (c2, r2, _a2) = cells[0], cells[-1]
    return Ref(sheet, c1, r1, c2, r2, abs_r1=a1)


# ===================================================================================== parse
@dataclass(frozen=True)
class XRef:
    ref: Ref
    raw: str


def parse_formula(formula: str) -> Any:
    """Excel formula -> AST (E.Num/Str/Bool/Call/Bin/Un + XRef). Ném E.ExprError nếu không parse được."""
    try:
        items = [t for t in Tokenizer(formula).items if t.type != Token.WSPACE]
    except Exception as e:  # tokenizer của openpyxl ném nhiều loại lỗi
        raise E.ExprError(L(f"không tokenize được: {e}", f"could not tokenize: {e}")) from e
    pos = 0

    def peek() -> Token | None:
        return items[pos] if pos < len(items) else None

    def take() -> Token:
        nonlocal pos
        t = peek()
        if t is None:
            raise E.ExprError(L("công thức kết thúc đột ngột", "the formula ends unexpectedly"))
        pos += 1
        return t

    def operand() -> Any:
        t = take()
        if t.type == Token.OP_PRE:
            x = operand()
            return x if t.value == "+" else E.Un("-", x)
        if t.type == Token.OPERAND:
            st = t.subtype
            if st == Token.NUMBER:
                v = float(t.value)
                node: Any = E.Num(int(v) if v.is_integer() else v)
            elif st == Token.TEXT:
                node = E.Str(t.value[1:-1].replace('""', '"'))
            elif st == Token.LOGICAL:
                node = E.Bool(t.value.upper() == "TRUE")
            elif st == Token.RANGE:
                r = parse_ref(t.value)
                if r is None:
                    raise E.ExprError(L(f"tham chiếu không hỗ trợ: {t.value}", f"unsupported reference: {t.value}"))
                node = XRef(r, t.value)
            else:
                raise E.ExprError(L(f"toán hạng không hỗ trợ: {t.value}", f"unsupported operand: {t.value}"))
        elif t.type == Token.FUNC and t.subtype == Token.OPEN:
            name = t.value[:-1].upper().replace("_XLFN.", "")
            args: list[Any] = []
            if peek() is not None and peek().type == Token.FUNC and peek().subtype == Token.CLOSE:
                take()
            else:
                while True:
                    nxt = peek()
                    if nxt is not None and nxt.type == Token.SEP:  # tham số rỗng: F(a,,c)
                        args.append(None)
                        take()
                        continue
                    args.append(binary(0))
                    s = take()
                    if s.type == Token.FUNC and s.subtype == Token.CLOSE:
                        break
                    if s.type != Token.SEP:
                        raise E.ExprError(L(f"cần ',' trong {name}", f"expected ',' in {name}"))
            node = E.Call(name, tuple(args))
        elif t.type == Token.PAREN and t.subtype == Token.OPEN:
            node = binary(0)
            c = take()
            if c.type != Token.PAREN:
                raise E.ExprError(L("thiếu ')'", "missing ')'"))
        else:
            raise E.ExprError(L(f"không hiểu '{t.value}'", f"cannot understand '{t.value}'"))
        while peek() is not None and peek().type == Token.OP_POST:
            take()
            node = E.Bin("/", node, E.Num(100))
        return node

    def binary(min_prec: int) -> Any:
        left = operand()
        while True:
            t = peek()
            if t is None or t.type != Token.OP_IN:
                return left
            op = t.value
            prec = E._PREC.get(op)
            if prec is None or prec < min_prec:
                return left
            take()
            right = binary(prec + (0 if op == "^" else 1))
            left = E.Bin(op, left, right)

    if not formula.startswith("="):
        raise E.ExprError(L("không phải công thức", "not a formula"))
    node = binary(0)
    if peek() is not None:
        raise E.ExprError(L(f"thừa '{peek().value}'", f"unexpected '{peek().value}'"))
    return node


# ===================================================================================== translate
@dataclass
class SheetMap:
    """Bảng đã phân tích của 1 sheet: cột Excel -> (tên cột SQL, nhãn), phạm vi dòng dữ liệu."""
    table: str
    columns: dict[int, tuple[str, str]]   # col index -> (name, label)
    first_row: int
    last_row: int


@dataclass
class JoinHint:
    base_column: str          # tên cột SQL của sheet đang chứa công thức
    source_table: str
    source_column: str


@dataclass
class Translation:
    rule: Rule | None = None
    joins: list[JoinHint] = field(default_factory=list)
    error: str | None = None


class _Fail(Exception):
    pass


class Translator:
    def __init__(self, sheets: dict[str, SheetMap], current_sheet: str, cell_value: Callable[[str, int, int], Any]) -> None:
        self.sheets = sheets
        self.sheet = current_sheet
        self.cell_value = cell_value

    def translate(self, formula: str, row: int) -> Translation:
        try:
            node = parse_formula(formula)
        except E.ExprError as e:
            return Translation(error=str(e))
        self.row = row
        default: Any = None
        round_digits: int | None = None
        # bóc vỏ IFERROR/IFNA/ROUND quanh 1 phép tra cứu/gộp
        while isinstance(node, E.Call) and node.fn in ("IFERROR", "IFNA", "ROUND") and node.args and self._is_cross(node.args[0]):
            if node.fn == "ROUND":
                round_digits = int(node.args[1].v) if len(node.args) > 1 and isinstance(node.args[1], E.Num) else 0
            else:
                d = node.args[1] if len(node.args) > 1 else None
                if d is not None:
                    try:
                        default = self._const(d)
                    except _Fail:
                        return Translation(error=L("giá trị mặc định của IFERROR không phải hằng số", "the IFERROR default value is not a constant"))
            node = node.args[0]
        try:
            if isinstance(node, E.Call) and node.fn in _LOOKUPS:
                tr = _LOOKUPS[node.fn](self, node.args)
            elif isinstance(node, E.Call) and node.fn in _AGGS:
                tr = _AGGS[node.fn](self, node.args)
            elif self._is_cross(node):
                return Translation(error=L("công thức kết hợp nhiều phép tra cứu/gộp — cần mô tả lại bằng quy tắc", "the formula combines several lookups/aggregations — describe it again as a rule"))
            else:
                const = self._try_const(node)
                if const is not _NO:
                    return Translation(rule=Rule(kind="constant", value=const))
                local = self._local(node)
                return Translation(rule=Rule(kind="formula", expression=E.to_text(local), round_digits=round_digits))
        except _Fail as e:
            return Translation(error=str(e))
        if tr.rule is not None:
            if default is not None:
                tr.rule.default = default
            if round_digits is not None:
                tr.rule.round_digits = round_digits
        return tr

    # ------------------------------------------------------------------ helpers
    def _is_cross(self, node: Any) -> bool:
        if isinstance(node, XRef):
            return node.ref.sheet is not None and node.ref.sheet != self.sheet and not node.ref.is_cell
        if isinstance(node, E.Call):
            return node.fn in _LOOKUPS or node.fn in _AGGS or any(self._is_cross(a) for a in node.args if a is not None)
        if isinstance(node, E.Bin):
            return self._is_cross(node.left) or self._is_cross(node.right)
        if isinstance(node, E.Un):
            return self._is_cross(node.x)
        return False

    def _try_const(self, node: Any) -> Any:
        try:
            return self._const(node)
        except _Fail:
            return _NO

    def _const(self, node: Any) -> Any:
        if isinstance(node, (E.Num, E.Str, E.Bool)):
            return node.v
        if isinstance(node, E.Un) and isinstance(node.x, E.Num):
            return -node.x.v
        if isinstance(node, E.Call) and node.fn in ("TRUE", "FALSE") and not node.args:
            return node.fn == "TRUE"
        if isinstance(node, XRef) and node.ref.is_cell and not self._in_table_row(node.ref):
            v = self.cell_value(node.ref.sheet or self.sheet, node.ref.r1, node.ref.c1)
            if isinstance(v, str) and v.startswith("="):
                raise _Fail(L("ô tham số là công thức", "the parameter cell is a formula"))
            return v
        raise _Fail(L("không phải hằng số", "not a constant"))

    def _in_table_row(self, ref: Ref) -> bool:
        """Ô thuộc vùng dữ liệu của chính bảng (cùng sheet, cùng dòng)?"""
        sheet = ref.sheet or self.sheet
        return sheet == self.sheet and ref.r1 == self.row and not ref.abs_r1

    def _row_col(self, node: Any) -> str:
        """Ô cùng dòng của sheet hiện tại -> tên cột SQL."""
        if not (isinstance(node, XRef) and node.ref.is_cell and (node.ref.sheet in (None, self.sheet)) and node.ref.r1 == self.row):
            raise _Fail(L("điều kiện tham chiếu ô không cùng dòng", "the condition refers to a cell in a different row"))
        sm = self.sheets.get(self.sheet)
        col = sm.columns.get(node.ref.c1) if sm else None
        if col is None:
            raise _Fail(L(f"ô {node.raw} nằm ngoài vùng bảng", f"cell {node.raw} is outside the table area"))
        return col[0]

    def _range_col(self, node: Any) -> tuple[str, str]:
        """Vùng 1 cột ở sheet khác -> (bảng, cột SQL)."""
        if not isinstance(node, XRef):
            raise _Fail(L("cần một vùng ô", "a cell range is required"))
        r = node.ref
        sheet = r.sheet or self.sheet
        sm = self.sheets.get(sheet)
        if sm is None:
            raise _Fail(L(f"sheet '{sheet}' không được phân tích thành bảng", f"sheet '{sheet}' was not analyzed as a table"))
        if r.c1 is None or r.c1 != r.c2:
            raise _Fail(L(f"vùng {node.raw} không phải 1 cột", f"range {node.raw} is not a single column"))
        col = sm.columns.get(r.c1)
        if col is None:
            raise _Fail(L(f"cột {get_column_letter(r.c1)} của '{sheet}' nằm ngoài bảng", f"column {get_column_letter(r.c1)} of '{sheet}' is outside the table"))
        return sm.table, col[0]

    def _local(self, node: Any) -> Any:
        """AST Excel trong dòng -> AST ngôn ngữ mô hình ([nhãn cột])."""
        if isinstance(node, (E.Num, E.Str, E.Bool)):
            return node
        if isinstance(node, XRef):
            if node.ref.is_cell and (node.ref.sheet in (None, self.sheet)) and node.ref.r1 == self.row and not node.ref.abs_r1:
                sm = self.sheets.get(self.sheet)
                col = sm.columns.get(node.ref.c1) if sm else None
                if col is None:
                    raise _Fail(L(f"ô {node.raw} nằm ngoài vùng bảng", f"cell {node.raw} is outside the table area"))
                return E.Col(col[1])
            v = self._const(node)
            if isinstance(v, bool):
                return E.Bool(v)
            if isinstance(v, (int, float)):
                return E.Num(v)
            if v is None:
                return E.Num(0)
            if hasattr(v, "isoformat"):
                d = v.date() if hasattr(v, "date") else v
                return E.Call("DATE", (E.Num(d.year), E.Num(d.month), E.Num(d.day)))
            return E.Str(str(v))
        if isinstance(node, E.Un):
            return E.Un(node.op, self._local(node.x))
        if isinstance(node, E.Bin):
            return E.Bin(node.op, self._local(node.left), self._local(node.right))
        if isinstance(node, E.Call):
            fn = {"TODAY": "AS_OF", "NOW": "AS_OF", "IFNA": "IFERROR"}.get(node.fn, node.fn)
            if fn == "DATEDIF":
                return E.Call("DAYS", (self._local(node.args[1]), self._local(node.args[0])))
            if fn not in E._FUNCS:
                raise _Fail(L(f"hàm {node.fn} chưa hỗ trợ", f"function {node.fn} is not supported yet"))
            return E.Call(fn, tuple(self._local(a) for a in node.args if a is not None))
        raise _Fail(L("biểu thức không hỗ trợ", "unsupported expression"))

    def _criterion(self, crit_range: Any, crit: Any, joins: list[JoinHint], filters: list[Condition]) -> None:
        table, col = self._range_col(crit_range)
        # ô cùng dòng -> khoá nối
        if isinstance(crit, XRef) and crit.ref.is_cell and (crit.ref.sheet in (None, self.sheet)) and crit.ref.r1 == self.row:
            joins.append(JoinHint(base_column=self._row_col(crit), source_table=table, source_column=col))
            return
        filters.append(_parse_criterion(col, crit, self))

    def _join_table(self, joins: list[JoinHint]) -> str:
        tables = {j.source_table for j in joins}
        if len(tables) != 1:
            raise _Fail(L("các vùng điều kiện thuộc nhiều bảng", "the condition ranges span several tables"))
        return tables.pop()


_NO = object()
_OPS = (("<>", "ne"), (">=", "gte"), ("<=", "lte"), (">", "gt"), ("<", "lt"), ("=", "eq"))


def _num_or_str(s: str) -> Any:
    try:
        v = float(s)
        return int(v) if v.is_integer() else v
    except ValueError:
        return s


def _parse_criterion(col: str, crit: Any, tr: Translator) -> Condition:
    # ">"&TODAY()-30  |  "<"&30  |  ">="&$B$1
    if isinstance(crit, E.Bin) and crit.op == "&" and isinstance(crit.left, E.Str):
        op_txt = crit.left.v.strip()
        op = dict(_OPS).get(op_txt)
        if op is None:
            raise _Fail(L(f"điều kiện '{op_txt}' chưa hỗ trợ", f"condition '{op_txt}' is not supported yet"))
        rhs = crit.right
        offset = _as_of_offset(rhs)
        if offset is not None:
            return Condition(column=col, op=op, as_of_offset_days=offset)
        return Condition(column=col, op=op, value=tr._const(rhs))
    if isinstance(crit, E.Num):
        return Condition(column=col, op="eq", value=crit.v)
    if isinstance(crit, E.Bool):
        return Condition(column=col, op="eq", value=crit.v)
    if isinstance(crit, XRef):
        v = tr._const(crit)
        return Condition(column=col, op="eq", value=v)
    if not isinstance(crit, E.Str):
        raise _Fail(L("điều kiện phức tạp chưa hỗ trợ", "complex conditions are not supported yet"))
    s = crit.v
    if s == "":
        return Condition(column=col, op="is_null")
    if s == "<>":
        return Condition(column=col, op="not_null")
    for txt, op in _OPS:
        if s.startswith(txt):
            val = s[len(txt):]
            if op in ("eq", "ne") and "*" in val:
                return Condition(column=col, op="contains" if op == "eq" else "not_contains", value=val.strip("*"))
            return Condition(column=col, op=op, value=_num_or_str(val))
    if "*" in s:
        if s.endswith("*") and not s.startswith("*"):
            return Condition(column=col, op="starts_with", value=s.rstrip("*"))
        return Condition(column=col, op="contains", value=s.strip("*"))
    return Condition(column=col, op="eq", value=_num_or_str(s))


def _as_of_offset(node: Any) -> int | None:
    """TODAY() | TODAY()+n | TODAY()-n -> n."""
    if isinstance(node, E.Call) and node.fn in ("TODAY", "NOW") and not node.args:
        return 0
    if isinstance(node, E.Bin) and node.op in ("+", "-") and isinstance(node.right, E.Num):
        base = _as_of_offset(node.left)
        if base is not None:
            return base + int(node.right.v) * (1 if node.op == "+" else -1)
    return None


# ------------------------------------------------------------------ tra cứu
def _vlookup(tr: Translator, args: tuple) -> Translation:
    if len(args) < 3:
        raise _Fail(L("VLOOKUP thiếu tham số", "VLOOKUP is missing arguments"))
    if len(args) > 3 and args[3] is not None and tr._try_const(args[3]) not in (False, 0):
        raise _Fail(L("VLOOKUP dò gần đúng (TRUE) chưa hỗ trợ", "approximate-match VLOOKUP (TRUE) is not supported yet"))
    rng = args[1]
    if not isinstance(rng, XRef) or rng.ref.c1 is None:
        raise _Fail(L("vùng VLOOKUP không hợp lệ", "invalid VLOOKUP range"))
    idx = tr._const(args[2])
    key_ref = XRef(Ref(rng.ref.sheet, rng.ref.c1, rng.ref.r1, rng.ref.c1, rng.ref.r2), rng.raw)
    ret_c = rng.ref.c1 + int(idx) - 1
    ret_ref = XRef(Ref(rng.ref.sheet, ret_c, rng.ref.r1, ret_c, rng.ref.r2), rng.raw)
    return _lookup(tr, args[0], key_ref, ret_ref)


def _xlookup(tr: Translator, args: tuple) -> Translation:
    if len(args) < 3:
        raise _Fail(L("XLOOKUP thiếu tham số", "XLOOKUP is missing arguments"))
    t = _lookup(tr, args[0], args[1], args[2])
    if len(args) > 3 and args[3] is not None and t.rule is not None:
        t.rule.default = tr._const(args[3])
    return t


def _index(tr: Translator, args: tuple) -> Translation:
    if len(args) < 2 or not (isinstance(args[1], E.Call) and args[1].fn == "MATCH"):
        raise _Fail(L("INDEX không đi kèm MATCH", "INDEX is not paired with MATCH"))
    m = args[1].args
    if len(m) > 2 and m[2] is not None and tr._try_const(m[2]) != 0:
        raise _Fail(L("MATCH dò gần đúng chưa hỗ trợ", "approximate-match MATCH is not supported yet"))
    ret = args[0]
    if len(args) > 2 and args[2] is not None and isinstance(ret, XRef) and ret.ref.c1 is not None:
        c = ret.ref.c1 + int(tr._const(args[2])) - 1
        ret = XRef(Ref(ret.ref.sheet, c, ret.ref.r1, c, ret.ref.r2), ret.raw)
    return _lookup(tr, m[0], m[1], ret)


def _lookup(tr: Translator, key: Any, key_range: Any, ret_range: Any) -> Translation:
    base_col = tr._row_col(key)
    t1, kcol = tr._range_col(key_range)
    t2, rcol = tr._range_col(ret_range)
    if t1 != t2:
        raise _Fail(L("vùng khoá và vùng kết quả ở hai bảng khác nhau", "the key range and result range are in different tables"))
    return Translation(rule=Rule(kind="lookup", table=t1, column=rcol, func="first"),
                       joins=[JoinHint(base_column=base_col, source_table=t1, source_column=kcol)])


# ------------------------------------------------------------------ gộp
def _ifs(func: str, has_value_range: bool) -> Callable[[Translator, tuple], Translation]:
    def run(tr: Translator, args: tuple) -> Translation:
        a = list(args)
        value_col = None
        if has_value_range:
            if not a:
                raise _Fail(L("thiếu vùng giá trị", "missing the value range"))
            vt, value_col = tr._range_col(a.pop(0))
        if len(a) < 2 or len(a) % 2:
            raise _Fail(L("cặp vùng/điều kiện không hợp lệ", "invalid range/condition pair"))
        joins: list[JoinHint] = []
        filters: list[Condition] = []
        for i in range(0, len(a), 2):
            tr._criterion(a[i], a[i + 1], joins, filters)
        if not joins:
            raise _Fail(L("không có điều kiện nối theo dòng hiện tại", "no condition linking to the current row"))
        table = tr._join_table(joins)
        if has_value_range and vt != table:
            raise _Fail(L("vùng giá trị khác bảng với vùng điều kiện", "the value range is in a different table from the condition ranges"))
        return Translation(rule=Rule(kind="aggregate", table=table, column=value_col, func=func, filters=filters,
                                     default=0 if func in ("count", "sum") else None), joins=joins)
    return run


def _if_single(func: str) -> Callable[[Translator, tuple], Translation]:
    """SUMIF(range, crit, [sum_range]) / COUNTIF(range, crit) / AVERAGEIF(...)"""
    def run(tr: Translator, args: tuple) -> Translation:
        if len(args) < 2:
            raise _Fail(L("thiếu tham số", "missing arguments"))
        rest = [args[2], args[0], args[1]] if func != "count" and len(args) > 2 and args[2] is not None else [args[0], args[0], args[1]]
        if func == "count":
            return _ifs("count", False)(tr, (args[0], args[1]))
        return _ifs(func, True)(tr, tuple(rest))
    return run


_LOOKUPS: dict[str, Callable[[Translator, tuple], Translation]] = {"VLOOKUP": _vlookup, "XLOOKUP": _xlookup, "INDEX": _index}
_AGGS: dict[str, Callable[[Translator, tuple], Translation]] = {
    "COUNTIFS": _ifs("count", False), "SUMIFS": _ifs("sum", True), "AVERAGEIFS": _ifs("avg", True),
    "MAXIFS": _ifs("max", True), "MINIFS": _ifs("min", True),
    "COUNTIF": _if_single("count"), "SUMIF": _if_single("sum"), "AVERAGEIF": _if_single("avg"),
}


def references_sheets(formula: str) -> list[str]:
    """Tên các sheet khác được công thức tham chiếu (cho bằng chứng/quan hệ khi không dịch được)."""
    out: list[str] = []
    try:
        items = Tokenizer(formula).items
    except Exception:
        return out
    for t in items:
        if t.type == Token.OPERAND and t.subtype == Token.RANGE and "!" in t.value:
            s = t.value.rsplit("!", 1)[0].strip("'").replace("''", "'")
            if s not in out:
                out.append(s)
    return out
