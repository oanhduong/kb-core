"""Integration Phần D (API): FastAPI TestClient trên DB thật, model giả lập."""
import json
import os

import pytest

ADMIN_KEY = "rk_test_bootstrap_admin_key_123456"


@pytest.fixture(scope="module")
def client(env):
    os.environ.update({"JWT_SECRET": "test-secret-at-least-32-characters-long!", "BOOTSTRAP_ADMIN_API_KEY": ADMIN_KEY,
                       "APP_ENV": "dev", "DEV_LOGIN_ENABLED": "true"})
    from core.config import reset_caches

    reset_caches()
    from fastapi.testclient import TestClient

    from api.main import create_app
    from api.state import build_services
    from core.llm import LLMClient, set_llm
    from tests.integration.test_qa_service import FakeModel, overlap_rerank

    set_llm(LLMClient(client=FakeModel(), sleep=lambda s: None))
    services = build_services(warmup=False)
    services.retriever.rerank_fn = overlap_rerank
    services.retriever._rerank_ready.set()
    services.cfg.retrieval.rerank.enabled = True
    app = create_app(services)
    with TestClient(app) as c:
        yield c
    set_llm(None)


def admin_h():
    return {"X-API-Key": ADMIN_KEY}


def jwt_h(client, user="nv01", store="GM-HN01", role="staff"):
    r = client.post("/auth/token", json={"user_id": user, "role": role, "store_code": store}, headers=admin_h())
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_health_and_auth_required(client):
    assert client.get("/healthz").status_code == 200
    r = client.post("/ask", json={"question": "xin chào"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized" and r.headers["X-Request-ID"]
    bad = client.post("/ask", json={"question": "x"}, headers={"Authorization": "Bearer abc"})
    assert bad.status_code == 401


def test_ingest_upload_and_job_status(client):
    with open("data/raw/xlsx/01_bang_gia_sku.xlsx", "rb") as f1, open("data/raw/docx/01_chinh_sach_doi_tra_hang_hoa.docx", "rb") as f2:
        r = client.post("/ingest", headers=admin_h(), files=[("files", ("01_bang_gia_sku.xlsx", f1)),
                                                            ("files", ("01_chinh_sach_doi_tra_hang_hoa.docx", f2))])
    assert r.status_code == 200, r.text
    jobs = r.json()["jobs"]
    assert len(jobs) == 2
    from worker.main import Worker

    Worker("api-test").loop(once=True)
    st = client.get(f"/ingest/{jobs[0]['job_id']}", headers=admin_h()).json()
    assert st["status"] == "done" and st["progress"]["percent"] in (100.0, None)
    # staff không được upload
    with open("data/raw/xlsx/01_bang_gia_sku.xlsx", "rb") as f:
        assert client.post("/ingest", headers=jwt_h(client), files=[("files", ("x.xlsx", f))]).status_code == 403


def test_store_scope_enforced(client):
    h = jwt_h(client, store="GM-HN01")
    r = client.post("/ask", json={"question": "Thời hạn đổi trả theo ngành hàng?", "store_code": "GM-HCM01"}, headers=h)
    assert r.status_code == 403 and "GM-HN01" in r.json()["error"]["message"]


def test_ask_returns_citations_and_trace_then_feedback(client):
    h = jwt_h(client)
    r = client.post("/ask", json={"question": "Thời hạn đổi trả theo ngành hàng được quy định thế nào?"}, headers=h)
    body = r.json()
    assert r.status_code == 200 and body["status"] == "answered", body
    assert body["citations"] and body["decision_trace"]["summary"]
    cit = body["citations"][0]
    chunk = client.get(cit["url"], headers=h)
    assert chunk.status_code == 200 and chunk.json()["citation"] == cit["label"]
    fb = client.post("/feedback", json={"trace_id": body["trace_id"], "rating": "down", "comment": "thiếu ý"}, headers=h)
    assert fb.status_code == 200
    again = client.post("/feedback", json={"trace_id": body["trace_id"], "rating": "down", "comment": "sửa comment"}, headers=h)
    assert again.status_code == 200  # idempotent (upsert)
    lst = client.get("/admin/feedback", headers=admin_h()).json()
    mine = [x for x in lst if x["trace_id"] == body["trace_id"]]
    assert len(mine) == 1 and mine[0]["comment"] == "sửa comment" and mine[0]["question"]


def test_chat_sse_stream(client):
    h = jwt_h(client)
    with client.stream("POST", "/chat", json={"question": "Thời hạn đổi trả theo ngành hàng được quy định thế nào?"}, headers=h) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        raw = "".join(r.iter_text())
    events = [(b.split("\n")[0][7:], json.loads(b.split("\n", 1)[1][6:])) for b in raw.strip().split("\n\n") if b.startswith("event:")]
    names = [e for e, _ in events]
    assert names[0] == "meta" and names[-1] == "done" and "citations" in names and "trace" in names
    assert names.index("citations") > max(i for i, n in enumerate(names) if n == "delta")
    sid = events[0][1]["session_id"]
    msgs = client.get(f"/chat/sessions/{sid}/messages", headers=h).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    other = jwt_h(client, user="nv99")
    assert client.get(f"/chat/sessions/{sid}/messages", headers=other).status_code == 403


def test_money_question_review_flow_via_api(client):
    staff = jwt_h(client, user="nv02")
    sup = jwt_h(client, user="sup01", role="supervisor")
    r = client.post("/ask", json={"question": "Giá bán ĐT-018 là bao nhiêu?"}, headers=staff).json()
    assert r["status"] == "pending_review" and r["review_id"]
    assert client.get(f"/answers/{r['trace_id']}", headers=staff).json()["answer"] is None  # chưa duyệt -> chưa lộ
    assert client.get("/review", headers=staff).status_code == 403
    queue = client.get("/review", headers=sup).json()
    assert any(i["id"] == r["review_id"] for i in queue)
    ok = client.post(f"/review/{r['review_id']}/approve", json={"edited_answer": "Giá ĐT-018 là 1.440.000đ [1]."}, headers=sup)
    assert ok.status_code == 200 and ok.json()["overridden"] is True
    assert client.post(f"/review/{r['review_id']}/reject", json={}, headers=sup).status_code == 409  # đã xử lý
    final = client.get(f"/answers/{r['trace_id']}", headers=staff).json()
    assert final["status"] == "answered" and final["answer"].startswith("Giá ĐT-018")


def test_kb_records_list_patch_delete_restore(client):
    lst = client.get("/kb/records", params={"sku": "ĐT-018", "doc_type": "price_list"}, headers=admin_h()).json()
    assert lst["total"] >= 1
    rid = lst["items"][0]["record_id"]
    from urllib.parse import quote

    path = quote(rid, safe="")
    p = client.patch(f"/kb/records/{path}", json={"content": lst["items"][0]["content"].replace("1440000", "1450000")}, headers=admin_h())
    assert p.status_code == 200, p.text
    assert p.json()["record"]["manually_edited"] is True and "1450000" in p.json()["record"]["content"]
    assert client.delete(f"/kb/records/{path}", headers=admin_h()).json()["status"] == "deleted"
    assert client.post(f"/kb/records/{path}/restore", headers=admin_h()).json()["status"] == "active"
    staff = jwt_h(client)
    assert client.patch(f"/kb/records/{path}", json={"content": "x"}, headers=staff).status_code == 403


def test_health_conflicts_config_and_rate_limit(client):
    sup = jwt_h(client, user="sup02", role="supervisor")
    h = client.get("/kb/health", headers=sup).json()
    assert {"records_by_doc_type", "blocked_files", "open_conflicts", "abstentions_last_days", "last_updated_by_doc_type"} <= set(h)
    assert isinstance(client.get("/conflicts", headers=sup).json(), list)
    cfg = client.get("/config", headers=sup).json()
    assert cfg["config_version"] and cfg["prompts"]["answer_system"]["sha256"] and cfg["config_hash"]
    # rate limit: key với giới hạn 3/phút
    key = client.post("/admin/api-keys", json={"name": "rl-test", "role": "staff", "rate_limit_per_minute": 3}, headers=admin_h()).json()
    hk = {"X-API-Key": key["api_key"]}
    codes = [client.get("/auth/me", headers=hk).status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429


def test_validation_error_is_friendly_json(client):
    r = client.post("/ask", json={"question": ""}, headers=jwt_h(client))
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_failed"
