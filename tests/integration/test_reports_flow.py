"""Integration reports: tạo báo cáo từ prompt -> job sinh (model giả lập lập dàn ý + agent) -> block có citation/chart
-> sửa block, ghim trace chat, xuất DOCX/XLSX/HTML."""
import json
from types import SimpleNamespace

import pytest

from tests.integration.test_assistant_flow import AgentModel, overlap_rerank


class ReportModel(AgentModel):
    """Thêm nhánh lập dàn ý (prompt report_plan) cho model giả lập của assistant."""

    def create(self, **kwargs):
        if "lập dàn ý báo cáo" in (kwargs.get("system") or ""):
            plan = {"title": "Báo cáo tổng quan kinh doanh", "sections": [
                {"heading": "Chính sách đổi trả", "goal": "tóm tắt", "kind": "narrative", "question": "Thời hạn đổi trả theo ngành hàng được quy định thế nào?"},
                {"heading": "Cơ cấu SKU", "goal": "thống kê", "kind": "chart", "question": "Mỗi ngành hàng có bao nhiêu SKU trong bảng giá?"}]}
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(plan, ensure_ascii=False))], stop_reason="end_turn",
                                   usage=SimpleNamespace(input_tokens=50, output_tokens=50))
        return super().create(**kwargs)


@pytest.fixture(scope="module")
def rctx(seeded):
    import os

    os.environ.update({"JWT_SECRET": "test-secret-at-least-32-characters-long!"})
    from core.config import reset_caches

    reset_caches()
    from fastapi.testclient import TestClient

    from api.main import create_app
    from api.state import build_services
    from core.llm import LLMClient, set_llm

    fake = ReportModel()
    set_llm(LLMClient(client=fake, sleep=lambda s: None))
    services = build_services(warmup=False)
    services.retriever.rerank_fn = overlap_rerank
    services.retriever._rerank_ready.set()
    services.cfg.retrieval.rerank.enabled = True
    app = create_app(services)
    with TestClient(app) as c:
        tok = c.post("/auth/login", json={"email": seeded["email"], "password": seeded["password"]}).json()["access_token"]
        yield {"c": c, "h": {"Authorization": f"Bearer {tok}"}, "ws": seeded["workspace_id"], "fake": fake}
    set_llm(None)


def test_generate_report_from_prompt_then_edit_pin_export(rctx):
    c, h, ws = rctx["c"], rctx["h"], rctx["ws"]
    r = c.post(f"/w/{ws}/reports", json={"prompt": "Báo cáo tổng quan về chính sách và cơ cấu SKU"}, headers=h).json()
    assert r["status"] == "generating" and r["job"]["type"] == "report_generate"
    from core.config import get_config
    from core.llm import LLMClient
    from worker.main import Worker

    w = Worker("rep-test")
    w._llm = LLMClient(cfg=get_config(), client=rctx["fake"], sleep=lambda s: None)
    w.loop(once=True)
    rep = c.get(f"/w/{ws}/reports/{r['id']}", headers=h).json()
    assert rep["status"] == "ready" and rep["title"] == "Báo cáo tổng quan kinh doanh", rep["job"]
    types = [b["type"] for b in rep["blocks"]]
    assert types[0] == "heading" and "chart" in types and "sources" in types
    md = [b for b in rep["blocks"] if b["type"] == "markdown" and b.get("citations")]
    assert md and "[1]" in md[0]["text"]
    # sửa: bỏ block cuối
    blocks = rep["blocks"][:-1]
    assert c.patch(f"/w/{ws}/reports/{r['id']}", json={"blocks": blocks, "title": "Báo cáo Q3"}, headers=h).json()["block_count"] == len(blocks)
    # ghim một câu trả lời chat vào báo cáo
    ask = c.post(f"/w/{ws}/ask", json={"question": "Mỗi ngành hàng có bao nhiêu SKU trong bảng giá?"}, headers=h).json()
    assert ask["status"] == "answered" and ask["artifacts"]
    pinned = c.post(f"/w/{ws}/reports/{r['id']}/pin", json={"trace_id": ask["trace_id"], "heading": "Ghim từ chat"}, headers=h).json()
    assert pinned["block_count"] >= len(blocks) + 2 and any(b.get("trace_id") == ask["trace_id"] and b["type"] == "chart" for b in pinned["blocks"])
    for fmt, ctype in (("html", "text/html"), ("docx", "wordprocessingml"), ("xlsx", "spreadsheetml")):
        resp = c.get(f"/w/{ws}/reports/{r['id']}/export", params={"fmt": fmt}, headers=h)
        assert resp.status_code == 200 and ctype in resp.headers["content-type"], (fmt, resp.text[:200])
    assert [x["id"] for x in c.get(f"/w/{ws}/reports", headers=h).json()] == [r["id"]]
    assert c.delete(f"/w/{ws}/reports/{r['id']}", headers=h).status_code == 200
    assert c.get(f"/w/{ws}/reports/{r['id']}", headers=h).status_code == 404


def test_empty_report_pin_requires_completed_trace(rctx):
    c, h, ws = rctx["c"], rctx["h"], rctx["ws"]
    r = c.post(f"/w/{ws}/reports", json={"title": "Trống"}, headers=h).json()
    assert r["status"] == "draft"
    assert c.post(f"/w/{ws}/reports/{r['id']}/pin", json={"trace_id": "khongco00000000000000000000000000"[:32]}, headers=h).status_code == 404
