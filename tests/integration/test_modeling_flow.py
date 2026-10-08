"""Integration mô hình dữ liệu trên Postgres thật: upload workbook (giá trị đã dán, không còn công thức) -> tạo mô hình ->
worker phân tích (bảng nạp vào schema riêng) -> duyệt hàng loạt + sửa 1 cột bằng quy tắc -> build view -> đối chiếu Excel
khớp 100% các cột tính toán -> tài liệu quan hệ (md/docx) -> kết nối BI -> view xuất sang Analytics để hỏi đáp."""
import pytest
from sqlalchemy import text

from tests.test_modeling import _write


@pytest.fixture(scope="module")
def modeler(env):
    """Tài khoản riêng: giới hạn request/phút tính theo user — không ăn vào hạn mức của các test khác dùng chung `account`."""
    from accounts import service as accounts
    from core.db import session_scope

    with session_scope() as s:
        user = accounts.create_user(s, email="modeler@example.com", password="modeler-pass-123", name="Modeler")
        ws = accounts.create_workspace(s, user, name="Models", domain_pack="generic")
        return {"user_id": user.id, "email": user.email, "password": "modeler-pass-123", "workspace_id": ws.id}


@pytest.fixture(scope="module")
def client(env, modeler):
    import os

    os.environ.update({"JWT_SECRET": "test-secret-at-least-32-characters-long!"})
    from core.config import reset_caches

    reset_caches()
    from fastapi.testclient import TestClient

    from api.main import create_app
    from api.state import build_services

    app = create_app(build_services(warmup=False))
    with TestClient(app) as c:
        yield c


def _h(client, account):
    r = client.post("/auth/login", json={"email": account["email"], "password": account["password"]})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_model_end_to_end(client, modeler, tmp_path):
    account = modeler
    from core.config import get_config
    from core.db import session_scope
    from ingest.service import UploadOptions, register_upload
    from modeling.warehouse import warehouse_engine
    from worker.main import Worker

    ws = account["workspace_id"]
    h = _h(client, account)
    path = tmp_path / "site_summary.xlsx"
    _write(path, formulas=False)
    with session_scope() as s:
        res = register_upload(s, get_config(), workspace_id=ws, src=str(path), filename="site_summary.xlsx",
                              opts=UploadOptions(title="Site summary demo"), uploaded_by="test")
        doc_id = res["document_id"] if isinstance(res, dict) else res.document_id

    created = client.post(f"/w/{ws}/models", json={"document_ids": [doc_id], "name": "Site summary"}, headers=h)
    assert created.status_code == 200, created.text
    mid = created.json()["id"]
    assert created.json()["status"] == "analyzing"
    Worker("model-test").loop(once=True)

    m = client.get(f"/w/{ws}/models/{mid}", headers=h).json()
    assert m["status"] == "review", m.get("error")
    spec = m["spec"]
    assert spec["main_table"] == "site_summary" and len(spec["tables"]) == 4
    assert m["texts"]["columns"]["open_issues"]["rule"]["vi"]
    with warehouse_engine().connect() as conn:
        n = conn.execute(text(f'SELECT count(*) FROM "{m["schema_name"]}"."issues"')).scalar()
    assert n == 400

    # sửa 1 cột: đổi điều kiện của Open Issues sang "thuộc Open, Pending" (vẫn khớp), kiểm thử trước khi lưu
    rule = {"kind": "aggregate", "relationship_id": "r_issues", "func": "count", "default": 0,
            "filters": [{"column": "status", "op": "in", "value": ["Open", "Pending"]}]}
    t = client.post(f"/w/{ws}/models/{mid}/columns/open_issues/test", json={"rule": rule}, headers=h).json()
    assert t["check"]["rate"] == 1.0 and "Pending" in t["text"]["vi"]
    m = client.patch(f"/w/{ws}/models/{mid}/columns/open_issues", json={"rule": rule}, headers=h).json()
    assert m["spec"]["columns"][2]["status"] == "confirmed"
    conf = client.post(f"/w/{ws}/models/{mid}/confirm", json={}, headers=h).json()
    assert conf["confirmed"] >= 4

    b = client.post(f"/w/{ws}/models/{mid}/build", headers=h)
    assert b.status_code == 200 and b.json()["status"] == "building"
    Worker("model-test").loop(once=True)
    m = client.get(f"/w/{ws}/models/{mid}", headers=h).json()
    assert m["status"] == "ready", m.get("error")
    v = m["validation"]
    assert v["rows_aligned"] and v["view_rows"] == v["excel_rows"] == 40
    for name in ("open_issues", "last_imv", "days_since_last_imv", "hours_on_site", "pi"):
        assert v["columns"][name]["rate"] == 1.0, (name, v["columns"][name])
    assert v["summary"]["matched"] == v["summary"]["calculated"] and v["score"] == 1.0
    assert not m["stale"]

    with warehouse_engine().connect() as conn:
        rows = conn.execute(text(f'SELECT source_row, open_issues FROM "{m["schema_name"]}"."{m["view_name"]}" ORDER BY 1 LIMIT 2')).all()
    assert rows[0][0] == 3

    prev = client.get(f"/w/{ws}/models/{mid}/preview", params={"limit": 5}, headers=h).json()
    assert prev["source"] == "warehouse" and len(prev["rows"]) == 5 and "open_issues" in prev["columns"]
    md = client.get(f"/w/{ws}/models/{mid}/document", params={"fmt": "md", "lang": "en"}, headers=h)
    assert md.status_code == 200 and "Relationships between sheets" in md.text and "Validation against the Excel file" in md.text
    docx = client.get(f"/w/{ws}/models/{mid}/document", params={"fmt": "docx", "lang": "vi"}, headers=h)
    assert docx.status_code == 200 and docx.content[:2] == b"PK"
    conn_info = client.get(f"/w/{ws}/models/{mid}/connection", headers=h).json()
    assert conn_info["qualified"].endswith(m["view_name"]) and "SELECT" in conn_info["sample_sql"]

    tables = client.get(f"/w/{ws}/tables", headers=h).json()
    pub = next(t for t in tables if t["id"] == m["build"]["data_table_id"])
    assert pub["row_count"] == 40
    q = client.post(f"/w/{ws}/analytics/query", json={"sql": f"SELECT sum(open_issues) AS n FROM {pub['name']}"}, headers=h).json()
    assert q["result"]["rows"][0][0] > 0

    ins = client.get(f"/w/{ws}/insights", headers=h).json()
    assert ins["knowledge"]["models_ready"] >= 1

    d = client.delete(f"/w/{ws}/models/{mid}", headers=h)
    assert d.status_code == 200
    with warehouse_engine().connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"), {"s": m["schema_name"]}).first()
    assert exists is None
