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
