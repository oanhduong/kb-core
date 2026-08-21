"""Pytest cho agent/orchestrator.py.

_run_subagent_for_file luôn bị monkeypatch để KHÔNG gọi Anthropic API thật — test chỉ kiểm tra
logic điều phối thuần Python (manifest, state, resume/only-failed, propagate decisions, so sánh
context). tmp_path dùng làm cả --root (file thô giả) và state_path (data/ingest_state.json giả).
"""
import os

import pytest

from agent import memory as agent_memory
from agent import orchestrator
from agent.loop import ToolCallRecord, TurnResult


def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("x")
    return path


def make_result(records=None, status="OK", final_text="Đã ghi 1 record.", blocked_reason="", issues=None, tool_calls=None):
    return TurnResult(
        final_text=final_text,
        turn_count=2,
        total_input_tokens=100,
        total_output_tokens=50,
        tool_calls=tool_calls or [],
        stop_reason="end_turn",
        status=status,
        blocked_reason=blocked_reason,
        records=records or [],
        verify_retry_count=0,
        issues=issues or [],
    )


def make_realistic_result(record_id, content):
    """Kết quả mô phỏng thật hơn make_result mặc định — record có content dài + một tool_call
    parse_document trả về chunk gốc, để so sánh before/after context có ý nghĩa (make_result mặc
    định quá nhỏ nên overhead của manifest có thể lớn hơn cả dữ liệu gốc, không phản ánh trường
    hợp thực tế)."""
    return make_result(
        records=[{
            "record_id": record_id, "doc_type": "policy", "metadata": {},
            "content": content, "confidence": 0.9,
        }],
        tool_calls=[ToolCallRecord(
            turn=1, tool_name="parse_document", arguments={"path": "x"}, is_error=False,
            input_tokens=100, output_tokens=50, latency_seconds=0.1,
            output={"chunks": [{"text": content, "location": "p.1"}], "stats": {"total_pages": 1}},
        )],
    )


# =====================================================================================
# _build_manifest
# =====================================================================================

def test_build_manifest_doc_type_chiem_da_so_va_tom_tat_2_dong():
    records = [
        {"record_id": "r1", "doc_type": "price_list", "metadata": {}},
        {"record_id": "r2", "doc_type": "price_list", "metadata": {}},
        {"record_id": "r3", "doc_type": "promotion", "metadata": {}},
    ]
    result = make_result(records=records, final_text="Đã ghi 3 record.\nChi tiết thêm không cần thiết.")
    manifest = orchestrator._build_manifest("data/raw/xlsx/a.xlsx", "xlsx", result)

    assert manifest["file_id"] == "data/raw/xlsx/a.xlsx"
    assert manifest["source_type"] == "xlsx"
    assert manifest["doc_type"] == "price_list"
    assert manifest["record_count"] == 3
    assert manifest["status"] == "OK"
    assert "3 record" in manifest["summary"].splitlines()[0]
    assert manifest["summary"].splitlines()[1] == "Đã ghi 3 record."
    assert manifest["output_paths"] == [
        os.path.join("data", "output", "r1.json"),
        os.path.join("data", "output", "r2.json"),
        os.path.join("data", "output", "r3.json"),
    ]


def test_build_manifest_khong_record_tra_ve_unknown():
    result = make_result(records=[])
    manifest = orchestrator._build_manifest("data/raw/pdf/b.pdf", "pdf", result)
    assert manifest["doc_type"] == "unknown"
    assert manifest["record_count"] == 0
    assert manifest["output_paths"] == []


# =====================================================================================
# decisions
# =====================================================================================

def test_infer_store_code_convention_tu_record_dau_tien_co_gia_tri():
    records = [
        {"metadata": {}},
        {"metadata": {"store_code": "HN-001"}},
        {"metadata": {"store_code": "HCM-002"}},
    ]
    convention = orchestrator._infer_store_code_convention(records)
    assert "HN-001" in convention


def test_infer_store_code_convention_khong_co_tra_ve_none():
    assert orchestrator._infer_store_code_convention([{"metadata": {}}]) is None


# =====================================================================================
# run_orchestrator — điều phối, state, resume, only-failed
# =====================================================================================

@pytest.fixture
def fake_subagent(monkeypatch):
    """Thay _run_subagent_for_file bằng bản giả tra theo file_path, ghi lại mọi lệnh gọi
    (kèm decisions nhận được) để test propagate quy ước."""
    calls = []

    def fake(file_path, model, max_turns, max_verify_retries, decisions):
        calls.append({"file_path": file_path, "decisions": dict(decisions)})
        canned = fake.results[file_path]
        return canned["source_type"], canned["result"]

    fake.results = {}
    fake.calls = calls
    monkeypatch.setattr(orchestrator, "_run_subagent_for_file", fake)
    return fake


def test_run_orchestrator_full_run_ghi_state_va_manifest(tmp_path, fake_subagent):
    root = str(tmp_path / "raw")
    ok_file = touch(os.path.join(root, "xlsx", "ok.xlsx"))
    blocked_file = touch(os.path.join(root, "pdf", "blocked.pdf"))
    state_path = str(tmp_path / "ingest_state.json")

    long_content = "Chính sách đổi trả hàng áp dụng cho toàn bộ cửa hàng. " * 20
    fake_subagent.results = {
        ok_file: {"source_type": "xlsx", "result": make_realistic_result("r1", long_content)},
        blocked_file: {"source_type": "pdf", "result": make_result(status="BLOCKED", blocked_reason="verifier fail", issues=[{"check": "routing", "message": "..."}])},
    }

    entries, context_stats = orchestrator.run_orchestrator(root=root, workers=2, state_path=state_path)

    assert {e["file_id"] for e in entries} == {ok_file, blocked_file}
    ok_entry = next(e for e in entries if e["file_id"] == ok_file)
    blocked_entry = next(e for e in entries if e["file_id"] == blocked_file)
    assert ok_entry["status"] == "OK"
    assert blocked_entry["status"] == "BLOCKED"

    state = agent_memory.load_state(state_path)
    assert ok_file in state["files_done"]
    assert blocked_file in state["files_blocked"]
    assert state["files_blocked"][blocked_file]["reason"] == "verifier fail"
    assert state["files_pending"] == []

    assert context_stats["after_split_tokens_approx"] < context_stats["before_split_tokens_approx"]
    assert context_stats["reduction_pct"] > 0


def test_run_orchestrator_error_status_dung_final_text_lam_reason_khi_blocked_reason_rong(tmp_path, fake_subagent):
    root = str(tmp_path / "raw")
    error_file = touch(os.path.join(root, "pdf", "error.pdf"))
    state_path = str(tmp_path / "ingest_state.json")

    fake_subagent.results = {
        error_file: {
            "source_type": "pdf",
            "result": make_result(status="ERROR", final_text="Dừng xử lý do lỗi invariant tầng code: bug ở parser."),
        },
    }

    orchestrator.run_orchestrator(root=root, workers=1, state_path=state_path)

    state = agent_memory.load_state(state_path)
    assert state["files_blocked"][error_file]["reason"] == "Dừng xử lý do lỗi invariant tầng code: bug ở parser."


def test_run_orchestrator_resume_bo_qua_file_da_done(tmp_path, fake_subagent):
    root = str(tmp_path / "raw")
    done_file = touch(os.path.join(root, "xlsx", "done.xlsx"))
    new_file = touch(os.path.join(root, "xlsx", "new.xlsx"))
    state_path = str(tmp_path / "ingest_state.json")

    state = agent_memory.new_state()
    agent_memory.mark_done(state, done_file, {"file_id": done_file, "status": "OK"})
    agent_memory.save_state(state, state_path)

    fake_subagent.results = {
        new_file: {"source_type": "xlsx", "result": make_result(records=[{"record_id": "r1", "doc_type": "policy", "metadata": {}}])},
    }

    entries, _ = orchestrator.run_orchestrator(root=root, workers=2, resume=True, state_path=state_path)

    assert [e["file_id"] for e in entries] == [new_file]
    assert len(fake_subagent.calls) == 1
    assert fake_subagent.calls[0]["file_path"] == new_file


def test_run_orchestrator_only_failed_chi_chay_lai_file_blocked(tmp_path, fake_subagent):
    root = str(tmp_path / "raw")
    ok_file = touch(os.path.join(root, "xlsx", "ok.xlsx"))
    blocked_file = touch(os.path.join(root, "pdf", "blocked.pdf"))
    state_path = str(tmp_path / "ingest_state.json")

    state = agent_memory.new_state()
    agent_memory.mark_done(state, ok_file, {"file_id": ok_file, "status": "OK"})
    agent_memory.mark_blocked(state, blocked_file, "verifier fail")
    agent_memory.save_state(state, state_path)

    fake_subagent.results = {
        blocked_file: {"source_type": "pdf", "result": make_result(records=[{"record_id": "r2", "doc_type": "policy", "metadata": {}}])},
    }

    entries, _ = orchestrator.run_orchestrator(root=root, workers=2, only_failed=True, state_path=state_path)

    assert [e["file_id"] for e in entries] == [blocked_file]
    new_state = agent_memory.load_state(state_path)
    assert blocked_file in new_state["files_done"]
    assert blocked_file not in new_state["files_blocked"]


def test_run_orchestrator_propagate_decision_cho_file_sau(tmp_path, fake_subagent):
    root = str(tmp_path / "raw")
    first_file = touch(os.path.join(root, "xlsx", "1_first.xlsx"))
    second_file = touch(os.path.join(root, "xlsx", "2_second.xlsx"))
    state_path = str(tmp_path / "ingest_state.json")

    fake_subagent.results = {
        first_file: {
            "source_type": "xlsx",
            "result": make_result(records=[{"record_id": "r1", "doc_type": "policy", "metadata": {"store_code": "HN-001"}}]),
        },
        second_file: {
            "source_type": "xlsx",
            "result": make_result(records=[{"record_id": "r2", "doc_type": "policy", "metadata": {}}]),
        },
    }

    # workers=1 để đảm bảo thứ tự xử lý tuần tự first -> second, propagate quy ước đúng như mô tả.
    orchestrator.run_orchestrator(root=root, workers=1, state_path=state_path)

    call_by_file = {c["file_path"]: c for c in fake_subagent.calls}
    assert call_by_file[first_file]["decisions"] == {}
    assert "store_code_format" in call_by_file[second_file]["decisions"]

    state = agent_memory.load_state(state_path)
    assert "store_code_format" in state["decisions"]


def test_main_resume_va_only_failed_khong_dung_ca_hai(tmp_path):
    assert orchestrator.main(["--root", str(tmp_path), "--resume", "--only-failed"]) == 2
