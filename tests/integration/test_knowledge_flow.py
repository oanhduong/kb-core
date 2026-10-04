"""Integration knowledge: LLM giả lập trả entity/quan hệ theo nội dung cửa sổ -> hợp nhất trong workspace,
mention trỏ đúng record, hồ sơ ngữ nghĩa tài liệu, API entities/neighborhood/overview/merge."""
import json
import re
from types import SimpleNamespace

import pytest
from sqlalchemy import select

ADMIN_KEY = "rk_test_bootstrap_admin_key_123456"


class KnowledgeModel:
    """knowledge_extract: entity = tên chính sách + 'GreenMart Việt Nam' + Điều có trong cửa sổ; quan hệ Điều -part_of-> chính sách.
    document_profile: JSON hồ sơ cố định. table_describe: mô tả ngắn."""

    def __init__(self):
        self.calls = {"knowledge": 0, "profile": 0, "table": 0, "other": 0}
        self.messages = self

    def create(self, **kwargs):
        system, user = kwargs.get("system") or "", kwargs["messages"][-1]["content"]
        if "trích thực thể và quan hệ" in system:
            self.calls["knowledge"] += 1
            arts = sorted(set(re.findall(r"Điều (\d+)\.", user)))[:3]
            ents = [{"type": "organization", "name": "GreenMart Việt Nam", "aliases": ["GreenMart"], "description": "Chuỗi bán lẻ"}]
            rels = []
            policy = "Chính sách đổi trả hàng hóa" if "ĐỔI TRẢ" in user else "Chính sách bảo hành sản phẩm"
            ents.append({"type": "document", "name": policy, "aliases": []})
            for a in arts:
                ents.append({"type": "policy_clause", "name": f"Điều {a} {policy}", "aliases": [f"Điều {a}"]})
                rels.append({"source": f"Điều {a} {policy}", "target": policy, "type": "part_of", "label": "thuộc", "evidence": f"Điều {a}.", "confidence": 0.9})
            rels.append({"source": policy, "target": "GreenMart Việt Nam", "type": "issued_by", "label": "ban hành bởi", "evidence": "GreenMart Việt Nam", "confidence": 0.8})
            text = json.dumps({"entities": ents, "relations": rels}, ensure_ascii=False)
        elif "hồ sơ ngữ nghĩa" in system:
            self.calls["profile"] += 1
            text = json.dumps({"summary": "Tài liệu quy định điều kiện và thời hạn đổi trả hàng hoá tại GreenMart.", "kind": "chính sách nội bộ",
                               "topics": ["đổi trả", "bảo hành"], "key_entities": [{"type": "organization", "name": "GreenMart Việt Nam"}],
                               "time_range": {"from": "2026-01-01", "to": None, "note": "ban hành 01/01/2026"}, "language": "vi",
                               "audience": "nhân viên cửa hàng", "structure": "5 chương, 18 điều", "sensitivity": "internal", "quality_notes": None}, ensure_ascii=False)
        elif "mô tả một bảng dữ liệu" in system:
            self.calls["table"] += 1
            text = json.dumps({"description": "Bảng mẫu", "columns": {}, "suggested_questions": ["Có bao nhiêu dòng?"]}, ensure_ascii=False)
        else:
            self.calls["other"] += 1
            text = "{}"
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=80, output_tokens=40))


@pytest.fixture(scope="module")
def kctx(env):
    """Workspace generic riêng + 2 docx chính sách, chạy knowledge_extract với model giả lập."""
    import os

    os.environ.update({"JWT_SECRET": "test-secret-at-least-32-characters-long!", "BOOTSTRAP_ADMIN_API_KEY": ADMIN_KEY})
    from core.config import reset_caches

    reset_caches()
    from accounts import service as accounts
    from core.config import get_config
    from core.db import session_scope
    from core.llm import LLMClient, set_llm
    from ingest.service import UploadOptions, register_upload
    from worker.main import Worker

    fake = KnowledgeModel()
    set_llm(LLMClient(client=fake, sleep=lambda s: None))
    with session_scope() as s:
        user = accounts.create_user(s, email="kg@example.com", password="kg-pass-12345", name="KG")
        ws = accounts.create_workspace(s, user, name="Generic KG", domain_pack="generic")
        ws_id = ws.id
        for f in ("01_chinh_sach_doi_tra_hang_hoa.docx", "02_chinh_sach_bao_hanh_san_pham.docx"):
            register_upload(s, get_config(), workspace_id=ws_id, src=f"data/raw/docx/{f}", filename=f, opts=UploadOptions(), uploaded_by="t")
    w = Worker("kg-test")
    w._llm = LLMClient(client=fake, sleep=lambda s: None)
    w.loop(once=True)
    from fastapi.testclient import TestClient

    from api.main import create_app
    from api.state import build_services

    app = create_app(build_services(warmup=False))
    with TestClient(app) as c:
        tok = c.post("/auth/login", json={"email": "kg@example.com", "password": "kg-pass-12345"}).json()["access_token"]
        yield {"ws": ws_id, "h": {"Authorization": f"Bearer {tok}"}, "client": c, "fake": fake}
    set_llm(None)


def test_profile_and_entities_extracted_and_merged(kctx):
    from core.db import session_scope
    from core.models import Document, Entity, EntityMention, Job, Relation

    with session_scope() as s:
        jobs = s.execute(select(Job).where(Job.workspace_id == kctx["ws"], Job.type == "knowledge_extract")).scalars().all()
        assert jobs and all(j.status == "done" for j in jobs), [(j.status, j.error, j.result) for j in jobs]
        assert all("error" not in str(j.result.get("steps", {}).get("entities", {})) for j in jobs), [j.result for j in jobs]
        docs = s.execute(select(Document).where(Document.workspace_id == kctx["ws"])).scalars().all()
        assert all(d.summary and d.semantic_profile.get("generated_by") == "llm" for d in docs)
        assert docs[0].semantic_profile["stats"]["articles"] >= 15 and docs[0].semantic_profile["kind"] == "chính sách nội bộ"
        gm = s.execute(select(Entity).where(Entity.workspace_id == kctx["ws"], Entity.name == "GreenMart Việt Nam")).scalars().all()
        assert len(gm) == 1 and gm[0].mention_count >= 2 and len(gm[0].document_ids) == 2  # hợp nhất xuyên tài liệu
        assert "GreenMart" in gm[0].aliases
        rels = s.execute(select(Relation).where(Relation.workspace_id == kctx["ws"])).scalars().all()
        assert {r.type for r in rels} >= {"part_of", "issued_by"}
        m = s.execute(select(EntityMention).where(EntityMention.entity_id == gm[0].id)).scalars().first()
        assert m.record_id and m.snippet
    assert kctx["fake"].calls["knowledge"] > 0 and kctx["fake"].calls["profile"] == 2


def test_knowledge_api_list_detail_neighborhood_overview_merge(kctx):
    c, h, ws = kctx["client"], kctx["h"], kctx["ws"]
    lst = c.get(f"/w/{ws}/entities", params={"q": "greenmart"}, headers=h).json()
    assert lst["total"] == 1
    gm = lst["items"][0]
    detail = c.get(f"/w/{ws}/entities/{gm['id']}", headers=h).json()
    assert detail["mentions"] and detail["mentions"][0]["citation"] and detail["relations_in"]
    nb = c.get(f"/w/{ws}/entities/{gm['id']}/neighborhood", params={"depth": 2}, headers=h).json()
    assert any(n["is_seed"] for n in nb["nodes"]) and nb["edges"] and len(nb["nodes"]) > 3
    ov = c.get(f"/w/{ws}/graph/overview", headers=h).json()
    assert ov["entity_count"] >= 5 and {t["type"] for t in ov["types"]} >= {"organization", "document", "policy_clause"} and ov["edges"]
    # gộp 2 chính sách thành 1 thực thể -> mention/quan hệ chuyển hết, entity kia biến mất
    docs = c.get(f"/w/{ws}/entities", params={"type": "document"}, headers=h).json()["items"]
    keep, other = docs[0], docs[1]
    merged = c.post(f"/w/{ws}/entities/{keep['id']}/merge", json={"merge_ids": [other["id"]]}, headers=h).json()
    assert other["name"] in merged["aliases"] and merged["mention_count"] == keep["mention_count"] + other["mention_count"]
    assert c.get(f"/w/{ws}/entities/{other['id']}", headers=h).status_code == 404
    doc_detail = c.get(f"/w/{ws}/documents", headers=h).json()["items"][0]
    assert doc_detail["summary"] and doc_detail["semantic_profile"]["topics"]
    full = c.get(f"/w/{ws}/documents/{doc_detail['document_id']}", headers=h).json()
    assert full["entities"] and full["entities"][0]["mentions"] >= 1
