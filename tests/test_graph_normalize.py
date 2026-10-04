"""Test cho graph/normalize.py — entity resolution thuần Python, không đụng file/DB nào."""
import pytest

from graph.normalize import (
    PRODUCT_NAME_ALIASES,
    extract_store_codes,
    is_wildcard_store_scope,
    normalize_code,
    normalize_text,
    resolve_category,
    resolve_product_name,
    resolve_sku,
    slugify,
    split_category_expression,
)

CATEGORIES = ["Điện máy", "Điện tử", "Gia dụng", "Thực phẩm", "Văn phòng phẩm", "Đồ chơi trẻ em", "Mỹ phẩm", "Thời trang"]


# =====================================================================================
# normalize_text / normalize_code / slugify
# =====================================================================================

def test_normalize_text_strips_and_collapses_whitespace():
    assert normalize_text("  Điện   máy \n") == "Điện máy"


def test_normalize_text_none_is_empty_string():
    assert normalize_text(None) == ""


def test_normalize_code_case_insensitive_for_vietnamese_diacritics():
    assert normalize_code("đt-018") == normalize_code("ĐT-018") == "ĐT-018"


def test_normalize_code_strips_surrounding_whitespace():
    assert normalize_code("  dm-001  ") == "DM-001"


def test_slugify_removes_diacritics_and_joins_with_dash():
    assert slugify("Sữa rửa mặt 100ml - PureSkin") == "sua-rua-mat-100ml-pureskin"


def test_slugify_is_stable_for_same_normalized_input():
    assert slugify("Điện Máy") == slugify("điện   máy")


# =====================================================================================
# resolve_category / split_category_expression
# =====================================================================================

def test_resolve_category_exact_match():
    assert resolve_category("Thực phẩm", CATEGORIES) == "Thực phẩm"


def test_resolve_category_accent_insensitive_alias():
    assert resolve_category("dien may", CATEGORIES) == "Điện máy"


def test_resolve_category_manual_alias_table():
    assert resolve_category("do choi", CATEGORIES) == "Đồ chơi trẻ em"


def test_resolve_category_unknown_returns_none():
    assert resolve_category("Ngành hàng không tồn tại", CATEGORIES) is None


def test_split_category_expression_hyphen_joined():
    assert split_category_expression("Điện máy - Gia dụng", CATEGORIES) == ["Điện máy", "Gia dụng"]


def test_split_category_expression_comma_joined():
    assert split_category_expression("Điện tử, Văn phòng phẩm", CATEGORIES) == ["Điện tử", "Văn phòng phẩm"]


def test_split_category_expression_wildcard_returns_all_categories():
    assert split_category_expression("Toàn ngành hàng", CATEGORIES) == CATEGORIES


def test_split_category_expression_single_category():
    assert split_category_expression("Thực phẩm", CATEGORIES) == ["Thực phẩm"]


def test_split_category_expression_drops_unresolvable_tokens_without_raising():
    assert split_category_expression("Thực phẩm, Ghi chú lạ không phải ngành hàng", CATEGORIES) == ["Thực phẩm"]


def test_split_category_expression_dedupes_repeated_categories():
    assert split_category_expression("Thực phẩm, Thực phẩm", CATEGORIES) == ["Thực phẩm"]


# =====================================================================================
# extract_store_codes / is_wildcard_store_scope
# =====================================================================================

def test_extract_store_codes_finds_single_code_in_free_text():
    assert extract_store_codes("Chỉ GM-HCM02") == ["GM-HCM02"]


def test_extract_store_codes_case_insensitive_and_normalizes():
    assert extract_store_codes("chỉ gm-hcm02 áp dụng") == ["GM-HCM02"]


def test_extract_store_codes_no_code_in_wildcard_text():
    assert extract_store_codes("Toàn hệ thống") == []


def test_is_wildcard_store_scope_true_for_toan_he_thong():
    assert is_wildcard_store_scope("Toàn hệ thống + Online")


def test_is_wildcard_store_scope_false_for_specific_store():
    assert not is_wildcard_store_scope("Chỉ GM-HCM02")


# =====================================================================================
# resolve_product_name / PRODUCT_NAME_ALIASES
# =====================================================================================

def test_resolve_product_name_returns_normalized_when_no_alias():
    assert resolve_product_name("  Gạo thơm  đặc sản 5kg - GoldRice ") == "Gạo thơm đặc sản 5kg - GoldRice"


def test_resolve_product_name_applies_known_alias():
    assert resolve_product_name("Tivi LED 55in - TechVision") == PRODUCT_NAME_ALIASES[normalize_text("Tivi LED 55in - TechVision")]
    assert resolve_product_name("Tivi LED 55in - TechVision") == "Tivi LED 55 inch - TechVision"


# =====================================================================================
# resolve_sku — kịch bản thật gặp trong data/raw (xem graph/normalize.py docstring)
# =====================================================================================

@pytest.fixture
def master():
    master_by_code = {"ĐM-017": {}, "GD-009": {}, "VPP-003": {}, "VPP-019": {}}
    master_by_name_category = {
        (resolve_product_name("Tivi LED 55 inch - TechVision"), "Điện máy"): ["ĐM-017"],
        (resolve_product_name("Nồi chiên không dầu 6L - HomeChef"), "Gia dụng"): ["GD-009"],
        (resolve_product_name("Giấy in A4 5 ràm/thùng - PrintSure"), "Văn phòng phẩm"): ["VPP-003", "VPP-019"],
    }
    return master_by_code, master_by_name_category


def test_resolve_sku_exact_code_match_even_when_case_differs(master):
    master_by_code, master_by_name_category = master
    result = resolve_sku("đm-017", "sản phẩm bất kỳ", "Điện máy", master_by_code, master_by_name_category)
    assert result.resolved and result.method == "exact_code" and result.sku_id == "ĐM-017"


def test_resolve_sku_does_not_cross_map_prefix_codes(master):
    """'DM-001' (tồn kho) KHÔNG được tự khớp 'ĐM-001' (nếu có) chỉ vì đổi tiền tố D<->Đ — 2 hệ
    đánh số độc lập (xem docstring graph/normalize.py). Phải rơi về khớp tên, không phải khớp mã."""
    master_by_code, master_by_name_category = master
    result = resolve_sku("DM-001", "Tivi LED 55 inch - TechVision", "Điện máy", master_by_code, master_by_name_category)
    assert result.method == "unique_name_category"
    assert result.sku_id == "ĐM-017"


def test_resolve_sku_unique_name_category_match(master):
    master_by_code, master_by_name_category = master
    result = resolve_sku("GD-001", "Nồi chiên không dầu 6L - HomeChef", "Gia dụng", master_by_code, master_by_name_category)
    assert result.resolved and result.method == "unique_name_category" and result.sku_id == "GD-009"


def test_resolve_sku_ambiguous_name_category_picks_smallest_candidate_deterministically(master):
    master_by_code, master_by_name_category = master
    result = resolve_sku("VP-001", "Giấy in A4 5 ràm/thùng - PrintSure", "Văn phòng phẩm", master_by_code, master_by_name_category)
    assert not result.resolved
    assert result.method == "ambiguous_name_category"
    assert result.sku_id == "VPP-003"
    assert result.candidates == ["VPP-003", "VPP-019"]


def test_resolve_sku_unresolved_falls_back_to_stable_id_from_name(master):
    master_by_code, master_by_name_category = master
    r1 = resolve_sku("DT-001", "Tai nghe bluetooth 5 - SoundMax", "Điện tử", master_by_code, master_by_name_category)
    r2 = resolve_sku("XX-999", "Tai nghe bluetooth 5 - SoundMax", "Điện tử", master_by_code, master_by_name_category)
    assert not r1.resolved and r1.method == "unresolved"
    # 2 store khác nhau bán CÙNG sản phẩm chưa có trong master -> phải gộp về CÙNG 1 node.
    assert r1.sku_id == r2.sku_id
