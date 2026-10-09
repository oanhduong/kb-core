"""Integration Phần C (+ A6): ingest thật trên data mẫu -> KB/graph -> thay thế -> lịch sử -> dedup ->
xoá/khôi phục -> reindex -> health. Chạy tuần tự (các test sau dùng trạng thái của test trước)."""
import shutil
from datetime import date

import pytest
from openpyxl import load_workbook
from sqlalchemy import func, select, text

DATA = "data/raw"
SEED_FILES = [
    ("xlsx/01_bang_gia_sku.xlsx", {"title": "Bảng giá tháng 8", "effective_date": date(2026, 8, 1)}),
    ("xlsx/02_ton_kho_theo_store.xlsx", {"title": "Tồn kho theo cửa hàng"}),
    ("xlsx/03_lich_khuyen_mai.xlsx", {"title": "Lịch khuyến mãi quý 3"}),
    ("xlsx/05_danh_muc_nha_cung_cap.xlsx", {"title": "Danh mục nhà cung cấp"}),
    ("docx/01_chinh_sach_doi_tra_hang_hoa.docx", {}),
    ("docx/02_chinh_sach_bao_hanh_san_pham.docx", {}),
]
STATE: dict = {}


def _upload(path, **opts):
    from core.config import get_config
    from core.db import session_scope
    from ingest.service import UploadOptions, register_upload

    with session_scope() as s:
        return register_upload(s, get_config(), workspace_id=STATE["ws"], src=path, filename=path.rsplit("/", 1)[-1],
                               opts=UploadOptions(**opts), uploaded_by="test")


def _drain():
    from worker.main import Worker

    w = Worker("test-worker")
    w.loop(once=True)


@pytest.fixture(scope="module", autouse=True)
def _env(env, account):
    STATE["ws"] = account["workspace_id"]
    return env


def test_ingest_sample_files_end_to_end():
    from core.db import session_scope
    from core.models import ChunkRow, Document, Job, KBRecordRow

    for rel, opts in SEED_FILES:
        res = _upload(f"{DATA}/{rel}", **opts)
        STATE[rel] = res.document_id
    _drain()
    with session_scope() as s:
        jobs = s.execute(select(Job.type, Job.status, Job.error)).all()
        # dò quan hệ giữa các bảng: 1 job cho cả loạt file (hẹn giờ ~20s để gom các file tải cùng lúc) -> còn chờ
        assert [st for t, st, _ in jobs if t == "relations_discover"] == ["queued"], jobs
        jobs = [j for j in jobs if j[0] != "relations_discover"]
        assert all(st == "done" for _, st, _ in jobs), jobs
        assert {t for t, _, _ in jobs} == {"ingest_file", "graph_sync", "knowledge_extract"}
        docs = s.execute(select(Document)).scalars().all()
        assert all(d.status == "active" and d.is_active for d in docs)
        by_name = {d.filename: d for d in docs}
        assert by_name["01_bang_gia_sku.xlsx"].doc_type == "price_list"
        assert by_name["02_ton_kho_theo_store.xlsx"].doc_type == "inventory"
        assert by_name["01_chinh_sach_doi_tra_hang_hoa.docx"].doc_type == "policy"
        assert by_name["01_chinh_sach_doi_tra_hang_hoa.docx"].effective_date == date(2026, 1, 1)  # "Ban hành: 01/01/2026"
        assert by_name["01_chinh_sach_doi_tra_hang_hoa.docx"].version == "3.2"
        price_doc = by_name["01_bang_gia_sku.xlsx"]
        rec = s.get(KBRecordRow, f"{price_doc.id}:BangGiaSKU!A3")
        assert rec is not None and rec.skus == ["ĐT-018"] and rec.entity_key == "price_list:ĐT-018|*"
        inv = s.execute(select(KBRecordRow).where(KBRecordRow.document_id == by_name["02_ton_kho_theo_store.xlsx"].id,
                                                  KBRecordRow.location == "GM-HN01!A3")).scalar_one()
        assert inv.store_code == "GM-HN01"
        n_chunks = s.execute(select(func.count()).select_from(ChunkRow)).scalar_one()
        n_emb = s.execute(text("SELECT count(*) FROM chunk_embeddings")).scalar_one()
        n_retrievable = s.execute(select(func.count()).select_from(ChunkRow).where(ChunkRow.role != "parent")).scalar_one()
        assert n_chunks > 0 and n_emb == n_retrievable


def test_graph_built_incrementally_in_age_with_record_links():
    from core.db import session_scope
    from core.models import GraphDocument
    from graph.store import NodeRef, get_graph_store

    st = get_graph_store(STATE["ws"]).stats()
    assert st["node_count_by_label"]["SKU"] >= 300 and st["node_count_by_label"]["Store"] == 8
    assert {"BELONGS_TO", "STOCKS", "APPLIES_TO", "SUPPLIES", "GOVERNS", "REFERENCES", "PART_OF"} <= set(st["edge_count_by_type"])
    node = get_graph_store(STATE["ws"]).get_node("SKU", "ĐT-018")
    price_doc = STATE["xlsx/01_bang_gia_sku.xlsx"]
    assert node.props["source_chunk_ids"] == [f"{price_doc}:BangGiaSKU!A3"]  # trỏ đúng record_id trong KB
    paths = get_graph_store(STATE["ws"]).paths_from([NodeRef("SKU", "ĐT-018")], 2, limit=200)
    assert any(p.nodes[-1].label == "Promotion" for p in paths)
    with session_scope() as s:
        assert {g.status for g in s.execute(select(GraphDocument)).scalars()} == {"done"}


def test_new_month_price_list_replaces_old_and_keeps_history(tmp_path):
    from core.db import session_scope
    from core.models import Document, Job, KBRecordRow
    from graph.store import get_graph_store
    from index.store import Filters
    from index.bm25_cache import load_bm25_documents
    from core.db import get_sessionmaker

    src = tmp_path / "bang_gia_thang_9_2026.xlsx"
    shutil.copy(f"{DATA}/xlsx/01_bang_gia_sku.xlsx", src)
    wb = load_workbook(src)
    ws = wb.worksheets[0]
    assert ws["A3"].value == "ĐT-018"
    ws["C3"] = 1290000
    wb.save(src)
    res = _upload(str(src), title="Bảng giá tháng 9", effective_date=date(2026, 9, 1), doc_type="price_list",
                  series_key="bang_gia_sku")
    STATE["sep"] = res.document_id
    _drain()
    with session_scope() as s:
        old = s.get(Document, STATE["xlsx/01_bang_gia_sku.xlsx"])
        new = s.get(Document, res.document_id)
        assert (old.status, old.is_active, old.superseded_by_id, old.valid_to) == ("superseded", False, new.id, date(2026, 9, 1))
        assert (new.status, new.is_active, new.supersedes_id) == ("active", True, old.id)
        old_rec = s.get(KBRecordRow, f"{old.id}:BangGiaSKU!A3")
        assert (old_rec.is_active, old_rec.status, old_rec.valid_to) == (False, "superseded", date(2026, 9, 1))
        assert s.execute(select(Job).where(Job.type == "graph_sync", Job.document_id == old.id, Job.status == "done")).first()
    # graph: SKU giờ trỏ về record của bảng giá mới, giá mới
    node = get_graph_store(STATE["ws"]).get_node("SKU", "ĐT-018")
    assert node.props["price"] == 1290000 and node.props["source_chunk_ids"] == [f"{res.document_id}:BangGiaSKU!A3"]
    # BM25/vector filter: mặc định chỉ active; as_of 15/8 ra bản cũ
    from index.store import _matches_filters

    docs = [d for d in load_bm25_documents(get_sessionmaker())
            if d.metadata["record_id"].endswith(":BangGiaSKU!A3") and d.metadata["workspace_id"] == STATE["ws"]]
    active = [d for d in docs if _matches_filters(d.metadata, Filters(is_active=True))]
    hist = [d for d in docs if _matches_filters(d.metadata, Filters(as_of="2026-08-15"))]
    assert [d.metadata["document_id"] for d in active] == [res.document_id]
    assert [d.metadata["document_id"] for d in hist] == [STATE["xlsx/01_bang_gia_sku.xlsx"]]


def test_reupload_same_file_is_idempotent():
    from core.db import session_scope
    from core.models import Document

    res = _upload(f"{DATA}/docx/01_chinh_sach_doi_tra_hang_hoa.docx")
    assert res.duplicate_of_upload and res.document_id == STATE["docx/01_chinh_sach_doi_tra_hang_hoa.docx"]
    with session_scope() as s:
        assert s.execute(select(func.count()).select_from(Document).where(
            Document.workspace_id == STATE["ws"], Document.filename == "01_chinh_sach_doi_tra_hang_hoa.docx")).scalar_one() == 1


def test_dedup_same_content_in_two_files_keeps_newer(tmp_path):
    from docx import Document as Docx

    from core.db import session_scope
    from core.models import KBRecordRow

    # file khác tên + khác bytes (thêm 1 đoạn) nhưng các Điều giống hệt bản đang có
    src = tmp_path / "ban_sao_chinh_sach_doi_tra_gui_cua_hang.docx"
    d = Docx(f"{DATA}/docx/01_chinh_sach_doi_tra_hang_hoa.docx")
    d.add_paragraph("Bản sao gửi cửa hàng.")
    d.save(src)
    res = _upload(str(src), doc_type="policy", effective_date=date(2026, 2, 1), replace_mode="none")
    _drain()
    with session_scope() as s:
        orig_doc = STATE["docx/01_chinh_sach_doi_tra_hang_hoa.docx"]
        dups = s.execute(select(KBRecordRow).where(KBRecordRow.document_id == orig_doc, KBRecordRow.status == "duplicate")).scalars().all()
        assert dups, "record của bản cũ hơn phải bị đánh dấu duplicate"
        kept = s.get(KBRecordRow, dups[0].duplicate_of)
        assert kept.document_id == res.document_id and kept.is_active


def test_soft_delete_newest_price_list_reactivates_previous_then_restore():
    from core.db import session_scope
    from core.models import Document, KBRecordRow
    from kb.lifecycle import restore_document, soft_delete_document

    old_id, new_id = STATE["xlsx/01_bang_gia_sku.xlsx"], STATE["sep"]
    with session_scope() as s:
        out = soft_delete_document(s, s.get(Document, new_id), "admin")
        assert out["reactivated"] == old_id
    with session_scope() as s:
        assert s.get(Document, old_id).is_active and s.get(KBRecordRow, f"{old_id}:BangGiaSKU!A3").is_active
        assert s.get(KBRecordRow, f"{new_id}:BangGiaSKU!A3").status == "deleted"
        restore_document(s, s.get(Document, new_id), "admin")
    with session_scope() as s:
        assert s.get(Document, new_id).is_active and not s.get(Document, old_id).is_active
        assert s.get(KBRecordRow, f"{new_id}:BangGiaSKU!A3").is_active


def test_reindex_is_idempotent_and_rechunks_on_config_change():
    from core.config import get_config
    from core.db import get_sessionmaker
    from index.embeddings import get_embedder
    from kb.reindex import run_reindex

    cfg = get_config()
    sm = get_sessionmaker()
    noop = run_reindex(cfg, sm, get_embedder(cfg), {"doc_type": "policy"}, progress=lambda *a: None)
    assert noop["rechunked_records"] == 0 and noop["embedded"] == 0 and noop["skipped_up_to_date"] > 0
    cfg2 = cfg.model_copy(deep=True)
    cfg2.chunking.child_chunk_tokens = 128
    out = run_reindex(cfg2, sm, get_embedder(cfg), {"doc_type": "policy"}, progress=lambda *a: None)
    assert out["rechunked_records"] > 0 and out["embedded"] > 0
    again = run_reindex(cfg2, sm, get_embedder(cfg), {"doc_type": "policy"}, progress=lambda *a: None)
    assert again["rechunked_records"] == 0 and again["embedded"] == 0


def test_kb_health_reports_all_required_signals():
    from core.config import get_config
    from core.db import session_scope
    from kb.health import kb_health

    with session_scope() as s:
        h = kb_health(s, get_config(), workspace_id=STATE["ws"])
    assert h["records_by_doc_type"]["price_list"]["superseded"] >= 300
    assert "policy" in h["last_updated_by_doc_type"]
    assert h["blocked_files"]["count"] == 0 and isinstance(h["open_conflicts"], int)
    assert h["abstentions_last_days"]["days"] == 7
    assert h["embedding_coverage"]["missing"] == 0
