"""Fixture integration: DB Postgres THẬT (pgvector + Apache AGE) qua RETAILKB_TEST_PG_DSN.

Mỗi session test: drop + tạo lại schema bằng ALEMBIC (đúng đường production, không create_all),
graph AGE riêng, embedding hashing (không tải model), rerank tắt. Không có DSN -> skip toàn bộ.
"""
import os
import shutil

import pytest

TEST_DSN = os.environ.get("RETAILKB_TEST_PG_DSN")


def pytest_collection_modifyitems(config, items):
    if TEST_DSN:
        return
    skip = pytest.mark.skip(reason="cần RETAILKB_TEST_PG_DSN (Postgres + pgvector + AGE) cho integration test")
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def env(tmp_path_factory):
    from urllib.parse import urlparse

    dbname = urlparse(TEST_DSN).path.lstrip("/")
    if not dbname.endswith("_test"):  # fixture DROP SCHEMA public — không bao giờ chạy trên DB thật
        pytest.exit(f"RETAILKB_TEST_PG_DSN phải trỏ tới database tên kết thúc bằng _test (đang là {dbname!r})", 2)
    upload_dir = tmp_path_factory.mktemp("uploads")
    os.environ.update({
        "DATABASE_URL": TEST_DSN.replace("postgresql://", "postgresql+psycopg://", 1),
        "EMBEDDING_PROVIDER": "hashing", "RERANK_ENABLED": "false", "UPLOAD_DIR": str(upload_dir),
        "GRAPH_ENABLED": "true",
    })
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
    from core.config import reset_caches
    from core.db import reset_engine_cache

    reset_caches()
    reset_engine_cache()

    import psycopg
    from alembic import command
    from alembic.config import Config

    with psycopg.connect(TEST_DSN, autocommit=True) as conn:
        # AGE nạp sẵn qua shared_preload_libraries chặn DROP SCHEMA khi DB (mới tạo) chưa có extension -> tạo trước
        if conn.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'age'").fetchone():
            conn.execute("CREATE EXTENSION IF NOT EXISTS age")
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")
        if conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'age'").fetchone():
            conn.execute("LOAD 'age'")
            for (name,) in conn.execute("SELECT name FROM ag_catalog.ag_graph").fetchall():
                conn.execute(f"SELECT ag_catalog.drop_graph('{name}', true)")
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "..", "migrations"))
    command.upgrade(cfg, "head")

    from graph.store import AgeGraphStore, graph_name_for, set_graph_store_factory

    def _factory(ws: str):
        store = AgeGraphStore(TEST_DSN, graph_name_for(ws))
        store.ensure()
        return store

    set_graph_store_factory(_factory)
    yield {"upload_dir": str(upload_dir)}
    set_graph_store_factory(None)
    shutil.rmtree(upload_dir, ignore_errors=True)
    reset_caches()
    reset_engine_cache()


SEED = [
    ("data/raw/xlsx/01_bang_gia_sku.xlsx", {"title": "Bảng giá tháng 8"}),
    ("data/raw/xlsx/02_ton_kho_theo_store.xlsx", {"title": "Tồn kho theo cửa hàng"}),
    ("data/raw/xlsx/03_lich_khuyen_mai.xlsx", {"title": "Lịch khuyến mãi quý 3"}),
    ("data/raw/xlsx/05_danh_muc_nha_cung_cap.xlsx", {"title": "Danh mục nhà cung cấp"}),
    ("data/raw/docx/01_chinh_sach_doi_tra_hang_hoa.docx", {}),
    ("data/raw/docx/02_chinh_sach_bao_hanh_san_pham.docx", {}),
]


@pytest.fixture(scope="session")
def account(env):
    """User + workspace pack retail dùng chung cho integration test. Trả dict id/email/password."""
    from accounts import service as accounts
    from core.db import session_scope

    with session_scope() as s:
        user = accounts.create_user(s, email="owner@example.com", password="owner-pass-123", name="Owner")
        ws = accounts.create_workspace(s, user, name="Test retail", domain_pack="retail")
        return {"user_id": user.id, "email": user.email, "password": "owner-pass-123", "workspace_id": ws.id}


@pytest.fixture(scope="session")
def seeded(env, account):
    """Ingest dữ liệu mẫu 1 lần vào workspace test (idempotent: file đã có thì trả document cũ)."""
    from core.config import get_config
    from core.db import session_scope
    from ingest.service import UploadOptions, register_upload
    from worker.main import Worker

    with session_scope() as s:
        for path, opts in SEED:
            register_upload(s, get_config(), workspace_id=account["workspace_id"], src=path, filename=path.rsplit("/", 1)[-1],
                            opts=UploadOptions(**opts), uploaded_by="test")
    Worker("seed-test").loop(once=True)
    return account
