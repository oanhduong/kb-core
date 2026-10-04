"""Chuẩn hoá + entity resolution cho graph/build.py.

Dữ liệu nguồn (data/raw/xlsx) có 3 kiểu lệch chuẩn thật gặp phải khi build KG:

1. SKU code lệch case/khoảng trắng giữa 2 hệ (vd " đt-018 " so với "ĐT-018") -> normalize_code().
2. SKU code TRÙNG giữa 2 hệ nhưng KHÔNG cùng sản phẩm (bảng giá đánh số độc lập với tồn kho —
   "GD-001" ở bảng giá là "Nồi chiên không dầu 4L", còn "GD-001" ở tồn kho là "...6L"). Vì vậy
   KHÔNG được dùng SKU code để khớp giữa 2 hệ — phải khớp qua (tên sản phẩm đã chuẩn hoá, category).
   Xem resolve_sku().
3. Tên ngành hàng viết tắt/gộp trong xlsx khuyến mãi ("Điện máy - Gia dụng", "Toàn ngành hàng") và
   mã cửa hàng viết tắt trong cột phạm vi ("Chỉ GM-HCM02") -> split_category_expression(),
   extract_store_codes().

Alias table (PRODUCT_NAME_ALIASES, CATEGORY_ALIASES) xử lý trường hợp 2 hệ gọi cùng 1 thực thể
bằng 2 chuỗi khác nhau hẳn (không chỉ khác hoa/thường) — ví dụ tên sản phẩm viết tắt. Dữ liệu mẫu
hiện tại không có case này nhưng cơ chế được test độc lập (xem tests/test_graph_normalize.py) để
sẵn sàng khi dữ liệu thật có alias.
"""
import re
import unicodedata
from dataclasses import dataclass, field

# =====================================================================================
# CHUẨN HOÁ CHUỖI CƠ BẢN
# =====================================================================================

_WS_RE = re.compile(r"\s+")


def normalize_text(s):
    """NFC + strip + gộp khoảng trắng liên tiếp. Không đụng dấu tiếng Việt (giữ nguyên ý nghĩa)."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFC", str(s))
    return _WS_RE.sub(" ", s).strip()


def normalize_code(s):
    """Chuẩn hoá mã (SKU code, store code, mã NCC): NFC + strip + upper.

    Xử lý đúng case tiếng Việt có dấu (vd "đt-018" -> "ĐT-018") vì .upper() trên chuỗi đã NFC
    hoạt động đúng theo Unicode case folding, không chỉ ASCII.
    """
    return normalize_text(s).upper()


def slugify(s):
    """Chuyển chuỗi có dấu thành slug ASCII (lowercase, '-' nối từ) — dùng làm id ổn định cho node
    tổng hợp từ tên (khi không có code định danh, xem resolve_sku nhánh 'unresolved')."""
    text = normalize_text(s)
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    ascii_only = ascii_only.replace("Đ", "D").replace("đ", "d")
    ascii_only = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    return ascii_only or "x"


# =====================================================================================
# ALIAS TABLE — SẢN PHẨM
# =====================================================================================

# Alias thủ công: tên viết tắt/khác biệt đã biết -> tên chuẩn (đúng như trong bảng giá SKU, nguồn
# chủ - "master" của danh mục sản phẩm). Khoá đã qua normalize_text(). Dữ liệu mẫu hiện tại KHÔNG
# có alias thật nào (2 hệ dùng chung 1 chuỗi tên khi trỏ cùng sản phẩm) nhưng cơ chế vẫn cần tồn
# tại + có test, vì đây là điểm chuẩn hoá mong manh nhất khi nạp dữ liệu thật từ hệ thống khác.
PRODUCT_NAME_ALIASES = {
    normalize_text("Tivi LED 55in - TechVision"): "Tivi LED 55 inch - TechVision",
}


def resolve_product_name(name, extra_aliases=None):
    """Chuẩn hoá tên sản phẩm rồi tra alias table -> tên chuẩn dùng làm khoá khớp với master.
    extra_aliases: dict tên đã normalize_text -> tên chuẩn, nạp từ bảng graph_aliases (kind=product)
    — admin sửa được không cần deploy; ưu tiên hơn PRODUCT_NAME_ALIASES tĩnh."""
    key = normalize_text(name)
    if extra_aliases and key in extra_aliases:
        return extra_aliases[key]
    return PRODUCT_NAME_ALIASES.get(key, key)


# =====================================================================================
# ALIAS TABLE — NGÀNH HÀNG (CATEGORY)
# =====================================================================================

# Alias thủ công cho biến thể viết tắt/không dấu đã biết -> tên chuẩn. Khoá qua _fold() (bỏ dấu +
# lowercase) để bắt luôn biến thể gõ không dấu, không chỉ biến thể liệt kê cứng ở đây.
CATEGORY_ALIASES = {
    "do choi": "Đồ chơi trẻ em",
    "van phong pham": "Văn phòng phẩm",
    "dien may gia dung": "Điện máy",
}

_CATEGORY_SPLIT_RE = re.compile(r"\s*(?:,|;|&|\+| và |\s-\s)\s*", re.IGNORECASE)


def _fold(s):
    """Bỏ dấu + lowercase — CHỈ dùng để so khớp alias/category, không dùng làm id hiển thị."""
    decomposed = unicodedata.normalize("NFKD", normalize_text(s))
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return ascii_only.replace("Đ", "D").replace("đ", "d").lower()


# Khoá đã fold (bỏ dấu + lowercase) — phải fold luôn phrase wildcard, không thì so với _fold(text)
# (đã bỏ dấu) không bao giờ khớp.
_WILDCARD_CATEGORY_PHRASES = {_fold(p) for p in ("toàn ngành hàng", "tất cả ngành hàng", "toàn bộ ngành hàng")}


def resolve_category(text, canonical_categories):
    """1 token ngành hàng -> tên chuẩn trong canonical_categories, hoặc None nếu không khớp được.

    Thử theo thứ tự: khớp chính xác (chuẩn hoá) -> alias table (CATEGORY_ALIASES, so khớp không
    dấu) -> khớp không dấu trực tiếp với canonical_categories (phòng trường hợp gõ thiếu dấu).
    """
    norm = normalize_text(text)
    for cat in canonical_categories:
        if normalize_text(cat) == norm:
            return cat

    folded = _fold(text)
    if folded in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[folded]

    for cat in canonical_categories:
        if _fold(cat) == folded:
            return cat
    return None


def split_category_expression(text, canonical_categories):
    """Chuỗi "ngành hàng áp dụng" trong xlsx khuyến mãi (vd "Điện máy - Gia dụng",
    "Điện tử, Văn phòng phẩm", "Toàn ngành hàng") -> list tên ngành hàng chuẩn, không trùng lặp,
    giữ thứ tự xuất hiện. Token nào không khớp được category nào thì bỏ qua (không raise — dữ liệu
    khuyến mãi có thể có ghi chú tự do lẫn vào)."""
    norm = normalize_text(text)
    if _fold(norm) in _WILDCARD_CATEGORY_PHRASES:
        return list(canonical_categories)

    resolved = []
    for token in _CATEGORY_SPLIT_RE.split(norm):
        token = token.strip()
        if not token:
            continue
        cat = resolve_category(token, canonical_categories)
        if cat and cat not in resolved:
            resolved.append(cat)
    return resolved


# =====================================================================================
# MÃ CỬA HÀNG TRONG VĂN BẢN TỰ DO (cột "Phạm vi cửa hàng" của xlsx khuyến mãi)
# =====================================================================================

_STORE_CODE_RE = re.compile(r"\bGM-[A-ZĐ]{2,3}\d{2}\b", re.IGNORECASE)
_WILDCARD_STORE_PHRASES = {_fold("toàn hệ thống")}


def extract_store_codes(text):
    """Trích mọi mã cửa hàng dạng "GM-XX##" xuất hiện trong văn bản tự do (vd "Chỉ GM-HCM02"),
    đã chuẩn hoá qua normalize_code(). "Toàn hệ thống"/biến thể có + kênh khác (vd "+ Online") vẫn
    trả list rỗng — wildcard này không có mã cụ thể để trích, gọi nơi dùng tự xử lý theo ngữ cảnh."""
    return sorted({normalize_code(m.group(0)) for m in _STORE_CODE_RE.finditer(text)})


def is_wildcard_store_scope(text):
    folded = _fold(text)
    return any(phrase in folded for phrase in _WILDCARD_STORE_PHRASES)


# =====================================================================================
# ENTITY RESOLUTION — SKU
# =====================================================================================

@dataclass
class SkuResolution:
    sku_id: str
    resolved: bool
    method: str  # "exact_code" | "unique_name_category" | "ambiguous_name_category" | "unresolved"
    candidates: list = field(default_factory=list)


def resolve_sku(local_code, local_name, local_category, master_by_code, master_by_name_category, product_aliases=None):
    """Khớp 1 dòng tồn kho (local_code/local_name/local_category, đọc thẳng từ xlsx tồn kho) với
    SKU chuẩn trong bảng giá (master). KHÔNG bao giờ suy ra bằng cách biến đổi qua lại giữa 2 tiền
    tố mã (vd "DM-" <-> "ĐM-") — 2 hệ đánh số độc lập nên phép biến đổi đó cho kết quả sai (xem
    docstring đầu file). Thứ tự thử:

    1. Mã trùng chính xác (sau normalize_code) với 1 SKU trong master -> khớp mã thật (case/khoảng
       trắng lệch chuẩn, KHÔNG phải lệch tiền tố).
    2. (tên đã chuẩn hoá qua alias, category chuẩn hoá) khớp DUY NHẤT 1 dòng master -> khớp tên.
    3. Khớp NHIỀU dòng master (master có SKU trùng tên trong cùng category, xảy ra thật trong dữ
       liệu mẫu) -> vẫn resolve về ứng viên nhỏ nhất (thứ tự chuỗi) để đồ thị có 1 node, nhưng đánh
       dấu ambiguous_name_category + giữ toàn bộ candidates để nơi gọi tự quyết định có tin hay không.
    4. Không khớp gì -> tạo id tổng hợp ổn định từ tên (KHÔNG dùng local_code, vì local_code không
       phải định danh sản phẩm) để mọi cửa hàng cùng bán 1 sản phẩm chưa có trong master vẫn gộp
       về 1 node duy nhất.
    """
    code = normalize_code(local_code)
    if code in master_by_code:
        return SkuResolution(sku_id=code, resolved=True, method="exact_code", candidates=[code])

    name_key = resolve_product_name(local_name, product_aliases)
    category = normalize_text(local_category)
    candidates = sorted(master_by_name_category.get((name_key, category), []))
    if len(candidates) == 1:
        return SkuResolution(sku_id=candidates[0], resolved=True, method="unique_name_category", candidates=candidates)
    if len(candidates) > 1:
        return SkuResolution(
            sku_id=candidates[0], resolved=False, method="ambiguous_name_category", candidates=candidates
        )

    fallback_id = f"SKU-LOCAL:{slugify(name_key)}"
    return SkuResolution(sku_id=fallback_id, resolved=False, method="unresolved", candidates=[])
