"""Integration analytics: ingest -> job knowledge_extract tạo data_tables -> API catalog/preview/query/ask (LLM giả lập
sinh SQL) / saved / export; xoá tài liệu -> bảng tắt."""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

ADMIN_KEY = "rk_test_bootstrap_admin_key_123456"


class SQLModel:
    """Model giả lập NL->SQL: lần 1 trả SQL sai cột (để test retry), lần 2 trả SQL đúng."""

    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        user = kwargs["messages"][-1]["content"]
        if "lỗi khi chạy" in user:
            text = json.dumps({"sql": "SELECT category, count(*) AS so_sku, avg(gia) AS gia_tb FROM {t} GROUP BY category ORDER BY so_sku DESC",
                               "chart": {"type": "bar", "x": "category", "y": "so_sku", "title": "SKU theo ngành hàng"}, "reason": "đếm theo ngành"})
        else:
            text = json.dumps({"sql": "SELECT category, count(*) AS so_sku FROM {t} GROUP BY khong_co_cot", "chart": None, "reason": "x"})
        import re

        m = re.search(r"- (\w+) \(\d+ dòng\) — Bảng giá", kwargs["system"])
        text = text.replace("{t}", m.group(1) if m else "bang_gia")
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=50, output_tokens=20))


@pytest.fixture(scope="module")
def client(env, account):
    import os

    os.environ.update({"JWT_SECRET": "test-secret-at-least-32-characters-long!", "BOOTSTRAP_ADMIN_API_KEY": ADMIN_KEY})
    from core.config import reset_caches

    reset_caches()
    from fastapi.testclient import TestClient

    from api.main import create_app
    from api.state import build_services
    from core.llm import LLMClient, set_llm

    set_llm(LLMClient(client=SQLModel(), sleep=lambda s: None))
    services = build_services(warmup=False)
    app = create_app(services)
    with TestClient(app) as c:
        yield c
    set_llm(None)


def _h(client, account):
    r = client.post("/auth/login", json={"email": account["email"], "password": account["password"]})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_tables_created_by_knowledge_job(client, seeded):
    from core.db import session_scope
    from core.models import DataTable, Job

    ws = seeded["workspace_id"]
    with session_scope() as s:
        jobs = s.execute(select(Job).where(Job.type == "knowledge_extract", Job.workspace_id == ws)).scalars().all()
        assert jobs and all(j.status == "done" for j in jobs), [(j.status, j.error) for j in jobs]
        tables = s.execute(select(DataTable).where(DataTable.workspace_id == ws)).scalars().all()
    names = {t.title for t in tables}
    assert any("Bảng giá tháng 8" in n for n in names) and any("GM-HN01" in n for n in names)
    h = _h(client, seeded)
    cat = client.get(f"/w/{ws}/tables", headers=h).json()
    assert len(cat) >= 10  # bảng giá + 8 sheet tồn kho + khuyến mãi + NCC
    price = next(t for t in cat if "Bảng giá tháng 8" in t["title"])
    assert price["row_count"] == 300 and {c["name"]: c["type"] for c in price["columns"]}["gia"] == "integer"
    detail = client.get(f"/w/{ws}/tables/{price['id']}", headers=h).json()
    assert detail["preview"]["row_count"] == 20 and detail["stats"]["gia"]["count"] == 300
    rows = client.get(f"/w/{ws}/tables/{price['id']}/rows", params={"limit": 5, "offset": 295}, headers=h).json()
    assert rows["row_count"] == 5


def test_sql_query_ask_saved_and_export(client, seeded):
    ws = seeded["workspace_id"]
    h = _h(client, seeded)
    price = next(t for t in client.get(f"/w/{ws}/tables", headers=h).json() if "Bảng giá tháng 8" in t["title"])
    q = client.post(f"/w/{ws}/analytics/query", json={"sql": f"SELECT category, count(*) AS n FROM {price['name']} GROUP BY category",
                                                      "chart": {"type": "bar", "x": "category", "y": "n"}}, headers=h).json()
    assert q["result"]["row_count"] == 8 and q["chart"]["vega_lite"]["mark"]["type"] == "bar"
    bad = client.post(f"/w/{ws}/analytics/query", json={"sql": "DROP TABLE x"}, headers=h)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "sql_not_allowed"
    ask = client.post(f"/w/{ws}/analytics/ask", json={"question": "Mỗi ngành hàng có bao nhiêu SKU?"}, headers=h).json()
    assert ask["attempts"] == 2 and ask["result"]["row_count"] == 8 and ask["chart"]["type"] == "bar" and ask["sql"]
    saved = client.post(f"/w/{ws}/analytics/saved", json={"title": "SKU theo ngành", "sql": ask["sql"], "chart_spec": {"type": "bar", "x": "category", "y": "so_sku"}}, headers=h).json()
    run = client.post(f"/w/{ws}/analytics/saved/{saved['id']}/run", headers=h).json()
    assert run["result"]["row_count"] == 8 and run["chart"]["type"] == "bar"
    xlsx = client.get(f"/w/{ws}/tables/{price['id']}/export", params={"fmt": "xlsx"}, headers=h)
    assert xlsx.status_code == 200 and xlsx.headers["content-type"].startswith("application/vnd.openxmlformats")
    csv_ = client.post(f"/w/{ws}/analytics/export?fmt=csv", json={"sql": f"SELECT sku, gia FROM {price['name']} LIMIT 3"}, headers=h)
    assert csv_.status_code == 200 and csv_.text.count("\n") >= 3


def test_deleting_document_deactivates_its_tables(client, seeded):
    from core.db import session_scope
    from core.models import DataTable, Document
    from kb.lifecycle import restore_document, soft_delete_document

    ws = seeded["workspace_id"]
    with session_scope() as s:
        doc = s.execute(select(Document).where(Document.workspace_id == ws, Document.filename == "05_danh_muc_nha_cung_cap.xlsx")).scalar_one()
        soft_delete_document(s, doc, "test")
        assert all(not t.is_active for t in s.execute(select(DataTable).where(DataTable.document_id == doc.id)).scalars())
        doc_id = doc.id
    h = _h(client, seeded)
    assert not any(t["document_id"] == doc_id for t in client.get(f"/w/{ws}/tables", headers=h).json())
    with session_scope() as s:
        restore_document(s, s.get(Document, doc_id), "test")
    assert any(t["document_id"] == doc_id for t in client.get(f"/w/{ws}/tables", headers=h).json())
