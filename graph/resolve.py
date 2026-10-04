"""Entity resolution (A3) — chuẩn hoá mã/tên và khớp thực thể trong câu chữ về node chuẩn của graph.

Ba lớp:
1. Hàm thuần (normalize_sku, fold_text, normalize_store_code): chuẩn hoá hoa/thường, khoảng trắng,
   biến thể gạch ngang, Unicode NFC. KHÔNG bao giờ đổi Đ<->D hay tự chèn tiền tố một cách mù quáng:
   dữ liệu thật có 2 hệ mã độc lập ("DM-001" tồn kho vs "ĐM-001" bảng giá — xem graph/normalize.py),
   nên biến thể chỉ được chấp nhận khi khớp DUY NHẤT một id đang tồn tại trong graph.
2. Alias table (bảng graph_aliases trong Postgres) — admin sửa được qua API/Admin UI: tên sản phẩm
   viết tắt, tên cửa hàng viết tắt ("Cầu Giấy", "HCM01"), tên ngành hàng gõ khác... Mỗi alias:
   (kind, alias, canonical). alias_norm = fold_text(alias) (hoặc normalize_sku với kind=sku).
3. EntityResolver: index dựng từ node graph + alias table -> resolve(kind, text) và
   find_mentions(câu hỏi) cho graph local search (A4, bước rule trước khi gọi LLM).
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# kind trong alias table -> label node graph
ALIAS_KINDS: dict[str, str] = {
    "sku": "SKU", "product": "SKU", "store": "Store", "category": "Category",
    "supplier": "Supplier", "promotion": "Promotion", "policy": "Policy",
}

_DASHES = "‐‑‒–—―−﹣－"
_DASH_RE = re.compile(f"[{_DASHES}]")
_WS_RE = re.compile(r"\s+")
_SKU_SPACED_DASH_RE = re.compile(r"\s*-\s*")
_SKU_TOKEN_RE = re.compile(r"(?<![\w-])([A-Za-zĐđ]{2,5})\s?-?\s?(\d{2,6})(?:-([A-Za-z0-9]+))?(?![\w-])")
_STORE_FULL_RE = re.compile(r"\bGM(?:\s?-\s?|\s)([A-Za-zĐđ]{2,3})\s?(\d{2})\b", re.IGNORECASE)
_STORE_BARE_RE = re.compile(r"(?<![\w-])([A-Za-z]{2,3})(\d{2})(?![\w-])")
_SUPPLIER_CODE_RE = re.compile(r"\bNCC\s?-?\s?(\d{2,4})\b", re.IGNORECASE)


# =====================================================================================
# HÀM THUẦN
# =====================================================================================

def normalize_text(s: Any) -> str:
    if s is None:
        return ""
    return _WS_RE.sub(" ", unicodedata.normalize("NFC", str(s))).strip()


def fold_text(s: Any) -> str:
    """Bỏ dấu + lowercase + gộp khoảng trắng — CHỈ dùng để so khớp, không dùng hiển thị."""
    decomposed = unicodedata.normalize("NFKD", normalize_text(s))
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    ascii_only = ascii_only.replace("Đ", "D").replace("đ", "d").lower()
    ascii_only = _DASH_RE.sub("-", ascii_only)
    return _WS_RE.sub(" ", re.sub(r"[^\w\s%-]", " ", ascii_only)).strip()


def normalize_sku(code: Any) -> str:
    """" đt - 018 " -> "ĐT-018"; "ĐT–018" (en dash) -> "ĐT-018"; "đt018" -> "ĐT018" (KHÔNG tự chèn
    gạch — biến thể chèn gạch chỉ thử ở EntityResolver khi khớp duy nhất id thật)."""
    s = unicodedata.normalize("NFC", str(code or ""))
    s = _DASH_RE.sub("-", s)
    s = _SKU_SPACED_DASH_RE.sub("-", s.strip())
    s = _WS_RE.sub("", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.upper()


def sku_variants(code: str) -> list[str]:
    """Các biến thể hợp lý của 1 mã SKU đã normalize: có/không gạch giữa phần chữ và số."""
    base = normalize_sku(code)
    out = [base]
    m = re.match(r"^([A-ZĐ]+)-?(\d+)(-.*)?$", base)
    if m:
        letters, digits, tail = m.group(1), m.group(2), m.group(3) or ""
        for v in (f"{letters}-{digits}{tail}", f"{letters}{digits}{tail}"):
            if v not in out:
                out.append(v)
    return out


def normalize_store_code(code: Any) -> str:
    """"gm hcm01" / "GM–HCM01" / "gm-hcm 01" -> "GM-HCM01". Mã không có tiền tố GM giữ nguyên dạng
    hoa (resolver sẽ thử thêm tiền tố nếu khớp duy nhất)."""
    s = normalize_text(code)
    m = _STORE_FULL_RE.search(s)
    if m:
        return f"GM-{m.group(1).upper()}{m.group(2)}"
    return _WS_RE.sub("", _DASH_RE.sub("-", s)).upper()


def normalize_alias(kind: str, alias: str) -> str:
    if kind == "sku":
        return normalize_sku(alias)
    if kind == "store":
        return fold_text(normalize_store_code(alias)) if _STORE_FULL_RE.search(alias) else fold_text(alias)
    return fold_text(alias)


# =====================================================================================
# ALIAS REPOSITORY (bảng graph_aliases)
# =====================================================================================

@dataclass
class AliasEntry:
    kind: str
    alias: str
    canonical: str
    id: int | None = None
    note: str | None = None


def load_aliases(session: Any) -> list[AliasEntry]:
    from sqlalchemy import select

    from core.models import GraphAlias

    rows = session.execute(select(GraphAlias)).scalars().all()
    return [AliasEntry(kind=r.kind, alias=r.alias, canonical=r.canonical, id=r.id, note=r.note) for r in rows]


def upsert_alias(session: Any, kind: str, alias: str, canonical: str, *, note: str | None = None,
                 actor: str | None = None) -> Any:
    """Idempotent theo (kind, alias_norm) — gọi lại cùng alias thì cập nhật canonical."""
    from sqlalchemy import select

    from core.errors import ValidationFailed
    from core.models import GraphAlias

    if kind not in ALIAS_KINDS:
        raise ValidationFailed(f"kind alias không hợp lệ: {kind!r}. Hợp lệ: {sorted(ALIAS_KINDS)}")
    alias_n = normalize_alias(kind, alias)
    if not alias_n or not normalize_text(canonical):
        raise ValidationFailed("alias và canonical không được rỗng")
    row = session.execute(select(GraphAlias).where(GraphAlias.kind == kind, GraphAlias.alias_norm == alias_n)).scalar_one_or_none()
    canonical_n = normalize_sku(canonical) if kind == "sku" else normalize_text(canonical)
    if row is None:
        row = GraphAlias(kind=kind, alias=normalize_text(alias), alias_norm=alias_n, canonical=canonical_n,
                         note=note, created_by=actor)
        session.add(row)
    else:
        row.alias = normalize_text(alias)
        row.canonical = canonical_n
        row.note = note
    session.flush()
    return row


def default_seed_aliases() -> list[AliasEntry]:
    """Alias khởi tạo cho dữ liệu mẫu GreenMart (scripts/seed.py nạp vào DB, admin sửa tiếp)."""
    return [
        AliasEntry("product", "Tivi LED 55in - TechVision", "Tivi LED 55 inch - TechVision"),
        AliasEntry("category", "đồ chơi", "Đồ chơi trẻ em"),
        AliasEntry("category", "văn phòng phẩm", "Văn phòng phẩm"),
        AliasEntry("category", "điện máy gia dụng", "Điện máy"),
        AliasEntry("store", "Cầu Giấy", "GM-HN01"),
        AliasEntry("store", "Thủ Đức", "GM-HCM02"),
        AliasEntry("policy", "chính sách đổi trả", "chinh_sach_doi_tra_hang_hoa"),
        AliasEntry("policy", "chính sách bảo hành", "chinh_sach_bao_hanh_san_pham"),
        AliasEntry("policy", "quy trình khiếu nại", "quy_trinh_xu_ly_khieu_nai"),
        AliasEntry("policy", "chính sách giao hàng", "chinh_sach_giao_hang"),
        AliasEntry("policy", "nội quy nhân viên", "noi_quy_nhan_vien_ban_hang"),
    ]


# =====================================================================================
# ENTITY RESOLVER
# =====================================================================================

@dataclass
class Resolution:
    label: str
    canonical_id: str | None
    method: str  # exact | alias | variant | fold | prefix | name | ambiguous | unresolved
    candidates: list[str] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.canonical_id is not None


@dataclass
class EntityMention:
    label: str
    id: str
    text: str
    method: str
    span: tuple[int, int] = (0, 0)


@dataclass
class _NameIndex:
    """fold(name) -> set(id) cho 1 label; dùng cả để resolve lẫn quét mention trong câu."""

    entries: dict[str, set[str]] = field(default_factory=dict)

    def add(self, name: str, node_id: str, min_len: int = 3) -> None:
        key = fold_text(name)
        if len(key) >= min_len:
            self.entries.setdefault(key, set()).add(node_id)


class EntityResolver:
    def __init__(self, nodes_by_label: dict[str, list[dict[str, Any]]], aliases: Iterable[AliasEntry] = ()) -> None:
        self.ids: dict[str, set[str]] = {label: set() for label in ALIAS_KINDS.values()}
        self.names: dict[str, _NameIndex] = {label: _NameIndex() for label in set(ALIAS_KINDS.values())}
        self.aliases: dict[tuple[str, str], str] = {}
        self._fold_ids: dict[str, dict[str, set[str]]] = {}
        self.policy_documents: dict[str, str] = {}
        for label, nodes in nodes_by_label.items():
            for props in nodes:
                node_id = str(props.get("id"))
                self.ids.setdefault(label, set()).add(node_id)
                self._fold_ids.setdefault(label, {}).setdefault(fold_text(node_id), set()).add(node_id)
                idx = self.names.setdefault(label, _NameIndex())
                if label == "SKU" and props.get("name"):
                    idx.add(props["name"], node_id, min_len=8)
                elif label in ("Category", "Promotion"):
                    idx.add(node_id, node_id, min_len=4)
                elif label == "Supplier" and props.get("name"):
                    idx.add(props["name"], node_id, min_len=6)
                elif label == "Store" and props.get("display_name"):
                    # "Cửa hàng GM-HN01 — Cầu Giấy, Hà Nội" -> alias "cầu giấy"
                    tail = re.split(r"[—–-]", props["display_name"])[-1]
                    idx.add(tail.split(",")[0], node_id, min_len=4)
                elif label == "Policy" and props.get("level") == "document" and props.get("title"):
                    idx.add(props["title"], node_id, min_len=8)
                    self.policy_documents[node_id] = props["title"]
        for a in aliases:
            label = ALIAS_KINDS.get(a.kind)
            if not label:
                continue
            canonical = normalize_sku(a.canonical) if a.kind == "sku" else normalize_text(a.canonical)
            if a.kind == "product":  # alias tên sản phẩm -> tên chuẩn -> id qua name index
                target_ids = self.names["SKU"].entries.get(fold_text(canonical), set())
                for tid in target_ids:
                    self.names["SKU"].add(a.alias, tid, min_len=3)
                if not target_ids and canonical in self.ids.get("SKU", set()):
                    self.names["SKU"].add(a.alias, canonical, min_len=3)
                continue
            self.aliases[(label, normalize_alias(a.kind, a.alias))] = canonical
            self.names[label].add(a.alias, canonical, min_len=3)

    # ------------------------------------------------------------------ resolve
    def resolve(self, label: str, text: str) -> Resolution:
        if label == "SKU":
            return self._resolve_sku(text)
        if label == "Store":
            return self._resolve_store(text)
        return self._resolve_generic(label, text)

    def _unique(self, label: str, candidates: set[str], method: str) -> Resolution | None:
        if len(candidates) == 1:
            return Resolution(label, next(iter(candidates)), method, sorted(candidates))
        if len(candidates) > 1:
            return Resolution(label, None, "ambiguous", sorted(candidates))
        return None

    def _resolve_sku(self, text: str) -> Resolution:
        ids = self.ids.get("SKU", set())
        code = normalize_sku(text)
        if code in ids:
            return Resolution("SKU", code, "exact", [code])
        alias = self.aliases.get(("SKU", code))
        if alias and alias in ids:
            return Resolution("SKU", alias, "alias", [alias])
        variants = {v for v in sku_variants(code) if v in ids}
        hit = self._unique("SKU", variants, "variant")
        if hit:
            return hit
        folded = {i for v in sku_variants(code) for i in self._fold_ids.get("SKU", {}).get(fold_text(v), set())}
        hit = self._unique("SKU", folded, "fold")
        if hit:
            return hit
        named = self.names["SKU"].entries.get(fold_text(text), set())
        hit = self._unique("SKU", named, "name")
        return hit or Resolution("SKU", None, "unresolved")

    def _resolve_store(self, text: str) -> Resolution:
        ids = self.ids.get("Store", set())
        code = normalize_store_code(text)
        if code in ids:
            return Resolution("Store", code, "exact", [code])
        prefixed = f"GM-{code}"
        if prefixed in ids:
            return Resolution("Store", prefixed, "prefix", [prefixed])
        alias = self.aliases.get(("Store", normalize_alias("store", text)))
        if alias:
            return Resolution("Store", alias, "alias", [alias])
        named = self.names["Store"].entries.get(fold_text(text), set())
        hit = self._unique("Store", named, "name")
        return hit or Resolution("Store", None, "unresolved")

    def _resolve_generic(self, label: str, text: str) -> Resolution:
        ids = self.ids.get(label, set())
        norm = normalize_text(text)
        if norm in ids:
            return Resolution(label, norm, "exact", [norm])
        kind = next((k for k, v in ALIAS_KINDS.items() if v == label and k != "product"), label.lower())
        alias = self.aliases.get((label, normalize_alias(kind, text)))
        if alias:
            return Resolution(label, alias, "alias", [alias])
        hit = self._unique(label, set(self._fold_ids.get(label, {}).get(fold_text(text), set())), "fold")
        if hit:
            return hit
        hit = self._unique(label, set(self.names.get(label, _NameIndex()).entries.get(fold_text(text), set())), "name")
        return hit or Resolution(label, None, "unresolved")

    # ------------------------------------------------------------------ mentions
    def find_mentions(self, question: str) -> list[EntityMention]:
        """Rule-based: mã SKU/cửa hàng/NCC bằng regex + tên (ngành hàng, khuyến mãi, chính sách, cửa
        hàng, sản phẩm, NCC, alias) bằng so khớp không dấu theo ranh giới từ. Ưu tiên match dài hơn,
        bỏ match chồng lấn."""
        found: list[EntityMention] = []
        for m in _SKU_TOKEN_RE.finditer(question):
            res = self._resolve_sku(m.group(0))
            if res.resolved:
                found.append(EntityMention("SKU", res.canonical_id, m.group(0), res.method, m.span()))
        for m in _STORE_FULL_RE.finditer(question):
            res = self._resolve_store(m.group(0))
            if res.resolved:
                found.append(EntityMention("Store", res.canonical_id, m.group(0), res.method, m.span()))
        for m in _STORE_BARE_RE.finditer(question):
            res = self._resolve_store(m.group(0))
            if res.resolved:
                found.append(EntityMention("Store", res.canonical_id, m.group(0), res.method, m.span()))
        for m in _SUPPLIER_CODE_RE.finditer(question):
            code = f"NCC-{m.group(1)}"
            if code in self.ids.get("Supplier", set()):
                found.append(EntityMention("Supplier", code, m.group(0), "exact", m.span()))

        folded_q = fold_text(question)
        for label, idx in self.names.items():
            for name_key, node_ids in idx.entries.items():
                for m in re.finditer(rf"(?<![\w]){re.escape(name_key)}(?![\w])", folded_q):
                    if len(node_ids) != 1:
                        continue
                    node_id = next(iter(node_ids))
                    found.append(EntityMention(label, node_id, name_key, "name", (-1 - m.start(), -1 - m.end())))
        return _dedupe_mentions(found, folded_q)


def _dedupe_mentions(mentions: list[EntityMention], folded_q: str) -> list[EntityMention]:
    """Giữ match dài nhất khi chồng lấn (name match dùng span âm trên câu đã fold — tách không gian
    span khỏi regex mã trên câu gốc), rồi bỏ trùng (label, id)."""
    def span_of(m: EntityMention) -> tuple[int, int, int]:
        if m.span[0] < 0:
            start, end = -1 - m.span[0], -1 - m.span[1]
            return (1, start, end)
        return (0, m.span[0], m.span[1])

    ordered = sorted(mentions, key=lambda m: -(span_of(m)[2] - span_of(m)[1]))
    kept: list[EntityMention] = []
    taken: dict[int, list[tuple[int, int]]] = {0: [], 1: []}
    for m in ordered:
        space, start, end = span_of(m)
        if any(not (end <= s or start >= e) for s, e in taken[space]):
            continue
        taken[space].append((start, end))
        kept.append(m)
    uniq: dict[tuple[str, str], EntityMention] = {}
    for m in kept:
        uniq.setdefault((m.label, m.id), m)
    return list(uniq.values())


# =====================================================================================
# CACHE resolver cho api (dựng lại khi graph/alias đổi)
# =====================================================================================

_resolver_cache: dict[str, Any] = {"resolver": None, "built_at": 0.0, "kb_version": None}


def build_resolver(store: Any, aliases: Iterable[AliasEntry]) -> EntityResolver:
    nodes = {label: [n.props for n in store.list_nodes(label)] for label in
             ("SKU", "Category", "Store", "Promotion", "Policy", "Supplier")}
    return EntityResolver(nodes, aliases)


def get_resolver(store: Any, session: Any, kb_version: int | None = None, ttl_seconds: float = 300.0) -> EntityResolver:
    now = time.monotonic()
    cached = _resolver_cache["resolver"]
    if cached is not None and _resolver_cache["kb_version"] == kb_version and now - _resolver_cache["built_at"] < ttl_seconds:
        return cached
    resolver = build_resolver(store, load_aliases(session))
    _resolver_cache.update(resolver=resolver, built_at=now, kb_version=kb_version)
    return resolver


def invalidate_resolver_cache() -> None:
    _resolver_cache.update(resolver=None, built_at=0.0, kb_version=None)
