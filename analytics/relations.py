"""Quan hệ giữa các bảng số liệu của workspace — mọi file, mọi sheet, mọi cặp bảng (không chỉ hình sao quanh 1 bảng chính).

Dò bằng DỮ LIỆU, không LLM:
1. Cột ứng viên khoá của mỗi bảng: text/integer, >= 5 giá trị khác nhau, <= 50% rỗng, giá trị ngắn; cột số đo
   (role measure) chỉ được xét khi tên cột giống mã (id, code, mã, số...).
2. Mỗi cột ứng viên được chuẩn hoá theo từng tổ hợp phép trong modeling.keys.CANDIDATE_TRANSFORMS (so trực tiếp,
   chỉ phần số, phần trước dấu phân cách...) -> tập giá trị. Cùng định nghĩa chuẩn hoá được dịch sang SQL khi nối.
3. Mỗi cặp bảng: thử mọi cặp cột x phép chuẩn hoá, đếm số giá trị trùng; giữ cặp điểm cao nhất nếu đủ chặt
   (>= MIN_MATCH giá trị trùng, >= MIN_CONTAIN tập nhỏ hơn nằm trong tập lớn hơn).
4. Độ tin cậy từ tỉ lệ trùng, số giá trị trùng, tên cột giống mã/giống nhau; trừ điểm khi phải chuẩn hoá nhiều.
   Loại: "chỉ lấy phần số" giữa 2 họ mã khác tiền tố (NCC001 vs SP001), và khớp chủ yếu bằng số 1–2 chữ số (5, 10, 20 —
   trùng ngẫu nhiên giữa số lượng, số lần khám, ngưỡng tiền...).
5. Các cột nối với nhau gom thành NHÓM KHOÁ CHUNG (union-find): "Site #", "Site No", "Site Number" ở 8 sheet = 1 nhóm —
   gửi cho model viết SQL theo nhóm (mỗi cột 1 biểu thức khoá chuẩn), dựng thực thể theo nhóm (knowledge/table_entities.py).
Kết quả lưu table_relationships ở trạng thái proposed; quyết định approved/rejected của người dùng được giữ khi dò lại.
Bảng sinh từ mô hình dữ liệu ("Data model · ...") là bảng phái sinh -> không đưa vào dò.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import session_scope
from core.logging import log_event
from core.models import DataTable, Document, TableRelationship, utcnow
from graph.resolve import fold_text
from modeling import keys as K
from modeling.spec import Transform

logger = logging.getLogger(__name__)

MIN_MATCH = 5
MIN_CONTAIN = 0.5
MIN_CONFIDENCE = 0.5
MAX_DISTINCT = 200_000
DERIVED_PREFIX = "Data model"
_ID_WORDS = {"id", "no", "nr", "number", "num", "code", "key", "ma", "so", "ref", "sku"}

Progress = Callable[[int, int], None]


@dataclass
class _Col:
    table_id: str
    name: str
    label: str
    type: str
    sets: list[set[str]]  # theo CANDIDATE_TRANSFORMS
    unique: list[bool]
    prefix: str | None = None  # tiền tố chữ chung của mã ("NCC" trong NCC001) nếu đa số giá trị có


@dataclass
class Found:
    left: _Col
    right: _Col
    li: int
    ri: int
    matched: int
    left_coverage: float
    right_coverage: float
    cardinality: str
    confidence: float


def idlike(label: str) -> bool:
    toks = set(re.findall(r"[a-z0-9]+", fold_text(label)))
    return "#" in label or bool(toks & _ID_WORDS)


def _label_sim(a: str, b: str) -> float:
    ta, tb = set(re.findall(r"[a-z0-9]+", fold_text(a))), set(re.findall(r"[a-z0-9]+", fold_text(b)))
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def key_columns(df: pd.DataFrame, columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    n = max(1, len(df))
    out = []
    for c in columns:
        name = c.get("name")
        if name not in df.columns or c.get("type") not in ("text", "integer"):
            continue
        label = str(c.get("original") or name)
        if c.get("role") == "measure" and not idlike(label):
            continue
        s = df[name].dropna()
        if len(s) < 0.5 * n or s.nunique() < 5:
            continue
        if s.astype(str).str.len().mean() > 40:
            continue
        out.append(c)
    return out


def column_profile(table_id: str, df: pd.DataFrame, c: dict[str, Any]) -> _Col:
    sets, unique = [], []
    for ts in K.CANDIDATE_TRANSFORMS:
        norm = K.apply_series(df[c["name"]], ts).dropna()
        vals = set(norm)
        if len(vals) > MAX_DISTINCT:
            vals = set(list(vals)[:MAX_DISTINCT])
        sets.append(vals)
        unique.append(bool(norm.is_unique))
    raw = df[c["name"]].dropna().astype(str).str.strip().str.upper()
    pre = raw.str.extract(r"^([A-Z]+)", expand=False).dropna()
    prefix = pre.mode().iloc[0] if len(pre) >= 0.6 * max(1, len(raw)) and not pre.empty else None
    return _Col(table_id, c["name"], str(c.get("original") or c["name"]), c.get("type", "text"), sets, unique, prefix)


def _small_numbers(values: set[str]) -> float:
    return sum(1 for v in values if v.isdigit() and len(v) <= 2) / max(1, len(values))


def best_link(a: list[_Col], b: list[_Col]) -> Found | None:
    """Cặp cột (a x b) x phép chuẩn hoá khớp nhất giữa 2 bảng, hoặc None nếu không đủ chặt."""
    best: tuple[float, Any] | None = None
    for ca in a:
        for cb in b:
            sim = _label_sim(ca.label, cb.label)
            idb = idlike(ca.label) or idlike(cb.label)
            for i, sa in enumerate(ca.sets):
                if len(sa) < MIN_MATCH:
                    continue
                for j, sb in enumerate(cb.sets):
                    if len(sb) < MIN_MATCH:
                        continue
                    common = sa & sb if len(sa) <= len(sb) else sb & sa
                    inter = len(common)
                    if inter < MIN_MATCH:
                        continue
                    steps = len(K.CANDIDATE_TRANSFORMS[i]) + len(K.CANDIDATE_TRANSFORMS[j])
                    if steps and ca.prefix and cb.prefix and ca.prefix != cb.prefix:
                        continue  # NCC001 vs SP001: 2 họ mã khác nhau, phần số trùng là ngẫu nhiên
                    if _small_numbers(common) > 0.5 and not (steps == 0 and idlike(ca.label) and idlike(cb.label)):
                        continue
                    contain = inter / min(len(sa), len(sb))
                    if contain < MIN_CONTAIN:
                        continue
                    score = inter * (0.5 + 0.5 * contain) * (1 - 0.08 * steps) * (1 + 0.2 * sim) * (1.3 if idb else 1.0)
                    if best is None or score > best[0]:
                        best = (score, (ca, cb, i, j, inter, contain, sim, idb, steps))
    if best is None:
        return None
    ca, cb, i, j, inter, contain, sim, idb, steps = best[1]
    conf = 0.3 + 0.3 * contain + 0.15 * min(1.0, inter / 50) + (0.12 if idb else 0.0) + 0.1 * sim - 0.05 * steps
    conf = round(max(0.0, min(0.97, conf)), 3)
    if conf < MIN_CONFIDENCE:
        return None
    ua, ub = ca.unique[i], cb.unique[j]
    # right = phía "một" (khoá duy nhất — bảng danh mục), left = phía tham chiếu
    if ua and not ub:
        ca, cb, i, j, ua, ub = cb, ca, j, i, ub, ua
    card = "one_to_one" if ua and ub else "many_to_one" if ub else "many_to_many"
    left_set, right_set = ca.sets[i], cb.sets[j]
    return Found(ca, cb, i, j, inter, round(inter / max(1, len(left_set)), 4), round(inter / max(1, len(right_set)), 4), card, conf)


def _active_tables(s: Session, workspace_id: str) -> list[DataTable]:
    return list(s.execute(select(DataTable).join(Document, Document.id == DataTable.document_id).where(
        DataTable.workspace_id == workspace_id, DataTable.is_active.is_(True), Document.deleted_at.is_(None),
        ~DataTable.source_location.startswith(DERIVED_PREFIX))).scalars())


def load_frame(t: DataTable) -> pd.DataFrame | None:
    try:
        return pd.read_parquet(t.storage_path)
    except Exception as e:  # file mất/hỏng -> bỏ bảng này, các bảng khác vẫn dò
        log_event(logger, "relations_table_unreadable", level=logging.WARNING, table=t.name, error=str(e)[:200])
        return None


def discover(workspace_id: str, progress: Progress | None = None) -> dict[str, Any]:
    with session_scope() as s:
        tables = _active_tables(s, workspace_id)
        s.expunge_all()
    profiles: dict[str, list[_Col]] = {}
    for t in tables:
        df = load_frame(t)
        cand = key_columns(df, list(t.columns or [])) if df is not None else []
        if cand:
            profiles[t.id] = [column_profile(t.id, df, c) for c in cand]
    ids = list(profiles)
    pairs = [(a, b) for k, a in enumerate(ids) for b in ids[k + 1:]]
    found: list[Found] = []
    for n, (a, b) in enumerate(pairs):
        f = best_link(profiles[a], profiles[b])
        if f is not None:
            found.append(f)
        if progress and n % 50 == 0:
            progress(n, len(pairs))
    saved = _persist(workspace_id, found)
    log_event(logger, "relations_discovered", workspace_id=workspace_id, tables=len(ids), pairs=len(pairs), found=len(found), **saved)
    return {"tables": len(ids), "pairs": len(pairs), "found": len(found), **saved}


def schedule_discovery(s: Session, workspace_id: str, *, created_by: str | None = None, delay_seconds: float = 20) -> Any:
    """Đẩy job dò quan hệ của workspace; đã có job đang chờ thì dùng lại (nhiều file upload cùng lúc -> chạy 1 lần)."""
    from core.models import Job, new_id
    from worker.queue import enqueue_job

    queued = s.execute(select(Job).where(Job.type == "relations_discover", Job.workspace_id == workspace_id,
                                         Job.status == "queued")).scalars().first()
    if queued is not None:
        return queued
    return enqueue_job(s, "relations_discover", {"workspace_id": workspace_id}, idempotency_key=f"relations:{workspace_id}:{new_id()}",
                       workspace_id=workspace_id, created_by=created_by, delay_seconds=delay_seconds)


def _ts(i: int) -> list[dict[str, Any]]:
    return [t.model_dump() for t in K.CANDIDATE_TRANSFORMS[i]]


def _persist(workspace_id: str, found: list[Found]) -> dict[str, int]:
    created = updated = removed = 0
    with session_scope() as s:
        existing = {(r.left_table_id, r.left_column, r.right_table_id, r.right_column): r for r in s.execute(
            select(TableRelationship).where(TableRelationship.workspace_id == workspace_id)).scalars()}
        seen = set()
        for f in found:
            key = (f.left.table_id, f.left.name, f.right.table_id, f.right.name)
            rev = (f.right.table_id, f.right.name, f.left.table_id, f.left.name)
            row = existing.get(key) or existing.get(rev)
            values = dict(left_table_id=f.left.table_id, left_column=f.left.name, left_transforms=_ts(f.li),
                          right_table_id=f.right.table_id, right_column=f.right.name, right_transforms=_ts(f.ri),
                          cardinality=f.cardinality, matched_keys=f.matched, left_coverage=f.left_coverage,
                          right_coverage=f.right_coverage, confidence=f.confidence)
            if row is None:
                row = TableRelationship(workspace_id=workspace_id, status="proposed", **values)
                s.add(row)
                created += 1
            else:
                for k, v in values.items():
                    setattr(row, k, v)
                row.updated_at = utcnow()
                updated += 1
            seen.add((row.left_table_id, row.left_column, row.right_table_id, row.right_column))
        # đề xuất không còn khớp (dữ liệu đổi) -> bỏ; quyết định của người dùng giữ lại
        for k, r in existing.items():
            if k not in seen and r.status == "proposed":
                s.delete(r)
                removed += 1
    return {"created": created, "updated": updated, "removed": removed}


# ------------------------------------------------------------------ nhóm khoá chung
class _UF:
    def __init__(self) -> None:
        self.p: dict[tuple[str, str], tuple[str, str]] = {}

    def find(self, x: tuple[str, str]) -> tuple[str, str]:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: tuple[str, str], b: tuple[str, str]) -> None:
        self.p[self.find(a)] = self.find(b)


def usable(r: TableRelationship, min_confidence: float = 0.6) -> bool:
    return r.status == "approved" or (r.status == "proposed" and r.confidence >= min_confidence)


def key_groups(rels: list[TableRelationship]) -> tuple[list[list[tuple[str, str]]], dict[tuple[str, str], list[Transform]]]:
    """-> (các nhóm cột khoá nối với nhau, phép chuẩn hoá của từng cột lấy từ quan hệ tin cậy nhất của cột đó).
    Mỗi nhóm có cột từ ít nhất 2 bảng; thứ tự ổn định (nhóm nhiều cột trước)."""
    uf = _UF()
    transforms: dict[tuple[str, str], tuple[float, list[Transform]]] = {}
    for r in rels:
        a, b = (r.left_table_id, r.left_column), (r.right_table_id, r.right_column)
        uf.union(a, b)
        for node, ts in ((a, r.left_transforms), (b, r.right_transforms)):
            conf = r.confidence + (1 if r.status == "approved" else 0)
            if node not in transforms or conf > transforms[node][0]:
                transforms[node] = (conf, [Transform(**t) for t in ts])
    groups: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for node in transforms:
        groups.setdefault(uf.find(node), []).append(node)
    out = sorted((sorted(g) for g in groups.values() if len({t for t, _ in g}) >= 2), key=lambda g: (-len(g), g))
    final = {k: v[1] for k, v in transforms.items()}
    # cả nhóm phải về CÙNG 1 dạng khoá: có cạnh nào phải "chỉ lấy phần số" ("STE6410" ~ "6410") thì mọi cột của nhóm cũng
    # lấy phần số — không thì cột "STE6410" và cột "6410" ra 2 khoá khác nhau (tách nút, ON giữa 2 cột đó không khớp)
    numeric_roots = {uf.find((r.left_table_id, r.left_column)) for r in rels
                     if any(t.get("op") == "number_part" for t in [*r.left_transforms, *r.right_transforms])}
    for g in out:
        if uf.find(g[0]) in numeric_roots:
            for n in g:
                if not any(t.op == "number_part" for t in final[n]):
                    final[n] = [*final[n], Transform(op="number_part")]
    return out, final


# ------------------------------------------------------------------ dùng khi viết SQL / API
@dataclass
class KeyGroup:
    """1 khoá chung giữa nhiều bảng; members: (bảng, cột, nhãn gốc, biểu thức SQL khoá chuẩn)."""
    label: str
    members: list[tuple[str, str, str, str]]
    approved: bool

    def to_prompt(self) -> str:
        head = f"- Khoá chung \"{self.label}\"" + (" (đã xác nhận)" if self.approved else " (khớp trên dữ liệu)")
        return head + "".join(f"\n    {t}.{c} '{lbl}': {expr}" for t, c, lbl, expr in self.members)


def join_hints(s: Session, tables: list[Any], *, min_confidence: float = 0.6) -> list[KeyGroup]:
    """Nhóm khoá chung giữa các bảng trong danh sách (quan hệ không bị từ chối), mỗi cột kèm biểu thức khoá đã chuẩn hoá
    (DuckDB): nối 2 bảng bất kỳ trong nhóm bằng ON <biểu thức bảng 1> = <biểu thức bảng 2>."""
    by_id = {t.id: t for t in tables}
    if len(by_id) < 2:
        return []
    rows = [r for r in s.execute(select(TableRelationship).where(
        TableRelationship.left_table_id.in_(list(by_id)), TableRelationship.right_table_id.in_(list(by_id)))).scalars()
        if usable(r, min_confidence)]
    groups, transforms = key_groups(rows)
    approved = {(r.left_table_id, r.left_column) for r in rows if r.status == "approved"} | \
               {(r.right_table_id, r.right_column) for r in rows if r.status == "approved"}
    out = []
    for g in groups:
        members = []
        for tid, col in g:
            t = by_id[tid]
            meta = next((c for c in t.columns if c.get("name") == col), {})
            expr = K.to_sql(f'{t.name}."{col}"', meta.get("type", "text"), transforms[(tid, col)])
            members.append((t.name, col, str(meta.get("original") or col), expr))
        labels = [m[2] for m in members]
        out.append(KeyGroup(max(set(labels), key=labels.count), members, any(n in approved for n in g)))
    return out


def relationship_view(r: TableRelationship, tables: dict[str, DataTable]) -> dict[str, Any]:
    def side(tid: str, col: str, transforms: list[dict[str, Any]]) -> dict[str, Any]:
        t = tables.get(tid)
        meta = next((c for c in (t.columns if t else []) if c.get("name") == col), {})
        return {"table_id": tid, "table": t.name if t else None, "title": t.title if t else None, "document_id": t.document_id if t else None,
                "column": col, "label": meta.get("original") or col, "examples": meta.get("examples", []),
                "transforms": [x.get("op") for x in transforms]}
    return {"id": r.id, "left": side(r.left_table_id, r.left_column, r.left_transforms),
            "right": side(r.right_table_id, r.right_column, r.right_transforms), "cardinality": r.cardinality,
            "matched_keys": r.matched_keys, "left_coverage": r.left_coverage, "right_coverage": r.right_coverage,
            "confidence": r.confidence, "status": r.status, "reviewed_by": r.reviewed_by, "updated_at": r.updated_at}
