"""Integration API: đăng ký/đăng nhập, workspace & thành viên, scope theo workspace, ingest, QA, SSE, review,
records, health, API key + rate limit. DB thật, model giả lập."""
import json
import os
from urllib.parse import quote

import pytest

ADMIN_KEY = "rk_test_bootstrap_admin_key_123456"


@pytest.fixture(scope="module")
def client(env):
    os.environ.update({"JWT_SECRET": "test-secret-at-least-32-characters-long!", "BOOTSTRAP_ADMIN_API_KEY": ADMIN_KEY, "APP_ENV": "dev"})
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


S: dict = {}  # trạng thái dùng chung giữa các test (chạy tuần tự)


def h(token):
    return {"Authorization": f"Bearer {token}"}


def test_signup_login_and_workspace_creation(client):
    r = client.post("/auth/signup", json={"email": "api-owner@example.com", "password": "secret-pass-1", "name": "API Owner",
                                          "workspace_name": "Cửa hàng A"})
    assert r.status_code == 200, r.text
    S["owner"] = r.json()["access_token"]
    S["ws"] = r.json()["workspace"]["id"]
    assert r.json()["workspace"]["role"] == "owner"
    dup = client.post("/auth/signup", json={"email": "API-Owner@example.com", "password": "secret-pass-1"})
    assert dup.status_code == 409
    bad = client.post("/auth/login", json={"email": "api-owner@example.com", "password": "wrong"})
    assert bad.status_code == 401
    ok = client.post("/auth/login", json={"email": "api-owner@example.com", "password": "secret-pass-1"})
    assert ok.status_code == 200
    me = client.get("/auth/me", headers=h(S["owner"])).json()
    assert me["user"]["email"] == "api-owner@example.com" and [w["id"] for w in me["workspaces"]] == [S["ws"]]
    # chuyển workspace sang pack retail để test luồng review/scope
    r = client.patch(f"/w/{S['ws']}", json={"domain_pack": "retail"}, headers=h(S["owner"]))
    assert r.status_code == 200 and r.json()["domain_pack"] == "retail"
    assert client.post("/ask", json={"question": "x"}).status_code in (401, 404, 405)


def test_members_roles_and_scope(client):
    # mời user chưa tồn tại -> invite; đăng ký bằng email đó -> tự vào workspace với role + store_code
    inv = client.post(f"/w/{S['ws']}/members", json={"email": "staff1@example.com", "role": "viewer", "attributes": {"store_code": "GM-HN01"}},
                      headers=h(S["owner"]))
    assert inv.status_code == 200 and inv.json()["status"] == "invited", inv.text
    r = client.post("/auth/signup", json={"email": "staff1@example.com", "password": "staff-pass-123", "name": "NV1"})
    S["staff"] = r.json()["access_token"]
    me = client.get("/auth/me", headers=h(S["staff"])).json()
    assert me["workspaces"][0]["role"] == "viewer" and me["workspaces"][0]["attributes"] == {"store_code": "GM-HN01"}
    # supervisor = editor
    r = client.post("/auth/signup", json={"email": "sup@example.com", "password": "sup-pass-12345"})
    S["sup"] = r.json()["access_token"]
    sup_id = r.json()["user"]["id"]
    assert client.post(f"/w/{S['ws']}/members", json={"email": "sup@example.com", "role": "editor"}, headers=h(S["owner"])).json()["status"] == "added"
    # viewer không mời được; thành viên ngoài workspace bị 403
    assert client.post(f"/w/{S['ws']}/members", json={"email": "x@example.com"}, headers=h(S["staff"])).status_code == 403
    r = client.post("/auth/signup", json={"email": "outsider@example.com", "password": "outsider-pass-1"})
    S["outsider"] = r.json()["access_token"]
    assert client.get(f"/w/{S['ws']}", headers=h(S["outsider"])).status_code == 403
    # thuộc tính không có trong pack -> 422
    assert client.patch(f"/w/{S['ws']}/members/{sup_id}", json={"attributes": {"region": "north"}}, headers=h(S["owner"])).status_code == 422
    members = client.get(f"/w/{S['ws']}/members", headers=h(S["staff"])).json()
    assert {m["role"] for m in members["members"]} == {"owner", "viewer", "editor"}


def test_ingest_requires_editor_and_runs_jobs(client):
    with open("data/raw/xlsx/01_bang_gia_sku.xlsx", "rb") as f:
        assert client.post(f"/w/{S['ws']}/ingest", headers=h(S["staff"]), files=[("files", ("x.xlsx", f))]).status_code == 403
    with open("data/raw/xlsx/01_bang_gia_sku.xlsx", "rb") as f1, open("data/raw/docx/01_chinh_sach_doi_tra_hang_hoa.docx", "rb") as f2:
        r = client.post(f"/w/{S['ws']}/ingest", headers=h(S["sup"]), files=[("files", ("01_bang_gia_sku.xlsx", f1)),
                                                                            ("files", ("01_chinh_sach_doi_tra_hang_hoa.docx", f2))])
    assert r.status_code == 200, r.text
    jobs = r.json()["jobs"]
    from worker.main import Worker

    Worker("api-test").loop(once=True)
    st = client.get(f"/w/{S['ws']}/ingest/{jobs[0]['job_id']}", headers=h(S["staff"])).json()
    assert st["status"] == "done"
    docs = client.get(f"/w/{S['ws']}/documents", headers=h(S["staff"])).json()
    assert docs["total"] == 2 and {d["doc_type"] for d in docs["items"]} == {"price_list", "policy"}
    S["price_doc"] = next(d["document_id"] for d in docs["items"] if d["doc_type"] == "price_list")
    detail = client.get(f"/w/{S['ws']}/documents/{S['price_doc']}", headers=h(S["staff"])).json()
    assert detail["record_count_live"] == 300 and "jobs" in detail


def test_workspace_isolation(client):
    # workspace khác của outsider không thấy tài liệu / không ask ra gì
    r = client.post("/workspaces", json={"name": "Khác", "domain_pack": "generic"}, headers=h(S["outsider"]))
    other = r.json()["id"]
    assert client.get(f"/w/{other}/documents", headers=h(S["outsider"])).json()["total"] == 0
    assert client.get(f"/w/{other}/documents/{S['price_doc']}", headers=h(S["outsider"])).status_code == 404
    a = client.post(f"/w/{other}/ask", json={"question": "Thời hạn đổi trả theo ngành hàng?"}, headers=h(S["outsider"])).json()
    assert a["status"] in ("abstained", "no_citation") and a["citations"] == []


def test_scope_enforced_for_viewer_with_store(client):
    r = client.post(f"/w/{S['ws']}/ask", json={"question": "Thời hạn đổi trả theo ngành hàng?", "scope": "GM-HCM01"}, headers=h(S["staff"]))
    assert r.status_code == 403 and "GM-HN01" in r.json()["error"]["message"]


def test_ask_citations_chunk_link_and_feedback(client):
    r = client.post(f"/w/{S['ws']}/ask", json={"question": "Thời hạn đổi trả theo ngành hàng được quy định thế nào?"}, headers=h(S["staff"]))
    body = r.json()
    assert r.status_code == 200 and body["status"] == "answered", body
    cit = body["citations"][0]
    chunk = client.get(f"/w/{S['ws']}{cit['url']}", headers=h(S["staff"]))
    assert chunk.status_code == 200 and chunk.json()["citation"] == cit["label"]
    assert client.post(f"/w/{S['ws']}/feedback", json={"trace_id": body["trace_id"], "rating": "down", "comment": "thiếu"}, headers=h(S["staff"])).status_code == 200
    assert client.post(f"/w/{S['ws']}/feedback", json={"trace_id": body["trace_id"], "rating": "down", "comment": "sửa"}, headers=h(S["staff"])).status_code == 200
    fb = client.get(f"/w/{S['ws']}/ops/feedback", headers=h(S["sup"])).json()
    assert [x for x in fb if x["trace_id"] == body["trace_id"]][0]["comment"] == "sửa"
    assert client.get(f"/w/{S['ws']}/ops/feedback", headers=h(S["staff"])).status_code == 403


def test_chat_sse_stream_and_session_ownership(client):
    with client.stream("POST", f"/w/{S['ws']}/chat", json={"question": "Thời hạn đổi trả theo ngành hàng được quy định thế nào?"},
                       headers=h(S["staff"])) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        raw = "".join(r.iter_text())
    events = [(b.split("\n")[0][7:], json.loads(b.split("\n", 1)[1][6:])) for b in raw.strip().split("\n\n") if b.startswith("event:")]
    names = [e for e, _ in events]
    assert names[0] == "meta" and names[-1] == "done" and "citations" in names and "trace" in names
    sid = events[0][1]["session_id"]
    msgs = client.get(f"/w/{S['ws']}/chat/sessions/{sid}/messages", headers=h(S["staff"])).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert client.get(f"/w/{S['ws']}/chat/sessions/{sid}/messages", headers=h(S["sup"])).status_code == 403
    assert sid in [s["session_id"] for s in client.get(f"/w/{S['ws']}/chat/sessions", headers=h(S["staff"])).json()]


def test_money_question_review_flow(client):
    r = client.post(f"/w/{S['ws']}/ask", json={"question": "Giá bán ĐT-018 là bao nhiêu?"}, headers=h(S["staff"])).json()
    assert r["status"] == "pending_review" and r["review_id"]
    assert client.get(f"/w/{S['ws']}/answers/{r['trace_id']}", headers=h(S["staff"])).json()["answer"] is None
    assert client.get(f"/w/{S['ws']}/review", headers=h(S["staff"])).status_code == 403
    assert any(i["id"] == r["review_id"] for i in client.get(f"/w/{S['ws']}/review", headers=h(S["sup"])).json())
    ok = client.post(f"/w/{S['ws']}/review/{r['review_id']}/approve", json={"edited_answer": "Giá ĐT-018 là 1.440.000đ [1]."}, headers=h(S["sup"]))
    assert ok.status_code == 200 and ok.json()["overridden"] is True
    assert client.post(f"/w/{S['ws']}/review/{r['review_id']}/reject", json={}, headers=h(S["sup"])).status_code == 409
    final = client.get(f"/w/{S['ws']}/answers/{r['trace_id']}", headers=h(S["staff"])).json()
    assert final["status"] == "answered" and final["answer"].startswith("Giá ĐT-018")
    # editor (supervisor) hỏi thì không cần duyệt
    assert client.post(f"/w/{S['ws']}/ask", json={"question": "Giá bán ĐT-018 là bao nhiêu?"}, headers=h(S["sup"])).json()["status"] == "answered"


def test_records_patch_delete_restore_roles(client):
    lst = client.get(f"/w/{S['ws']}/records", params={"sku": "ĐT-018", "doc_type": "price_list"}, headers=h(S["staff"])).json()
    rid = lst["items"][0]["record_id"]
    path = quote(rid, safe="")
    assert client.patch(f"/w/{S['ws']}/records/{path}", json={"content": "x"}, headers=h(S["staff"])).status_code == 403
    p = client.patch(f"/w/{S['ws']}/records/{path}", json={"content": lst["items"][0]["content"].replace("1440000", "1450000")}, headers=h(S["sup"]))
    assert p.status_code == 200 and p.json()["record"]["manually_edited"] is True
    assert client.delete(f"/w/{S['ws']}/records/{path}", headers=h(S["sup"])).json()["status"] == "deleted"
    assert client.post(f"/w/{S['ws']}/records/{path}/restore", headers=h(S["sup"])).json()["status"] == "active"


def test_health_conflicts_config_api_key_rate_limit(client):
    hlth = client.get(f"/w/{S['ws']}/kb/health", headers=h(S["staff"])).json()
    assert {"records_by_doc_type", "blocked_files", "open_conflicts", "abstentions_last_days", "totals"} <= set(hlth)
    assert hlth["totals"]["documents"] == 2
    assert isinstance(client.get(f"/w/{S['ws']}/conflicts", headers=h(S["staff"])).json(), list)
    assert client.get("/config", headers=h(S["staff"])).json()["config_version"]
    key = client.post(f"/w/{S['ws']}/api-keys", json={"name": "pos", "role": "viewer", "rate_limit_per_minute": 3}, headers=h(S["owner"])).json()
    hk = {"X-API-Key": key["api_key"]}
    codes = [client.get(f"/w/{S['ws']}", headers=hk).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    # key gắn workspace này không vào được workspace khác; bootstrap key vào được mọi nơi
    other = client.get("/admin/workspaces", headers={"X-API-Key": ADMIN_KEY}).json()
    assert len(other) >= 2
    assert client.get("/admin/users", headers=h(S["staff"])).status_code == 403
    assert client.get("/admin/users", headers={"X-API-Key": ADMIN_KEY}).status_code == 200


def test_validation_and_unknown_token(client):
    assert client.post(f"/w/{S['ws']}/ask", json={"question": ""}, headers=h(S["staff"])).json()["error"]["code"] == "validation_failed"
    assert client.get("/auth/me", headers={"Authorization": "Bearer abc"}).status_code == 401
