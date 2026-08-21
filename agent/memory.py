"""Bộ nhớ ngoài (external memory) cho agent/orchestrator.py.

Trạng thái chạy ingest nằm trên đĩa (data/ingest_state.json), KHÔNG giữ trong lịch sử hội thoại
hay biến RAM sống suốt quá trình chạy của orchestrator:
- files_done: file_path -> manifest gọn (kết quả sub-agent trả về, xem agent/orchestrator.py).
- files_blocked: file_path -> {"reason": str, "issues": [...]}.
- files_pending: danh sách file còn lại của lần chạy gần nhất (để biết run trước dừng dở ở đâu
  nếu bị ngắt giữa chừng).
- decisions: quy ước đã chốt trong quá trình chạy (ví dụ cách đặt store_code) — orchestrator đọc
  lại và truyền cho sub-agent của các file sau, vì mỗi sub-agent có context sạch nên không tự nhớ
  được quy ước của file trước đó.

Orchestrator đọc file này ở đầu run thay vì giữ toàn bộ lịch sử; chạy lại với --resume sẽ bỏ qua
file đã có trong files_done.
"""
import json
import os

STATE_PATH = os.path.join("data", "ingest_state.json")


def new_state():
    return {"files_done": {}, "files_blocked": {}, "files_pending": [], "decisions": {}}


def load_state(path=STATE_PATH):
    if not os.path.isfile(path):
        return new_state()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    state = new_state()
    state.update({k: v for k, v in data.items() if k in state})
    return state


def save_state(state, path=STATE_PATH):
    """Ghi qua file tạm rồi os.replace — tránh state.json bị hỏng dở nếu process bị ngắt giữa
    chừng lúc ghi (orchestrator gọi save_state sau MỖI file, không chỉ cuối run)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def mark_done(state, file_path, manifest):
    state["files_done"][file_path] = manifest
    state["files_blocked"].pop(file_path, None)
    if file_path in state["files_pending"]:
        state["files_pending"].remove(file_path)


def mark_blocked(state, file_path, reason, issues=None):
    state["files_blocked"][file_path] = {"reason": reason, "issues": issues or []}
    if file_path in state["files_pending"]:
        state["files_pending"].remove(file_path)


def set_pending(state, file_paths):
    state["files_pending"] = list(file_paths)


def record_decision(state, key, value):
    state["decisions"][key] = value


def is_done(state, file_path):
    return file_path in state["files_done"]
