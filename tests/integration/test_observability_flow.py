"""Integration E1/E2/E3/E5: span Langfuse + metadata trên 1 request QA thật (client Langfuse giả lập ghi
lại lời gọi), trace ingest theo file, alert webhook có cooldown, retention."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text


class _Obj:
    def __init__(self, rec, kind, **kw):
        self.rec, self.kind, self.kw = rec, kind, kw
        rec.append((kind, kw))

    def span(self, **kw):
        return _Obj(self.rec, "span", parent=self.kw.get("name") or self.kw.get("id"), **kw)

    def generation(self, **kw):
        return _Obj(self.rec, "generation", parent=self.kw.get("name"), **kw)

    def end(self, **kw):
        self.rec.append(("end", {"name": self.kw.get("name"), **kw}))

    def update(self, **kw):
        self.rec.append(("update", kw))


class FakeLangfuse:
    def __init__(self):
        self.rec = []

    def trace(self, **kw):
        return _Obj(self.rec, "trace", **kw)

    def flush(self):
        pass


@pytest.fixture
def langfuse(monkeypatch, env):
    from observability import tracing

    fake = FakeLangfuse()
    monkeypatch.setattr(tracing, "_langfuse_client", fake)
    monkeypatch.setattr(tracing, "_langfuse_init_done", True)
    return fake


def test_qa_request_produces_all_required_spans_and_metadata(langfuse, seeded):
    from tests.integration.test_qa_service import make_service, req

    svc, _ = make_service()
    r = svc.answer(req("Thời hạn đổi trả theo ngành hàng được quy định thế nào?", channel="chat"))
    assert r.status == "answered"
    traces = [kw for k, kw in langfuse.rec if k == "trace"]
    assert traces[0]["id"] == r.trace_id
    meta = traces[0]["metadata"]
    assert meta["config_version"] and meta["store_code"] == "GM-HN01" and meta["user_id"] == "u-staff"
    spans = {kw["name"] for k, kw in langfuse.rec if k == "span"}
    assert {"router", "retrieval", "retrieval.dense", "retrieval.bm25", "retrieval.fusion", "retrieval.rerank",
            "model.answer", "answer.postcheck", "answer.final"} <= spans
    gens = [kw for k, kw in langfuse.rec if k == "generation"]
    answer_gen = [g for g in gens if g["name"] == "llm.answer"][0]
    assert answer_gen["usage"]["input"] > 0 and answer_gen["usage"]["total_cost"] > 0  # token + cost
    updates = [kw for k, kw in langfuse.rec if k == "update"]
    assert any(u.get("session_id") == r.session_id for u in updates)  # session_id gắn lên trace
    assert any((u.get("metadata") or {}).get("question_type") for u in updates)


def test_ingest_has_one_trace_per_file(langfuse, env, tmp_path):
    from docx import Document as Docx

    from core.config import get_config
    from core.db import session_scope
    from ingest.service import UploadOptions, register_upload
    from worker.main import Worker

    for i in range(2):
        p = tmp_path / f"quy_dinh_ca_lam_viec_{i}.docx"
        d = Docx()
        d.add_heading(f"QUY ĐỊNH CA LÀM VIỆC SỐ {i}", level=0)
        d.add_heading("Điều 1. Giờ làm", level=2)
        d.add_paragraph(f"Ca sáng bắt đầu lúc {7 + i} giờ, nhân viên có mặt trước 15 phút để bàn giao.")
        d.save(p)
        with session_scope() as s:
            register_upload(s, get_config(), src=str(p), filename=p.name, opts=UploadOptions(), uploaded_by="t")
    Worker("obs").loop(once=True)
    ingest_traces = [kw for k, kw in langfuse.rec if k == "trace" and kw["name"] == "ingest.file"]
    assert len(ingest_traces) == 2
    assert {"ingest.detect", "ingest.parse", "ingest.write", "ingest.embed"} <= {kw["name"] for k, kw in langfuse.rec if k == "span"}


def test_alerts_fire_once_within_cooldown_and_mark_jobs(env):
    from core.config import get_config
    from core.db import session_scope
    from core.models import AlertEvent, Job
    from observability.alerts import run_alert_checks

    cfg = get_config().model_copy(deep=True)
    cfg.alerts.daily_cost_usd = 0.0  # chắc chắn vượt (đã có llm_usage từ các test trước? không bắt buộc)
    with session_scope() as s:
        s.add(Job(type="ingest_file", status="error", error="boom", finished_at=datetime.now(timezone.utc)))
        s.execute(text("INSERT INTO llm_usage (created_at, role, model, input_tokens, output_tokens, cache_read_tokens, "
                       "cache_write_tokens, cost_usd, latency_ms, status) VALUES (now(), 'answer', 'm', 1, 1, 0, 0, 0.5, 1, 'ok')"))
    sent = []
    first = run_alert_checks(cfg, sender=lambda al, c, st: sent.append(al.rule))
    assert {"daily_cost", "ingest_failed"} <= set(sent) and first
    again = run_alert_checks(cfg, sender=lambda al, c, st: sent.append("DUP-" + al.rule))
    assert not [x for x in sent if x.startswith("DUP-")] and again == []  # cooldown + job đã đánh dấu alerted
    with session_scope() as s:
        assert s.execute(select(Job).where(Job.status == "error", Job.alerted.is_(False), Job.type == "ingest_file")).first() is None
        assert s.execute(select(AlertEvent).where(AlertEvent.rule == "daily_cost", AlertEvent.delivered.is_(True))).first()


def test_alert_delivery_failure_is_recorded_and_retried(env):
    from core.config import get_config
    from core.db import session_scope
    from core.models import AlertEvent, ReviewItem
    from observability.alerts import run_alert_checks

    cfg = get_config().model_copy(deep=True)
    cfg.alerts.review_queue_max = -1  # 0 mục chờ vẫn > -1 -> kích hoạt
    with session_scope() as s:
        s.add(ReviewItem(trace_id="t-backlog", question="q", draft_answer="a", reason="test"))

    def broken(al, c, st):
        raise RuntimeError("webhook down")
    run_alert_checks(cfg, sender=broken)
    with session_scope() as s:
        ev = s.execute(select(AlertEvent).where(AlertEvent.rule == "review_backlog")).scalars().all()
        assert ev and not ev[-1].delivered and "webhook down" in ev[-1].error
    ok = []
    run_alert_checks(cfg, sender=lambda al, c, st: ok.append(al.rule))
    assert "review_backlog" in ok  # chưa delivered -> không bị cooldown chặn


def test_retention_deletes_old_traces_but_keeps_recent(env):
    from core.config import get_config
    from core.db import session_scope
    from observability.retention import run_retention

    old = datetime.now(timezone.utc) - timedelta(days=40)
    with session_scope() as s:
        for tid, ts in (("oldtrace0000000000000000000000001", old), ("newtrace0000000000000000000000001", datetime.now(timezone.utc))):
            s.execute(text("INSERT INTO qa_traces (trace_id, created_at, channel, question, status, citations, decision_trace, "
                           "conflicts, config_version, config_hash, cost_usd, input_tokens, output_tokens) VALUES "
                           "(:t, :c, 'ask', 'q', 'answered', '[]', '{}', '[]', 'v', 'h', 0, 0, 0)"), {"t": tid[:32], "c": ts})
    out = run_retention(get_config())
    assert out["qa_traces"] >= 1
    with session_scope() as s:
        ids = set(s.execute(text("SELECT trace_id FROM qa_traces WHERE trace_id LIKE '%trace0000%'")).scalars())
    assert ids == {"newtrace0000000000000000000000001"[:32]}
