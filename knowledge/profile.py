"""Hồ sơ ngữ nghĩa + tóm tắt tài liệu (LLM, model rẻ) — "ngữ nghĩa của mỗi loại file".

Đầu vào cho model: tên file, loại, trích đoạn đại diện (đầu/giữa/cuối tới knowledge.summary_max_chars,
bọc untrusted), danh sách bảng số liệu đã trích. Kết quả lưu documents.summary + semantic_profile
(kind, topics, key_entities, time_range, language, audience, structure, sensitivity, quality_notes,
+ thống kê cấu trúc do code đếm). Không có LLM -> hồ sơ tối thiểu từ thống kê để UI vẫn có thông tin.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from core.config import AppConfig, read_prompt
from core.db import session_scope
from core.logging import log_event
from core.models import DataTable, Document, KBRecordRow
from security.provenance import wrap_untrusted

logger = logging.getLogger(__name__)


class _Entity(BaseModel):
    type: str = "other"
    name: str


class _TimeRange(BaseModel):
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    note: str | None = None

    model_config = {"populate_by_name": True}


class ProfileOut(BaseModel):
    summary: str
    kind: str = ""
    topics: list[str] = Field(default_factory=list, max_length=12)
    key_entities: list[_Entity] = Field(default_factory=list, max_length=30)
    time_range: _TimeRange | None = None
    language: str = "vi"
    audience: str | None = None
    structure: str | None = None
    sensitivity: str = "none"
    quality_notes: str | None = None


# nội dung do AI sinh theo ngôn ngữ của tài liệu (không theo giao diện): tài liệu Anh -> hồ sơ tiếng Anh
_OUTPUT_LANG = "\n\nNgôn ngữ đầu ra: viết mọi trường văn bản bằng ngôn ngữ chính của nội dung tài liệu (tài liệu tiếng Anh -> tiếng Anh, tiếng Việt -> tiếng Việt); giữ nguyên tên riêng, mã và số."


def _structure_stats(records: list[KBRecordRow], source_type: str, tables: list[DataTable]) -> dict[str, Any]:
    kinds = Counter((r.meta or {}).get("unit_kind", "") for r in records)
    locs = [r.location for r in records]
    stats: dict[str, Any] = {"records": len(records), "tables": len(tables), "table_rows": sum(t.row_count for t in tables),
                             "characters": sum(len(r.content) for r in records)}
    if source_type == "docx":
        stats["articles"] = kinds.get("article", 0)
        stats["sections"] = kinds.get("section", 0)
    elif source_type in ("xlsx", "csv"):
        stats["sheets"] = len({loc.split("!")[0] for loc in locs})
        stats["rows"] = kinds.get("row", 0)
    elif source_type == "pdf":
        stats["pages"] = len({loc.split(" > ")[0] for loc in locs})
    elif source_type in ("audio", "video"):
        stats["segments"] = len(records)
        ends = [(r.meta or {}).get("end") for r in records if (r.meta or {}).get("end")]
        stats["duration"] = max(ends) if ends else None
    return stats


def _excerpt(records: list[KBRecordRow], max_chars: int) -> str:
    texts = [r.content.strip() for r in records if r.content and r.content.strip()]
    if not texts:
        return ""
    joined = "\n".join(texts)
    if len(joined) <= max_chars:
        return joined
    part = max_chars // 3
    head, mid, tail = joined[:part], joined[len(joined) // 2 - part // 2: len(joined) // 2 + part // 2], joined[-part:]
    return f"{head}\n[...]\n{mid}\n[...]\n{tail}"


def build_document_profile(cfg: AppConfig, sm: sessionmaker, llm: Any, document_id: str, *, force: bool = False) -> dict[str, Any]:
    if not cfg.knowledge.enabled:
        return {"skipped": "knowledge.enabled=false"}
    with session_scope() as s:
        doc = s.get(Document, document_id)
        if doc is None:
            return {"skipped": "không có tài liệu"}
        if doc.summary and not force and (doc.semantic_profile or {}).get("generated_by") == "llm":
            return {"skipped": "đã có hồ sơ"}
        records = s.execute(select(KBRecordRow).where(KBRecordRow.document_id == document_id, KBRecordRow.deleted_at.is_(None))
                            .order_by(KBRecordRow.created_at, KBRecordRow.record_id)).scalars().all()
        tables = s.execute(select(DataTable).where(DataTable.document_id == document_id, DataTable.is_active.is_(True))).scalars().all()
        stats = _structure_stats(records, doc.source_type, tables)
        excerpt = _excerpt(records, cfg.knowledge.summary_max_chars)
        table_lines = "\n".join(f"- {t.title}: {t.row_count} dòng, cột: {', '.join(c['original'] for c in (t.columns or [])[:12])}" for t in tables)
        filename, stype, doc_type, title = doc.filename, doc.source_type, doc.doc_type, doc.title

    profile: dict[str, Any] = {"stats": stats, "generated_at": datetime.now(timezone.utc).isoformat()}
    summary: str | None = None
    if llm is not None and excerpt:
        from core.llm import LLMOutputInvalid, LLMUnavailable

        user = (f"Tên file: {filename}\nLoại file: {stype} (doc_type hiện tại: {doc_type}, tên hiển thị: {title})\n"
                f"Thống kê cấu trúc: {stats}\n" + (f"Bảng số liệu:\n{table_lines}\n" if table_lines else "")
                + "Trích đoạn nội dung:\n" + wrap_untrusted(excerpt, source=filename, doc_type=doc_type) + _OUTPUT_LANG)
        try:
            out = llm.complete_json("document_profile", system=read_prompt("document_profile", cfg), user=user, schema=ProfileOut,
                                    max_tokens=1200, max_retries=1)
            summary = out.summary.strip()
            profile.update({"generated_by": "llm", "kind": out.kind, "topics": out.topics,
                            "key_entities": [e.model_dump() for e in out.key_entities],
                            "time_range": out.time_range.model_dump(by_alias=True) if out.time_range else None,
                            "language": out.language, "audience": out.audience, "structure": out.structure,
                            "sensitivity": out.sensitivity, "quality_notes": out.quality_notes})
        except (LLMOutputInvalid, LLMUnavailable) as e:
            log_event(logger, "document_profile_llm_failed", level=logging.WARNING, document_id=document_id, error=str(e)[:200])
    if summary is None:  # fallback xác định
        first = excerpt.split("\n")
        summary = " ".join(first[:3])[:500] if first else ""
        profile.update({"generated_by": "stats", "kind": doc_type, "topics": [], "key_entities": [], "language": "vi",
                        "structure": ", ".join(f"{k}: {v}" for k, v in stats.items() if v)})
    with session_scope() as s:
        doc = s.get(Document, document_id)
        if doc is not None:
            doc.summary = summary
            doc.semantic_profile = profile
    log_event(logger, "document_profile_done", document_id=document_id, generated_by=profile["generated_by"])
    return {"generated_by": profile["generated_by"], "summary_chars": len(summary or ""), "stats": stats}
