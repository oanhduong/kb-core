"""Unit test phần xác định của ingest/KB lifecycle: series_key (C2), doc_type/ngày hiệu lực/phiên bản,
metadata từng record, đơn vị record từ parser, record_id ổn định."""
import pytest

from ingest.metadata import classify_doc_type_by_rules, extract_effective_date, extract_version, record_fields, short_location
from ingest.units import RecordUnit, _row_fields, article_number, units_from_docx
from kb.ids import content_hash, document_id_of, record_id_for
from kb.lifecycle import derive_series_key


@pytest.mark.parametrize("a,b", [
    ("bang_gia_thang_8_2026.xlsx", "Bảng giá T9.xlsx"),
    ("01_chinh_sach_doi_tra_v3.1.docx", "Chính sách đổi trả v3.2.docx"),
    ("lich_khuyen_mai_Q3_2026.xlsx", "lich khuyen mai quy 4 2026.xlsx"),
    ("ton kho 2026-08-20.xlsx", "ton_kho_2026_09_01.xlsx"),
])
def test_series_key_same_for_two_periods_of_same_document(a, b):
    assert derive_series_key(a) == derive_series_key(b)


def test_series_key_differs_for_different_documents():
    assert derive_series_key("bang_gia.xlsx") != derive_series_key("bang_chiet_khau.xlsx")


@pytest.mark.parametrize("filename,head,stype,expected", [
    ("01_bang_gia_sku.xlsx", "", "xlsx", "price_list"),
    ("02_ton_kho_theo_store.xlsx", "", "xlsx", "inventory"),
    ("file.xlsx", "sku là A, ten_san_pham là B, ton_kho là 5", "xlsx", "inventory"),
    ("file.xlsx", "Mã NCC là NCC-01", "xlsx", "supplier"),
    ("Poster KM thang 7.png", "", "image", "promotion"),
    ("05_chinh_sach_giao_hang.docx", "", "docx", "policy"),
    ("ghi_am_01.mp3", "", "audio", "training"),
    ("abc.pdf", "", "pdf", None),
])
def test_classify_doc_type_by_rules(filename, head, stype, expected):
    assert classify_doc_type_by_rules(filename, head, stype) == expected


@pytest.mark.parametrize("text,filename,expected", [
    ("Áp dụng toàn hệ thống — Ban hành: 01/01/2026 — Phiên bản 3.2", "", "2026-01-01"),
    ("Chính sách có hiệu lực kể từ ngày 15/03/2026.", "", "2026-03-15"),
    ("Ngày bắt đầu là 2026-07-01", "", "2026-07-01"),
    ("không có ngày", "bang_gia_thang_8_2026.xlsx", "2026-08-01"),
    ("không có ngày", "ton_kho_2026-09-15.xlsx", "2026-09-15"),
    ("không có ngày", "file.xlsx", None),
])
def test_extract_effective_date(text, filename, expected):
    assert extract_effective_date(text, filename) == expected


def test_extract_version():
    assert extract_version("Ban hành 01/01/2026 — Phiên bản 3.2") == "3.2"
    assert extract_version("v2.0 cập nhật") == "2.0"
    assert extract_version("không có") is None


def test_record_fields_price_row_and_inventory_row_store_from_sheet():
    price = RecordUnit("Gia!A3", "sku là ĐT-018, ten_san_pham là Loa, gia là 1440000, category là Điện tử", "row",
                       {"sheet": "Gia", "row": 3, "fields": {"sku": "ĐT-018", "ten_san_pham": "Loa", "gia": "1440000", "category": "Điện tử"}})
    f = record_fields(price, doc_type="price_list", doc_store=None)
    assert f["skus"] == ["ĐT-018"] and f["entity_key"] == "price_list:ĐT-018|*" and f["category"] == "Điện tử"
    inv = RecordUnit("GM-HN01!A3", "sku là DM-001, ...", "row", {"sheet": "GM-HN01", "row": 3, "fields": {"sku": "DM-001", "ton_kho": "5"}})
    g = record_fields(inv, doc_type="inventory", doc_store=None)
    assert g["store_code"] == "GM-HN01" and g["entity_key"] == "inventory:DM-001|GM-HN01"


def test_row_fields_split_respects_header_order_even_with_commas_in_values():
    text = "Tên là Nồi chiên, loại lớn, Giá là 1.000.000, Ghi chú là Hàng mới, chính hãng"
    assert _row_fields(text, ["Tên", "Giá", "Ghi chú"]) == {"Tên": "Nồi chiên, loại lớn", "Giá": "1.000.000",
                                                            "Ghi chú": "Hàng mới, chính hãng"}


def test_units_from_docx_groups_paragraphs_under_article():
    parsed = {"chunks": [
        {"text": "CHÍNH SÁCH X", "location": "CHÍNH SÁCH X", "kind": "heading"},
        {"text": "Ban hành: 01/01/2026", "location": "CHÍNH SÁCH X", "kind": "text"},
        {"text": "Điều 5. Thời hạn", "location": "CHÍNH SÁCH X > Điều 5. Thời hạn", "kind": "heading"},
        {"text": "Điện tử 15 ngày.", "location": "CHÍNH SÁCH X > Điều 5. Thời hạn", "kind": "text"},
        {"text": "Thực phẩm không đổi.", "location": "CHÍNH SÁCH X > Điều 5. Thời hạn", "kind": "text"},
    ]}
    units = units_from_docx(parsed)
    art = [u for u in units if u.kind == "article"][0]
    assert art.meta["article_no"] == 5 and art.content == "Điều 5. Thời hạn\nĐiện tử 15 ngày.\nThực phẩm không đổi."
    assert article_number("A > Điều 12. Khác") == 12


def test_record_id_is_deterministic_and_reversible():
    rid = record_id_for("doc1", "  CHÍNH SÁCH  >  Điều 5 ")
    assert rid == "doc1:CHÍNH SÁCH > Điều 5" and document_id_of(rid) == "doc1"
    assert record_id_for("doc1", "Sheet!A2", 2) == "doc1:Sheet!A2#2"
    assert content_hash("Giá  Loa") == content_hash("giá loa")


def test_short_location_for_context_prefix():
    assert short_location("xlsx", "HCM01!A42") == "Sheet HCM01 dòng 42"
    assert short_location("docx", "A > B > Điều 5. X") == "B › Điều 5. X"
