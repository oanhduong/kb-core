"""2 file Excel khác nhau, mã site viết khác nhau ("STE0870" vs 870): sau upload hệ thống tự thấy 2 bảng nối qua site,
gửi cách nối cho model viết SQL, dựng thực thể site trên bản đồ tri thức (mention trỏ về đúng dòng Excel), giữ quyết định
xác nhận/bỏ của người dùng khi dò lại."""
import pytest
from openpyxl import Workbook

from tests.integration.test_api import ADMIN_KEY, client  # noqa: F401  (fixture dùng chung)


def _xlsx(path, header, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return str(path)


@pytest.fixture(scope="module")
def two_files(env, tmp_path_factory):
    from accounts import service as accounts
    from core.config import get_config
    from core.db import session_scope
    from ingest.service import UploadOptions, register_upload
    from worker.main import Worker

    d = tmp_path_factory.mktemp("rel")
    sites = _xlsx(d / "sites.xlsx", ["Site #", "Site Name", "Country"],
                  [[f"STE{n:04d}", f"Clinic {n}", "VN" if n % 2 else "TH"] for n in range(860, 880)])
    visits = _xlsx(d / "visits.xlsx", ["Site Number", "Subject", "Visits"],
                   [[860 + (i % 15), f"SUBJ-{i:03d}", i % 7] for i in range(60)])
    with session_scope() as s:
        user = accounts.create_user(s, email="rel@example.com", password="rel-pass-123", name="Rel")
        ws = accounts.create_workspace(s, user, name="Clinical", domain_pack="generic")
        ws_id = ws.id
        for path, title in ((sites, "Sites"), (visits, "Visits")):
            register_upload(s, get_config(), workspace_id=ws_id, src=path, filename=path.rsplit("/", 1)[-1],
                            opts=UploadOptions(title=title), uploaded_by="test")
    Worker("rel-test").loop(once=True)  # ingest + trích bảng; job dò quan hệ có hẹn giờ -> gọi trực tiếp bên dưới
    return ws_id


def test_tables_link_across_files_and_feed_sql_and_map(client, two_files):  # noqa: F811
    from analytics import engine
    from analytics.relations import discover, join_hints
    from core.db import session_scope
    from core.models import Entity, EntityMention, Job, KBRecordRow, Relation
    from knowledge.table_entities import build_key_entities

    ws = two_files
    with session_scope() as s:
        assert s.query(Job).filter(Job.type == "relations_discover", Job.workspace_id == ws).count() == 1  # tự đẩy sau khi có 2 bảng
    out = discover(ws)
    assert out["found"] >= 1
    head = {"X-API-Key": ADMIN_KEY}
    items = client.get(f"/w/{ws}/analytics/relationships", headers=head).json()["items"]
    site = next(i for i in items if {i["left"]["label"], i["right"]["label"]} == {"Site Number", "Site #"})
    assert site["right"]["label"] == "Site #" and site["cardinality"] == "many_to_one" and site["matched_keys"] == 15

    with session_scope() as s:
        [g] = [g for g in join_hints(s, engine.catalog(s, ws)) if any(m[2] == "Site #" for m in g.members)]
        tables = engine.catalog(s, ws)
    a, b = [m for m in g.members]
    from core.config import get_config

    res = engine.run_query(get_config(), tables, f"SELECT COUNT(*) AS n FROM {a[0]} JOIN {b[0]} ON {a[3]} = {b[3]}")
    assert res.rows[0][0] == 60  # mọi lượt khám nối được về site dù "860" vs "STE0860"

    stats = build_key_entities(ws)
    assert stats["entities"] >= 15
    with session_scope() as s:
        e = s.query(Entity).filter(Entity.workspace_id == ws, Entity.name == "STE0870").one()
        assert e.type == "location" and "870" in e.aliases and e.description.endswith("Clinic 870") and "Clinic 870" in e.aliases
        docs = {m.document_id for m in s.query(EntityMention).filter(EntityMention.entity_id == e.id)}
        assert len(docs) == 2  # nhắc tới ở cả 2 file
        rid = s.query(EntityMention).filter(EntityMention.entity_id == e.id).first().record_id
        assert "870" in s.get(KBRecordRow, rid).content
        n_mentions = s.query(EntityMention).filter(EntityMention.entity_id == e.id).count()
        edges = s.query(Relation).filter(Relation.workspace_id == ws, Relation.type == "table_link").count()

    # chạy lại không nhân bản; bỏ liên kết -> thực thể từ cột mã không còn
    build_key_entities(ws)
    with session_scope() as s:
        e = s.query(Entity).filter(Entity.workspace_id == ws, Entity.name == "STE0870").one()
        assert s.query(EntityMention).filter(EntityMention.entity_id == e.id).count() == n_mentions
        assert s.query(Relation).filter(Relation.workspace_id == ws, Relation.type == "table_link").count() == edges
    r = client.patch(f"/w/{ws}/analytics/relationships/{site['id']}", json={"status": "rejected"}, headers=head)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    discover(ws)  # dò lại vẫn giữ quyết định
    build_key_entities(ws)
    with session_scope() as s:
        assert s.query(Entity).filter(Entity.workspace_id == ws, Entity.name == "STE0870").count() == 0
        assert all(g.label != "Site #" for g in join_hints(s, engine.catalog(s, ws)))
    items = client.get(f"/w/{ws}/analytics/relationships", headers=head).json()["items"]
    assert next(i for i in items if i["id"] == site["id"])["status"] == "rejected"
