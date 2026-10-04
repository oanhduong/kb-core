"""Unit test entity resolution (A3): normalize SKU/store, alias table, resolve, find_mentions."""
import pytest

from graph.resolve import (
    AliasEntry,
    EntityResolver,
    fold_text,
    normalize_alias,
    normalize_sku,
    normalize_store_code,
    sku_variants,
)


@pytest.mark.parametrize("raw,expected", [
    (" đt-018 ", "ĐT-018"),
    ("ĐT–018", "ĐT-018"),          # en dash
    ("ĐT — 018", "ĐT-018"),        # em dash + khoảng trắng
    ("dt 018", "DT018"),           # không tự chèn gạch, không tự đổi D<->Đ
    ("vpp-003", "VPP-003"),
    ("ab-01-x", "AB-01-X"),
])
def test_normalize_sku(raw, expected):
    assert normalize_sku(raw) == expected


def test_sku_variants_with_and_without_dash():
    assert sku_variants("dt018") == ["DT018", "DT-018"]
    assert sku_variants("ĐT-018") == ["ĐT-018", "ĐT018"]


@pytest.mark.parametrize("raw,expected", [
    ("gm hcm01", "GM-HCM01"), ("GM–HN01", "GM-HN01"), ("gm-hcm 02", "GM-HCM02"), ("hcm01", "HCM01"),
])
def test_normalize_store_code(raw, expected):
    assert normalize_store_code(raw) == expected


def test_fold_text_removes_diacritics_case_and_extra_spaces():
    assert fold_text("  Điện   Máy ") == "dien may"
    assert fold_text("Đồ chơi trẻ em") == "do choi tre em"


def test_normalize_alias_per_kind():
    assert normalize_alias("sku", " đt - 018") == "ĐT-018"
    assert normalize_alias("store", "Cầu  Giấy") == "cau giay"


@pytest.fixture
def resolver():
    nodes = {
        "SKU": [{"id": "ĐT-018", "name": "Loa bluetooth di động 30W - NovaPhone"},
                {"id": "DM-001", "name": "Tivi LED 55 inch - TechVision"},
                {"id": "ĐM-001", "name": "Tủ lạnh 300L"},
                {"id": "VPP-003", "name": "Giấy in A4 5 ram"}],
        "Store": [{"id": "GM-HCM01", "display_name": "Cửa hàng GM-HCM01 — Quận 1, TP.HCM"},
                  {"id": "GM-HN01", "display_name": "Cửa hàng GM-HN01 — Cầu Giấy, Hà Nội"}],
        "Category": [{"id": "Điện tử"}, {"id": "Điện máy"}, {"id": "Đồ chơi trẻ em"}],
        "Promotion": [{"id": "Tuần lễ điện máy xanh"}],
        "Policy": [{"id": "chinh_sach_doi_tra_hang_hoa", "level": "document", "title": "CHÍNH SÁCH ĐỔI TRẢ HÀNG HÓA"}],
        "Supplier": [{"id": "NCC-01", "name": "Công ty TNHH Điện tử Sao Việt"}],
    }
    aliases = [AliasEntry("product", "tivi 55in", "Tivi LED 55 inch - TechVision"),
               AliasEntry("store", "Q1", "GM-HCM01"), AliasEntry("category", "đồ chơi", "Đồ chơi trẻ em"),
               AliasEntry("sku", "LOA30", "ĐT-018")]
    return EntityResolver(nodes, aliases)


def test_resolve_sku_exact_variant_alias_and_name(resolver):
    assert resolver.resolve("SKU", "đt-018").canonical_id == "ĐT-018"
    assert resolver.resolve("SKU", "ĐT018").method == "variant"
    assert resolver.resolve("SKU", "loa30").canonical_id == "ĐT-018"
    assert resolver.resolve("SKU", "tivi 55in").canonical_id == "DM-001"


def test_resolve_sku_ambiguous_fold_is_not_guessed(resolver):
    # "dm-001" khớp cả DM-001 (exact) — exact thắng; nhưng "dm001" fold ra 2 id khác hệ -> phải exact/variant
    assert resolver.resolve("SKU", "dm-001").canonical_id == "DM-001"
    res = resolver.resolve("SKU", "đm001")
    assert res.canonical_id == "ĐM-001"  # biến thể chèn gạch khớp duy nhất ĐM-001, không lẫn DM-001


def test_resolve_store_prefix_alias_and_display_name(resolver):
    assert resolver.resolve("Store", "hcm01").canonical_id == "GM-HCM01"
    assert resolver.resolve("Store", "Q1").canonical_id == "GM-HCM01"
    assert resolver.resolve("Store", "cầu giấy").canonical_id == "GM-HN01"
    assert not resolver.resolve("Store", "Đà Nẵng").resolved


def test_resolve_category_alias_and_unaccented(resolver):
    assert resolver.resolve("Category", "do choi").canonical_id == "Đồ chơi trẻ em"
    assert resolver.resolve("Category", "dien tu").canonical_id == "Điện tử"


def test_find_mentions_extracts_codes_and_names_without_overlap(resolver):
    q = "SKU ĐT-018 có được áp dụng Tuần lễ điện máy xanh ở GM-HCM01 không, theo chính sách đổi trả hàng hóa?"
    found = {(m.label, m.id) for m in resolver.find_mentions(q)}
    assert ("SKU", "ĐT-018") in found
    assert ("Store", "GM-HCM01") in found
    assert ("Promotion", "Tuần lễ điện máy xanh") in found
    assert ("Policy", "chinh_sach_doi_tra_hang_hoa") in found
    # "điện máy" nằm TRONG tên khuyến mãi -> không được tách thêm thành ngành hàng Điện máy
    assert ("Category", "Điện máy") not in found


def test_find_mentions_unknown_codes_are_ignored(resolver):
    assert resolver.find_mentions("mã XY-999 giá bao nhiêu") == []
