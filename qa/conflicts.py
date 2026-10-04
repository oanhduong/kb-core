"""Conflict detector (B4) — XÁC ĐỊNH, không dùng LLM.

Mâu thuẫn = cùng 1 thực thể + cùng thuộc tính (hiện tại: giá bán của 1 SKU trong cùng phạm vi cửa
hàng) nhưng các record ĐANG HIỆU LỰC từ nguồn khác nhau cho giá trị khác nhau (vd bảng giá xlsx ghi
1.440.000đ, poster khuyến mãi ghi 1.290.000đ). Bảng giá cũ đã bị thay (is_active=False, C2) KHÔNG
phải mâu thuẫn — nó là lịch sử.

Cách trình bày (yêu cầu B4): nêu ĐỦ mọi giá trị kèm effective_date + nguồn, nói rõ nguồn nào mới
hơn, KHÔNG tự chọn. Model được dặn qua khối <conflicts>; ngoài ra ensure_conflict_disclosure() kiểm
bằng code: câu trả lời thiếu giá trị nào -> tự nối thêm đoạn liệt kê đầy đủ (không phụ thuộc model).
Mọi mâu thuẫn phát hiện được lưu bảng conflicts (idempotent theo fingerprint) cho admin xử lý.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from graph.build import map_fields
from graph.resolve import normalize_sku

_PRICE_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})+|\d{4,})(?:\s*(?:đ|đồng|vnđ|vnd|VNĐ|VND))?(?![\d])")
_PRICE_CONTEXT_RE = re.compile(r"\b(giá|gia là|giá bán|đơn giá|chỉ còn|còn|price)\b", re.IGNORECASE)
_SKU_RE = re.compile(r"\b[A-ZĐ]{2,5}-\d{2,6}\b")
_DATE_IN_TEXT_RE = re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{4}\b|\b\d{4}-\d{2}-\d{2}\b")
_CURRENCY_TAIL_RE = re.compile(r"^\s*(?:đ|đồng|vnđ|vnd)\b", re.IGNORECASE)


@dataclass
class FactValue:
    entity_key: str
    attribute: str
    value: int
    record_id: str
    document_id: str
    label: str
    effective_date: str | None
    store_code: str | None
    raw: str = ""


@dataclass
class ConflictInfo:
    kind: str
    entity_key: str
    attribute: str
    values: list[FactValue]
    newest_record_id: str | None
    fingerprint: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "entity_key": self.entity_key, "attribute": self.attribute,
            "newest_record_id": self.newest_record_id, "fingerprint": self.fingerprint,
            "values": [{"value": v.value, "display": format_vnd(v.value), "record_id": v.record_id,
                        "document_id": v.document_id, "source": v.label, "effective_date": v.effective_date,
                        "store_code": v.store_code, "is_newest": v.record_id == self.newest_record_id}
                       for v in self.values],
        }


def parse_vnd(raw: str) -> int | None:
    digits = re.sub(r"[.,\s]", "", raw or "")
    return int(digits) if digits.isdigit() else None


def format_vnd(value: int) -> str:
    return f"{value:,}".replace(",", ".") + "đ"


def _entity_key(sku: str, store_code: str | None) -> str:
    return f"sku:{normalize_sku(sku)}|store:{store_code or '*'}"


def extract_price_facts(record: Any, label: str) -> list[FactValue]:
    """record: object/dict có record_id, document_id, content, meta (metadata), skus, store_code,
    effective_date. Ưu tiên cột giá có cấu trúc (xlsx: meta.fields), sau đó regex "SKU ... giá X đ"
    trong cùng 1 dòng văn bản (poster/catalogue/pdf)."""
    get = (lambda k, d=None: record.get(k, d)) if isinstance(record, dict) else (lambda k, d=None: getattr(record, k, d))
    meta = get("meta") or get("metadata") or {}
    store = get("store_code")
    eff = get("effective_date")
    eff = eff.isoformat() if hasattr(eff, "isoformat") else eff
    rid, did = get("record_id"), get("document_id") or ""
    facts: list[FactValue] = []
    fields = map_fields(meta.get("fields") or {}) if isinstance(meta, dict) else {}
    if fields.get("sku") and fields.get("price"):
        value = parse_vnd(str(fields["price"]))
        if value:
            facts.append(FactValue(_entity_key(fields["sku"], store), "price", value, rid, did, label, eff, store, str(fields["price"])))
        return facts
    content = get("content") or ""
    for line in re.split(r"[\n;]+", content):
        skus = _SKU_RE.findall(line)
        if len(set(skus)) != 1 or not _PRICE_CONTEXT_RE.search(line):
            continue
        clean = _DATE_IN_TEXT_RE.sub(" ", line)  # "01/10/2026" không phải giá
        found = [(parse_vnd(m.group(1)), bool(_CURRENCY_TAIL_RE.match(clean[m.end(1):]))) for m in _PRICE_RE.finditer(clean)]
        found = [(p, cur) for p, cur in found if p and p >= 1000]
        if any(cur for _, cur in found):  # có số kèm đơn vị tiền -> chỉ tin các số đó
            found = [(p, cur) for p, cur in found if cur]
        prices = [p for p, _ in found]
        if len(set(prices)) == 1:
            facts.append(FactValue(_entity_key(skus[0], store), "price", prices[0], rid, did, label, eff, store, line.strip()[:160]))
    return facts


def fingerprint_of(kind: str, entity_key: str, attribute: str, record_ids: Iterable[str]) -> str:
    raw = "|".join([kind, entity_key, attribute, *sorted(set(record_ids))])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def detect_conflicts(facts: list[FactValue]) -> list[ConflictInfo]:
    groups: dict[tuple[str, str], list[FactValue]] = {}
    for f in facts:
        groups.setdefault((f.entity_key, f.attribute), []).append(f)
    out: list[ConflictInfo] = []
    for (entity, attr), vals in groups.items():
        by_record: dict[str, FactValue] = {}
        for v in vals:
            by_record.setdefault(v.record_id, v)
        distinct = {v.value for v in by_record.values()}
        if len(distinct) < 2:
            continue
        ordered = sorted(by_record.values(), key=lambda v: (v.effective_date or "", v.record_id), reverse=True)
        dates = {v.effective_date for v in ordered}
        newest = ordered[0].record_id if len(dates) > 1 and ordered[0].effective_date else None
        info = ConflictInfo(kind=f"{attr}_mismatch", entity_key=entity, attribute=attr, values=ordered, newest_record_id=newest)
        info.fingerprint = fingerprint_of(info.kind, entity, attr, by_record)
        out.append(info)
    return out


def conflict_prompt_block(conflicts: list[ConflictInfo], sid_by_record: dict[str, str]) -> str:
    """Khối <conflicts> đưa vào prompt — chỉ dữ kiện, không kết luận hộ."""
    lines = []
    for c in conflicts:
        lines.append(f"- {c.entity_key.replace('|', ', ')} — thuộc tính {c.attribute}:")
        for v in c.values:
            sid = sid_by_record.get(v.record_id)
            newest = " (MỚI HƠN)" if v.record_id == c.newest_record_id else ""
            lines.append(f"    * {format_vnd(v.value)} theo {v.label}" + (f" [{sid}]" if sid else "")
                         + f", hiệu lực {v.effective_date or 'không rõ'}{newest}")
        if c.newest_record_id is None:
            lines.append("    * Các nguồn cùng ngày hiệu lực/không rõ ngày — không xác định được nguồn nào mới hơn.")
    return "<conflicts>\n" + "\n".join(lines) + "\n</conflicts>"


def ensure_conflict_disclosure(answer: str, conflicts: list[ConflictInfo], number_by_record: dict[str, int]) -> tuple[str, bool]:
    """Nếu câu trả lời chưa nêu đủ mọi giá trị đang mâu thuẫn -> nối đoạn công bố đầy đủ. -> (answer, appended)."""
    if not conflicts:
        return answer, False
    norm = re.sub(r"[.,\s]", "", answer)
    missing = [c for c in conflicts if not all(str(v.value) in norm for v in c.values)]
    if not missing:
        return answer, False
    lines = ["", "⚠️ Lưu ý: các nguồn đang ghi KHÁC NHAU, hệ thống không tự chọn — vui lòng xác nhận với supervisor:"]
    for c in missing:
        for v in c.values:
            n = number_by_record.get(v.record_id)
            ref = f" [{n}]" if n else ""
            newest = " — nguồn MỚI HƠN" if v.record_id == c.newest_record_id else ""
            lines.append(f"- {format_vnd(v.value)} theo {v.label}{ref}, hiệu lực {v.effective_date or 'không rõ'}{newest}")
    return answer.rstrip() + "\n" + "\n".join(lines), True


def persist_conflicts(session: Any, conflicts: list[ConflictInfo], workspace_id: str) -> list[str]:
    """Lưu bảng conflicts, idempotent theo fingerprint. -> id các conflict (mới hoặc đã có)."""
    from sqlalchemy import select

    from core.models import Conflict

    ids = []
    for c in conflicts:
        row = session.execute(select(Conflict).where(Conflict.fingerprint == c.fingerprint)).scalar_one_or_none()
        if row is None:
            row = Conflict(workspace_id=workspace_id, kind=c.kind, entity_key=c.entity_key, fingerprint=c.fingerprint,
                           record_ids=[v.record_id for v in c.values], details=c.to_dict(), status="open")
            session.add(row)
            session.flush()
        ids.append(row.id)
    return ids
