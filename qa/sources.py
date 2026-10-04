"""Lắp context cho model (B1): chunk -> Source có mã [S1..Sn] + nhãn citation chuẩn (qa/citation.py),
nội dung bọc <untrusted_document> (F6 — nội dung tài liệu khách hàng là DỮ LIỆU, không phải lệnh,
áp dụng cho cả đường QA chứ không chỉ ingest)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.models import ChunkRow, Document
from qa.citation import format_citation
from security.provenance import wrap_untrusted


@dataclass
class Source:
    sid: str
    chunk_id: str
    record_id: str
    document_id: str
    title: str
    source_type: str
    doc_type: str
    location: str
    text: str
    label: str
    effective_date: str | None
    valid_to: str | None
    store_code: str | None
    is_active: bool
    origin: str  # vector | graph
    score: float | None = None

    def to_found(self, used: bool) -> dict[str, Any]:
        return {"sid": self.sid, "citation": self.label, "origin": self.origin, "score": None if self.score is None else round(self.score, 4),
                "effective_date": self.effective_date, "is_active": self.is_active, "used_in_answer": used,
                "snippet": self.text[:200], "chunk_id": self.chunk_id}


def build_sources(chunks: list[ChunkRow], docs: dict[str, Document], *, origin_by_chunk: dict[str, str],
                  score_by_chunk: dict[str, float | None], max_sources: int, max_chars: int) -> list[Source]:
    out: list[Source] = []
    total = 0
    for c in chunks:
        if len(out) >= max_sources:
            break
        doc = docs.get(c.document_id)
        title = doc.title if doc else c.meta.get("source_file", "tài liệu")
        source_type = doc.source_type if doc else c.meta.get("source_type", "")
        body = c.text if total + len(c.text) <= max_chars else c.text[: max(0, max_chars - total)]
        if not body.strip():
            break
        total += len(body)
        out.append(Source(
            sid=f"S{len(out) + 1}", chunk_id=c.chunk_id, record_id=c.record_id, document_id=c.document_id,
            title=title, source_type=source_type, doc_type=c.doc_type, location=c.location, text=body,
            label=format_citation(title, source_type, c.location, c.meta),
            effective_date=c.effective_date.isoformat() if c.effective_date else None,
            valid_to=c.valid_to.isoformat() if c.valid_to else None, store_code=c.store_code,
            is_active=c.is_active, origin=origin_by_chunk.get(c.chunk_id, "vector"), score=score_by_chunk.get(c.chunk_id),
        ))
    return out


def sources_prompt_block(sources: list[Source]) -> str:
    parts = []
    for s in sources:
        attrs = [f'id="{s.sid}"', f'citation="{s.label}"']
        if s.effective_date:
            attrs.append(f'effective_date="{s.effective_date}"')
        if not s.is_active:
            attrs.append('status="đã hết hiệu lực (lịch sử)"')
        attrs.append(f'store="{s.store_code or "toàn hệ thống"}"')
        parts.append(f"<source {' '.join(attrs)}>\n{wrap_untrusted(s.text, source=s.label, doc_type=s.doc_type)}\n</source>")
    return "<sources>\n" + "\n".join(parts) + "\n</sources>"


def build_user_prompt(question: str, sources: list[Source], *, store_code: str | None, as_of: str | None,
                      conflicts_block: str | None, computed_facts: list[str], graph_paths: list[str]) -> str:
    parts = [sources_prompt_block(sources)]
    if computed_facts:
        parts.append("<computed_facts>\n" + "\n".join(f"- {f}" for f in computed_facts) + "\n</computed_facts>")
    if graph_paths:
        parts.append("<graph_paths>\n" + "\n".join(f"- {p}" for p in graph_paths[:10]) + "\n</graph_paths>")
    if conflicts_block:
        parts.append(conflicts_block)
    scope = f"Cửa hàng của người hỏi: {store_code}." if store_code else "Người hỏi không gắn cửa hàng cụ thể."
    when = f" Câu hỏi về thời điểm {as_of} — dùng nguồn có hiệu lực tại thời điểm đó." if as_of else ""
    parts.append(f"{scope}{when}\nCâu hỏi: {question}\n\nTrả lời theo đúng quy tắc trích dẫn [S#].")
    return "\n\n".join(parts)
