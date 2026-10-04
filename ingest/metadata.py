"""Metadata xác định cho ingest deterministic (rule trước, LLM chỉ khi rule không ra — doc_type).

- classify_doc_type_by_rules(filename, header_text, source_type) -> doc_type | None
- extract_effective_date(text, filename) -> 'YYYY-MM-DD' | None  ("Ban hành: 01/01/2026", "hiệu lực từ
  ngày ...", "áp dụng từ ...", tên file "bang_gia_thang_8_2026")
- extract_version(text) -> "3.2" | None
- record_fields(unit, doc) -> skus, store_code, category, entity_key, price... cho từng record
Tất cả thuần — unit test trong tests/test_ingest_metadata.py.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from graph.build import map_fields
from graph.resolve import fold_text, normalize_sku, normalize_store_code
from ingest.units import RecordUnit

_SKU_RE = re.compile(r"\b[A-ZĐ]{2,5}-\d{2,6}(?:-[A-Z0-9]+)?\b")
_STORE_RE = re.compile(r"\bGM-[A-Z]{2,3}\d{2}\b")
_DATE_VN = r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})"
_EFFECTIVE_PATTERNS = (
    re.compile(r"(?:ban hành|hiệu lực|áp dụng)\s*(?:từ|kể từ)?\s*(?:ngày)?\s*[:：]?\s*" + _DATE_VN, re.IGNORECASE),
    re.compile(r"(?:có hiệu lực|cập nhật)\s*(?:từ|kể từ|ngày|lúc)?\s*[:：]?\s*" + _DATE_VN, re.IGNORECASE),
)
_ISO_EFFECTIVE_RE = re.compile(r"(?:ngày bắt đầu|hiệu lực|áp dụng từ|cập nhật lúc)\s*(?:là|:)?\s*(\d{4})-(\d{2})-(\d{2})", re.IGNORECASE)
_FILENAME_MONTH_RE = re.compile(r"(?:thang|t)[ _-]?(\d{1,2})[ _-](\d{4})")
_FILENAME_ISO_RE = re.compile(r"(\d{4})[ _-](\d{2})(?:[ _-](\d{2}))?")
_VERSION_RE = re.compile(r"(?:phiên bản|version|ver\.?|\bv)\s*[:：]?\s*(\d+(?:\.\d+)*)", re.IGNORECASE)

# (từ khoá trong tên file đã fold, doc_type) — thứ tự quan trọng (cụ thể trước)
_FILENAME_RULES: tuple[tuple[str, str], ...] = (
    ("ton kho", "inventory"), ("bang gia", "price_list"), ("gia ban", "price_list"), ("chiet khau", "price_list"),
    ("khuyen mai", "promotion"), ("poster", "promotion"), ("flash sale", "promotion"),
    ("nha cung cap", "supplier"), ("ncc", "supplier"),
    ("hop dong", "contract"), ("catalogue", "catalog"), ("catalog", "catalog"), ("danh muc san pham", "catalog"),
    ("chinh sach", "policy"), ("quy dinh", "policy"), ("quy trinh", "policy"), ("noi quy", "policy"),
    ("training", "training"), ("dao tao", "training"), ("huong dan", "training"), ("callcenter", "training"),
    ("video", "training"), ("bien ban", "contract"), ("phieu bao hanh", "policy"),
)


def classify_doc_type_by_rules(filename: str, header_text: str = "", source_type: str = "") -> str | None:
    name = fold_text(filename.replace("_", " ").replace("-", " "))
    for key, dt in _FILENAME_RULES:
        if re.search(rf"(?<!\w){key}(?!\w)", name):
            return dt
    head = fold_text(header_text[:2000].replace("_", " "))
    if "ten chuong trinh" in head:
        return "promotion"
    if "ma ncc" in head:
        return "supplier"
    if re.search(r"\bsku\b", head) and "ton kho" in head:
        return "inventory"
    if re.search(r"\bsku\b", head) and re.search(r"\bgia\b", head):
        return "price_list"
    if re.search(r"\bdieu \d+\b", head) or head.startswith("chinh sach") or head.startswith("quy dinh"):
        return "policy"
    if source_type in ("audio", "video"):
        return "training"
    return None


def _valid(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def extract_effective_date(text: str, filename: str = "") -> str | None:
    for rx in _EFFECTIVE_PATTERNS:
        m = rx.search(text or "")
        if m:
            out = _valid(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            if out:
                return out
    m = _ISO_EFFECTIVE_RE.search(text or "")
    if m:
        out = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if out:
            return out
    name = fold_text(filename.replace("_", " "))
    m = _FILENAME_MONTH_RE.search(name)
    if m:
        return _valid(int(m.group(2)), int(m.group(1)), 1)
    m = _FILENAME_ISO_RE.search(filename)
    if m:
        return _valid(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
    return None


def extract_version(text: str) -> str | None:
    m = _VERSION_RE.search(text or "")
    return m.group(1) if m else None


def find_skus(text: str) -> list[str]:
    return sorted({normalize_sku(s) for s in _SKU_RE.findall(text or "")})


def record_fields(unit: RecordUnit, *, doc_type: str, doc_store: str | None) -> dict[str, Any]:
    """-> {skus, store_code, category, entity_key, effective_date?, extra meta} cho 1 record."""
    fields = map_fields(unit.meta.get("fields") or {})
    skus = find_skus(unit.content)
    if fields.get("sku"):
        skus = sorted({normalize_sku(fields["sku"]), *skus})
    store = None
    if fields.get("store"):
        store = normalize_store_code(fields["store"])
    elif unit.meta.get("sheet") and _STORE_RE.match(normalize_store_code(unit.meta["sheet"])):
        store = normalize_store_code(unit.meta["sheet"])
    store = store or doc_store
    out: dict[str, Any] = {"skus": skus, "store_code": store, "category": fields.get("category") or fields.get("supplier_category")}
    if unit.kind == "row" and fields.get("sku") and doc_type in ("price_list", "inventory"):
        out["entity_key"] = f"{doc_type}:{normalize_sku(fields['sku'])}|{store or '*'}"
    elif unit.kind == "row" and fields.get("promo_name"):
        out["entity_key"] = f"promotion:{fold_text(fields['promo_name'])}"
        if fields.get("start_date"):
            out["effective_date"] = fields["start_date"][:10]
        if fields.get("end_date"):
            out["end_date"] = fields["end_date"][:10]
    elif unit.kind == "row" and fields.get("supplier_code"):
        out["entity_key"] = f"supplier:{fields['supplier_code'].upper()}"
    if fields.get("price"):
        out["price"] = fields["price"]
    return out


def short_location(source_type: str, location: str) -> str:
    if source_type == "docx":
        parts = [p for p in location.split(" > ") if p]
        return " › ".join(parts[-2:]) if len(parts) > 1 else (parts[0] if parts else "")
    if source_type == "xlsx":
        m = re.match(r"^(.+?)!A(\d+)", location)
        return f"Sheet {m.group(1)} dòng {m.group(2)}" if m else location
    if source_type in ("audio", "video"):
        return f"thời điểm {location.split('-')[0]}"
    return location
