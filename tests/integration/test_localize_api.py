"""Nội dung AI sinh trả theo ngôn ngữ người gọi: chuỗi có bản dịch trong content_translations được thay theo Accept-Language."""
from tests.integration.test_api import ADMIN_KEY, client  # noqa: F401  (fixture dùng chung)


def test_document_summary_follows_accept_language(client, seeded):  # noqa: F811
    from core.db import session_scope
    from core.models import ContentTranslation, Document
    from knowledge.translate import text_hash

    ws = seeded["workspace_id"]
    with session_scope() as s:
        doc = s.query(Document).filter(Document.workspace_id == ws, Document.deleted_at.is_(None)).first()
        doc.summary = "Chính sách đổi trả hàng hóa theo ngành hàng"
        s.merge(ContentTranslation(workspace_id=ws, lang="en", source_hash=text_hash(doc.summary), text="Return policy by product category"))
        doc_id = doc.id
    head = {"X-API-Key": ADMIN_KEY}
    en = client.get(f"/w/{ws}/documents/{doc_id}", headers={**head, "Accept-Language": "en"}).json()
    vi = client.get(f"/w/{ws}/documents/{doc_id}", headers={**head, "Accept-Language": "vi"}).json()
    assert en["summary"] == "Return policy by product category" and en["document_id"] == doc_id
    assert vi["summary"] == "Chính sách đổi trả hàng hóa theo ngành hàng"
    listed = client.get(f"/w/{ws}/documents", headers={**head, "Accept-Language": "en"}).json()
    assert any(d["summary"] == "Return policy by product category" for d in listed["items"])


def test_past_answers_follow_accept_language(client, seeded, monkeypatch):  # noqa: F811
    """Câu hỏi/câu trả lời đã lưu (trang chủ, Hỏi đáp) hiện theo ngôn ngữ giao diện; câu chưa có bản dịch được dịch bù ở nền."""
    from core.db import session_scope
    from core.models import ContentTranslation, QATrace
    from knowledge import translate as tr

    ws = seeded["workspace_id"]
    question, answer = "What is the warranty period for drones?", "I did not find any information about drone warranties."
    with session_scope() as s:
        s.add(QATrace(trace_id="tr_localize_1", workspace_id=ws, channel="chat", question=question, status="answered", answer=answer,
                      config_version="t", config_hash="t", user_id="api-key"))
        s.merge(ContentTranslation(workspace_id=ws, lang="vi", source_hash=tr.text_hash(question), text="Thời hạn bảo hành drone là bao lâu?"))
    scheduled = []
    monkeypatch.setattr(tr, "translate_later", lambda llm, ws_id, texts: scheduled.extend(texts))
    head = {"X-API-Key": ADMIN_KEY}
    vi = client.get(f"/w/{ws}/answers/tr_localize_1", headers={**head, "Accept-Language": "vi"}).json()
    assert vi["question"] == "Thời hạn bảo hành drone là bao lâu?" and vi["answer"] == answer
    assert answer in scheduled and question not in scheduled  # câu trả lời chưa có bản dịch -> dịch bù
    scheduled.clear()
    en = client.get(f"/w/{ws}/answers/tr_localize_1", headers={**head, "Accept-Language": "en"}).json()
    assert en["question"] == question and scheduled == []  # đã đúng ngôn ngữ người gọi -> không dịch
    recent = client.get(f"/w/{ws}/insights", headers={**head, "Accept-Language": "vi"}).json()["answers"]["recent"]
    assert any(r["question"] == "Thời hạn bảo hành drone là bao lâu?" for r in recent)
