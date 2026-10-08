"""Chuẩn hoá giá trị khoá trước khi so khớp giữa 2 bảng — cùng 1 định nghĩa cho Python (dò quan hệ trên dữ liệu)
và SQL (view). Mặc định luôn so dạng text đã TRIM + UPPER (giống VLOOKUP/COUNTIFS của Excel: không phân biệt hoa thường).

Phép chuẩn hoá (Transform.op):
- number_part     chỉ giữ chữ số, bỏ số 0 đầu:            "STE0870" -> "870",  870 -> "870"
- first_token     phần trước ký tự không phải chữ/số đầu:  "8644-5686" -> "8644"
- remove_prefix/remove_suffix(text), left/right(n), replace(a, b), trim, lower, upper
"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd

from modeling.spec import Transform

_NON_DIGIT = re.compile(r"\D+")
_FIRST_TOKEN = re.compile(r"[^0-9A-Za-z].*$", re.S)

# tổ hợp được thử khi tự dò quan hệ (rẻ -> đắt); [] = so trực tiếp
CANDIDATE_TRANSFORMS: list[list[Transform]] = [
    [], [Transform(op="number_part")], [Transform(op="first_token")],
    [Transform(op="first_token"), Transform(op="number_part")],
]


def _base_str(v: Any) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    return s.upper() if s else None


def apply_py(v: Any, transforms: list[Transform]) -> str | None:
    s = _base_str(v)
    for t in transforms:
        if s is None:
            return None
        a = t.args
        if t.op == "number_part":
            d = _NON_DIGIT.sub("", s).lstrip("0")
            s = d or None
        elif t.op == "first_token":
            s = _FIRST_TOKEN.sub("", s) or None
        elif t.op == "trim":
            s = s.strip()
        elif t.op in ("lower", "upper"):
            s = s.upper()
        elif t.op == "remove_prefix":
            p = str(a[0]).upper() if a else ""
            s = s[len(p):] if p and s.startswith(p) else s
        elif t.op == "remove_suffix":
            p = str(a[0]).upper() if a else ""
            s = s[: -len(p)] if p and s.endswith(p) else s
        elif t.op == "left":
            s = s[: int(a[0])] if a else s
        elif t.op == "right":
            s = s[-int(a[0]):] if a else s
        elif t.op == "replace":
            s = s.replace(str(a[0]).upper(), str(a[1]).upper() if len(a) > 1 else "") if a else s
        s = s or None
    return s


def apply_series(series: pd.Series, transforms: list[Transform]) -> pd.Series:
    return series.map(lambda v: apply_py(v, transforms))


def _lit(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def to_sql(col_sql: str, col_type: str, transforms: list[Transform]) -> str:
    """Biểu thức SQL chuẩn hoá khoá (Postgres + DuckDB)."""
    if col_type == "double":
        s = f"CAST(CASE WHEN {col_sql} = FLOOR({col_sql}) THEN CAST({col_sql} AS BIGINT) END AS VARCHAR)"
        s = f"COALESCE({s}, CAST({col_sql} AS VARCHAR))"
    else:
        s = f"CAST({col_sql} AS VARCHAR)"
    s = f"NULLIF(UPPER(TRIM({s})), '')"
    for t in transforms:
        a = t.args
        if t.op == "number_part":
            s = f"NULLIF(LTRIM(REGEXP_REPLACE({s}, '[^0-9]', '', 'g'), '0'), '')"
        elif t.op == "first_token":
            s = f"NULLIF(REGEXP_REPLACE({s}, '[^0-9A-Za-z].*$', ''), '')"
        elif t.op == "trim":
            s = f"TRIM({s})"
        elif t.op in ("lower", "upper"):
            s = f"UPPER({s})"
        elif t.op == "remove_prefix" and a:
            p = str(a[0]).upper()
            s = f"(CASE WHEN LEFT({s}, {len(p)}) = {_lit(p)} THEN SUBSTR({s}, {len(p) + 1}) ELSE {s} END)"
        elif t.op == "remove_suffix" and a:
            p = str(a[0]).upper()
            s = f"(CASE WHEN RIGHT({s}, {len(p)}) = {_lit(p)} THEN LEFT({s}, LENGTH({s}) - {len(p)}) ELSE {s} END)"
        elif t.op == "left" and a:
            s = f"LEFT({s}, {int(a[0])})"
        elif t.op == "right" and a:
            s = f"RIGHT({s}, {int(a[0])})"
        elif t.op == "replace" and a:
            s = f"REPLACE({s}, {_lit(str(a[0]).upper())}, {_lit(str(a[1]).upper() if len(a) > 1 else '')})"
        s = f"NULLIF({s}, '')"
    return s
