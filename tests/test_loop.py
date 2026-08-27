"""Pytest cho agent/loop.py — tập trung vào 2 phần mới: prompt caching (layout system + boundary
"di động" trên messages) và compaction (nén lịch sử hội thoại cũ).

Không gọi Anthropic API thật: dùng FakeClient/FakeResponse tự dựng, mô phỏng đúng các thuộc tính
agent/loop.py đọc trên response (usage.*, content[].type/.text/.name/.input/.id, stop_reason).
Phần _compact_messages/_approx_tokens/_split_turn_groups là hàm thuần nên test trực tiếp không cần
qua FakeClient, theo đúng phong cách tests/test_orchestrator.py (gọi thẳng hàm private của module).
"""
import copy
from types import SimpleNamespace

from agent.loop import (
    COMPACT_KEEP_RECENT_TURNS,
    AgentLoop,
    _approx_tokens,
    _compact_messages,
    _extract_verbatim,
    _split_turn_groups,
)

FILE = "data/raw/xlsx/fake.xlsx"


# =====================================================================================
# FAKE CLIENT — mô phỏng client.messages.create, không gọi API thật
# =====================================================================================

def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_use_block(name, input_, id_="tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=input_, id=id_)


def fake_response(content, stop_reason, input_tokens=100, output_tokens=50, cache_created=0, cache_read=0):
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens,
            cache_creation_input_tokens=cache_created, cache_read_input_tokens=cache_read,
        ),
    )


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []  # deepcopy các kwargs mỗi lần create() được gọi, để assert sau

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if not self._responses:
            raise AssertionError("FakeMessages hết response đã script — kịch bản test thiếu 1 lượt.")
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def noop_ok(**kwargs):
    return {"data": "SUCCESS_CHUNK_DATA_" * 50}


def noop_err(**kwargs):
    raise Exception("toolerr-marker-abc")


def noop_step(**kwargs):
    return {"ok": True}


FAKE_TOOLS = {
    "noop_ok": {"function": noop_ok, "schema": {"description": "d", "input_schema": {"type": "object", "properties": {}}}},
    "noop_err": {"function": noop_err, "schema": {"description": "d", "input_schema": {"type": "object", "properties": {}}}},
    "noop_step": {"function": noop_step, "schema": {"description": "d", "input_schema": {"type": "object", "properties": {}}}},
}


def make_loop(responses, **kwargs):
    client = FakeClient(responses)
    loop = AgentLoop(
        system_prompt="SYS", tools=FAKE_TOOLS, client=client, enable_verifier=False, **kwargs,
    )
    return loop, client


# =====================================================================================
# _build_system_blocks — layout tĩnh -> động + cache_control ở khối cuối
# =====================================================================================

def test_build_system_blocks_thu_tu_va_cache_control_o_khoi_cuoi():
    loop, _ = make_loop([])
    blocks = loop._build_system_blocks()
    assert [b["text"] for b in blocks] == [loop.system_prompt, loop.extraction_guideline, loop.few_shot_examples]
    assert "cache_control" not in blocks[0]
    assert "cache_control" not in blocks[1]
    assert blocks[2]["cache_control"] == {"type": "ephemeral"}


def test_break_cache_chen_timestamp_thay_doi_moi_lan_goi():
    loop, _ = make_loop([], break_cache=True)
    b1 = loop._build_system_blocks()[0]["text"]
    b2 = loop._build_system_blocks()[0]["text"]
    assert "--break-cache" in b1 and "--break-cache" in b2
    assert b1 != b2  # timestamp khác nhau mỗi lần gọi, không phải cố định lúc khởi tạo
    assert loop.system_prompt in b1  # nội dung gốc vẫn còn nguyên, chỉ chèn thêm


def test_khong_break_cache_thi_system_prompt_khong_doi():
    loop, _ = make_loop([])
    b1 = loop._build_system_blocks()[0]["text"]
    b2 = loop._build_system_blocks()[0]["text"]
    assert b1 == b2 == loop.system_prompt


# =====================================================================================
# _set_cache_boundary — breakpoint "di động" trên message cuối của `messages`
# =====================================================================================

def test_set_cache_boundary_gan_len_message_user_cuoi_va_go_marker_cu():
    loop, _ = make_loop([])
    loop._cache_marked_block = None
    messages = [{"role": "user", "content": "hello"}]
    loop._set_cache_boundary(messages)
    assert messages[0]["content"] == [{"type": "text", "text": "hello", "cache_control": {"type": "ephemeral"}}]

    messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "y"}]})
    loop._set_cache_boundary(messages)
    # marker cũ (trên message đầu) đã bị gỡ, chỉ còn marker mới trên message cuối
    assert "cache_control" not in messages[0]["content"][0]
    assert messages[1]["content"][0]["cache_control"] == {"type": "ephemeral"}


# =====================================================================================
# Ghi log cache usage mỗi lượt + request gửi đúng layout (integration qua FakeClient)
# =====================================================================================

def test_run_gui_request_dung_layout_va_log_cache_usage(caplog):
    responses = [fake_response([text_block("xong")], "end_turn", cache_created=120, cache_read=340)]
    loop, client = make_loop(responses)

    with caplog.at_level("INFO"):
        result = loop.run("làm việc đi", FILE)

    assert result.status == "OK"
    call = client.messages.calls[0]
    assert [b["text"] for b in call["system"]] == [loop.system_prompt, loop.extraction_guideline, loop.few_shot_examples]
    assert call["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert call["messages"][0]["content"][-1]["cache_control"] == {"type": "ephemeral"}

    assert any("cache: creation=120 read=340" in r.message for r in caplog.records)


# =====================================================================================
# _split_turn_groups / _approx_tokens — hàm thuần
# =====================================================================================

def test_split_turn_groups_gop_tung_cap_assistant_user():
    messages = [
        {"role": "user", "content": "instruction"},
        {"role": "assistant", "content": []},
        {"role": "user", "content": []},
        {"role": "assistant", "content": []},
        {"role": "user", "content": []},
    ]
    groups = _split_turn_groups(messages)
    assert len(groups) == 2
    assert groups[0] == messages[1:3]
    assert groups[1] == messages[3:5]


def test_approx_tokens_tang_theo_do_dai_noi_dung():
    short = [{"role": "user", "content": "a"}]
    long = [{"role": "user", "content": "a" * 10_000}]
    assert _approx_tokens(long) > _approx_tokens(short)


# =====================================================================================
# _extract_verbatim — giữ đúng phần bắt buộc, bỏ tool_result thành công
# =====================================================================================

def test_extract_verbatim_giu_text_agent_va_loi_tool_bo_ket_qua_thanh_cong():
    group = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Quyết định: dùng định dạng HN-999."},
                {"type": "tool_use", "name": "parse_document", "input": {"path": "x"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "1", "content": "SUCCESS_LONG_DATA", "is_error": False},
                {"type": "tool_result", "tool_use_id": "2", "content": "toolerr-marker-abc", "is_error": True},
            ],
        },
    ]
    verbatim, trace = _extract_verbatim(group)
    joined = "\n".join(verbatim)
    assert "Quyết định: dùng định dạng HN-999." in joined
    assert "toolerr-marker-abc" in joined
    assert "SUCCESS_LONG_DATA" not in joined
    assert any("parse_document" in t for t in trace)


def test_extract_verbatim_giu_tin_nhan_verifier_dang_string():
    group = [
        {"role": "assistant", "content": []},
        {"role": "user", "content": "Tự kiểm tra (verifier) phát hiện vấn đề: thiếu sheet B."},
    ]
    verbatim, _ = _extract_verbatim(group)
    assert any("thiếu sheet B" in v for v in verbatim)


# =====================================================================================
# _compact_messages — không đủ lượt thì bỏ qua; đủ thì nén đúng phần cũ
# =====================================================================================

def test_compact_messages_khong_du_luot_thi_khong_doi():
    messages = [{"role": "user", "content": "instr"}] + [
        {"role": "assistant", "content": []}, {"role": "user", "content": []},
    ]
    new_messages, merged = _compact_messages(messages, keep_recent=COMPACT_KEEP_RECENT_TURNS)
    assert merged == 0
    assert new_messages is messages


def test_compact_messages_nen_dung_phan_cu_giu_phan_gan_nhat():
    messages = [
        {"role": "user", "content": "instr"},
        {"role": "assistant", "content": [{"type": "text", "text": "quyết định lượt 1"}]},
        {"role": "user", "content": [{"type": "tool_result", "content": "big-old-data", "is_error": False}]},
        {"role": "assistant", "content": [{"type": "text", "text": "quyết định lượt 2 gần nhất"}]},
        {"role": "user", "content": [{"type": "tool_result", "content": "recent-data", "is_error": False}]},
    ]
    new_messages, merged = _compact_messages(messages, keep_recent=1)
    assert merged == 1
    assert new_messages[0] == messages[0]
    assert "quyết định lượt 1" in new_messages[1]["content"]
    assert "big-old-data" not in new_messages[1]["content"]
    # lượt gần nhất giữ nguyên văn, không bị đưa vào summary
    assert new_messages[2:] == messages[3:]


# =====================================================================================
# Compaction end-to-end qua AgentLoop.run — ngưỡng thấp để trigger chắc chắn, không phụ
# thuộc dữ liệu thật lớn
# =====================================================================================

def test_run_compact_khi_vuot_nguong_va_log_ro_token_truoc_sau(caplog):
    responses = [
        fake_response(
            [
                text_block("Quyết định ban đầu: ưu tiên sheet A trước."),
                tool_use_block("noop_err", {}, id_="tu_err"),
                tool_use_block("noop_ok", {}, id_="tu_ok"),
            ],
            "tool_use",
        ),
        fake_response([tool_use_block("noop_step", {}, id_="tu_step")], "tool_use"),
        fake_response([text_block("Đã ghi xong.")], "end_turn"),
    ]
    loop, client = make_loop(
        responses, compact_turn_threshold=1, compact_token_threshold=10**9, compact_keep_recent_turns=1,
    )

    with caplog.at_level("WARNING"):
        result = loop.run("làm việc đi", FILE)

    assert result.status == "OK"
    assert len(result.compaction_events) == 1
    event = result.compaction_events[0]
    assert event["merged_turns"] == 1
    assert event["tokens_before"] > event["tokens_after"]
    assert any("compaction" in r.message for r in caplog.records)

    # request của turn 3 (sau khi compact) không còn chứa tool output lớn của turn 1, nhưng vẫn
    # còn nguyên văn lỗi tool + quyết định của turn 1 trong message tóm tắt.
    turn3_messages = client.messages.calls[2]["messages"]
    dumped = str(turn3_messages)
    assert "SUCCESS_CHUNK_DATA_" not in dumped
    assert "toolerr-marker-abc" in dumped
    assert "Quyết định ban đầu: ưu tiên sheet A trước." in dumped


def test_run_khong_vuot_nguong_thi_khong_compact():
    responses = [
        fake_response([tool_use_block("noop_step", {}, id_="tu_1")], "tool_use"),
        fake_response([text_block("xong")], "end_turn"),
    ]
    loop, _ = make_loop(responses, compact_turn_threshold=100, compact_token_threshold=10**9)
    result = loop.run("làm việc đi", FILE)
    assert result.compaction_events == []
