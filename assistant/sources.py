"""Registry nguồn của 1 lượt trả lời: mọi dữ kiện tool trả về được gán mã [S#] để model trích dẫn và
post-check kiểm bằng code. Mỗi Source có đủ trường cho qa.citation.postcheck (label, chunk_id, record_id,
document_id, location, text, effective_date) + kind để UI render (chunk / document / table_query / entity)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Source:
    sid: str
    kind: str  # chunk | document | table_query | table_row | entity | relation
    label: str
    text: str
    chunk_id: str = ""
    record_id: str = ""
    document_id: str = ""
    location: str = ""
    effective_date: str | None = None
    score: float | None = None
    origin: str = ""  # tên tool
    extra: dict[str, Any] = field(default_factory=dict)

    def to_found(self, used: bool) -> dict[str, Any]:
        return {"sid": self.sid, "kind": self.kind, "citation": self.label, "origin": self.origin,
                "score": None if self.score is None else round(self.score, 4), "effective_date": self.effective_date,
                "used_in_answer": used, "snippet": self.text[:200], "chunk_id": self.chunk_id or None, "document_id": self.document_id or None,
                **({k: v for k, v in self.extra.items() if k in ("entity_id", "sql", "table")})}


class SourceRegistry:
    def __init__(self) -> None:
        self._by_sid: dict[str, Source] = {}
        self._dedupe: dict[tuple[str, str], str] = {}

    def add(self, kind: str, label: str, text: str, **kw: Any) -> Source:
        key = (kind, kw.get("chunk_id") or kw.get("record_id") or kw.get("document_id") or kw.get("extra", {}).get("entity_id") or label)
        if key in self._dedupe:
            s = self._by_sid[self._dedupe[key]]
            if kw.get("score") is not None and (s.score is None or kw["score"] > s.score):
                s.score = kw["score"]
            return s
        sid = f"S{len(self._by_sid) + 1}"
        s = Source(sid=sid, kind=kind, label=label, text=text, **kw)
        self._by_sid[sid] = s
        self._dedupe[key] = sid
        return s

    def get(self, sid: str) -> Source | None:
        return self._by_sid.get(sid)

    def all(self) -> list[Source]:
        return list(self._by_sid.values())

    def as_mapping(self) -> dict[str, Source]:
        return dict(self._by_sid)

    def __len__(self) -> int:
        return len(self._by_sid)
