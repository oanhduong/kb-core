"""Test graph/build.py (A1) + cập nhật incremental trên MemoryGraphStore (A6, phần thuần).

- End-to-end trên data thật (data/raw/xlsx, data/raw/docx): đủ loại node/edge, 0 chi phí LLM, mọi
  node/edge có source_chunk_ids trỏ về record_id hợp lệ.
- Fixture nhỏ tự dựng cho từng nhánh (GOVERNS, REFERENCES "Điều X", HAS_EXCEPTION_FOR, header đồng nghĩa).
- Incremental: build từng document vào store, gỡ/build lại 1 document không đụng phần của document khác.
"""
from docx import Document as DocxDocument
from openpyxl import Workbook

from graph.build import (
    DEFAULT_DATA_DIR,
    BuildContext,
    DocumentInput,
    build_document,
    build_graph,
    detect_builder,
    load_units,
    map_fields,
)
from graph.store import MemoryGraphStore, NodeRef
from kb.lifecycle import derive_series_key

EXPECTED_NODE_LABELS = {"SKU", "Category", "Store", "Promotion", "Policy", "Supplier"}
EXPECTED_EDGE_TYPES = {"BELONGS_TO", "STOCKS", "APPLIES_TO", "SUPPLIES", "GOVERNS", "REFERENCES", "PART_OF"}


# =====================================================================================
# helpers
# =====================================================================================

def _doc(path, doc_type, doc_id=None):
    fname = path.name if hasattr(path, "name") else str(path).rsplit("/", 1)[-1]
    return DocumentInput(document_id=doc_id or fname.split(".")[0], path=str(path), filename=fname,
                         source_type=fname.rsplit(".", 1)[-1], doc_type=doc_type, title=fname,
                         series_key=derive_series_key(fname))


def _policy_docx(path, title, subtitle, articles):
    doc = DocxDocument()
    doc.add_heading(title, level=0)
    doc.add_paragraph(subtitle)
    for heading, paragraphs in articles:
        doc.add_heading(heading, level=2)
        for p in paragraphs:
            doc.add_paragraph(p)
    doc.save(path)


def _xlsx(path, title, header, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = title
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(path)


# =====================================================================================
# End-to-end trên data/raw thật
# =====================================================================================

def test_build_graph_on_real_data_has_every_node_label_and_edge_type():
    acc, stats = build_graph(DEFAULT_DATA_DIR)
    assert set(stats["node_count_by_label"]) == EXPECTED_NODE_LABELS
    assert EXPECTED_EDGE_TYPES.issubset(set(stats["edge_count_by_type"]))
    assert stats["node_count_by_label"]["SKU"] >= 300
    assert stats["node_count_by_label"]["Store"] == 8


def test_build_graph_on_real_data_never_calls_llm():
    _, stats = build_graph(DEFAULT_DATA_DIR)
    assert stats["llm_cost_usd"] == 0.0


def test_real_data_every_edge_has_source_chunk_ids_in_record_id_format():
    acc, _ = build_graph(DEFAULT_DATA_DIR)
    for e in acc.edge_list():
        assert e["source_chunk_ids"], e
        for rid in e["source_chunk_ids"]:
            doc_id, _, loc = rid.partition(":")
            assert doc_id and loc


def test_real_data_policy_dieu_references_link_articles_of_same_document():
    acc, _ = build_graph(DEFAULT_DATA_DIR)
    refs = [e for e in acc.edge_list() if e["type"] == "REFERENCES"]
    # "Điều 4 ... trong thời hạn quy định tại Điều 5" -> chinh_sach_doi_tra_hang_hoa#dieu-4 -> #dieu-5
    assert any(e["from_id"] == "chinh_sach_doi_tra_hang_hoa#dieu-4" and e["to_id"] == "chinh_sach_doi_tra_hang_hoa#dieu-5"
               for e in refs)


def test_real_data_inventory_rows_all_resolved_or_reported():
    _, stats = build_graph(DEFAULT_DATA_DIR)
    inv = stats["documents"]["02_ton_kho_theo_store.xlsx"]
    assert sum(inv["sku_resolution"].values()) == 80


# =====================================================================================
# Header đồng nghĩa + chọn builder theo cột thật
# =====================================================================================

def test_map_fields_handles_vietnamese_and_grouped_headers():
    f = map_fields({"Mã SKU": "ĐT-018", "Tên sản phẩm": "Loa", "Đơn giá": "1.440.000", "Ngành hàng": "Điện tử"})
    assert f == {"sku": "ĐT-018", "name": "Loa", "price": "1.440.000", "category": "Điện tử"}
    g = map_fields({"THÔNG TIN CHƯƠNG TRÌNH - Tên chương trình": "Sale", "MỨC ƯU ĐÃI - Mức giảm": "10%"})
    assert g == {"promo_name": "Sale", "discount": "10%"}


def test_detect_builder_uses_columns_even_if_doc_type_wrong(tmp_path):
    p = tmp_path / "file_la.xlsx"
    _xlsx(p, "S", ["Mã SKU", "Tên sản phẩm", "Giá bán", "Ngành hàng"], [["AB-01", "X", 1000, "Điện tử"]])
    d = _doc(p, "unknown")
    assert detect_builder(d, load_units(d)) == "price_list"


# =====================================================================================
# Policy: GOVERNS / REFERENCES "Điều X" / HAS_EXCEPTION_FOR
# =====================================================================================

def test_policy_governs_from_subtitle_and_article_mentions(tmp_path):
    p = tmp_path / "01_chinh_sach_test.docx"
    _policy_docx(p, "CHÍNH SÁCH TEST", "GreenMart — Ngành hàng Điện máy - Điện tử",
                 [("Điều 1. Phạm vi", ["Áp dụng cho hàng Thực phẩm tươi sống và các mặt hàng khác trong hệ thống."])])
    ctx = BuildContext(categories=["Điện máy", "Điện tử", "Thực phẩm"])
    res = build_document(_doc(p, "policy"), ctx)
    governs = {(e["from_id"], e["to_id"]) for e in res.acc.edge_list() if e["type"] == "GOVERNS"}
    assert ("chinh_sach_test", "Điện máy") in governs and ("chinh_sach_test", "Điện tử") in governs
    assert ("chinh_sach_test#dieu-1", "Thực phẩm") in governs


def test_policy_dieu_reference_to_other_policy_when_title_named(tmp_path):
    p = tmp_path / "01_chinh_sach_a.docx"
    _policy_docx(p, "CHÍNH SÁCH A", "Phụ đề", [
        ("Điều 1. Xử lý", ["Trường hợp hàng lỗi xử lý theo Điều 3 của Chính sách bảo hành sản phẩm."]),
        ("Điều 2. Khác", ["Nội dung khác, xem Điều 1."]),
    ])
    ctx = BuildContext(policies={"chinh_sach_bao_hanh_san_pham": {"title": "CHÍNH SÁCH BẢO HÀNH SẢN PHẨM"}},
                       policy_articles={"chinh_sach_bao_hanh_san_pham#dieu-3"})
    res = build_document(_doc(p, "policy"), ctx)
    refs = {(e["from_id"], e["to_id"]) for e in res.acc.edge_list() if e["type"] == "REFERENCES"}
    assert ("chinh_sach_a#dieu-1", "chinh_sach_bao_hanh_san_pham#dieu-3") in refs
    assert ("chinh_sach_a#dieu-2", "chinh_sach_a#dieu-1") in refs


def test_policy_no_self_loop_for_article_referencing_itself(tmp_path):
    p = tmp_path / "01_solo.docx"
    _policy_docx(p, "CHÍNH SÁCH SOLO", "Phụ đề", [("Điều 5. Thời hạn", ["Theo quy định tại Điều 5 này, thời hạn là 7 ngày."])])
    res = build_document(_doc(p, "policy"), BuildContext())
    assert not [e for e in res.acc.edge_list() if e["type"] == "REFERENCES"]


def test_has_exception_for_only_when_promotion_named(tmp_path):
    p = tmp_path / "01_doi_tra.docx"
    _policy_docx(p, "CHÍNH SÁCH ĐỔI TRẢ", "Phụ đề", [
        ("Điều 6. Ngoại lệ", ["Hàng thuộc chương trình Sale khai trương không áp dụng đổi trả.",
                             "Hàng khuyến mãi thanh lý có ghi chú không đổi trả."]),
    ])
    ctx = BuildContext(promotions={"Sale khai trương": {}})
    res = build_document(_doc(p, "policy"), ctx)
    edges = [e for e in res.acc.edge_list() if e["type"] == "HAS_EXCEPTION_FOR"]
    assert [(e["from_id"], e["to_id"]) for e in edges] == [("doi_tra#dieu-6", "Sale khai trương")]


# =====================================================================================
# Incremental trên MemoryGraphStore
# =====================================================================================

def _price_and_stock(tmp_path):
    price = tmp_path / "bang_gia.xlsx"
    _xlsx(price, "Gia", ["sku", "ten_san_pham", "gia", "category"],
          [["AB-01", "Nồi chiên 4L", 1000, "Gia dụng"], ["AB-02", "Ấm đun", 500, "Gia dụng"]])
    stock = tmp_path / "ton_kho.xlsx"
    _xlsx(stock, "GM-HCM01", ["sku", "ten_san_pham", "category", "ton_kho", "ton_kho_toi_thieu", "trang_thai", "cap_nhat_luc"],
          [["AB-01", "Nồi chiên 4L", "Gia dụng", 0, 5, "Hết hàng", "2026-08-01"],
           ["ZZ-9", "Nồi chiên 4L", "Gia dụng", 3, 5, "Sắp hết", "2026-08-01"]])
    return price, stock


def test_incremental_build_and_remove_document_keeps_other_documents_parts(tmp_path):
    price, stock = _price_and_stock(tmp_path)
    store = MemoryGraphStore()
    r1 = build_document(_doc(price, "price_list", "D1"), BuildContext.from_store(store))
    store.write_accumulator(r1.acc)
    r2 = build_document(_doc(stock, "inventory", "D2"), BuildContext.from_store(store))
    store.write_accumulator(r2.acc)
    stocks = store.edges_of_type("STOCKS")
    assert {e.dst.id for e in stocks} == {"AB-01"}  # ZZ-9 khớp về AB-01 qua tên + ngành hàng
    assert store.stats()["node_count_by_label"]["Store"] == 1

    # gỡ tồn kho -> mất Store + STOCKS, SKU của bảng giá còn nguyên
    out = store.remove_document("D2")
    assert out["removed_edges"] >= 1
    assert store.get_node("Store", "GM-HCM01") is None
    assert store.get_node("SKU", "AB-01").props["price"] == 1000
    assert not store.edges_of_type("STOCKS")


def test_removing_master_marks_sku_stale_but_keeps_it_when_other_doc_edges_remain(tmp_path):
    price, stock = _price_and_stock(tmp_path)
    store = MemoryGraphStore()
    store.write_accumulator(build_document(_doc(price, "price_list", "D1"), BuildContext.from_store(store)).acc)
    store.write_accumulator(build_document(_doc(stock, "inventory", "D2"), BuildContext.from_store(store)).acc)
    store.remove_document("D1")
    node = store.get_node("SKU", "AB-01")
    assert node is not None and node.props.get("stale") is True  # còn STOCKS từ D2
    assert store.get_node("SKU", "AB-02") is None  # không còn cạnh nào -> xoá hẳn


def test_rebuilding_same_document_is_idempotent(tmp_path):
    price, _ = _price_and_stock(tmp_path)
    store = MemoryGraphStore()
    for _ in range(3):
        store.remove_document("D1")
        store.write_accumulator(build_document(_doc(price, "price_list", "D1"), BuildContext.from_store(store)).acc)
    st = store.stats()
    assert st["node_count_by_label"] == {"SKU": 2, "Category": 1}
    assert st["edge_count"] == 2
    assert store.get_node("SKU", "AB-01").props["source_chunk_ids"] == ["D1:Gia!A2"]


def test_paths_from_real_graph_connects_sku_to_policy_within_two_hops():
    acc, _ = build_graph(DEFAULT_DATA_DIR)
    store = MemoryGraphStore()
    store.write_accumulator(acc)
    paths = store.paths_from([NodeRef("SKU", "ĐT-018")], 2, limit=500)
    ends = {(p.nodes[-1].label, p.nodes[-1].id) for p in paths}
    assert ("Category", "Điện tử") in ends
    assert any(label == "Policy" for label, _ in ends)
    assert all(p.length <= 2 for p in paths)
