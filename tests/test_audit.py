"""Pytest cho security/audit.py — append-only audit log, rate limiter, anomaly detection, CLI."""
import json
import os

import pytest

from security import audit as security_audit


# =====================================================================================
# AppendOnlyAuditLog — append/read
# =====================================================================================

def _log(tmp_path, name="audit_trail.log"):
    return security_audit.AppendOnlyAuditLog(path=os.path.join(tmp_path, name))


def test_append_writes_jsonl_entry_with_expected_fields(tmp_path):
    log = _log(tmp_path)
    entry = log.append(
        run_id="run-1", file_id="data/raw/pdf/f.pdf", tool_name="write_kb_record",
        arguments={"record": {"record_id": "price-1"}}, result={"status": "written"},
        policy_decision="committed", approved_by=None, latency_seconds=0.123,
    )
    assert entry.run_id == "run-1"
    assert entry.file_id == "data/raw/pdf/f.pdf"
    assert entry.result_hash.startswith("sha256:")

    entries = log.read_entries()
    assert len(entries) == 1
    assert entries[0]["tool_name"] == "write_kb_record"
    assert entries[0]["policy_decision"] == "committed"
    assert entries[0]["latency_seconds"] == 0.123


def test_append_is_append_only_multiple_calls_accumulate(tmp_path):
    log = _log(tmp_path)
    for i in range(3):
        log.append(
            run_id="run-1", file_id="f.pdf", tool_name="parse_document", arguments={"path": "f.pdf"},
            result={"chunks": []}, policy_decision="auto_approved", latency_seconds=0.01,
        )
    entries = log.read_entries()
    assert len(entries) == 3


def test_read_entries_filters_by_run_id(tmp_path):
    log = _log(tmp_path)
    log.append(
        run_id="run-A", file_id="a.pdf", tool_name="detect_file_type", arguments={"path": "a.pdf"},
        result={"kind": "pdf"}, policy_decision="auto_approved", latency_seconds=0.01,
    )
    log.append(
        run_id="run-B", file_id="b.pdf", tool_name="detect_file_type", arguments={"path": "b.pdf"},
        result={"kind": "pdf"}, policy_decision="auto_approved", latency_seconds=0.01,
    )
    only_a = log.read_entries(run_id="run-A")
    assert len(only_a) == 1
    assert only_a[0]["file_id"] == "a.pdf"


def test_read_entries_on_missing_file_returns_empty(tmp_path):
    log = _log(tmp_path, name="does-not-exist.log")
    assert log.read_entries() == []


def test_read_entries_skips_corrupted_line_without_failing(tmp_path):
    log = _log(tmp_path)
    log.append(
        run_id="run-1", file_id="f.pdf", tool_name="detect_file_type", arguments={"path": "f.pdf"},
        result={"kind": "pdf"}, policy_decision="auto_approved", latency_seconds=0.01,
    )
    with open(log._path, "a", encoding="utf-8") as f:
        f.write("{ khong phai json hop le\n")
    log.append(
        run_id="run-1", file_id="f.pdf", tool_name="parse_document", arguments={"path": "f.pdf"},
        result={"chunks": []}, policy_decision="auto_approved", latency_seconds=0.01,
    )
    entries = log.read_entries()
    assert len(entries) == 2  # dòng hỏng ở giữa bị bỏ qua, không mất 2 entry hợp lệ


# =====================================================================================
# arguments hash-if-long vs result luôn hash
# =====================================================================================

def test_short_arguments_kept_verbatim_in_entry(tmp_path):
    log = _log(tmp_path)
    entry = log.append(
        run_id="r", file_id="f.pdf", tool_name="detect_file_type", arguments={"path": "f.pdf"},
        result="pdf", policy_decision="auto_approved", latency_seconds=0.01,
    )
    assert entry.arguments == {"path": "f.pdf"}


def test_long_arguments_are_hashed_not_kept_verbatim(tmp_path):
    log = _log(tmp_path)
    long_content = "x" * 5000
    entry = log.append(
        run_id="r", file_id="f.pdf", tool_name="write_kb_record",
        arguments={"record": {"content": long_content}}, result={"status": "written"},
        policy_decision="committed", latency_seconds=0.01,
    )
    assert long_content not in json.dumps(entry.arguments)
    assert entry.arguments["sha256"].startswith("sha256:")
    assert entry.arguments["truncated"] is True


def test_result_always_hashed_even_when_short(tmp_path):
    log = _log(tmp_path)
    entry = log.append(
        run_id="r", file_id="f.pdf", tool_name="detect_file_type", arguments={"path": "f.pdf"},
        result={"kind": "pdf"}, policy_decision="auto_approved", latency_seconds=0.01,
    )
    assert entry.result_hash.startswith("sha256:")
    assert "pdf" not in entry.result_hash


# =====================================================================================
# RateLimiter
# =====================================================================================

def test_rate_limiter_allows_up_to_max_calls_within_window():
    clock = {"t": 0.0}
    limiter = security_audit.RateLimiter(max_calls=3, window_seconds=60, time_fn=lambda: clock["t"])
    for _ in range(3):
        limiter.check("run-1", "parse_document")  # không raise


def test_rate_limiter_blocks_call_over_limit_within_window():
    clock = {"t": 0.0}
    limiter = security_audit.RateLimiter(max_calls=2, window_seconds=60, time_fn=lambda: clock["t"])
    limiter.check("run-1", "parse_document")
    limiter.check("run-1", "parse_document")
    with pytest.raises(security_audit.RateLimitExceeded) as exc_info:
        limiter.check("run-1", "parse_document")
    assert exc_info.value.subject == "run-1"
    assert exc_info.value.tool_name == "parse_document"


def test_rate_limiter_window_slides_old_calls_evicted():
    clock = {"t": 0.0}
    limiter = security_audit.RateLimiter(max_calls=2, window_seconds=10, time_fn=lambda: clock["t"])
    limiter.check("run-1", "transcribe_media")
    limiter.check("run-1", "transcribe_media")
    clock["t"] = 11.0  # vượt window_seconds -> 2 lần gọi cũ bị evict
    limiter.check("run-1", "transcribe_media")  # không raise


def test_rate_limiter_is_independent_per_tool_and_subject():
    clock = {"t": 0.0}
    limiter = security_audit.RateLimiter(max_calls=1, window_seconds=60, time_fn=lambda: clock["t"])
    limiter.check("run-1", "parse_document")
    limiter.check("run-1", "transcribe_media")  # tool khác -> hạn mức riêng
    limiter.check("run-2", "parse_document")  # subject khác -> hạn mức riêng
    with pytest.raises(security_audit.RateLimitExceeded):
        limiter.check("run-1", "parse_document")


def test_rate_limiter_rejects_invalid_max_calls():
    with pytest.raises(ValueError):
        security_audit.RateLimiter(max_calls=0, window_seconds=60)


# =====================================================================================
# detect_anomalies
# =====================================================================================

def _entries(counts):
    """counts: {file_id: số lần gọi} -> list entry dict tối giản đủ cho detect_anomalies."""
    out = []
    for file_id, n in counts.items():
        out.extend({"file_id": file_id} for _ in range(n))
    return out


def test_detect_anomalies_needs_at_least_two_files():
    assert security_audit.detect_anomalies(_entries({"a.pdf": 100})) == []


def test_detect_anomalies_flags_file_far_above_average():
    entries = _entries({"a.pdf": 5, "b.pdf": 6, "c.pdf": 5, "outlier.pdf": 60})
    anomalies = security_audit.detect_anomalies(entries, threshold_multiplier=3.0, min_calls=5)
    assert len(anomalies) == 1
    assert anomalies[0].file_id == "outlier.pdf"
    assert anomalies[0].call_count == 60


def test_detect_anomalies_no_flag_when_all_similar():
    entries = _entries({"a.pdf": 5, "b.pdf": 6, "c.pdf": 4})
    assert security_audit.detect_anomalies(entries) == []


def test_detect_anomalies_respects_min_calls_floor():
    # outlier gọi nhiều hơn hẳn về TỈ LỆ nhưng số lần tuyệt đối quá nhỏ -> không đủ ý nghĩa thống kê
    entries = _entries({"a.pdf": 1, "b.pdf": 1, "spike.pdf": 4})
    assert security_audit.detect_anomalies(entries, threshold_multiplier=3.0, min_calls=5) == []


# =====================================================================================
# CLI
# =====================================================================================

def test_cli_run_shows_entries_for_run_id(tmp_path, capsys):
    log_path = os.path.join(tmp_path, "trail.log")
    log = security_audit.AppendOnlyAuditLog(path=log_path)
    log.append(
        run_id="run-1", file_id="f.pdf", tool_name="write_kb_record", arguments={"record_id": "price-1"},
        result={"status": "written"}, policy_decision="committed", approved_by=None, latency_seconds=0.05,
    )
    log.append(
        run_id="run-2", file_id="g.pdf", tool_name="write_kb_record", arguments={"record_id": "price-2"},
        result={"status": "written"}, policy_decision="committed", latency_seconds=0.05,
    )

    exit_code = security_audit.main(["--run", "run-1", "--log-path", log_path])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "run=run-1" in out
    assert "run=run-2" not in out


def test_cli_no_run_shows_anomaly_report(tmp_path, capsys):
    log_path = os.path.join(tmp_path, "trail.log")
    log = security_audit.AppendOnlyAuditLog(path=log_path)
    for file_id, n in {"a.pdf": 5, "b.pdf": 5, "c.pdf": 5, "d.pdf": 6, "outlier.pdf": 100}.items():
        for _ in range(n):
            log.append(
                run_id="run-x", file_id=file_id, tool_name="parse_document", arguments={"path": file_id},
                result={"chunks": []}, policy_decision="auto_approved", latency_seconds=0.01,
            )

    exit_code = security_audit.main(["--log-path", log_path])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "Cảnh báo anomaly" in out
    assert "outlier.pdf" in out


def test_cli_empty_log_reports_nothing_gracefully(tmp_path, capsys):
    log_path = os.path.join(tmp_path, "empty.log")
    exit_code = security_audit.main(["--log-path", log_path])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Không có entry" in out
