"""Build metadata graph từ dữ liệu có cấu trúc — KHÔNG dùng LLM (A1). LLM chỉ chạy ở graph/extract.py
cho văn bản tự do (A2), được gọi tuỳ chọn từ build_document(..., extractor=...).

Đơn vị build là 1 DOCUMENT (không phải toàn bộ corpus) — để cập nhật incremental (A6, graph/sync.py):
build_document(doc, ctx) chỉ đọc file của doc đó + BuildContext (những gì graph ĐÃ có: SKU master,
ngành hàng, khuyến mãi, chính sách...). Rebuild toàn bộ = clear + build lần lượt mọi document theo
thứ tự phụ thuộc (BUILD_ORDER).

Nguồn -> node/edge (mọi node/edge mang source_chunk_ids = record_id của dòng/Điều gốc, kb/ids.py):
    bảng giá (xlsx: sku+giá)         -> SKU + Category + BELONGS_TO
    tồn kho (xlsx: sku+tồn kho)      -> Store (1 sheet = 1 cửa hàng, hoặc cột cửa hàng) + STOCKS;
                                        SKU khớp về master qua graph.normalize.resolve_sku (mã trùng
                                        chính xác -> tên+ngành hàng duy nhất -> SKU-LOCAL nếu không khớp)
    khuyến mãi (xlsx: tên chương trình) -> Promotion + APPLIES_TO->Category (cột ngành hàng) /
                                        APPLIES_TO->SKU (cột SKU hoặc mã SKU trong dòng)
    nhà cung cấp (xlsx: mã NCC)      -> Supplier + SUPPLIES->SKU chỉ khi ngành hàng có ĐÚNG 1 NCC
    chính sách (docx)                -> Policy cấp document + Policy cấp Điều (PART_OF), GOVERNS
                                        (dòng "Ngành hàng ..." dưới tiêu đề + Điều nhắc tên ngành hàng),
                                        REFERENCES ("Điều X" bằng regex — cùng văn bản, hoặc văn bản
                                        khác nếu câu nêu tên chính sách đó), HAS_EXCEPTION_FOR (câu có
                                        từ khoá ngoại lệ + nêu đích danh 1 chương trình khuyến mãi)
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from graph.model import GraphAccumulator
from graph.normalize import (
    extract_store_codes,
    is_wildcard_store_scope,
    normalize_code,
    normalize_text,
    resolve_category,
    resolve_product_name,
    resolve_sku,
    split_category_expression,
)
from graph.resolve import fold_text, normalize_sku, normalize_store_code
from ingest.units import RecordUnit, units_from_docx, units_from_xlsx
from kb.ids import record_id_for

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "raw")

# Thứ tự build khi rebuild toàn bộ: master SKU trước (tồn kho/NCC cần để resolve), khuyến mãi trước
# chính sách (HAS_EXCEPTION_FOR cần danh sách khuyến mãi).
BUILD_ORDER = ("price_list", "inventory", "promotion", "supplier", "policy", "training")

# Builder X đổi -> các builder phụ thuộc cần làm lại phần rule (graph/sync.py).
DEPENDENTS: dict[str, tuple[str, ...]] = {
    "price_list": ("inventory", "supplier", "promotion"),
    "promotion": ("policy",),
    "supplier": ("supplier",),
}

_ARTICLE_REF_RE = re.compile(r"Điều\s+(\d+)")
_NGANH_HANG_LINE_RE = re.compile(r"Ngành hàng\s+(.+)$", re.MULTILINE)
_EXCEPTION_RE = re.compile(r"không áp dụng|ngoại lệ|không đổi trả|trừ khi|ngoại trừ|không được hưởng", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;:!?])\s+|\n+")
_SKU_IN_TEXT_RE = re.compile(r"\b[A-ZĐ]{2,5}-\d{2,6}\b")

# Từ đồng nghĩa header (đã fold) — file thật của khách đặt tên cột không thống nhất.
HEADER_SYNONYMS: dict[str, tuple[str, ...]] = {
    "sku": ("sku", "ma sku", "ma san pham", "ma hang", "ma sp"),
    "name": ("ten san pham", "ten hang", "san pham", "ten sp"),
    "price": ("gia", "gia ban", "don gia", "gia niem yet", "gia ban le"),
    "category": ("category", "nganh hang", "danh muc", "nhom hang"),
    "stock": ("ton kho", "so luong ton", "ton"),
    "min_stock": ("ton kho toi thieu", "ton toi thieu"),
    "status": ("trang thai", "tinh trang"),
    "updated_at": ("cap nhat luc", "ngay cap nhat", "thoi gian cap nhat"),
    "store": ("store", "cua hang", "ma cua hang", "chi nhanh"),
    "promo_name": ("ten chuong trinh", "chuong trinh", "ten khuyen mai"),
    "promo_categories": ("nganh hang ap dung",),
    "promo_skus": ("sku ap dung", "ma sku ap dung", "san pham ap dung"),
    "store_scope": ("pham vi cua hang", "cua hang ap dung", "pham vi"),
    "start_date": ("ngay bat dau", "tu ngay"),
    "end_date": ("ngay ket thuc", "den ngay"),
    "discount": ("muc giam", "uu dai", "muc uu dai"),
    "note": ("ghi chu",),
    "supplier_code": ("ma ncc", "ma nha cung cap"),
    "supplier_name": ("ten nha cung cap", "nha cung cap", "ten ncc"),
    "supplier_category": ("nganh hang cung cap",),
    "contact": ("nguoi lien he",),
    "phone": ("sdt", "so dien thoai", "dien thoai"),
    "email": ("email",),
    "address": ("dia chi",),
    "tax_code": ("ma so thue", "mst"),
    "partnership_date": ("ngay hop tac",),
}


def _fold_header(h: str) -> str:
    # header nhóm 2 tầng của parse_xlsx: "THÔNG TIN CHƯƠNG TRÌNH - Tên chương trình" -> lấy phần cuối
    tail = h.split(" - ")[-1]
    return fold_text(tail.replace("_", " "))


def map_fields(fields: dict[str, str]) -> dict[str, str]:
    """{header gốc: giá trị} -> {khoá chuẩn: giá trị} theo HEADER_SYNONYMS (khớp chính xác sau fold)."""
    out: dict[str, str] = {}
    for header, value in fields.items():
        fh = _fold_header(header)
        for key, syns in HEADER_SYNONYMS.items():
            if fh in syns and key not in out:
                out[key] = value
                break
    return out


def _to_number(value: Any) -> float | int | None:
    if value is None:
        return None
    s = re.sub(r"[^\d,.\-]", "", str(value))
    if not s:
        return None
    if s.count(".") > 1 or (s.count(".") == 1 and len(s.split(".")[-1]) == 3 and "," not in s):
        s = s.replace(".", "")
    s = s.replace(",", ".") if s.count(",") == 1 and "." not in s else s.replace(",", "")
    try:
        f = float(s)
    except ValueError:
        return None
    return int(f) if f.is_integer() else f


# =====================================================================================
# INPUT + CONTEXT
# =====================================================================================

@dataclass
class DocumentInput:
    document_id: str
    path: str
    filename: str
    source_type: str
    doc_type: str
    title: str
    series_key: str
    units: list[RecordUnit] | None = None  # truyền sẵn (từ ingest) để khỏi parse lại
    records: list[dict[str, Any]] = field(default_factory=list)  # KB record (cho LLM extraction training)

    def rid(self, location: str) -> str:
        return record_id_for(self.document_id, location)


@dataclass
class BuildContext:
    """Những gì graph ĐÃ có — đủ để build phần graph của 1 document mà không rebuild toàn bộ."""

    sku_props: dict[str, dict[str, Any]] = field(default_factory=dict)
    categories: list[str] = field(default_factory=list)
    promotions: dict[str, dict[str, Any]] = field(default_factory=dict)
    policies: dict[str, dict[str, Any]] = field(default_factory=dict)  # policy document id -> props
    policy_articles: set[str] = field(default_factory=set)
    suppliers_by_category: dict[str, set[str]] = field(default_factory=dict)
    supplier_props: dict[str, dict[str, Any]] = field(default_factory=dict)
    store_props: dict[str, dict[str, Any]] = field(default_factory=dict)
    product_aliases: dict[str, str] = field(default_factory=dict)

    def master_by_code(self) -> dict[str, dict[str, Any]]:
        return {sid: p for sid, p in self.sku_props.items() if not sid.startswith("SKU-LOCAL:")}

    def master_by_name_category(self) -> dict[tuple[str, str], list[str]]:
        out: dict[tuple[str, str], list[str]] = {}
        for sid, p in self.master_by_code().items():
            if p.get("name") and p.get("category"):
                key = (resolve_product_name(p["name"], self.product_aliases), normalize_text(p["category"]))
                out.setdefault(key, []).append(sid)
        return out

    def sku_by_category(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for sid, p in self.master_by_code().items():
            if p.get("category"):
                out.setdefault(normalize_text(p["category"]), []).append(sid)
        return out

    @classmethod
    def from_store(cls, store: Any, product_aliases: dict[str, str] | None = None) -> "BuildContext":
        ctx = cls(product_aliases=dict(product_aliases or {}))
        for n in store.list_nodes("SKU"):
            ctx.sku_props[n.id] = n.props
        ctx.categories = sorted(n.id for n in store.list_nodes("Category"))
        ctx.promotions = {n.id: n.props for n in store.list_nodes("Promotion")}
        for n in store.list_nodes("Policy"):
            if n.props.get("level") == "article":
                ctx.policy_articles.add(n.id)
            else:
                ctx.policies[n.id] = n.props
        for n in store.list_nodes("Supplier"):
            ctx.supplier_props[n.id] = n.props
            cat = n.props.get("category")
            if cat:
                ctx.suppliers_by_category.setdefault(cat, set()).add(n.id)
        ctx.store_props = {n.id: n.props for n in store.list_nodes("Store")}
        return ctx

    def absorb(self, acc: GraphAccumulator) -> None:
        """Cập nhật context sau khi build xong 1 document (dùng khi rebuild tuần tự trong bộ nhớ)."""
        for n in acc.node_list():
            label, nid, props = n["label"], n["id"], n["properties"]
            if label == "SKU":
                self.sku_props[nid] = {**self.sku_props.get(nid, {}), **props}
            elif label == "Category" and nid not in self.categories:
                self.categories = sorted([*self.categories, nid])
            elif label == "Promotion":
                self.promotions[nid] = props
            elif label == "Policy":
                if props.get("level") == "article":
                    self.policy_articles.add(nid)
                else:
                    self.policies[nid] = props
            elif label == "Supplier":
                self.supplier_props[nid] = {**self.supplier_props.get(nid, {}), **props}
                if props.get("category"):
                    self.suppliers_by_category.setdefault(props["category"], set()).add(nid)
            elif label == "Store":
                self.store_props[nid] = {**self.store_props.get(nid, {}), **props}

    def nodes_by_label(self) -> dict[str, list[dict[str, Any]]]:
        """Dạng đầu vào cho graph.resolve.EntityResolver."""
        return {
            "SKU": [{"id": k, **v} for k, v in self.sku_props.items()],
            "Category": [{"id": c} for c in self.categories],
            "Promotion": [{"id": k, **v} for k, v in self.promotions.items()],
            "Policy": [{"id": k, "level": "document", **v} for k, v in self.policies.items()],
            "Supplier": [{"id": k, **v} for k, v in self.supplier_props.items()],
            "Store": [{"id": k, **v} for k, v in self.store_props.items()],
        }


@dataclass
class BuildResult:
    builder: str
    acc: GraphAccumulator
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    llm_edges: int = 0


# =====================================================================================
# LOAD UNITS
# =====================================================================================

def load_units(doc: DocumentInput) -> list[RecordUnit]:
    if doc.units is not None:
        return doc.units
    if doc.source_type == "xlsx":
        from ingest.parsers import parse_xlsx

        doc.units = units_from_xlsx(parse_xlsx(doc.path))
    elif doc.source_type == "docx":
        from ingest.parsers import parse_docx

        doc.units = units_from_docx(parse_docx(doc.path))
    else:
        doc.units = []
    return doc.units


def detect_builder(doc: DocumentInput, units: list[RecordUnit]) -> str | None:
    """Chọn builder theo cột thật của file (xlsx) hoặc doc_type (docx/media) — file khách đặt doc_type
    sai vẫn build đúng nếu header nói rõ."""
    if doc.source_type == "xlsx":
        keys: set[str] = set()
        for u in units[:20]:
            if u.kind == "row":
                keys |= set(map_fields(u.meta.get("fields") or {}))
        if "promo_name" in keys:
            return "promotion"
        if "supplier_code" in keys:
            return "supplier"
        if "sku" in keys and "stock" in keys:
            return "inventory"
        if "sku" in keys and "price" in keys:
            return "price_list"
        return None
    if doc.source_type == "docx" and doc.doc_type in ("policy", "contract"):
        return "policy"
    if doc.doc_type == "training":
        return "training"
    return None


# =====================================================================================
# BUILDERS (rule-based)
# =====================================================================================

def _build_price_list(doc: DocumentInput, ctx: BuildContext, acc: GraphAccumulator, warnings: list[str]) -> dict[str, Any]:
    rows = 0
    for u in load_units(doc):
        if u.kind != "row":
            continue
        f = map_fields(u.meta.get("fields") or {})
        if not f.get("sku"):
            continue
        rid = doc.rid(u.location)
        sku = normalize_sku(f["sku"])
        category = normalize_text(f.get("category")) or None
        props = {"name": normalize_text(f.get("name")) or None, "price": _to_number(f.get("price")),
                 "category": category, "price_document": doc.document_id}
        acc.add_node("SKU", sku, props, [rid])
        if category:
            acc.add_node("Category", category, {}, [rid])
            acc.add_edge("BELONGS_TO", "SKU", sku, "Category", category, {"extracted_by": "rule"}, [rid])
        rows += 1
    return {"rows": rows}


def _build_inventory(doc: DocumentInput, ctx: BuildContext, acc: GraphAccumulator, warnings: list[str]) -> dict[str, Any]:
    master = ctx.master_by_code()
    by_name_cat = ctx.master_by_name_category()
    resolution: dict[str, int] = {"exact_code": 0, "unique_name_category": 0, "ambiguous_name_category": 0, "unresolved": 0}
    store_names: dict[str, str] = {}
    for u in load_units(doc):
        sheet = u.meta.get("sheet") or ""
        if u.kind == "sheet_title":
            store_names[sheet] = normalize_text(u.content)
            continue
        if u.kind != "row":
            continue
        f = map_fields(u.meta.get("fields") or {})
        if not f.get("sku") and not f.get("name"):
            continue
        rid = doc.rid(u.location)
        store = normalize_store_code(f.get("store") or sheet)
        acc.add_node("Store", store, {"display_name": store_names.get(sheet)}, [rid])
        res = resolve_sku(f.get("sku"), f.get("name"), f.get("category"), master, by_name_cat, ctx.product_aliases)
        resolution[res.method] += 1
        category = resolve_category(f.get("category") or "", ctx.categories) or normalize_text(f.get("category")) or None
        if res.method == "unresolved":
            acc.add_node("SKU", res.sku_id, {"name": normalize_text(f.get("name")), "category": category,
                                             "resolved": False}, [rid])
            if category:
                acc.add_node("Category", category, {}, [rid])
                acc.add_edge("BELONGS_TO", "SKU", res.sku_id, "Category", category, {"extracted_by": "rule"}, [rid])
        else:
            acc.add_node("SKU", res.sku_id, {}, [])  # node "chạm" để MATCH khi ghi edge (nguồn thuộc bảng giá)
            if res.method == "ambiguous_name_category":
                warnings.append(f"SKU tồn kho '{f.get('sku')}' ({f.get('name')!r}, {store}) khớp nhiều SKU master "
                                f"{res.candidates} — chọn {res.sku_id}.")
        acc.add_edge("STOCKS", "Store", store, "SKU", res.sku_id, {
            "ton_kho": _to_number(f.get("stock")), "ton_kho_toi_thieu": _to_number(f.get("min_stock")),
            "trang_thai": normalize_text(f.get("status")) or None, "cap_nhat_luc": f.get("updated_at"),
            "local_sku": normalize_code(f.get("sku")) if f.get("sku") else None,
            "sku_resolution_method": res.method, "extracted_by": "rule",
        }, [rid])
    # display_name cho store xuất hiện ở dòng tiêu đề sheet (sau dòng đầu đã add với None)
    for (label, nid), node in acc.nodes.items():
        if label == "Store" and not node["properties"].get("display_name"):
            name = next((v for k, v in store_names.items() if normalize_store_code(k) == nid), None)
            if name:
                node["properties"]["display_name"] = name
    return {"sku_resolution": resolution}


def _build_promotions(doc: DocumentInput, ctx: BuildContext, acc: GraphAccumulator, warnings: list[str]) -> dict[str, Any]:
    rows = 0
    for u in load_units(doc):
        if u.kind != "row":
            continue
        f = map_fields(u.meta.get("fields") or {})
        if not f.get("promo_name"):
            continue
        rid = doc.rid(u.location)
        promo = normalize_text(f["promo_name"])
        scope = f.get("store_scope") or ""
        acc.add_node("Promotion", promo, {
            "category_expr": f.get("promo_categories"), "store_scope_text": scope or None,
            "store_scope_codes": extract_store_codes(scope), "store_scope_wildcard": is_wildcard_store_scope(scope),
            "start_date": f.get("start_date"), "end_date": f.get("end_date"),
            "discount": f.get("discount"), "note": f.get("note"),
        }, [rid])
        for cat in split_category_expression(f.get("promo_categories") or "", ctx.categories):
            acc.add_node("Category", cat, {}, [rid])
            acc.add_edge("APPLIES_TO", "Promotion", promo, "Category", cat, {"extracted_by": "rule"}, [rid])
        sku_text = " ".join(filter(None, [f.get("promo_skus"), f.get("note")]))
        for code in sorted(set(_SKU_IN_TEXT_RE.findall(sku_text))):
            sku = normalize_sku(code)
            if sku in ctx.sku_props:
                acc.add_node("SKU", sku, {}, [rid])
                acc.add_edge("APPLIES_TO", "Promotion", promo, "SKU", sku, {"extracted_by": "rule"}, [rid])
        rows += 1
    return {"rows": rows}


def _build_suppliers(doc: DocumentInput, ctx: BuildContext, acc: GraphAccumulator, warnings: list[str]) -> dict[str, Any]:
    this_doc: list[tuple[str, str, str]] = []
    for u in load_units(doc):
        if u.kind != "row":
            continue
        f = map_fields(u.meta.get("fields") or {})
        if not f.get("supplier_code"):
            continue
        rid = doc.rid(u.location)
        sid = normalize_code(f["supplier_code"])
        category = resolve_category(f.get("supplier_category") or "", ctx.categories) or normalize_text(f.get("supplier_category")) or None
        acc.add_node("Supplier", sid, {
            "name": normalize_text(f.get("supplier_name")) or None, "category": category,
            "contact": f.get("contact"), "phone": f.get("phone"), "email": f.get("email"),
            "address": f.get("address"), "tax_code": f.get("tax_code"), "partnership_date": f.get("partnership_date"),
        }, [rid])
        if category:
            this_doc.append((sid, category, rid))
    suppliers_by_cat: dict[str, set[str]] = {k: set(v) for k, v in ctx.suppliers_by_category.items()}
    for sid, cat, _ in this_doc:
        suppliers_by_cat.setdefault(cat, set()).add(sid)
    sku_by_cat = ctx.sku_by_category()
    supplies = 0
    for sid, cat, rid in this_doc:
        if len(suppliers_by_cat.get(cat, set())) != 1:
            warnings.append(f"Bỏ qua SUPPLIES cho ngành hàng {cat!r}: có {len(suppliers_by_cat.get(cat, set()))} nhà "
                            "cung cấp (không phải 1-1) nên không suy ra được NCC nào cung cấp SKU nào.")
            continue
        for sku in sku_by_cat.get(cat, []):
            acc.add_node("SKU", sku, {}, [])
            acc.add_edge("SUPPLIES", "Supplier", sid, "SKU", sku, {"extracted_by": "rule", "basis": "category_1_1"}, [rid])
            supplies += 1
    return {"suppliers": len(this_doc), "supplies_edges": supplies}


def _short_title(title: str) -> str:
    t = normalize_text(title)
    return t[:1].upper() + t[1:].lower() if t.isupper() else t


def _policy_titles_in(text: str, ctx: BuildContext, own_id: str) -> list[str]:
    folded = fold_text(text)
    hits = []
    for pid, props in ctx.policies.items():
        if pid == own_id:
            continue
        title = props.get("title") or ""
        for cand in {fold_text(title), fold_text(re.sub(r"(?i)^(chính sách|quy trình|nội quy)\s+", "", title))}:
            if len(cand) >= 8 and cand in folded:
                hits.append(pid)
                break
    return hits


def _build_policy(doc: DocumentInput, ctx: BuildContext, acc: GraphAccumulator, warnings: list[str]) -> dict[str, Any]:
    units = load_units(doc)
    if not units:
        return {"articles": 0}
    pid = doc.series_key
    title = units[0].content.split("\n")[0] if units[0].kind == "section" else doc.title
    title = normalize_text(title) or doc.title
    display = _short_title(title)
    first_rid = doc.rid(units[0].location)
    acc.add_node("Policy", pid, {"level": "document", "title": title, "display": display,
                                  "document_id": doc.document_id, "filename": doc.filename}, [first_rid])
    ctx_local = BuildContext(**{**ctx.__dict__, "policies": {**ctx.policies, pid: {"title": title}}})

    # GOVERNS cấp văn bản: dòng "Ngành hàng ..." trong khối tiêu đề
    head = units[0]
    governs = 0
    for m in _NGANH_HANG_LINE_RE.finditer(head.content):
        for cat in split_category_expression(m.group(1), ctx.categories):
            acc.add_node("Category", cat, {}, [first_rid])
            acc.add_edge("GOVERNS", "Policy", pid, "Category", cat, {"extracted_by": "rule", "level": "document"}, [first_rid])
            governs += 1

    articles: dict[int, str] = {}
    for u in units:
        n = u.meta.get("article_no") if u.kind == "article" else None
        if n is not None and n not in articles:
            art_id = f"{pid}#dieu-{n}"
            articles[n] = art_id
            acc.add_node("Policy", art_id, {"level": "article", "article_no": n, "heading": u.meta.get("heading"),
                                             "parent_policy": pid, "title": title, "display": f"{display}, Điều {n}",
                                             "document_id": doc.document_id}, [doc.rid(u.location)])
            acc.add_edge("PART_OF", "Policy", art_id, "Policy", pid, {"extracted_by": "rule"}, [doc.rid(u.location)])

    references = exceptions = 0
    cat_folded = {fold_text(c): c for c in ctx.categories if len(fold_text(c)) >= 4}
    for u in units:
        rid = doc.rid(u.location)
        own_n = u.meta.get("article_no")
        src_id = articles.get(own_n, pid) if own_n is not None else pid
        body = u.content
        # GOVERNS cấp Điều: Điều nhắc tên ngành hàng (vd "Điều 5. Thời hạn đổi trả theo ngành hàng")
        if own_n is not None:
            folded_body = fold_text(body)
            for fc, cat in cat_folded.items():
                if re.search(rf"(?<!\w){re.escape(fc)}(?!\w)", folded_body):
                    acc.add_edge("GOVERNS", "Policy", src_id, "Category", cat, {"extracted_by": "rule", "level": "article"}, [rid])
                    governs += 1
        for sentence in _SENTENCE_SPLIT_RE.split(body):
            if not sentence.strip():
                continue
            other_policies = _policy_titles_in(sentence, ctx_local, pid)
            for m in _ARTICLE_REF_RE.finditer(sentence):
                n = int(m.group(1))
                if other_policies:
                    for other in other_policies:
                        target = f"{other}#dieu-{n}" if f"{other}#dieu-{n}" in ctx.policy_articles else other
                        acc.add_node("Policy", target, {}, [])
                        acc.add_edge("REFERENCES", "Policy", src_id, "Policy", target,
                                     {"extracted_by": "rule", "article_no": n, "evidence": sentence.strip()[:200]}, [rid])
                        references += 1
                elif n != own_n and n in articles:
                    acc.add_edge("REFERENCES", "Policy", src_id, "Policy", articles[n],
                                 {"extracted_by": "rule", "article_no": n, "evidence": sentence.strip()[:200]}, [rid])
                    references += 1
            if other_policies and not _ARTICLE_REF_RE.search(sentence):
                for other in other_policies:
                    acc.add_node("Policy", other, {}, [])
                    acc.add_edge("REFERENCES", "Policy", src_id, "Policy", other,
                                 {"extracted_by": "rule", "evidence": sentence.strip()[:200]}, [rid])
                    references += 1
            if _EXCEPTION_RE.search(sentence):
                folded_s = fold_text(sentence)
                for promo_id in ctx.promotions:
                    fp = fold_text(promo_id)
                    if len(fp) >= 6 and fp in folded_s:
                        acc.add_node("Promotion", promo_id, {}, [])
                        acc.add_edge("HAS_EXCEPTION_FOR", "Policy", src_id, "Promotion", promo_id,
                                     {"extracted_by": "rule", "evidence": sentence.strip()[:200]}, [rid])
                        exceptions += 1
    return {"articles": len(articles), "governs": governs, "references": references, "exceptions": exceptions}


_BUILDERS: dict[str, Callable[[DocumentInput, BuildContext, GraphAccumulator, list[str]], dict[str, Any]]] = {
    "price_list": _build_price_list,
    "inventory": _build_inventory,
    "promotion": _build_promotions,
    "supplier": _build_suppliers,
    "policy": _build_policy,
}


def _strip_placeholder_nodes(acc: GraphAccumulator, ctx: BuildContext) -> None:
    """Node "chạm" (add_node với props rỗng chỉ để MATCH edge) không được mang source_docs của doc
    này nếu node đó thuộc doc khác — tránh remove_document(doc) sau này làm node mất nguồn sai."""
    for (label, nid), node in list(acc.nodes.items()):
        if node["source_chunk_ids"] or node["properties"]:
            continue
        exists = (label == "SKU" and nid in ctx.sku_props) or (label == "Category" and nid in ctx.categories) or \
                 (label == "Promotion" and nid in ctx.promotions) or (label == "Policy" and (nid in ctx.policies or nid in ctx.policy_articles))
        if exists:
            node["source_docs"] = []


def build_document(doc: DocumentInput, ctx: BuildContext, *, extractor: Any = None,
                   builder: str | None = None) -> BuildResult:
    """Build phần graph của 1 document. extractor (graph.extract.GraphExtractor | None): chạy LLM
    extraction trên văn bản tự do (policy/training) — None = chỉ rule (không tốn LLM)."""
    acc = GraphAccumulator(document_id=doc.document_id)
    warnings: list[str] = []
    units = load_units(doc) if doc.source_type in ("xlsx", "docx") else []
    builder = builder or detect_builder(doc, units)
    if builder is None:
        return BuildResult(builder="none", acc=acc, warnings=[f"Không có builder graph cho {doc.filename} "
                                                              f"({doc.source_type}/{doc.doc_type}) — bỏ qua."])
    stats: dict[str, Any] = {}
    if builder in _BUILDERS:
        stats = _BUILDERS[builder](doc, ctx, acc, warnings)
    llm_edges = 0
    if extractor is not None and builder in ("policy", "training"):
        llm_acc = GraphAccumulator(document_id=f"{doc.document_id}~llm")
        llm_edges, llm_warn = extractor.extract_document(doc, ctx, acc, llm_acc)
        warnings.extend(llm_warn)
        for n in llm_acc.node_list():
            acc.nodes.setdefault((n["label"], n["id"]), n)
        for key, e in llm_acc.edges.items():
            if key in acc.edges:  # rule đã có cạnh này -> chỉ gộp bằng chứng, không đổi nguồn gốc rule
                continue
            acc.edges[key] = e
    _strip_placeholder_nodes(acc, ctx)
    return BuildResult(builder=builder, acc=acc, warnings=warnings, stats=stats, llm_edges=llm_edges)


# =====================================================================================
# BUILD TỪ THƯ MỤC (CLI/dry-run, không cần DB) — dùng dữ liệu mẫu data/raw
# =====================================================================================

_FILENAME_DOC_TYPES = (
    ("bang_gia", "price_list"), ("ton_kho", "inventory"), ("khuyen_mai", "promotion"),
    ("nha_cung_cap", "supplier"), ("chinh_sach", "policy"), ("quy_trinh", "policy"), ("noi_quy", "policy"),
)


def _guess_doc_type(filename: str) -> str:
    low = filename.lower()
    return next((dt for key, dt in _FILENAME_DOC_TYPES if key in low), "unknown")


def documents_from_dir(data_dir: str) -> list[DocumentInput]:
    from kb.lifecycle import derive_series_key

    docs = []
    for path in sorted(glob.glob(os.path.join(data_dir, "xlsx", "*.xlsx")) + glob.glob(os.path.join(data_dir, "docx", "*.docx"))):
        fname = os.path.basename(path)
        stem = os.path.splitext(fname)[0]
        docs.append(DocumentInput(document_id=stem, path=path, filename=fname, source_type=fname.rsplit(".", 1)[-1],
                                  doc_type=_guess_doc_type(fname), title=stem, series_key=derive_series_key(fname)))
    return docs


def build_graph(data_dir: str = DEFAULT_DATA_DIR, extractor: Any = None) -> tuple[GraphAccumulator, dict[str, Any]]:
    """Build TOÀN BỘ trong bộ nhớ từ 1 thư mục (dry-run/kiểm thử). -> (accumulator gộp, stats)."""
    start = time.perf_counter()
    ctx = BuildContext()
    merged = GraphAccumulator()
    per_doc: dict[str, Any] = {}
    warnings: list[str] = []
    docs = documents_from_dir(data_dir)
    for d in docs:
        load_units(d)
    order = {b: i for i, b in enumerate(BUILD_ORDER)}
    planned = sorted(((detect_builder(d, d.units or []), d) for d in docs), key=lambda x: order.get(x[0] or "", 99))
    for builder, d in planned:
        if builder is None:
            continue
        res = build_document(d, ctx, extractor=extractor, builder=builder)
        ctx.absorb(res.acc)
        for n in res.acc.node_list():
            merged.add_node(n["label"], n["id"], n["properties"], n["source_chunk_ids"])
            merged.nodes[(n["label"], n["id"])].setdefault("source_docs", [])
        for e in res.acc.edge_list():
            merged.add_edge(e["type"], e["from_label"], e["from_id"], e["to_label"], e["to_id"], e["properties"], e["source_chunk_ids"])
        per_doc[d.filename] = {"builder": builder, **res.stats, "nodes": len(res.acc.nodes), "edges": len(res.acc.edges)}
        warnings.extend(res.warnings)
    stats = {**merged.stats(), "build_seconds": round(time.perf_counter() - start, 4),
             "llm_cost_usd": 0.0 if extractor is None else None, "documents": per_doc, "warnings": warnings}
    return merged, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build graph (dry-run trong bộ nhớ) từ thư mục dữ liệu, không dùng LLM.")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--graph-out", default=None, help="Ghi đồ thị ra JSON để xem")
    args = parser.parse_args(argv)
    acc, stats = build_graph(args.data_dir)
    if args.graph_out:
        from graph.store import MemoryGraphStore

        store = MemoryGraphStore()
        store.write_accumulator(acc)
        store.save_json(args.graph_out)
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
