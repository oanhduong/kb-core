"""Gom output của ingest/parsers.py thành "đơn vị record" (RecordUnit) xác định — mỗi unit thành
đúng 1 KBRecord ở chế độ ingest deterministic, và cũng là đơn vị graph/build.py trỏ source_chunk_ids
về (dùng chung kb.ids.record_id_for(document_id, unit.location)).

- docx: 1 unit / mục có breadcrumb riêng (thường là 1 "Điều"): dòng heading + các đoạn bên dưới.
  Bảng trong docx là unit riêng (location "... > Bảng N").
- xlsx: 1 unit / dòng dữ liệu (location "Sheet!A<row>"), kèm `fields` (cột -> giá trị) tách lại từ
  câu "cột là giá trị, ..." theo đúng thứ tự header — để lấy giá/SKU/ngành hàng cho metadata và
  conflict detector mà không phải đọc lại file.
- pdf: 1 unit / trang (gộp text cùng trang) + 1 unit / bảng.
- audio/video: 1 unit / segment (location HH:MM:SS, meta.end) — chunker gom cụm sau.
- image: không có text từ parser — unit được tạo ở pipeline sau khi gọi vision (extract_image_content).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_ARTICLE_RE = re.compile(r"\bĐiều\s+(\d+)\b")
_XLSX_LOC_RE = re.compile(r"^(?P<sheet>.+)!A(?P<row>\d+)$")


@dataclass
class RecordUnit:
    location: str
    content: str
    kind: str  # article | section | table | row | sheet_title | page | segment | image
    meta: dict[str, Any] = field(default_factory=dict)


def article_number(location_or_heading: str) -> int | None:
    """Số Điều cuối cùng xuất hiện trong breadcrumb/heading ("... > Điều 5. Thời hạn" -> 5)."""
    matches = _ARTICLE_RE.findall(location_or_heading or "")
    return int(matches[-1]) if matches else None


def last_segment(location: str) -> str:
    return location.split(" > ")[-1].strip() if location else ""


def units_from_docx(parsed: dict[str, Any]) -> list[RecordUnit]:
    units: list[RecordUnit] = []
    by_location: dict[str, RecordUnit] = {}
    order: list[str] = []
    for chunk in parsed.get("chunks", []):
        text = (chunk.get("text") or "").strip()
        loc = chunk.get("location") or ""
        if not text or not loc:
            continue
        kind = chunk.get("kind")
        if kind == "table":
            units.append(RecordUnit(location=loc, content=text, kind="table",
                                    meta={"article_no": article_number(loc), "order": len(order) + len(units)}))
            continue
        unit = by_location.get(loc)
        if unit is None:
            heading = last_segment(loc)
            art = article_number(heading)
            unit = RecordUnit(location=loc, content=heading if kind == "heading" else "",
                              kind="article" if art is not None else "section",
                              meta={"article_no": art, "heading": heading, "breadcrumb": loc})
            by_location[loc] = unit
            order.append(loc)
            if kind == "heading":
                continue
        if kind == "heading":
            continue
        unit.content = f"{unit.content}\n{text}".strip() if unit.content else text
    result = [by_location[loc] for loc in order if by_location[loc].content.strip()]
    # Heading trơ (không có đoạn nào bên dưới, vd tiêu đề CHƯƠNG) — vẫn giữ nếu là Title/subtitle có
    # chữ, nhưng bỏ unit chỉ gồm đúng 1 dòng heading của cấp trung gian để KB không đầy record rỗng nghĩa.
    filtered = [u for u in result if "\n" in u.content or u.kind == "article" or u.location.count(" > ") == 0]
    return filtered + units


def _row_fields(text: str, headers: list[str]) -> dict[str, str]:
    """Tách "h1 là v1, h2 là v2, ..." (định dạng parse_xlsx) về dict theo đúng thứ tự header — dò vị
    trí "<header> là " lần lượt nên giá trị có dấu phẩy vẫn tách đúng."""
    positions: list[tuple[int, int, str]] = []
    cursor = 0
    for h in headers:
        if not h:
            continue
        marker = f"{h} là "
        idx = text.find(marker, cursor)
        if idx < 0:
            continue
        positions.append((idx, idx + len(marker), h))
        cursor = idx + len(marker)
    fields: dict[str, str] = {}
    for i, (_start, val_start, h) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        fields[h] = text[val_start:end].rstrip().rstrip(",").strip()
    return fields


def units_from_xlsx(parsed: dict[str, Any]) -> list[RecordUnit]:
    sheets = (parsed.get("stats") or {}).get("sheets") or {}
    units: list[RecordUnit] = []
    for chunk in parsed.get("chunks", []):
        text = (chunk.get("text") or "").strip()
        loc = chunk.get("location") or ""
        if not text or not loc:
            continue
        m = _XLSX_LOC_RE.match(loc)
        sheet = m.group("sheet") if m else loc.split("!")[0]
        row = int(m.group("row")) if m else None
        headers = [str(h) for h in (sheets.get(sheet, {}).get("header") or []) if h is not None]
        if chunk.get("kind") == "row":
            units.append(RecordUnit(location=loc, content=text, kind="row",
                                    meta={"sheet": sheet, "row": row, "fields": _row_fields(text, headers)}))
        else:
            units.append(RecordUnit(location=loc, content=text, kind="sheet_title", meta={"sheet": sheet, "row": row}))
    return units


def units_from_pdf(parsed: dict[str, Any]) -> list[RecordUnit]:
    pages: dict[str, RecordUnit] = {}
    order: list[str] = []
    tables: list[RecordUnit] = []
    for chunk in parsed.get("chunks", []):
        text = (chunk.get("text") or "").strip()
        loc = chunk.get("location") or "Không rõ trang"
        if not text:
            continue
        if chunk.get("kind") == "table":
            tables.append(RecordUnit(location=loc, content=text, kind="table"))
            continue
        if loc not in pages:
            pages[loc] = RecordUnit(location=loc, content=text, kind="page")
            order.append(loc)
        else:
            pages[loc].content += "\n" + text
    return [pages[loc] for loc in order] + tables


def units_from_media(parsed: dict[str, Any]) -> list[RecordUnit]:
    units = []
    for chunk in parsed.get("chunks", []):
        text = (chunk.get("text") or "").strip()
        if not text:
            continue
        units.append(RecordUnit(location=chunk["location"], content=text, kind="segment",
                                meta={"end": chunk.get("end")}))
    return units


def make_units(kind: str, parsed: dict[str, Any]) -> list[RecordUnit]:
    if kind == "docx":
        return units_from_docx(parsed)
    if kind in ("xlsx", "csv"):
        return units_from_xlsx(parsed)
    if kind == "pdf":
        return units_from_pdf(parsed)
    if kind in ("audio", "video"):
        return units_from_media(parsed)
    raise ValueError(f"make_units không hỗ trợ loại {kind!r} (ảnh đi qua vision ở pipeline)")


def save_units(path: str, units: list[RecordUnit]) -> None:
    """Cache unit đã parse cạnh file upload — graph sync/reindex dùng lại, không parse lại file
    (parse docx/pdf/media tốn thời gian và có thể phải chạy trong sandbox)."""
    import json
    import os

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump([{"location": u.location, "content": u.content, "kind": u.kind, "meta": u.meta} for u in units],
                  f, ensure_ascii=False)
    os.replace(tmp, path)


def load_units_file(path: str) -> list[RecordUnit] | None:
    import json
    import os

    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return [RecordUnit(**u) for u in json.load(f)]
