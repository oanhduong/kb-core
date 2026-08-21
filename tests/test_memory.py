"""Pytest cho agent/memory.py.

Toàn bộ test dùng tmp_path làm state_path — không đụng data/ingest_state.json thật.
"""
import json
import os

from agent import memory


def state_path(tmp_path):
    return os.path.join(tmp_path, "ingest_state.json")


# =====================================================================================
# load_state
# =====================================================================================

def test_load_state_file_khong_ton_tai_tra_ve_state_rong(tmp_path):
    state = memory.load_state(state_path(tmp_path))
    assert state == {"files_done": {}, "files_blocked": {}, "files_pending": [], "decisions": {}}


def test_load_state_bo_qua_key_la(tmp_path):
    path = state_path(tmp_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"files_done": {"a.pdf": {"status": "OK"}}, "key_khong_ro": 123}, f)
    state = memory.load_state(path)
    assert state["files_done"] == {"a.pdf": {"status": "OK"}}
    assert "key_khong_ro" not in state


# =====================================================================================
# save_state / round-trip
# =====================================================================================

def test_save_state_roundtrip(tmp_path):
    path = state_path(tmp_path)
    state = memory.new_state()
    state["decisions"]["store_code_format"] = "dạng 'AA-999'"
    memory.save_state(state, path)

    assert not os.path.exists(f"{path}.tmp")
    loaded = memory.load_state(path)
    assert loaded == state


# =====================================================================================
# mark_done / mark_blocked / is_done
# =====================================================================================

def test_mark_done_ghi_manifest_va_xoa_khoi_pending_blocked():
    state = memory.new_state()
    memory.set_pending(state, ["a.pdf", "b.pdf"])
    memory.mark_blocked(state, "a.pdf", "lỗi verifier")

    manifest = {"file_id": "a.pdf", "record_count": 3, "status": "OK"}
    memory.mark_done(state, "a.pdf", manifest)

    assert state["files_done"]["a.pdf"] == manifest
    assert "a.pdf" not in state["files_blocked"]
    assert "a.pdf" not in state["files_pending"]
    assert memory.is_done(state, "a.pdf")
    assert not memory.is_done(state, "b.pdf")


def test_mark_blocked_ghi_reason_va_issues():
    state = memory.new_state()
    memory.set_pending(state, ["a.pdf"])
    memory.mark_blocked(state, "a.pdf", "verifier fail", issues=[{"check": "routing", "message": "..."}])

    assert state["files_blocked"]["a.pdf"]["reason"] == "verifier fail"
    assert state["files_blocked"]["a.pdf"]["issues"] == [{"check": "routing", "message": "..."}]
    assert "a.pdf" not in state["files_pending"]
    assert not memory.is_done(state, "a.pdf")


def test_mark_blocked_khong_truyen_issues_mac_dinh_rong():
    state = memory.new_state()
    memory.mark_blocked(state, "a.pdf", "lỗi")
    assert state["files_blocked"]["a.pdf"]["issues"] == []


# =====================================================================================
# decisions
# =====================================================================================

def test_record_decision():
    state = memory.new_state()
    memory.record_decision(state, "store_code_format", "dạng 'AA-999'")
    assert state["decisions"]["store_code_format"] == "dạng 'AA-999'"
