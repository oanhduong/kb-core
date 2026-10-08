"""Biên dịch ModelSpec -> 1 câu SELECT (view). Cùng SQL chạy trên Postgres (warehouse, Superset đọc) và DuckDB
(kiểm chứng ứng viên khi phân tích) — chỉ khác cách gọi tên bảng (qualify).

Cấu trúc:
    WITH base AS (SELECT * FROM <bảng chính>),
         x_<cột> AS (SELECT <khoá nguồn đã chuẩn hoá> AS k1.., <gộp/tra cứu> AS v FROM <bảng nguồn> WHERE <lọc> GROUP BY ..),
         l1 AS (SELECT base._row, <cột input/lookup/aggregate/constant> FROM base LEFT JOIN x_.. ON ..),
         l2 AS (SELECT l1.*, <công thức chỉ phụ thuộc cột tầng 1> FROM l1), ...
    SELECT _row AS source_row, <các cột theo thứ tự> FROM l<n>
Mỗi dòng view ứng với 1 dòng của sheet chính (source_row = số dòng Excel) -> đối chiếu được từng dòng. Cột chưa có
quy tắc hợp lệ trả NULL và được liệt kê trong CompiledView.problems (không làm hỏng cả view).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from core.i18n import L
from modeling import expr as E
from modeling import keys as K
from modeling.spec import Condition, ModelSpec, OutputColumn, Relationship, Rule

SQL_TYPE = {"integer": "BIGINT", "double": "FLOAT8", "percent": "FLOAT8", "date": "DATE", "timestamp": "TIMESTAMP",
            "boolean": "BOOLEAN", "text": "VARCHAR"}


def q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def lit(v: Any, col_type: str | None = None) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v) if isinstance(v, float) else str(v)
    if hasattr(v, "isoformat"):
        return f"DATE '{v.isoformat()[:10]}'"
    s = str(v)
    if col_type in ("date", "timestamp") and len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return f"DATE '{s[:10]}'"
    return "'" + s.replace("'", "''") + "'"


def as_of_sql(spec: ModelSpec) -> str:
    v = spec.parameters.as_of_date
    if not v or v == "today":
        return "CURRENT_DATE"
    return f"DATE '{v[:10]}'"


@dataclass
class CompiledView:
    sql: str
    problems: dict[str, str] = field(default_factory=dict)   # tên cột -> lý do trả NULL
    order: list[str] = field(default_factory=list)


class CompileError(ValueError):
    pass


def _col_type(spec: ModelSpec, table: str, column: str) -> str:
    t = spec.table(table)
    c = next((c for c in t.columns if c.name == column), None) if t else None
    if c is None:
        raise CompileError(L(f"bảng {table} không có cột {column}", f"table {table} has no column {column}"))
    return c.type


def _cond_sql(spec: ModelSpec, table: str, c: Condition, alias: str) -> str:
    ctype = _col_type(spec, table, c.column)
    col = f"{alias}.{q(c.column)}"
    is_text = ctype == "text"
    is_date = ctype in ("date", "timestamp")
    lhs = f"UPPER(CAST({col} AS VARCHAR))" if is_text else (f"CAST({col} AS DATE)" if is_date else col)

    def val(v: Any) -> str:
        if c.as_of_offset_days is not None:
            n = int(c.as_of_offset_days)
            return f"({as_of_sql(spec)} + {n})" if n >= 0 else f"({as_of_sql(spec)} - {-n})"
        if is_text:
            return "UPPER(" + lit(str(v)) + ")"
        if ctype == "boolean" and isinstance(v, str):
            return "TRUE" if v.strip().lower() in ("true", "1", "yes") else "FALSE"
        return lit(v, ctype)

    op = c.op
    if op == "is_null":
        return f"({col} IS NULL" + (f" OR TRIM(CAST({col} AS VARCHAR)) = ''" if is_text else "") + ")"
    if op == "not_null":
        return f"({col} IS NOT NULL" + (f" AND TRIM(CAST({col} AS VARCHAR)) <> ''" if is_text else "") + ")"
    if op in ("in", "not_in"):
        vals = c.value if isinstance(c.value, list) else [c.value]
        if not vals:
            return "TRUE" if op == "not_in" else "FALSE"
        inner = ", ".join(val(v) for v in vals)
        return f"({lhs} IN ({inner}))" if op == "in" else f"({lhs} IS NULL OR {lhs} NOT IN ({inner}))"
    if op in ("contains", "not_contains", "starts_with"):
        s = str(c.value or "").upper().replace("'", "''")
        pat = f"'{s}%'" if op == "starts_with" else f"'%{s}%'"
        cond = f"UPPER(CAST({col} AS VARCHAR)) LIKE {pat}"
        return f"({cond})" if op != "not_contains" else f"({col} IS NULL OR NOT ({cond}))"
    sym = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
    if op == "ne":  # Excel "<>x" đếm cả ô trống
        return f"({lhs} IS NULL OR {lhs} <> {val(c.value)})"
    return f"({lhs} {sym} {val(c.value)})"


def _agg_sql(func: str, col: str | None) -> str:
    if func == "count":
        return "COUNT(*)" if not col else f"COUNT({col})"
    if func == "count_distinct":
        return f"COUNT(DISTINCT {col})"
    if func == "avg":
        return f"AVG(CAST({col} AS FLOAT8))"
    return f"{func.upper()}({col})"


def _source_cte(spec: ModelSpec, rel: Relationship, rule: Rule, qualify: Callable[[str], str]) -> tuple[str, list[str]]:
    """CTE cho 1 cột lookup/aggregate -> (sql, danh sách cột khoá k1..kn)."""
    src = rel.source_table
    keys_sql = []
    for i, kp in enumerate(rel.keys, 1):
        keys_sql.append((f"k{i}", K.to_sql(f"s.{q(kp.source_column)}", _col_type(spec, src, kp.source_column), kp.source_transforms)))
    knames = [k for k, _ in keys_sql]
    where = [f"{expr} IS NOT NULL" for _, expr in keys_sql]
    where += [_cond_sql(spec, src, c, "s") for c in rule.filters]
    wsql = " AND ".join(where) if where else "TRUE"
    sel_keys = ", ".join(f"{expr} AS {k}" for k, expr in keys_sql)
    if rule.kind == "lookup" and (rule.func in (None, "first")):
        if not rule.column:
            raise CompileError(L("tra cứu thiếu cột kết quả", "the lookup is missing its result column"))
        _col_type(spec, src, rule.column)
        sql = (f"SELECT DISTINCT ON ({', '.join(knames)}) {', '.join(knames)}, v FROM ("
               f"SELECT {sel_keys}, s.{q(rule.column)} AS v, s._row AS _r FROM {qualify(src)} s WHERE {wsql}"
               f") t ORDER BY {', '.join(knames)}, _r")
        return sql, knames
    func = rule.func or "count"
    col = None
    if rule.column:
        ctype = _col_type(spec, src, rule.column)
        col = f"s.{q(rule.column)}"
        if ctype == "timestamp" and func in ("min", "max"):
            col = f"CAST({col} AS DATE)"
    elif func not in ("count",):
        raise CompileError(L(f"phép {func} cần chọn cột", f"the {func} operation needs a column"))
    sql = f"SELECT {sel_keys}, {_agg_sql(func, col)} AS v FROM {qualify(src)} s WHERE {wsql} GROUP BY {', '.join(str(i) for i in range(1, len(knames) + 1))}"
    return sql, knames


def _resolver(spec: ModelSpec, known: dict[str, str], alias: str) -> Callable[[str], E.SqlCol]:
    by_label = {c.label.strip().lower(): c for c in spec.columns}
    by_name = {c.name: c for c in spec.columns}

    def resolve(ref: str) -> E.SqlCol:
        c = by_name.get(ref) or by_label.get(ref.strip().lower())
        if c is None:
            raise E.ExprError(L(f"không có cột [{ref}]", f"no column [{ref}]"))
        if c.name not in known:
            raise E.ExprError(L(f"cột [{ref}] chưa tính được ở bước này", f"column [{ref}] cannot be calculated at this step yet"))
        return E.SqlCol(f"{alias}.{q(c.name)}", known[c.name])

    return resolve


def formula_deps(spec: ModelSpec, col: OutputColumn) -> list[str]:
    node = E.parse(col.rule.expression or "")
    by_label = {c.label.strip().lower(): c.name for c in spec.columns}
    names = {c.name for c in spec.columns}
    out = []
    for ref in E.columns_used(node):
        n = ref if ref in names else by_label.get(ref.strip().lower())
        if n is None:
            raise E.ExprError(L(f"không có cột [{ref}]", f"no column [{ref}]"))
        out.append(n)
    return out


def compile_view(spec: ModelSpec, qualify: Callable[[str], str] = q, *, override: dict[str, Rule] | None = None,
                 only: list[str] | None = None) -> CompiledView:
    """override: thay quy tắc tạm thời (thử ứng viên); only: chỉ tính các cột này (+ cột chúng phụ thuộc)."""
    main = spec.main()
    if main is None:
        raise CompileError(L("chưa chọn sheet chính", "no main sheet selected"))
    main_cols = {c.name: c.type for c in main.columns}
    cols = []
    for c in spec.columns:
        if override and c.name in override:
            c = c.model_copy(update={"rule": override[c.name]})
        cols.append(c)
    problems: dict[str, str] = {}
    ctes = [f"base AS (SELECT * FROM {qualify(main.key)})"]
    joins: list[str] = []
    level1: list[str] = ["base._row AS _row"]
    known: dict[str, str] = {}
    formulas: list[OutputColumn] = []
    aso = as_of_sql(spec)

    wanted = None
    if only is not None:
        wanted = set(only)
        by_name = {c.name: c for c in cols}
        stack = list(only)
        while stack:
            n = stack.pop()
            c = by_name.get(n)
            if c is None or c.rule.kind != "formula":
                continue
            try:
                for d in formula_deps(spec.model_copy(update={"columns": cols}), c):
                    if d not in wanted:
                        wanted.add(d)
                        stack.append(d)
            except E.ExprError:
                pass

    for i, c in enumerate(cols):
        if wanted is not None and c.name not in wanted:
            continue
        r = c.rule
        ctype = SQL_TYPE.get(c.type, "VARCHAR")
        try:
            if r.kind == "input":
                src_col = r.column or c.name
                if src_col not in main_cols:
                    raise CompileError(L(f"sheet chính không có cột {src_col}", f"the main sheet has no column {src_col}"))
                level1.append(f"base.{q(src_col)} AS {q(c.name)}")
                known[c.name] = main_cols[src_col]
            elif r.kind == "constant":
                level1.append(f"CAST({lit(r.value, c.type)} AS {ctype}) AS {q(c.name)}")
                known[c.name] = c.type
            elif r.kind in ("lookup", "aggregate"):
                rel = spec.relationship(r.relationship_id)
                if rel is None or rel.status == "rejected":
                    raise CompileError(L("quan hệ dùng cho cột này không còn hiệu lực", "the relationship used by this column is no longer valid"))
                if rel.target_table != main.key:
                    raise CompileError(L("quan hệ phải nối tới sheet chính", "the relationship must connect to the main sheet"))
                if r.table and r.table != rel.source_table:
                    raise CompileError(L("bảng nguồn không khớp quan hệ", "the source table does not match the relationship"))
                sql, knames = _source_cte(spec, rel, r, qualify)
                alias = f"x{i}"
                ctes.append(f"{alias} AS ({sql})")
                on = []
                for kp, kn in zip(rel.keys, knames):
                    if kp.base_column not in main_cols:
                        raise CompileError(L(f"sheet chính không có cột khoá {kp.base_column}", f"the main sheet has no key column {kp.base_column}"))
                    on.append(f"{alias}.{kn} = {K.to_sql(f'base.{q(kp.base_column)}', main_cols[kp.base_column], kp.base_transforms)}")
                joins.append(f"LEFT JOIN {alias} ON {' AND '.join(on)}")
                v = f"{alias}.v"
                if r.default is not None:
                    v = f"COALESCE({v}, {lit(r.default, c.type)})"
                if r.round_digits is not None:
                    v = f"ROUND(CAST({v} AS DECIMAL(38,10)), {int(r.round_digits)})"
                level1.append(f"{v} AS {q(c.name)}")
                vt = _col_type(spec, rel.source_table, r.column) if r.column else "integer"
                known[c.name] = "integer" if (r.func in ("count", "count_distinct")) else ("double" if r.func == "avg" else vt)
            elif r.kind == "formula":
                formulas.append(c)
                continue
            else:
                raise CompileError(L("chưa có quy tắc", "no rule yet"))
        except (CompileError, E.ExprError) as e:
            problems[c.name] = str(e)
            level1.append(f"CAST(NULL AS {ctype}) AS {q(c.name)}")
            known[c.name] = c.type

    # công thức: xếp tầng theo phụ thuộc
    layers: list[list[str]] = [level1]
    pending = list(formulas)
    level = 1
    while pending:
        layer: list[str] = []
        nxt: list[OutputColumn] = []
        newly: dict[str, str] = {}
        for c in pending:
            try:
                deps = formula_deps(spec.model_copy(update={"columns": cols}), c)
            except E.ExprError as e:
                problems[c.name] = str(e)
                layer.append(f"CAST(NULL AS {SQL_TYPE.get(c.type, 'VARCHAR')}) AS {q(c.name)}")
                newly[c.name] = c.type
                continue
            if all(d in known for d in deps):
                try:
                    node = E.parse(c.rule.expression or "")
                    res = _resolver(spec.model_copy(update={"columns": cols}), known, f"l{level}")
                    sql = E.to_sql(node, res, aso)
                    if c.rule.round_digits is not None:
                        sql = f"ROUND(CAST({sql} AS DECIMAL(38,10)), {int(c.rule.round_digits)})"
                    layer.append(f"{sql} AS {q(c.name)}")
                    newly[c.name] = E.infer_type(node, res)
                except E.ExprError as e:
                    problems[c.name] = str(e)
                    layer.append(f"CAST(NULL AS {SQL_TYPE.get(c.type, 'VARCHAR')}) AS {q(c.name)}")
                    newly[c.name] = c.type
            else:
                nxt.append(c)
        if not layer:  # vòng phụ thuộc
            for c in nxt:
                problems[c.name] = L("công thức phụ thuộc vòng tròn hoặc vào cột chưa tính được", "the formula has a circular dependency or depends on a column that cannot be calculated")
                layer.append(f"CAST(NULL AS {SQL_TYPE.get(c.type, 'VARCHAR')}) AS {q(c.name)}")
                newly[c.name] = c.type
            nxt = []
        known.update(newly)
        layers.append(layer)
        pending = nxt
        level += 1

    parts = [f"l1 AS (SELECT {', '.join(layers[0])} FROM base {' '.join(joins)})"]
    for li, layer in enumerate(layers[1:], start=2):
        parts.append(f"l{li} AS (SELECT l{li - 1}.*, {', '.join(layer)} FROM l{li - 1} l{li - 1})")
    last = f"l{len(layers)}"
    order = [c.name for c in cols if wanted is None or c.name in wanted]
    select = ", ".join(["_row AS source_row"] + [q(n) for n in order])
    sql = "WITH " + ",\n".join(ctes + parts) + f"\nSELECT {select} FROM {last} ORDER BY _row"
    return CompiledView(sql=sql, problems=problems, order=order)
