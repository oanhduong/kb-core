"""So khớp giá trị tính lại với giá trị trong file gốc + chạy spec trên DuckDB (in-memory) để kiểm chứng.

Quy tắc so khớp (giống cách người dùng nhìn Excel):
- số: bằng nhau sau khi làm tròn theo số chữ số thập phân mà Excel đang hiển thị/lưu ở cột đó (8.7 vs 8.6999999 là khớp)
- ngày: so phần ngày; text: so sau TRIM, không phân biệt hoa thường; boolean: so trực tiếp
- dòng Excel để trống: không tính vào "đã so" (không có gì để đối chiếu) nhưng được đếm riêng (expected_nulls)
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from core.i18n import L
from modeling.compile import compile_view
from modeling.spec import ColumnCheck, ModelSpec, Rule

NUMERIC = ("integer", "double", "percent")
DATES = ("date", "timestamp")


def decimals_of(series: pd.Series, cap: int = 6) -> int:
    vals = pd.to_numeric(series, errors="coerce").dropna()
    if vals.empty:
        return 0
    d = 0
    for v in vals.head(500):
        fv = float(v)
        for k in range(cap + 1):
            if abs(round(fv, k) - fv) < 1e-9:
                d = max(d, k)
                break
        else:
            d = cap
    return d


def to_days(series: pd.Series) -> np.ndarray:
    s = pd.to_datetime(series, errors="coerce")
    out = np.full(len(s), np.nan)
    mask = s.notna().to_numpy()
    if mask.any():
        out[mask] = (s[mask].dt.normalize() - pd.Timestamp("1970-01-01")).dt.days.to_numpy(dtype=float)
    return out


def to_float(series: pd.Series) -> np.ndarray:
    return pd.to_numeric(series, errors="coerce").to_numpy(dtype=float, na_value=np.nan)


def to_text(series: pd.Series) -> np.ndarray:
    def f(v: Any) -> Any:
        if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA or v is pd.NaT:
            return None
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        if hasattr(v, "isoformat") and not isinstance(v, str):
            return v.isoformat()[:10]
        s = str(v).strip().upper()
        return s or None
    return np.array([f(v) for v in series], dtype=object)


def kind_of(col_type: str) -> str:
    if col_type in NUMERIC:
        return "num"
    if col_type in DATES:
        return "date"
    if col_type == "boolean":
        return "bool"
    return "text"


def vectorize(series: pd.Series, col_type: str) -> np.ndarray:
    k = kind_of(col_type)
    if k == "num":
        return to_float(series)
    if k == "date":
        return to_days(series)
    if k == "bool":
        return series.map(lambda v: np.nan if v is None or v is pd.NA or (isinstance(v, float) and math.isnan(v)) else float(bool(v))).to_numpy(dtype=float)
    return to_text(series)


def compare(expected: pd.Series, actual: pd.Series, col_type: str, *, rows: pd.Series | None = None,
            decimals: int | None = None, max_samples: int = 8) -> ColumnCheck:
    k = kind_of(col_type)
    e = vectorize(expected.reset_index(drop=True), col_type)
    a = vectorize(actual.reset_index(drop=True), col_type if k != "text" else "text")
    if k == "text":
        e_null = np.array([v is None for v in e])
        a_null = np.array([v is None for v in a])
        eq = np.array([x == y for x, y in zip(e, a)]) & ~e_null
    else:
        e_null = np.isnan(e)
        a_null = np.isnan(a)
        if k == "num":
            d = decimals if decimals is not None else decimals_of(expected)
            with np.errstate(invalid="ignore"):
                eq = (np.abs(np.round(a, d) - e) <= 0.5 * 10 ** (-d) + 1e-9) | (np.abs(a - e) <= 1e-9 * np.maximum(1, np.abs(e)))
        else:
            eq = a == e
        eq = eq & ~e_null & ~a_null
    compared = int((~e_null).sum())
    matched = int(eq.sum())
    samples = []
    if compared and matched < compared:
        bad = np.where(~e_null & ~eq)[0][:max_samples]
        rr = rows.reset_index(drop=True) if rows is not None else None
        for i in bad:
            samples.append({"row": int(rr.iloc[i]) if rr is not None else int(i), "expected": _jsonable(expected.iloc[i]),
                            "actual": _jsonable(actual.iloc[i])})
    return ColumnCheck(compared=compared, matched=matched, rate=(matched / compared) if compared else None,
                       expected_nulls=int(e_null.sum()), samples=samples)


def _jsonable(v: Any) -> Any:
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10]
    if isinstance(v, (int, float, bool, str)):
        return v
    return str(v)


class DuckEval:
    """Bảng của mô hình nạp vào DuckDB in-memory để chạy SQL biên dịch từ spec (cùng SQL với view Postgres)."""

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        import duckdb

        self.con = duckdb.connect()
        self.frames = frames
        for name, df in frames.items():
            self.con.register(f"__df_{name}", df)
            self.con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM "__df_{name}"')
            self.con.unregister(f"__df_{name}")

    def run(self, spec: ModelSpec, *, override: dict[str, Rule] | None = None, only: list[str] | None = None) -> tuple[pd.DataFrame, dict[str, str]]:
        cv = compile_view(spec, override=override, only=only)
        df = self.con.execute(cv.sql).fetch_df()
        return df, cv.problems

    def check_rule(self, spec: ModelSpec, column: str, rule: Rule) -> tuple[ColumnCheck | None, str | None]:
        col = spec.column(column)
        main = spec.main()
        if col is None or main is None:
            return None, L("không có cột", "no such column")
        try:
            out, problems = self.run(spec, override={column: rule}, only=[column])
        except Exception as e:  # SQL lỗi (vd kiểu dữ liệu không hợp) -> báo lại cho người dùng/AI
            return None, f"{type(e).__name__}: {str(e)[:300]}"
        if column in problems:
            return None, problems[column]
        base = self.frames[main.key]
        if col.name not in base.columns:
            return None, L("sheet chính không có cột này để đối chiếu", "the main sheet has no such column to compare against")
        calc = out[["source_row", column]].rename(columns={column: "__calc"})
        m = base[["_row", col.name]].merge(calc, left_on="_row", right_on="source_row", how="left")
        return compare(m[col.name], m["__calc"], col.type, rows=m["_row"]), None

    def check_all(self, spec: ModelSpec) -> tuple[dict[str, ColumnCheck], dict[str, str]]:
        main = spec.main()
        if main is None:
            return {}, {}
        out, problems = self.run(spec)
        checks = compare_frames(spec, self.frames[main.key], out)
        # công thức phụ thuộc cột chưa xác định -> thử riêng với đầu vào lấy từ Excel
        from modeling.compile import formula_deps

        for c in spec.columns:
            if c.rule.kind != "formula":
                continue
            try:
                deps = formula_deps(spec, c)
            except Exception:
                continue
            # đầu vào chưa xác định hoặc chưa khớp Excel -> chấm riêng logic công thức với đầu vào lấy từ Excel
            waiting = [d for d in deps if (spec.column(d) and spec.column(d).rule.kind == "unresolved")
                       or (spec.column(d) and spec.column(d).rule.kind != "input" and checks.get(d) is not None
                           and (checks[d].rate is None or checks[d].rate < 0.98))]
            if not waiting:
                continue
            override = {d: Rule(kind="input") for d in deps}
            chk, err = self.check_rule(spec.model_copy(update={"columns": [x.model_copy(update={"rule": override[x.name]}) if x.name in override else x
                                                                           for x in spec.columns]}), c.name, c.rule)
            if chk is not None:
                chk.basis = "isolated"
                chk.waiting_on = waiting
                checks[c.name] = chk
        return checks, problems


def compare_frames(spec: ModelSpec, base: pd.DataFrame, out: pd.DataFrame) -> dict[str, ColumnCheck]:
    """base: sheet chính (_row + cột gốc); out: kết quả view (source_row + cột tính lại)."""
    calc = out.rename(columns={c: f"{c}__calc" for c in out.columns if c != "source_row"})
    merged = base.merge(calc, left_on="_row", right_on="source_row", how="left")
    checks: dict[str, ColumnCheck] = {}
    for c in spec.columns:
        if c.name not in base.columns or f"{c.name}__calc" not in merged.columns:
            continue
        checks[c.name] = compare(merged[c.name], merged[f"{c.name}__calc"], c.type, rows=merged["_row"])
    return checks
