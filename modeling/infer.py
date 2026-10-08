"""Phân tích tự động: file -> bảng -> sheet chính -> quan hệ -> logic từng cột đầu ra (kèm bằng chứng, độ tin cậy).

Thứ tự ưu tiên bằng chứng cho mỗi cột của sheet chính:
1. Công thức Excel trong cột (đọc trực tiếp, đúng tuyệt đối về ý đồ) — modeling/excel_formula.py
2. Mẫu dữ liệu: thử có hệ thống các phép tra cứu / đếm / tổng / min / max (+ 1 điều kiện) qua từng quan hệ và các phép
   tính trong dòng (hiệu ngày, "số ngày đến ngày báo cáo", cộng/trừ cột), giữ phép tái tạo khớp nhiều giá trị nhất.
   Pivot table trong file được dịch thành ứng viên riêng và được cộng điểm.
3. AI (nếu có model): đề xuất quy tắc từ ý nghĩa tên cột cho các cột còn lại — LUÔN được kiểm chứng trên dữ liệu
   trước khi hiển thị (tỉ lệ khớp đi kèm đề xuất).
Không bước nào tự "xác nhận": mọi thứ ở trạng thái proposed cho tới khi người dùng duyệt.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from analytics.tables import _coerce, sql_identifier
from graph.resolve import fold_text
from modeling import expr as E
from modeling import keys as K
from modeling.evaluate import DuckEval, compare, decimals_of, kind_of, to_days, to_float, to_text
from modeling.excel_formula import JoinHint, SheetMap, Translator, references_sheets
from modeling.spec import (Candidate, ColumnCheck, Condition, Evidence, KeyPair, ModelParameters, ModelSpec, Notice,
                           OutputColumn, PivotInfo, Relationship, Rule, SheetColumn, TableSpec, Transform, now_iso)
from modeling.workbook import SheetTable, Workbook, read_csv_table, read_workbook

logger = logging.getLogger(__name__)

ProgressFn = Callable[[int, int, str], None]


@dataclass
class SourceFile:
    document_id: str | None
    filename: str
    title: str
    path: str
    source_type: str          # xlsx | csv


@dataclass
class Loaded:
    spec: TableSpec
    df: pd.DataFrame          # cột SQL + _row
    sheet: SheetTable
    workbook: Workbook


# ===================================================================================== bảng
_MAIN_WORDS = ("summary", "report", "dashboard", "overview", "output", "result", "tong hop", "bao cao", "ket qua", "tom tat")


def _examples(s: pd.Series) -> list[str]:
    out = []
    for v in s.dropna().head(200):
        t = v.isoformat()[:10] if hasattr(v, "isoformat") and not isinstance(v, str) else str(v)
        if t and t not in out:
            out.append(t[:60])
        if len(out) >= 3:
            break
    return out


def build_table(sheet: SheetTable, wb: Workbook, src: SourceFile, used: set[str], *, multi_file: bool) -> Loaded:
    raw = pd.DataFrame(sheet.rows, columns=sheet.labels)
    names: set[str] = set()
    out = pd.DataFrame(index=raw.index)
    cols: list[SheetColumn] = []
    letters = sheet.letters
    for i, label in enumerate(sheet.labels):
        name = sql_identifier(label, names)
        if name in ("_row", "source_row"):
            name = sql_identifier(f"{label} col", names)
        series, kind = _coerce(raw[label])
        if kind == "percent":
            kind = "double"
        out[name] = series
        pat, n, total = sheet.dominant_formula(i)
        cols.append(SheetColumn(name=name, label=label, group=sheet.groups[i], type=kind, letter=letters[i],
                                examples=_examples(raw[label]), distinct=int(series.nunique(dropna=True)),
                                nulls=int(series.isna().sum()), formula_count=total, formula=pat))
    out["_row"] = sheet.excel_rows
    title = sheet.sheet if not multi_file else f"{src.title} · {sheet.sheet}"
    key = sql_identifier(sheet.sheet if not multi_file else f"{src.title} {sheet.sheet}", used, fallback="sheet")
    notes = []
    for n in sheet.notes:
        if n.startswith("truncated:"):
            notes.append(n)
        else:
            notes.append(f"note:{n}")
    unique = [c.name for c in cols if c.type in ("text", "integer") and c.distinct == len(out) and len(out) > 1 and c.nulls == 0]
    spec = TableSpec(key=key, title=title, document_id=src.document_id, filename=src.filename, sheet=sheet.sheet,
                     columns=cols, row_count=len(out), header_row=sheet.header_row,
                     data_range=f"{letters[0]}{sheet.header_row}:{letters[-1]}{sheet.excel_rows[-1] if sheet.excel_rows else sheet.header_row}",
                     formula_count=sum(c.formula_count for c in cols), notes=notes, unique_keys=unique)
    return Loaded(spec=spec, df=out.reset_index(drop=True), sheet=sheet, workbook=wb)


def load_sources(sources: list[SourceFile], *, max_rows: int = 1_000_000) -> tuple[list[Loaded], list[PivotInfo]]:
    used: set[str] = set()
    loaded: list[Loaded] = []
    pivots: list[PivotInfo] = []
    multi = len(sources) > 1
    for src in sources:
        wb = read_workbook(src.path, max_rows=max_rows) if src.source_type in ("xlsx", "xlsm") else read_csv_table(src.path, src.title, max_rows=max_rows)
        pivots.extend(wb.pivots)
        for sheet in wb.tables:
            loaded.append(build_table(sheet, wb, src, used, multi_file=multi))
    return loaded, pivots


def _cross_formula_share(t: Loaded) -> tuple[float, set[str]]:
    cols_with = 0
    refs: set[str] = set()
    for c in t.spec.columns:
        if c.formula:
            others = [s for s in references_sheets(c.formula) if s != t.sheet.sheet]
            if others:
                cols_with += 1
                refs.update(others)
    return (cols_with / max(1, len(t.spec.columns))), refs


def choose_main(tables: list[Loaded]) -> str | None:
    if not tables:
        return None
    referenced: Counter[str] = Counter()
    shares: dict[str, float] = {}
    for t in tables:
        share, refs = _cross_formula_share(t)
        shares[t.spec.key] = share
        for r in refs:
            referenced[r] += 1
    best, best_score = None, -1e9
    for t in tables:
        title = fold_text(t.sheet.sheet)
        score = 0.0
        if any(w in title for w in _MAIN_WORDS):
            score += 3
        if t.sheet.sheet_index == 0:
            score += 2
        if any(g for g in t.sheet.groups):
            score += 1.5
        if any(n.startswith("note:") for n in t.spec.notes):
            score += 0.5
        score += 6 * shares[t.spec.key]
        score -= 1.5 * referenced.get(t.sheet.sheet, 0)
        if len(t.spec.columns) < 3:
            score -= 3
        t.spec.main_score = round(score, 2)
        if score > best_score:
            best, best_score = t.spec.key, score
    return best


# ===================================================================================== quan hệ
def _key_candidates(t: Loaded) -> list[str]:
    out = []
    n = max(1, len(t.df))
    for c in t.spec.columns:
        if c.type not in ("text", "integer"):
            continue
        if c.distinct < 5 or c.nulls > 0.5 * n:
            continue
        s = t.df[c.name].dropna().astype(str)
        if not s.empty and s.str.len().mean() > 40:
            continue
        out.append(c.name)
    return out


def _norm_sets(t: Loaded, col: str) -> list[tuple[pd.Series, set[str]]]:
    res = []
    for ts in K.CANDIDATE_TRANSFORMS:
        s = K.apply_series(t.df[col], ts)
        res.append((s, set(s.dropna())))
    return res


def _label_sim(a: str, b: str) -> float:
    ta = set(re.findall(r"[a-z0-9]+", fold_text(a)))
    tb = set(re.findall(r"[a-z0-9]+", fold_text(b)))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


_ID_WORDS = {"id", "no", "nr", "number", "num", "code", "key", "ma", "so", "site", "ref"}


def _idlike(label: str) -> bool:
    toks = set(re.findall(r"[a-z0-9]+", fold_text(label)))
    return "#" in label or bool(toks & _ID_WORDS)


def discover_relationships(main: Loaded, others: list[Loaded]) -> list[Relationship]:
    mcands = _key_candidates(main)
    msets = {c: _norm_sets(main, c) for c in mcands}
    labels_m = {c.name: c.label for c in main.spec.columns}
    found: dict[str, list[tuple[float, Any]]] = {}
    for o in others:
        labels_o = {c.name: c.label for c in o.spec.columns}
        options: list[tuple[float, Any]] = []
        for oc in _key_candidates(o):
            osets = _norm_sets(o, oc)
            for j, (oser, oset) in enumerate(osets):
                if len(oset) < 3:
                    continue
                for mc in mcands:
                    for i, (mser, mset) in enumerate(msets[mc]):
                        if len(mset) < 5:
                            continue
                        inter = len(mset & oset)
                        if inter < 5:
                            continue
                        src_cont = inter / len(oset)
                        if src_cont < 0.3:
                            continue
                        penalty = 1 - 0.06 * (len(K.CANDIDATE_TRANSFORMS[i]) + len(K.CANDIDATE_TRANSFORMS[j]))
                        sim = _label_sim(labels_m[mc], labels_o[oc])
                        idb = 1.3 if (_idlike(labels_m[mc]) or _idlike(labels_o[oc])) else 1.0
                        score = inter * (0.5 + 0.5 * src_cont) * penalty * (1 + 0.15 * sim) * idb
                        options.append((score, (mc, oc, i, j, inter, src_cont, mser, oser, mset)))
        if options:
            options.sort(key=lambda x: -x[0])
            found[o.spec.key] = options
    # khoá của sheet chính được nhiều bảng dùng nhất -> ưu tiên khi chênh lệch điểm nhỏ (tránh nối nhầm qua cột trùng hợp)
    votes = Counter(opts[0][1][0] for opts in found.values())
    dominant = votes.most_common(1)[0][0] if votes else None
    rels: list[Relationship] = []
    for o in others:
        options = found.get(o.spec.key)
        if not options:
            continue
        best = options[0]
        if dominant and best[1][0] != dominant:
            alt = next((x for x in options if x[1][0] == dominant), None)
            if alt is not None and alt[0] >= 0.6 * best[0]:
                best = alt
        _, (mc, oc, i, j, inter, src_cont, mser, oser, mset) = best
        okeys = oser.dropna()
        one = bool(okeys.is_unique)
        oset = set(okeys)
        coverage = float(mser.isin(oset).mean()) if len(mser) else 0.0
        conf = min(0.95, 0.45 + 0.35 * src_cont + 0.2 * coverage)
        rels.append(Relationship(
            id=f"r_{o.spec.key}", source_table=o.spec.key, target_table=main.spec.key,
            keys=[KeyPair(base_column=mc, source_column=oc, base_transforms=list(K.CANDIDATE_TRANSFORMS[i]),
                          source_transforms=list(K.CANDIDATE_TRANSFORMS[j]))],
            cardinality="one_to_one" if one else "many_to_one", coverage=round(coverage, 4), source_match=round(src_cont, 4),
            matched_keys=inter, confidence=round(conf, 3),
            evidence=[Evidence(kind="data_match", detail={"matched": inter, "compared": len(oset), "what": "keys"})]))
    return rels


def _merge_join_hints(spec: ModelSpec, main_key: str, hints: list[JoinHint], formula: str) -> Relationship:
    """Khoá nối đọc từ công thức Excel -> quan hệ (tạo mới hoặc gộp bằng chứng vào quan hệ đã dò)."""
    src = hints[0].source_table
    pairs = [(h.base_column, h.source_column) for h in hints]
    for r in spec.relationships:
        if r.source_table == src and [(k.base_column, k.source_column) for k in r.keys] == pairs:
            if not any(e.kind == "formula" for e in r.evidence):
                r.evidence.insert(0, Evidence(kind="formula", detail={"formula": formula}))
            r.confidence = max(r.confidence, 0.95)
            return r
    rid = f"r_{src}"
    n = 2
    while spec.relationship(rid):
        rid = f"r_{src}_{n}"
        n += 1
    rel = Relationship(id=rid, source_table=src, target_table=main_key,
                       keys=[KeyPair(base_column=b, source_column=s) for b, s in pairs],
                       cardinality="many_to_one", confidence=0.95, evidence=[Evidence(kind="formula", detail={"formula": formula})])
    spec.relationships.append(rel)
    return rel


def measure_relationship(rel: Relationship, frames: dict[str, pd.DataFrame]) -> None:
    """Tính lại coverage/cardinality của 1 quan hệ (sau khi tạo từ công thức hoặc người dùng sửa)."""
    m, o = frames.get(rel.target_table), frames.get(rel.source_table)
    if m is None or o is None or not rel.keys:
        return
    try:
        mk = pd.Series(list(zip(*[K.apply_series(m[k.base_column], k.base_transforms) for k in rel.keys])))
        ok = pd.Series(list(zip(*[K.apply_series(o[k.source_column], k.source_transforms) for k in rel.keys])))
    except KeyError:
        return
    valid_o = ok[ok.map(lambda t: all(x is not None for x in t))]
    oset = set(valid_o)
    rel.cardinality = "one_to_one" if valid_o.is_unique else "many_to_one"
    rel.coverage = round(float(mk.isin(oset).mean()), 4) if len(mk) else 0.0
    mset = set(mk)
    inter = len(oset & mset)
    rel.matched_keys = inter
    rel.source_match = round(inter / len(oset), 4) if oset else 0.0


# ===================================================================================== dò logic cột
@dataclass
class _Cand:
    rule: Rule
    values: np.ndarray        # đã vector hoá theo kind (num: float, date: days, text: object)
    kind: str                 # num | date | text | bool
    evidence: list[Evidence] = field(default_factory=list)
    bonus: float = 0.0


def _vec(series: pd.Series, col_type: str) -> tuple[np.ndarray, str]:
    k = kind_of(col_type)
    if k == "num":
        return to_float(series), k
    if k == "date":
        return to_days(series), k
    if k == "bool":
        return series.map(lambda v: np.nan if v is None or v is pd.NA or (isinstance(v, float) and math.isnan(v)) else float(bool(v))).to_numpy(dtype=float), k
    return to_text(series), k


class _Expected:
    def __init__(self, df: pd.DataFrame, col: SheetColumn) -> None:
        self.col = col
        self.values, self.kind = _vec(df[col.name], col.type)
        if self.kind == "text":
            self.mask = np.array([v is not None for v in self.values])
        else:
            self.mask = ~np.isnan(self.values)
        self.n = int(self.mask.sum())
        self.decimals = decimals_of(df[col.name]) if self.kind == "num" else 0

    def score(self, cands: list[_Cand]) -> list[tuple[float, int, int, bool]]:
        """-> [(rate, matched, compared, needs_round)] cho từng ứng viên cùng kind."""
        out = []
        if self.n == 0:
            return [(0.0, 0, 0, False)] * len(cands)
        if self.kind == "text":
            e = self.values
            for c in cands:
                eq = np.fromiter((x is not None and x == y for x, y in zip(e, c.values)), dtype=bool, count=len(e))
                m = int(eq.sum())
                out.append((m / self.n, m, self.n, False))
            return out
        mat = np.vstack([c.values for c in cands]) if cands else np.empty((0, len(self.values)))
        e = self.values
        with np.errstate(invalid="ignore"):
            exact = (np.abs(mat - e) <= 1e-9 * np.maximum(1, np.abs(e))) & self.mask
            if self.kind == "num":
                d = self.decimals
                rounded = (np.abs(np.round(mat, d) - e) <= 0.5 * 10 ** (-d) + 1e-9) & self.mask
            else:
                rounded = exact
        me = exact.sum(axis=1)
        mr = rounded.sum(axis=1)
        # cột nhiều số 0: khớp "0 = 0" rất dễ trùng hợp -> tỉ lệ tính cả trên các dòng khác 0
        nz = self.mask & (np.abs(np.nan_to_num(e)) > 1e-12)
        n_nz = int(nz.sum())
        zero_heavy = self.kind == "num" and n_nz < 0.8 * self.n
        me_nz = (exact & nz).sum(axis=1) if zero_heavy else me
        mr_nz = (rounded & nz).sum(axis=1) if zero_heavy else mr
        for a, b, anz, bnz in zip(me, mr, me_nz, mr_nz):
            use_round = b > a + max(1, 0.02 * self.n)
            m = int(b if use_round else a)
            rate = m / self.n
            if zero_heavy:
                rate = min(rate, (int(bnz if use_round else anz) / n_nz) if n_nz else 0.0)
            out.append((rate, m, self.n, bool(use_round)))
        return out


def _categoricals(t: Loaded, exclude: set[str]) -> list[tuple[str, list[Any]]]:
    out = []
    for c in t.spec.columns:
        if c.name in exclude or c.type not in ("text", "boolean"):
            continue
        if 2 <= c.distinct <= 12:
            vals = t.df[c.name].dropna().value_counts().index.tolist()[:12]
            out.append((c.name, vals))
    return out


def _filters_for(t: Loaded, rel: Relationship) -> list[list[Condition]]:
    keys = {k.source_column for k in rel.keys}
    out: list[list[Condition]] = [[]]
    for col, vals in _categoricals(t, keys):
        for v in vals:
            vv = bool(v) if isinstance(v, (bool, np.bool_)) else (v.item() if hasattr(v, "item") else v)
            out.append([Condition(column=col, op="eq", value=vv)])
            if len(vals) >= 3:
                out.append([Condition(column=col, op="ne", value=vv)])
    return out


def _mask_for(df: pd.DataFrame, conds: list[Condition], types: dict[str, str], as_of: pd.Timestamp | None) -> pd.Series:
    m = pd.Series(True, index=df.index)
    for c in conds:
        s = df[c.column]
        t = types.get(c.column, "text")
        if c.as_of_offset_days is not None:
            if as_of is None:
                return pd.Series(False, index=df.index)
            v: Any = as_of + pd.Timedelta(days=c.as_of_offset_days)
            s = pd.to_datetime(s, errors="coerce").dt.normalize()
        else:
            v = c.value
            if t == "text":
                s = s.astype("string").str.strip().str.upper()
                v = [str(x).strip().upper() for x in v] if isinstance(v, list) else (str(v).strip().upper() if v is not None else None)
            elif t in ("date", "timestamp") and v is not None:
                s = pd.to_datetime(s, errors="coerce").dt.normalize()
                v = [pd.Timestamp(x) for x in v] if isinstance(v, list) else pd.Timestamp(v)
        if c.op == "eq":
            cur = s == v
        elif c.op == "ne":
            cur = s.isna() | (s != v)
        elif c.op == "gt":
            cur = s > v
        elif c.op == "gte":
            cur = s >= v
        elif c.op == "lt":
            cur = s < v
        elif c.op == "lte":
            cur = s <= v
        elif c.op == "in":
            cur = s.isin(v if isinstance(v, list) else [v])
        elif c.op == "not_in":
            cur = s.isna() | ~s.isin(v if isinstance(v, list) else [v])
        elif c.op == "is_null":
            cur = s.isna()
        elif c.op == "not_null":
            cur = s.notna()
        else:
            cur = s.astype("string").str.upper().str.contains(str(c.value or "").upper(), regex=False)
            if c.op == "not_contains":
                cur = ~cur
        m &= cur.fillna(False).astype(bool)
    return m


class ColumnSearch:
    """Sinh ứng viên quy tắc từ dữ liệu cho mọi cột của sheet chính, chấm điểm theo tỉ lệ tái tạo giá trị Excel."""

    def __init__(self, spec: ModelSpec, tables: dict[str, Loaded], as_of: pd.Timestamp | None) -> None:
        self.spec = spec
        self.tables = tables
        self.main = tables[spec.main_table]
        self.as_of = as_of
        self.expected = {c.name: _Expected(self.main.df, c) for c in self.main.spec.columns}
        self.best: dict[str, list[tuple[float, Candidate]]] = {c: [] for c in self.expected}

    # ------------------------------------------------------------------ chấm
    def _offer(self, cands: list[_Cand], only: set[str] | None = None) -> None:
        by_kind: dict[str, list[_Cand]] = {}
        for c in cands:
            by_kind.setdefault(c.kind, []).append(c)
        for name, exp in self.expected.items():
            if only is not None and name not in only:
                continue
            group = by_kind.get(exp.kind)
            if not group or exp.n == 0:
                continue
            for cand, (rate, m, n, rnd) in zip(group, exp.score(group)):
                if rate < 0.3 or m < 3:
                    continue
                rule = cand.rule.model_copy(deep=True)
                if rnd:
                    rule.round_digits = exp.decimals
                sim = _label_sim(exp.col.label, _rule_label(self.spec, rule))
                total = rate + cand.bonus + 0.02 * sim
                check = ColumnCheck(compared=n, matched=m, rate=rate)
                ev = list(cand.evidence) + [Evidence(kind="data_match", detail={"matched": m, "compared": n})]
                if sim >= 0.5:
                    ev.append(Evidence(kind="name_match", detail={"similarity": round(sim, 2)}))
                self.best[name].append((total, Candidate(rule=rule, check=check, evidence=ev, confidence=round(min(0.97, rate * 0.92 + cand.bonus), 3))))

    # ------------------------------------------------------------------ ứng viên theo quan hệ
    def _aligned(self, rel: Relationship) -> tuple[pd.Series, pd.Series] | None:
        o = self.tables.get(rel.source_table)
        if o is None:
            return None
        if len(rel.keys) == 1:
            kp = rel.keys[0]
            mk = K.apply_series(self.main.df[kp.base_column], kp.base_transforms)
            ok = K.apply_series(o.df[kp.source_column], kp.source_transforms)
        else:
            mk = pd.Series(list(zip(*[K.apply_series(self.main.df[k.base_column], k.base_transforms) for k in rel.keys])))
            ok = pd.Series(list(zip(*[K.apply_series(o.df[k.source_column], k.source_transforms) for k in rel.keys])))
        return mk, ok

    def relationship_candidates(self, rel: Relationship) -> None:
        al = self._aligned(rel)
        if al is None:
            return
        mk, ok = al
        o = self.tables[rel.source_table]
        df = o.df.assign(__k=ok.values).dropna(subset=["__k"])
        if df.empty:
            return
        types = {c.name: c.type for c in o.spec.columns}
        keys = {k.source_column for k in rel.keys}
        num_cols = [c.name for c in o.spec.columns if c.type in ("integer", "double") and c.name not in keys]
        date_cols = [c.name for c in o.spec.columns if c.type in ("date", "timestamp")]
        cands: list[_Cand] = []
        # tra cứu: dòng đầu tiên theo thứ tự Excel
        first = df.sort_values("_row").drop_duplicates("__k", keep="first").set_index("__k")
        for c in o.spec.columns:
            if c.name in keys:
                continue
            series = first[c.name].reindex(mk.values)
            vals, kind = _vec(series.reset_index(drop=True), c.type)
            cands.append(_Cand(Rule(kind="lookup", relationship_id=rel.id, table=o.spec.key, column=c.name, func="first"), vals, kind,
                               bonus=0.01))
            if kind == "num":  # lookup số mà Excel không có dòng khớp -> trống; nhiều báo cáo hiển thị 0
                pass
        # gộp theo từng bộ lọc
        for conds in _filters_for(o, rel):
            sub = df[_mask_for(df, conds, types, self.as_of)] if conds else df
            g = sub.groupby("__k", sort=False)
            idx = mk.values
            cnt = g.size().reindex(idx).fillna(0).to_numpy(dtype=float)
            bonus = 0.01 if not conds else 0.0
            cands.append(_Cand(Rule(kind="aggregate", relationship_id=rel.id, table=o.spec.key, func="count", filters=conds, default=0),
                               cnt, "num", bonus=bonus))
            if sub.empty:
                continue
            for col in num_cols:
                agg = g[col].agg(["sum", "mean", "min", "max"]).reindex(idx)
                for fn, key in (("sum", "sum"), ("avg", "mean"), ("min", "min"), ("max", "max")):
                    vals = agg[key].to_numpy(dtype=float)
                    if fn == "sum":
                        vals = np.nan_to_num(vals, nan=0.0)
                    cands.append(_Cand(Rule(kind="aggregate", relationship_id=rel.id, table=o.spec.key, column=col, func=fn, filters=conds,
                                            default=0 if fn == "sum" else None), vals, "num", bonus=bonus))
            for col in date_cols:
                s = pd.to_datetime(sub[col], errors="coerce")
                gd = s.groupby(sub["__k"], sort=False)
                for fn in ("min", "max"):
                    vals = to_days(gd.agg(fn).reindex(idx).reset_index(drop=True))
                    cands.append(_Cand(Rule(kind="aggregate", relationship_id=rel.id, table=o.spec.key, column=col, func=fn, filters=conds),
                                       vals, "date", bonus=bonus))
            if len(cands) > 4000:
                self._offer(cands)
                cands = []
        self._offer(cands)

    def pivot_candidates(self, pivots: list[PivotInfo]) -> None:
        by_sheet = {t.sheet.sheet: t for t in self.tables.values()}
        for p in pivots:
            src = by_sheet.get(p.source_sheet or p.sheet)
            if src is None or not p.rows or not p.values:
                continue
            label_to_name = {c.label.strip().lower(): c.name for c in src.spec.columns}
            row_col = label_to_name.get(p.rows[0].strip().lower())
            rel = next((r for r in self.spec.active_relationships() if r.source_table == src.spec.key and len(r.keys) == 1
                        and r.keys[0].source_column == row_col), None)
            if rel is None:
                continue
            conds: list[Condition] = []
            for f in p.filters:
                fcol = label_to_name.get(f.field.strip().lower())
                if not fcol:
                    continue
                if f.selected:
                    conds.append(Condition(column=fcol, op="in", value=f.selected))
                elif f.excluded:
                    conds.append(Condition(column=fcol, op="not_in", value=f.excluded))
            al = self._aligned(rel)
            if al is None:
                continue
            mk, ok = al
            df = src.df.assign(__k=ok.values).dropna(subset=["__k"])
            types = {c.name: c.type for c in src.spec.columns}
            sub = df[_mask_for(df, conds, types, self.as_of)] if conds else df
            cands = []
            for v in p.values:
                vcol = label_to_name.get(v.field.strip().lower())
                func = v.func if v.func in ("count", "sum", "avg", "min", "max") else None
                if func is None or (func != "count" and not vcol):
                    continue
                g = sub.groupby("__k", sort=False)
                ctype = types.get(vcol, "text") if vcol else "integer"
                if func == "count":
                    vals = g.size().reindex(mk.values).fillna(0).to_numpy(dtype=float)
                    kind = "num"
                    rule = Rule(kind="aggregate", relationship_id=rel.id, table=src.spec.key, func="count", filters=conds, default=0)
                elif ctype in ("date", "timestamp"):
                    s = pd.to_datetime(sub[vcol], errors="coerce").groupby(sub["__k"], sort=False).agg(func)
                    vals = to_days(s.reindex(mk.values).reset_index(drop=True))
                    kind = "date"
                    rule = Rule(kind="aggregate", relationship_id=rel.id, table=src.spec.key, column=vcol, func=func, filters=conds)
                else:
                    agg = g[vcol].agg({"sum": "sum", "avg": "mean", "min": "min", "max": "max"}[func])
                    vals = agg.reindex(mk.values).to_numpy(dtype=float)
                    kind = "num"
                    rule = Rule(kind="aggregate", relationship_id=rel.id, table=src.spec.key, column=vcol, func=func, filters=conds,
                                default=0 if func == "sum" else None)
                summary = f"{v.label or v.func + ' ' + v.field} by {', '.join(p.rows)}"
                cands.append(_Cand(rule, vals, kind, bonus=0.05,
                                   evidence=[Evidence(kind="pivot", detail={"name": p.name, "sheet": p.sheet, "location": p.location, "summary": summary})]))
            self._offer(cands)

    # ------------------------------------------------------------------ công thức trong dòng
    def row_formula_candidates(self, skip: set[str]) -> pd.Timestamp | None:
        m = self.main
        dates = [c for c in m.spec.columns if c.type in ("date", "timestamp")]
        nums = [c for c in m.spec.columns if c.type in ("integer", "double")]
        dvals = {c.name: to_days(m.df[c.name]) for c in dates}
        nvals = {c.name: to_float(m.df[c.name]) for c in nums}
        inferred_as_of: Counter[int] = Counter()
        cands: list[_Cand] = []
        for y in nums:
            if y.name in skip:
                continue
            yv = nvals[y.name]
            ym = ~np.isnan(yv)
            if ym.sum() < 3:
                continue
            for d in dates:
                dv = dvals[d.name]
                both = ym & ~np.isnan(dv)
                if both.sum() < 3:
                    continue
                for sign in (1, -1):
                    s = (dv + yv)[both] if sign == 1 else (dv - yv)[both]
                    vals, counts = np.unique(s, return_counts=True)
                    top = int(np.argmax(counts))
                    if counts[top] >= 0.9 * both.sum():
                        day = int(vals[top])
                        inferred_as_of[day] += int(counts[top])
                        expr_ = f"DAYS(AS_OF(), [{d.label}])" if sign == 1 else f"DAYS([{d.label}], AS_OF())"
                        cands.append(_Cand(Rule(kind="formula", expression=expr_), (day - dv) if sign == 1 else (dv - day), "num",
                                           bonus=0.02))
        as_of = pd.Timestamp("1970-01-01") + pd.Timedelta(days=inferred_as_of.most_common(1)[0][0]) if inferred_as_of else None
        for i, a in enumerate(dates):
            for b in dates:
                if a.name == b.name:
                    continue
                cands.append(_Cand(Rule(kind="formula", expression=f"DAYS([{a.label}], [{b.label}])"), dvals[a.name] - dvals[b.name], "num", bonus=0.02))
        for i, a in enumerate(nums):
            for b in nums[i + 1:]:
                av, bv = nvals[a.name], nvals[b.name]
                cands.append(_Cand(Rule(kind="formula", expression=f"[{a.label}] + [{b.label}]"), av + bv, "num"))
                cands.append(_Cand(Rule(kind="formula", expression=f"[{a.label}] - [{b.label}]"), av - bv, "num"))
                cands.append(_Cand(Rule(kind="formula", expression=f"[{b.label}] - [{a.label}]"), bv - av, "num"))
                cands.append(_Cand(Rule(kind="formula", expression=f"[{a.label}] * [{b.label}]"), av * bv, "num"))
                with np.errstate(divide="ignore", invalid="ignore"):
                    cands.append(_Cand(Rule(kind="formula", expression=f"[{a.label}] / [{b.label}]"), np.where(bv != 0, av / bv, np.nan), "num"))
                    cands.append(_Cand(Rule(kind="formula", expression=f"[{b.label}] / [{a.label}]"), np.where(av != 0, bv / av, np.nan), "num"))
        texts = [c for c in m.spec.columns if c.type == "text"]
        for t in texts:
            digits = K.apply_series(m.df[t.name], [Transform(op="number_part")])
            cands.append(_Cand(Rule(kind="formula", expression=f"VALUE(DIGITS([{t.label}]))"), to_float(digits), "num", bonus=0.01))
        targets = {c.name for c in nums if c.name not in skip}
        # công thức không được tự tham chiếu chính cột đích
        for name in targets:
            label = self.expected[name].col.label
            own = [c for c in cands if f"[{label}]" not in (c.rule.expression or "")]
            self._offer(own, only={name})
        return as_of

    def results(self) -> dict[str, list[Candidate]]:
        out = {}
        for name, lst in self.best.items():
            lst.sort(key=lambda x: -x[0])
            seen: set[str] = set()
            uniq = []
            for _score, c in lst:
                sig = c.rule.model_dump_json()
                if sig in seen:
                    continue
                seen.add(sig)
                uniq.append(c)
                if len(uniq) >= 4:
                    break
            out[name] = uniq
        return out


def _rule_label(spec: ModelSpec, r: Rule) -> str:
    if r.kind in ("lookup", "aggregate"):
        t = spec.table(r.table)
        c = next((c for c in t.columns if c.name == r.column), None) if t and r.column else None
        words = {"count": "count number", "sum": "total sum", "avg": "average", "min": "min first earliest", "max": "max last latest"}
        return f"{c.label if c else ''} {words.get(r.func or '', '')} {t.title if t else ''}"
    return r.expression or ""


# ===================================================================================== điều phối
def analyze(sources: list[SourceFile], *, llm_propose: Callable[[ModelSpec, list[str], DuckEval], dict[str, list[Candidate]]] | None = None,
            max_rows: int = 1_000_000, progress: ProgressFn | None = None, view_name: str | None = None,
            main_override: str | None = None) -> tuple[ModelSpec, dict[str, pd.DataFrame]]:
    def step(i: int, note: str) -> None:
        if progress:
            progress(i, 7, note)

    step(0, "read")
    loaded, pivots = load_sources(sources, max_rows=max_rows)
    spec = ModelSpec(analyzed_at=now_iso())
    if not loaded:
        spec.notices.append(Notice(code="no_tables", level="error"))
        return spec, {}
    tables = {t.spec.key: t for t in loaded}
    for t in loaded:
        t.spec.pivots = [p for p in pivots if p.sheet == t.sheet.sheet]
        spec.tables.append(t.spec)
    frames = {k: t.df for k, t in tables.items()}
    step(1, "main")
    main_key = main_override if main_override in tables else choose_main(loaded)
    spec.main_table = main_key
    main = tables[main_key]
    main.spec.role = "main"
    spec.view_name = view_name or f"vw_{main.spec.key}"[:60]
    if pivots:
        spec.notices.append(Notice(code="pivots_found", params={"count": len(pivots)}))
    for t in loaded:
        for n in t.spec.notes:
            if n.startswith("truncated:"):
                spec.notices.append(Notice(code="truncated", level="warning", params={"table": t.spec.title, "rows": n.split(":")[1]}))

    step(2, "relationships")
    others = [t for t in loaded if t.spec.key != main_key]
    spec.relationships = discover_relationships(main, others)

    # công thức trong sheet chính
    step(3, "formulas")
    sheet_maps = {t.sheet.sheet: SheetMap(table=t.spec.key, columns={t.sheet.first_col + i: (c.name, c.label) for i, c in enumerate(t.spec.columns)},
                                          first_row=t.sheet.excel_rows[0] if t.sheet.excel_rows else t.sheet.header_row + 1,
                                          last_row=t.sheet.excel_rows[-1] if t.sheet.excel_rows else t.sheet.header_row + 1)
                  for t in loaded if t.workbook is main.workbook}
    tr = Translator(sheet_maps, main.sheet.sheet, main.workbook.cell_value)
    first_row = main.sheet.excel_rows[0] if main.sheet.excel_rows else main.sheet.header_row + 1
    formula_rules: dict[str, tuple[Rule | None, Evidence, str | None]] = {}
    total_formulas = 0
    for i, c in enumerate(main.spec.columns):
        if not c.formula:
            continue
        total_formulas += c.formula_count
        share = c.formula_count / max(1, len(main.df))
        t = tr.translate(c.formula, first_row)
        ev = Evidence(kind="formula", detail={"formula": c.formula, "cell": f"{c.letter}{first_row}", "share": round(share, 3)})
        if t.rule is not None and t.joins:
            rel = _merge_join_hints(spec, main_key, t.joins, c.formula)
            measure_relationship(rel, frames)
            t.rule.relationship_id = rel.id
        formula_rules[c.name] = (t.rule, ev, t.error)
    spec.notices.append(Notice(code="main_has_formulas", params={"count": total_formulas}) if total_formulas
                        else Notice(code="main_no_formulas", level="warning"))

    # cột khoá + cột dùng nối
    key_cols = {k.base_column for r in spec.relationships for k in r.keys}
    for kc in key_cols:
        c = next((c for c in main.spec.columns if c.name == kc), None)
        if c is not None and c.distinct < len(main.df):
            spec.notices.append(Notice(code="duplicate_keys", level="info", params={"column": c.label, "rows": len(main.df), "distinct": c.distinct}))
            break

    step(4, "search")
    search = ColumnSearch(spec, tables, None)
    formula_done = {n for n, (r, _e, _err) in formula_rules.items() if r is not None}
    as_of = search.row_formula_candidates(skip=formula_done)
    if as_of is not None:
        spec.parameters = ModelParameters(as_of_date=as_of.date().isoformat(), as_of_source="inferred")
        spec.notices.append(Notice(code="as_of_inferred", params={"date": as_of.date().isoformat()}))
    search.as_of = as_of
    for rel in spec.relationships:
        search.relationship_candidates(rel)
    search.pivot_candidates(pivots)
    found = search.results()

    step(5, "assemble")
    for c in main.spec.columns:
        out = OutputColumn(name=c.name, label=c.label, group=c.group, type=c.type, rule=Rule(kind="unresolved"),
                           excel_formula=c.formula, is_key=c.name in key_cols)
        cands = found.get(c.name, [])
        f_rule, f_ev, f_err = formula_rules.get(c.name, (None, None, None))
        if f_rule is not None:
            out.rule = f_rule
            out.evidence = [f_ev]
            out.confidence = 0.95
            out.alternatives = [x for x in cands if x.rule != f_rule][:2]
        elif c.name in key_cols:
            out.rule = Rule(kind="input")
            out.confidence = 0.95
            out.evidence = [Evidence(kind="structure", note="key")]
        elif cands and (cands[0].check.rate or 0) >= 0.5 and not (c.type == "text" and (cands[0].check.rate or 0) < 0.98):
            best = cands[0]
            out.rule = best.rule
            out.evidence = best.evidence
            out.confidence = best.confidence
            out.alternatives = cands[1:]
        elif c.type == "text" or (c.type == "boolean"):
            out.rule = Rule(kind="input")
            out.confidence = 0.7
            out.evidence = [Evidence(kind="structure", note="descriptive")]
            out.alternatives = cands[:3]
        else:
            out.alternatives = cands[:3]
            if f_ev is not None:
                out.evidence = [f_ev]
        if f_err and f_ev is not None:
            out.evidence = [e for e in out.evidence if e.kind != "formula"] + [f_ev.model_copy(update={"note": f_err})]
        spec.columns.append(out)

    step(6, "ai")
    unresolved = [c.name for c in spec.columns if c.rule.kind == "unresolved" or (c.rule.kind not in ("input",) and c.confidence < 0.85
                                                                                     and not any(e.kind == "formula" for e in c.evidence))]
    ev = DuckEval(frames)
    if llm_propose is not None and unresolved:
        try:
            proposals = llm_propose(spec, unresolved, ev)
        except Exception as e:  # AI lỗi không làm hỏng phân tích
            logger.warning("AI đề xuất quy tắc lỗi: %s", e)
            proposals = {}
        for name, cands in proposals.items():
            col = spec.column(name)
            if col is None or not cands:
                continue
            best = cands[0]
            cur_rate = col.check.rate if col.check else (col.alternatives[0].check.rate if col.alternatives and col.alternatives[0].check else None)
            best_rate = best.check.rate if best.check else None
            if col.rule.kind == "unresolved" or (best_rate is not None and (cur_rate is None or best_rate > (col.confidence or 0))):
                if col.rule.kind != "unresolved":
                    col.alternatives.insert(0, Candidate(rule=col.rule, check=col.check, evidence=col.evidence, confidence=col.confidence))
                col.rule = best.rule
                col.evidence = best.evidence
                col.confidence = best.confidence
                col.alternatives = cands[1:] + col.alternatives
            else:
                col.alternatives = col.alternatives + cands
            col.alternatives = col.alternatives[:4]
    elif llm_propose is None:
        spec.notices.append(Notice(code="no_llm", level="info"))

    # kiểm chứng toàn bộ bằng đúng SQL của view
    checks, problems = ev.check_all(spec)
    for c in spec.columns:
        c.check = checks.get(c.name)
        c.problem = problems.get(c.name) if c.rule.kind != "unresolved" else None
        if c.rule.kind in ("lookup", "aggregate", "formula") and c.check and c.check.rate is not None and not any(e.kind == "formula" for e in c.evidence):
            c.confidence = round(min(c.confidence or 1, 0.4 + 0.55 * c.check.rate), 3)
    calc = [c for c in spec.columns if c.rule.kind not in ("input", "constant")]
    bad = [c for c in calc if c.rule.kind == "unresolved" or (c.check and c.check.rate is not None and c.check.rate < 0.9)]
    if bad:
        spec.notices.append(Notice(code="unmatched_columns", level="warning", params={"count": len(bad)}))
    linked = {r.source_table for r in spec.relationships}
    unlinked = [t for t in spec.tables if t.key != main_key and t.key not in linked]
    for t in spec.tables:
        if t.key != main_key:
            t.role = "source" if t.key in linked else "ignored"
    if unlinked:
        spec.notices.append(Notice(code="unlinked_tables", level="info", params={"count": len(unlinked)}))
    step(7, "done")
    return spec, frames
