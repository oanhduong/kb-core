"""Thực thể dựng từ CỘT MÃ của bảng số liệu (không LLM) — bổ sung cho knowledge graph do LLM đọc văn bản.

Từ các quan hệ giữa bảng (analytics/relations.py, trừ quan hệ bị từ chối):
1. Gom cột khoá nối với nhau thành nhóm (union-find): "Site Number" ở sheet A, "Site #" ở file B, "STE0870" ở file C
   -> 1 nhóm. Mỗi cột có phép chuẩn hoá của quan hệ -> giá trị khoá chuẩn ("STE0870" và "870" cùng khoá "870").
2. Mỗi khoá chuẩn = 1 thực thể (tên = giá trị ở bảng danh mục, các cách viết khác là tên khác; cột "tên" cạnh mã trong
   bảng danh mục cũng là tên khác). Nhóm quá nhiều khoá (mã giao dịch) bị bỏ — không phải "sự vật" để vẽ bản đồ.
   Gộp với thực thể đã có cùng tên (do LLM trích) qua knowledge.extract.EntityIndex.
3. Mention trỏ về đúng dòng Excel (record của dòng) -> bấm nút trên bản đồ ra nguồn; tối đa MENTIONS_PER_TABLE dòng/bảng.
4. Hai cột khoá thuộc 2 nhóm khác nhau trong cùng 1 dòng -> cạnh giữa 2 thực thể (vd bệnh nhân -> site), nhãn = tên cột đích.
Loại thực thể đoán theo tên cột (site -> địa điểm, product/SKU -> sản phẩm...), không đoán được -> other.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from analytics.relations import _active_tables, idlike, key_groups, load_frame, usable
from core.db import session_scope
from core.logging import log_event
from core.models import DataTable, Entity, EntityMention, KBRecordRow, Relation, TableRelationship
from graph.resolve import fold_text
from knowledge.extract import EntityIndex, _Ent, _recount
from modeling import keys as K
from modeling.spec import Transform

logger = logging.getLogger(__name__)

MAX_KEYS = 1000
MENTIONS_PER_TABLE = 3
MAX_EDGES_PER_TABLE = 3000
MIN_CONFIDENCE = 0.6
REL_TYPE = "table_link"

_TYPE_WORDS: list[tuple[str, set[str]]] = [
    ("location", {"site", "store", "branch", "warehouse", "location", "country", "city", "region", "cua hang", "chi nhanh", "kho",
                  "dia diem", "quoc gia", "tinh", "thanh pho", "khu vuc"}),
    ("person", {"subject", "patient", "employee", "staff", "investigator", "cra", "monitor", "benh nhan", "nhan vien", "nguoi"}),
    ("organization", {"customer", "supplier", "vendor", "company", "sponsor", "partner", "khach hang", "nha cung cap", "cong ty",
                      "doi tac"}),
    ("product", {"product", "sku", "item", "article", "san pham", "mat hang", "hang hoa"}),
    ("project", {"project", "study", "protocol", "trial", "du an", "nghien cuu"}),
    ("event", {"visit", "event", "su kien", "lan kham"}),
    ("document", {"invoice", "contract", "document", "hoa don", "hop dong", "chung tu"}),
]
_NAME_WORDS = {"name", "ten", "title", "tieu de", "description", "mo ta"}


def guess_type(labels: list[str]) -> str:
    text = " ".join(fold_text(x) for x in labels)
    for etype, words in _TYPE_WORDS:
        if any(re.search(rf"\b{re.escape(w)}\b", text) for w in words):
            return etype
    return "other"


class RecordFinder:
    """Tìm record (dòng Excel) chứa 1 giá trị trong 1 sheet: chỉ mục token -> record_id, giá trị nhiều token thì quét chuỗi."""

    def __init__(self, records: list[tuple[str, str]]) -> None:
        self.raw = records
        self.records = [(rid, content.upper()) for rid, content in records]
        self.index: dict[str, list[str]] = defaultdict(list)
        for rid, up in self.records:
            for tok in set(re.findall(r"[0-9A-Z]+", fold_text(up).upper())):
                if len(self.index[tok]) < MENTIONS_PER_TABLE:
                    self.index[tok].append(rid)

    def snippet(self, rid: str) -> str:
        return next((c for r, c in self.raw if r == rid), "")[:240]

    def find(self, raw: str, limit: int = MENTIONS_PER_TABLE) -> list[str]:
        v = str(raw).strip().upper()
        if not v:
            return []
        if re.fullmatch(r"[0-9A-Z]+", fold_text(v).upper()):
            return self.index.get(fold_text(v).upper(), [])[:limit]
        return [rid for rid, up in self.records if v in up][:limit]


def _sheet_records(s: Any, t: DataTable) -> list[tuple[str, str]]:
    q = select(KBRecordRow.record_id, KBRecordRow.content).where(KBRecordRow.document_id == t.document_id,
                                                                 KBRecordRow.deleted_at.is_(None), KBRecordRow.is_active.is_(True))
    if t.source_location.startswith("Sheet "):
        q = q.where(KBRecordRow.meta["sheet"].astext == t.source_location[len("Sheet "):])
    return list(s.execute(q.order_by(KBRecordRow.record_id)).all())


def _raw(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _name_column(t: DataTable, df: pd.DataFrame, key_col: str) -> str | None:
    """Cột tên của chính mã này trong bảng danh mục ("Site Name" cạnh "Site #"): chỉ khi cột khoá là mã, cột tên gần như
    duy nhất theo từng mã (cột trạng thái/nhóm có ít giá trị -> không phải tên, gộp theo nó sẽ dồn nhiều mã vào 1 nút)."""
    key_meta = next((c for c in t.columns or [] if c.get("name") == key_col), {})
    if not idlike(str(key_meta.get("original") or key_col)):
        return None
    keys = df[key_col].nunique()
    for c in t.columns or []:
        if c.get("name") == key_col or c.get("type") != "text" or c.get("name") not in df.columns:
            continue
        label = fold_text(c.get("original") or c["name"])
        if not any(re.search(rf"\b{w}\b", label) for w in _NAME_WORDS) or idlike(c.get("original") or ""):
            continue
        pairs = df[[key_col, c["name"]]].dropna().drop_duplicates()
        if pairs[c["name"]].nunique() >= 0.9 * keys and pairs[key_col].is_unique:
            return c["name"]
    return None


def _reset(s: Any, workspace_id: str) -> None:
    """Chạy lại: gỡ phần lần trước đã thêm (mention trỏ dòng bảng, cạnh table_link, tên khác) — thực thể do LLM trích
    được gộp vào vẫn giữ mention/tên của LLM; thực thể chỉ còn từ bảng mà không còn khoá sẽ bị _recount xoá."""
    from sqlalchemy import and_, delete, func

    ents = s.execute(select(Entity).where(Entity.workspace_id == workspace_id, Entity.properties["from_tables"].astext == "true")).scalars().all()
    if ents:
        ours = select(EntityMention.id).join(KBRecordRow, KBRecordRow.record_id == EntityMention.record_id).where(
            and_(EntityMention.entity_id.in_([e.id for e in ents]), EntityMention.snippet == func.left(KBRecordRow.content, 240)))
        s.execute(delete(EntityMention).where(EntityMention.id.in_(ours)))
        for e in ents:
            added = set((e.properties or {}).get("table_aliases") or [])
            e.aliases = [a for a in e.aliases or [] if a not in added]
            e.properties = {k: v for k, v in (e.properties or {}).items() if k not in ("from_tables", "table_aliases")}
    s.execute(delete(Relation).where(Relation.workspace_id == workspace_id, Relation.type == REL_TYPE))
    s.flush()


def build_key_entities(workspace_id: str) -> dict[str, Any]:
    with session_scope() as s:
        tables = {t.id: t for t in _active_tables(s, workspace_id)}
        rels = [r for r in s.execute(select(TableRelationship).where(TableRelationship.workspace_id == workspace_id)).scalars()
                if usable(r, MIN_CONFIDENCE)]
        rels = [r for r in rels if r.left_table_id in tables and r.right_table_id in tables]
        groups, transforms = key_groups(rels)
        s.expunge_all()
    if not groups:  # không còn khoá chung (bị bỏ hết / bảng bị xoá) -> gỡ phần đã dựng lần trước
        with session_scope() as s:
            _reset(s, workspace_id)
            _recount(s, workspace_id)
        return {"groups": 0, "entities": 0}
    frames = {tid: load_frame(tables[tid]) for tid in {t for g in groups for t, _ in g}}
    stats = {"groups": 0, "skipped_groups": 0, "entities": 0, "mentions": 0, "edges": 0}
    with session_scope() as s:
        _reset(s, workspace_id)
        finders = {tid: RecordFinder(_sheet_records(s, tables[tid])) for tid, df in frames.items() if df is not None}
        index = EntityIndex(s, workspace_id)
        have = set()
        # (nhóm, khoá chuẩn) -> thực thể; dùng tiếp để nối cạnh giữa các nhóm
        by_key: dict[tuple[int, str], Any] = {}
        for gi, group in enumerate(groups):
            canon: dict[tuple[str, str], pd.Series] = {}
            for tid, col in group:
                df = frames.get(tid)
                if df is not None and col in df.columns:
                    canon[(tid, col)] = K.apply_series(df[col], transforms[(tid, col)])
            keys = set().union(*(set(v.dropna()) for v in canon.values())) if canon else set()
            if not keys or len(keys) > MAX_KEYS:
                stats["skipped_groups"] += 1
                continue
            stats["groups"] += 1
            # bảng danh mục = cột có khoá duy nhất và nhiều khoá nhất -> tên chuẩn lấy ở đây
            master = max(canon, key=lambda n: (bool(canon[n].dropna().is_unique), canon[n].nunique()))
            labels = [next((c.get("original") or c["name"] for c in tables[t].columns if c.get("name") == col), col) for t, col in canon]
            etype = guess_type(labels)
            mlabel = labels[list(canon).index(master)]
            mdf = frames[master[0]]
            name_col = _name_column(tables[master[0]], mdf, master[1])
            raws: dict[str, list[str]] = defaultdict(list)
            human: dict[str, str] = {}
            for (tid, col), ser in canon.items():
                df = frames[tid]
                # cách viết khác chỉ lấy từ cột so trực tiếp / chỉ lấy phần số; cột cắt theo dấu phân cách ("7667-5256Form 0")
                # là cột ghép, giá trị gốc không phải tên của mã
                variants = not any(t.op == "first_token" for t in transforms[(tid, col)])
                for idx, k in ser.dropna().items():
                    r = _raw(df.at[idx, col])
                    if (variants or (tid, col) == master) and r not in raws[k] and len(r) <= 40:
                        if (tid, col) == master:
                            raws[k].insert(0, r)
                        else:
                            raws[k].append(r)
                    if (tid, col) == master and name_col and k not in human and pd.notna(mdf.at[idx, name_col]):
                        human[k] = str(mdf.at[idx, name_col]).strip()
            for k in sorted(keys):
                mentions = []
                for (tid, col), ser in canon.items():
                    hit = ser[ser == k]
                    if hit.empty or tid not in finders:
                        continue
                    rids = finders[tid].find(_raw(frames[tid].at[hit.index[0], col]))
                    mentions += [(tables[tid].document_id, rid, finders[tid].snippet(rid)) for rid in rids]
                if not mentions:
                    continue
                first = raws[k][0] if raws[k] else k
                name = f"{mlabel} {first}" if re.fullmatch(r"\d+", first) else first
                aliases = [a for a in [*raws[k], human.get(k)] if a and a != name][:8]
                before = set(index.by_key.get((etype, fold_text(name)), None).aliases or []) if (etype, fold_text(name)) in index.by_key else set()
                ent = index.resolve(_Ent(type=etype, name=name[:200], aliases=aliases,
                                         description=f"{mlabel}: {first}" + (f" — {human[k]}" if k in human else "")))
                if ent is None:
                    continue
                props = ent.properties or {}
                added = [a for a in ent.aliases or [] if a not in before and a in aliases]
                ent.properties = {**props, "from_tables": True, "table_aliases": sorted(set(props.get("table_aliases") or []) | set(added))}
                by_key[(gi, k)] = ent
                stats["entities"] += 1
                for doc_id, rid, snip in mentions:
                    if (ent.id, rid) in have:
                        continue
                    have.add((ent.id, rid))
                    s.add(EntityMention(entity_id=ent.id, document_id=doc_id, record_id=rid, snippet=snip or None))
                    stats["mentions"] += 1
            s.flush()
        stats["edges"] = _row_edges(s, workspace_id, groups, transforms, frames, tables, finders, by_key)
        _dedupe_mentions(s, [e.id for e in by_key.values()])
        _recount(s, workspace_id)
    log_event(logger, "key_entities_built", workspace_id=workspace_id, **stats)
    return stats


def _dedupe_mentions(s: Any, entity_ids: list[str]) -> None:
    """Chạy lại nhiều lần không nhân bản mention: giữ 1 mention cho mỗi (thực thể, record)."""
    from sqlalchemy import text

    if not entity_ids:
        return
    s.execute(text("DELETE FROM entity_mentions m USING entity_mentions d WHERE m.entity_id = d.entity_id AND m.record_id = d.record_id "
                   "AND m.id > d.id AND m.entity_id = ANY(:ids)"), {"ids": entity_ids})


def _row_edges(s: Any, workspace_id: str, groups: list[list[tuple[str, str]]], transforms: dict[tuple[str, str], list[Transform]],
               frames: dict[str, pd.DataFrame | None], tables: dict[str, DataTable], finders: dict[str, RecordFinder],
               by_key: dict[tuple[int, str], Any]) -> int:
    node_group = {node: gi for gi, g in enumerate(groups) for node in g}
    per_table: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for (tid, col) in node_group:
        per_table[tid].append((tid, col))
    rows = []
    for tid, nodes in per_table.items():
        df = frames.get(tid)
        if df is None or len(nodes) < 2 or tid not in finders:
            continue
        canon = {n: K.apply_series(df[n[1]], transforms[n]) for n in nodes}
        # nguồn = cột định danh dòng (khoá duy nhất / nhiều giá trị nhất), đích = các cột mã còn lại
        src = max(nodes, key=lambda n: (bool(canon[n].dropna().is_unique), canon[n].nunique()))
        label_of = {c["name"]: c.get("original") or c["name"] for c in tables[tid].columns}
        seen = set()
        for tgt in nodes:
            if tgt == src or node_group[tgt] == node_group[src]:
                continue
            for idx in df.index:
                a, b = canon[src].get(idx), canon[tgt].get(idx)
                ea, eb = by_key.get((node_group[src], a)), by_key.get((node_group[tgt], b))
                if ea is None or eb is None or ea.id == eb.id or (ea.id, eb.id) in seen:
                    continue
                rid = next(iter(finders[tid].find(_raw(df.at[idx, src[1]]), 1)), None)
                if rid is None:
                    continue
                seen.add((ea.id, eb.id))
                rows.append({"workspace_id": workspace_id, "source_id": ea.id, "target_id": eb.id, "type": REL_TYPE,
                             "label": label_of.get(tgt[1], tgt[1]), "confidence": 0.9, "document_id": tables[tid].document_id,
                             "record_id": rid, "properties": {"table": tables[tid].name},
                             "evidence": f"{tables[tid].title}: {label_of.get(src[1], src[1])} = {_raw(df.at[idx, src[1]])}, "
                                         f"{label_of.get(tgt[1], tgt[1])} = {_raw(df.at[idx, tgt[1]])}"})
                if len(seen) >= MAX_EDGES_PER_TABLE:
                    break
    if not rows:
        return 0
    from core.models import new_id

    for r in rows:
        r["id"] = new_id()
    for i in range(0, len(rows), 1000):
        s.execute(insert(Relation).values(rows[i:i + 1000]).on_conflict_do_nothing(constraint="uq_relation_edge"))
    return len(rows)
