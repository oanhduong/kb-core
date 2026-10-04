"""Unit test E3 (luật alert thuần) + E5 (audit log xoay vòng theo tháng, xoá nguyên file quá hạn)."""
import os
from datetime import datetime, timezone

from core.config import get_config
from observability.alerts import AlertMetrics, evaluate_rules
from observability.retention import RotatingAuditLog, prune_audit_files

CFG = get_config()


def test_no_alert_when_everything_normal():
    assert evaluate_rules(AlertMetrics(cost_today_usd=1, requests_5m=50, errors_5m=1, questions_1h=40, abstentions_1h=2,
                                       questions_baseline=1000, abstentions_baseline=60, today="2026-10-03"), CFG) == []


def test_daily_cost_alert():
    [a] = evaluate_rules(AlertMetrics(cost_today_usd=CFG.alerts.daily_cost_usd + 1, today="2026-10-03"), CFG)
    assert a.rule == "daily_cost" and a.fingerprint == "2026-10-03"


def test_error_rate_requires_min_requests():
    assert evaluate_rules(AlertMetrics(requests_5m=3, errors_5m=3), CFG) == []  # quá ít request -> không kết luận
    [a] = evaluate_rules(AlertMetrics(requests_5m=20, errors_5m=10), CFG)
    assert a.rule == "error_rate_5m" and "50%" in a.message


def test_abstention_spike_vs_baseline():
    m = AlertMetrics(questions_1h=40, abstentions_1h=20, questions_baseline=1000, abstentions_baseline=100)
    [a] = evaluate_rules(m, CFG)
    assert a.rule == "abstention_spike" and "50%" in a.message and "10%" in a.message
    # cùng tỉ lệ nhưng nền vốn cao -> không phải "đột biến"
    assert evaluate_rules(AlertMetrics(questions_1h=40, abstentions_1h=20, questions_baseline=1000, abstentions_baseline=450), CFG) == []


def test_ingest_failed_and_review_backlog_alerts():
    m = AlertMetrics(failed_ingest_jobs=[{"job_id": "j1", "status": "blocked", "document_id": "d1", "filename": "a.pdf"}],
                     review_pending=CFG.alerts.review_queue_max + 5)
    rules = {a.rule: a for a in evaluate_rules(m, CFG)}
    assert set(rules) == {"ingest_failed", "review_backlog"}
    assert rules["ingest_failed"].job_ids == ["j1"] and "a.pdf (blocked)" in rules["ingest_failed"].message


def test_rotating_audit_log_writes_monthly_file_and_reads_back(tmp_path):
    log = RotatingAuditLog(str(tmp_path))
    log.append(run_id="r1", file_id="f", tool_name="api:test", arguments={"a": 1}, result={"ok": True},
               policy_decision="committed", latency_seconds=0.0, approved_by="admin")
    files = os.listdir(tmp_path)
    assert files == [f"audit_trail-{datetime.now(timezone.utc):%Y-%m}.log"]
    assert [e["tool_name"] for e in log.read_entries("r1")] == ["api:test"]


def test_prune_audit_files_only_removes_whole_expired_months(tmp_path):
    for name in ("audit_trail-2025-01.log", "audit_trail-2025-09.log", "audit_trail-2026-09.log", "gateway-2025-02.log", "other.txt"):
        (tmp_path / name).write_text("x")
    removed = prune_audit_files(str(tmp_path), 365, now=datetime(2026, 10, 3, tzinfo=timezone.utc))
    # cutoff = 2025-10-03: tháng 9/2025 kết thúc 2025-10-01 < cutoff -> mọi dòng đã quá hạn -> xoá cả file
    assert sorted(os.path.basename(p) for p in removed) == ["audit_trail-2025-01.log", "audit_trail-2025-09.log", "gateway-2025-02.log"]
    assert sorted(os.listdir(tmp_path)) == ["audit_trail-2026-09.log", "other.txt"]
