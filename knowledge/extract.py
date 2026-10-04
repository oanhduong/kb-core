"""Knowledge graph mở: LLM trích entity/quan hệ theo cửa sổ văn bản, hợp nhất theo workspace, lưu Postgres.

Hợp nhất entity (resolution): khoá (type, name_norm = fold_text(name)); alias do model đưa ra cũng
được đối chiếu với name_norm/aliases của entity đã có cùng type -> gộp; type ngoài danh sách -> other.
Quan hệ chỉ được lưu khi 2 đầu đều là entity trong CÙNG cửa sổ (tránh model bịa tên không có trong
văn bản); type snake_case; label tiếng Việt. Mọi mention/relation giữ record_id -> bấm node ra nguồn.
force: xoá mention/relation cũ của tài liệu rồi trích lại; entity không còn mention nào bị xoá.
"""
from __future__ import annotations

import contextvars
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from core.config import AppConfig, read_prompt
from core.db import session_scope
from core.logging import log_event
from core.models import Document, Entity, EntityMention, KBRecordRow, Relation
from graph.resolve import fold_text, normalize_text
from security.provenance import wrap_untrusted

logger = logging.getLogger(__name__)

ENTITY_TYPES = ("person", "organization", "product", "location", "project", "event", "document", "policy_clause", "metric",
                "date", "money", "other")
_GENERIC_NAMES = {"khach hang", "nhan vien", "cong ty", "cua hang", "san pham", "he thong", "nguoi dung", "customer", "company",
                  "employee", "staff", "store", "product", "system", "user"}
_REL_TYPE_RE = re.compile(r"[^a-z0-9_]+")
MAX_WORKERS = 4


class _Ent(BaseModel):
    type: str = "other"
    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=8)
    description: str | None = Field(default=None, max_length=400)


class _Rel(BaseModel):
    source: str
    target: str
    type: str = Field(min_length=1, max_length=64)
    label: str = Field(default="", max_length=120)
    evidence: str = Field(default="", max_length=400)
    confidence: float = Field(default=0.6, ge=0, le=1)


class ExtractOut(BaseModel):
    entities: list[_Ent] = Field(default_factory=list, max_length=60)
    relations: list[_Rel] = Field(default_factory=list, max_length=80)


@dataclass
class Window:
    location: str
    text: str
    record_ids: list[str]


@dataclass
class WindowResult:
    window: Window
    entities: list[_Ent] = field(default_factory=list)
    relations: list[_Rel] = field(default_factory=list)
    error: str | None = None


def normalize_rel_type(t: str) -> str:
    t = _REL_TYPE_RE.sub("_", fold_text(t).replace(" ", "_")).strip("_")[:64]
    return t or "related_to"


def entity_type(t: str) -> str:
    t = (t or "other").strip().lower()
    return t if t in ENTITY_TYPES else "other"


def is_generic_name(name: str) -> bool:
    return fold_text(name) in _GENERIC_NAMES or len(fold_text(name)) < 2


def build_windows(records: list[KBRecordRow], window_chars: int, max_windows: int) -> list[Window]:
    """Gom record liền kề tới window_chars; dòng bảng (unit_kind=row) gom theo nhóm để không gọi LLM từng dòng."""
    windows: list[Window] = []
    buf: list[str] = []
    ids: list[str] = []
    start: str | None = None
    size = 0

    def flush() -> None:
        nonlocal buf, ids, start, size
        if buf:
            windows.append(Window(start or "", "\n".join(buf), ids))
        buf, ids, start, size = [], [], None, 0

    for r in records:
        text = (r.content or "").strip()
        if not text:
            continue
        if size + len(text) > window_chars and buf:
            flush()
        if start is None:
            start = r.location
        buf.append(text)
        ids.append(r.record_id)
        size += len(text)
    flush()
    return windows[:max_windows]


def _extract_window(cfg: AppConfig, llm: Any, system: str, doc_title: str, w: Window) -> WindowResult:
    from core.llm import LLMOutputInvalid, LLMUnavailable

    user = f"Tài liệu: {doc_title} — vị trí: {w.location}\n" + wrap_untrusted(w.text, source=f"{doc_title} / {w.location}")
    try:
        out = llm.complete_json("knowledge_extract", system=system, user=user, schema=ExtractOut, max_tokens=1800, max_retries=1)
        return WindowResult(w, out.entities, out.relations)
    except (LLMOutputInvalid, LLMUnavailable) as e:
        return WindowResult(w, error=str(e)[:300])


# =====================================================================================
# Resolution + ghi DB
# =====================================================================================

class EntityIndex:
    """Chỉ mục entity của workspace: (type, norm) -> Entity; alias norm cũng trỏ về entity."""

    def __init__(self, session: Session, workspace_id: str) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.by_key: dict[tuple[str, str], Entity] = {}
        for e in session.execute(select(Entity).where(Entity.workspace_id == workspace_id)).scalars():
            self.by_key[(e.type, e.name_norm)] = e
            for a in e.aliases or []:
                self.by_key.setdefault((e.type, fold_text(a)), e)

    def resolve(self, ent: _Ent) -> Entity | None:
        etype = entity_type(ent.type)
        name = normalize_text(ent.name)
        if is_generic_name(name):
            return None
        norms = [fold_text(name)] + [fold_text(a) for a in ent.aliases if a and not is_generic_name(a)]
        found = next((self.by_key[(etype, n)] for n in norms if (etype, n) in self.by_key), None)
        if found is None and etype == "other":  # cùng tên, type cụ thể đã có -> gộp về type cụ thể
            found = next((e for (t, n), e in self.by_key.items() if n == norms[0]), None)
        if found is None:
            found = Entity(workspace_id=self.workspace_id, type=etype, name=name, name_norm=norms[0],
                           aliases=[a for a in ent.aliases if a and fold_text(a) != norms[0]][:8], description=ent.description)
            self.session.add(found)
            self.session.flush()
        else:
            new_aliases = [a for a in ent.aliases if a and fold_text(a) not in (found.name_norm, *[fold_text(x) for x in found.aliases or []])
                           and fold_text(a) != fold_text(found.name)]
            if new_aliases:
                found.aliases = [*(found.aliases or []), *new_aliases][:12]
            if ent.description and not found.description:
                found.description = ent.description
        for n in norms:
            self.by_key.setdefault((etype, n), found)
        return found


def _clear_document(session: Session, document_id: str) -> None:
    session.execute(delete(Relation).where(Relation.document_id == document_id))
    session.execute(delete(EntityMention).where(EntityMention.document_id == document_id))


def _recount(session: Session, workspace_id: str) -> int:
    """mention_count/document_ids tính lại từ bảng mention; entity không còn mention -> xoá."""
    rows = session.execute(select(EntityMention.entity_id, func.count(), func.array_agg(func.distinct(EntityMention.document_id)))
                           .join(Entity, Entity.id == EntityMention.entity_id).where(Entity.workspace_id == workspace_id)
                           .group_by(EntityMention.entity_id)).all()
    counts = {eid: (n, docs) for eid, n, docs in rows}
    removed = 0
    for e in session.execute(select(Entity).where(Entity.workspace_id == workspace_id)).scalars().all():
        if e.id in counts:
            e.mention_count, e.document_ids = counts[e.id][0], sorted(counts[e.id][1])
        else:
            session.delete(e)
            removed += 1
    return removed


def extract_entities_for_document(cfg: AppConfig, sm: sessionmaker, llm: Any, document_id: str, *, force: bool = False) -> dict[str, Any]:
    kc = cfg.knowledge
    if not (kc.enabled and kc.entity_extraction):
        return {"skipped": "knowledge.entity_extraction=false"}
    if llm is None:
        return {"skipped": "không có LLM (ANTHROPIC_API_KEY)"}
    with session_scope() as s:
        doc = s.get(Document, document_id)
        if doc is None:
            return {"skipped": "không có tài liệu"}
        has = s.execute(select(func.count()).select_from(EntityMention).where(EntityMention.document_id == document_id)).scalar_one()
        if has and not force:
            return {"skipped": "đã trích", "mentions": int(has)}
        records = s.execute(select(KBRecordRow).where(KBRecordRow.document_id == document_id, KBRecordRow.deleted_at.is_(None),
                                                      KBRecordRow.is_active.is_(True))
                            .order_by(KBRecordRow.created_at, KBRecordRow.record_id)).scalars().all()
        windows = build_windows(records, kc.window_chars, kc.max_chunks_per_document)
        doc_title, ws_id = doc.title, doc.workspace_id
    if not windows:
        return {"skipped": "tài liệu không có nội dung văn bản"}
    system = read_prompt("knowledge_extract", cfg)
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(windows))) as ex:
        results = list(ex.map(lambda w: contextvars.copy_context().run(_extract_window, cfg, llm, system, doc_title, w), windows))

    stats = {"windows": len(windows), "failed_windows": sum(1 for r in results if r.error), "entities": 0, "relations": 0, "mentions": 0}
    with session_scope() as s:
        _clear_document(s, document_id)
        s.flush()
        index = EntityIndex(s, ws_id)
        seen_rel: set[tuple[str, str, str, str]] = set()
        for r in results:
            if r.error:
                continue
            local: dict[str, Entity] = {}
            rid = r.window.record_ids[0]
            for ent in r.entities:
                e = index.resolve(ent)
                if e is None:
                    continue
                for key in (fold_text(ent.name), *[fold_text(a) for a in ent.aliases]):
                    local.setdefault(key, e)
                snippet = _snippet(r.window.text, ent.name)
                s.add(EntityMention(entity_id=e.id, document_id=document_id, record_id=_record_for(r.window, ent.name, s) or rid, snippet=snippet))
                stats["mentions"] += 1
            for rel in r.relations:
                if rel.confidence < kc.min_confidence:
                    continue
                src, dst = local.get(fold_text(rel.source)), local.get(fold_text(rel.target))
                if src is None or dst is None or src.id == dst.id:
                    continue
                rtype = normalize_rel_type(rel.type)
                rec_id = _record_for(r.window, rel.evidence or rel.source, s) or rid
                key = (src.id, dst.id, rtype, rec_id)
                if key in seen_rel or s.execute(select(Relation.id).where(Relation.source_id == src.id, Relation.target_id == dst.id,
                                                                           Relation.type == rtype, Relation.record_id == rec_id)).first():
                    continue
                seen_rel.add(key)
                s.add(Relation(workspace_id=ws_id, source_id=src.id, target_id=dst.id, type=rtype,
                               label=normalize_text(rel.label) or rtype.replace("_", " "), evidence=(rel.evidence or "")[:400] or None,
                               confidence=rel.confidence, document_id=document_id, record_id=rec_id))
                stats["relations"] += 1
        s.flush()
        stats["removed_orphans"] = _recount(s, ws_id)
        stats["entities"] = s.execute(select(func.count(func.distinct(EntityMention.entity_id))).where(EntityMention.document_id == document_id)).scalar_one()
    log_event(logger, "entities_extracted", document_id=document_id, **stats)
    return stats


_record_cache: dict[tuple[str, ...], dict[str, str]] = {}


def _record_for(window: Window, needle: str, session: Session) -> str | None:
    """record_id trong cửa sổ có chứa `needle` (so khớp không dấu) — để mention trỏ đúng đoạn nguồn."""
    key = tuple(window.record_ids)
    if key not in _record_cache:
        rows = session.execute(select(KBRecordRow.record_id, KBRecordRow.content).where(KBRecordRow.record_id.in_(window.record_ids))).all()
        _record_cache[key] = {rid: fold_text(c) for rid, c in rows}
        if len(_record_cache) > 500:
            _record_cache.clear()
            _record_cache[key] = {rid: fold_text(c) for rid, c in rows}
    n = fold_text(needle)[:60]
    if not n:
        return None
    for rid in window.record_ids:
        if n in _record_cache[key].get(rid, ""):
            return rid
    return None


def _snippet(text: str, name: str, width: int = 90) -> str | None:
    i = fold_text(text).find(fold_text(name))
    if i < 0:
        return None
    # fold_text có thể đổi độ dài (bỏ ký tự) -> xấp xỉ vị trí theo tỉ lệ
    ratio = len(text) / max(1, len(fold_text(text)))
    pos = int(i * ratio)
    lo, hi = max(0, pos - width), min(len(text), pos + len(name) + width)
    return ("…" if lo else "") + text[lo:hi].strip() + ("…" if hi < len(text) else "")
