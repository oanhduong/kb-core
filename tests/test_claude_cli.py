"""core.claude_cli — client tương thích anthropic chạy qua Claude Code CLI (dùng binary giả, không gọi CLI thật)."""
import json
import os
import stat
import sys

import pytest

from core.claude_cli import ClaudeCLIClient, ClaudeCLIError, build_user_content, parse_tool_calls

FAKE_CLI = """\
import json, os, sys
argv = sys.argv[1:]
req = json.loads(sys.stdin.readline())
with open(argv[argv.index("--system-prompt-file") + 1], encoding="utf-8") as f:
    system = f.read()
scenario = json.loads(os.environ["FAKE_SCENARIO"])
log = {"argv": argv, "system": system, "content": req["message"]["content"],
       "has_key": "ANTHROPIC_API_KEY" in os.environ}
with open(os.environ["FAKE_LOG"], "w", encoding="utf-8") as f:
    json.dump(log, f)
for piece in scenario["pieces"]:
    ev = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": piece}}
    print(json.dumps({"type": "stream_event", "event": ev}), flush=True)
for text in scenario.get("messages") or []:  # CLI tự viết tiếp -> nhiều message, result chỉ giữ message cuối
    print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}), flush=True)
final = (scenario.get("messages") or ["".join(scenario["pieces"])])[-1]
print(json.dumps({"type": "result", "is_error": scenario.get("is_error", False), "result": final,
                  "stop_reason": "end_turn", "usage": {"input_tokens": 11, "output_tokens": 7}}), flush=True)
"""


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    script = tmp_path / "claude"
    script.write_text(f"#!{sys.executable}\n{FAKE_CLI}", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "log.json"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")

    def run(pieces, is_error=False, messages=None):
        monkeypatch.setenv("FAKE_SCENARIO", json.dumps({"pieces": pieces, "is_error": is_error, "messages": messages}))
        return ClaudeCLIClient(binary=str(script), timeout=30), lambda: json.loads(log.read_text(encoding="utf-8"))

    return run


def test_build_user_content_flattens_turns_and_keeps_images():
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}, "cache_control": {"type": "ephemeral"}}
    content = build_user_content([
        {"role": "user", "content": [img, {"type": "text", "text": "Giá DT-018?"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "get_price", "input": {"sku": "DT-018"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "1.290.000đ"}]},
    ])
    assert [b["type"] for b in content] == ["text", "image", "text"]
    assert "cache_control" not in content[1]
    tail = content[2]["text"]
    assert "=== ASSISTANT ===" in tail and '[tool_call id=t1 name=get_price] {"sku": "DT-018"}' in tail
    assert "[tool_result id=t1]\n1.290.000đ" in tail


def test_single_user_message_is_passed_through():
    assert build_user_content([{"role": "user", "content": "xin chào"}]) == [{"type": "text", "text": "xin chào"}]


def test_parse_tool_calls():
    prefix, calls = parse_tool_calls('Để tra giá.\n```json\n{"tool_calls": [{"name": "get_price", "input": {"sku": "A"}}]}\n```',
                                     {"get_price"})
    assert prefix == "Để tra giá." and calls == [{"name": "get_price", "input": {"sku": "A"}}]
    assert parse_tool_calls('{"tool_calls": [{"name": "rm_rf"}]}', {"get_price"})[1] == []
    assert parse_tool_calls("Giá là {1.290.000đ}", {"get_price"}) == ("Giá là {1.290.000đ}", [])


def test_create_strips_api_key_and_returns_sdk_shape(fake_cli):
    client, log = fake_cli(["Hà Nội"])
    msg = client.messages.create(model="claude-haiku-4-5", max_tokens=64, system=[{"type": "text", "text": "Ngắn gọn."}],
                                 messages=[{"role": "user", "content": "Thủ đô?"}])
    assert [(b.type, b.text) for b in msg.content] == [("text", "Hà Nội")]
    assert (msg.usage.input_tokens, msg.usage.output_tokens, msg.stop_reason) == (11, 7, "end_turn")
    seen = log()
    assert seen["has_key"] is False and seen["system"] == "Ngắn gọn."
    assert seen["argv"][seen["argv"].index("--model") + 1] == "claude-haiku-4-5"
    assert seen["argv"][seen["argv"].index("--tools") + 1] == ""


def test_stream_with_tools_hides_tool_call_json(fake_cli):
    client, log = fake_cli(["Để tra giá. ", '{"tool_calls": [{"name": "get_price", ', '"input": {"sku": "A"}}]}'])
    tools = [{"name": "get_price", "description": "Tra giá", "input_schema": {"type": "object"}}]
    with client.messages.stream(model="m", max_tokens=100, messages=[{"role": "user", "content": "Giá A?"}], tools=tools) as s:
        streamed = "".join(s.text_stream)
        final = s.get_final_message()
    assert streamed == "Để tra giá. "
    assert final.stop_reason == "tool_use"
    assert [(b.type, getattr(b, "name", None), getattr(b, "input", None)) for b in final.content] == [
        ("text", None, None), ("tool_use", "get_price", {"sku": "A"})]
    assert "get_price" in log()["system"] and '"tool_calls"' in log()["system"]


def test_tool_call_stops_before_hallucinated_results(fake_cli):
    client, _ = fake_cli(['{"tool_calls": [{"name": "get_price", "input": {"sku": "A"}}]}', "\n\n[tool_result] {\"price\": 1}",
                          "\nGiá là 1đ [S3]"])
    tools = [{"name": "get_price", "input_schema": {"type": "object"}}]
    msg = client.messages.create(model="m", max_tokens=10, messages=[{"role": "user", "content": "?"}], tools=tools)
    assert msg.stop_reason == "tool_use"
    assert [(b.type, getattr(b, "input", None)) for b in msg.content] == [("tool_use", {"sku": "A"})]


def test_invented_user_turn_is_cut_and_never_streamed(fake_cli):
    client, _ = fake_cli(["Không có khuyến mãi riêng cho ĐT-018 [S1].", "\n\n=== USER ===\nTổng doanh số?",
                          '\n\n=== ASSISTANT ===\n{"tool_calls": [{"name": "list_tables", "input": {}}]}'])
    tools = [{"name": "list_tables", "input_schema": {"type": "object"}}]
    history = [{"role": "user", "content": "Loa ĐT-018 có khuyến mãi?"}, {"role": "assistant", "content": "..."},
               {"role": "user", "content": "[tool_result id=t1]"}]
    with client.messages.stream(model="m", max_tokens=100, messages=history, tools=tools) as s:
        streamed = "".join(s.text_stream)
        final = s.get_final_message()
    assert streamed.rstrip() == "Không có khuyến mãi riêng cho ĐT-018 [S1]." and "===" not in streamed
    assert final.stop_reason == "end_turn" and [b.type for b in final.content] == ["text"]
    assert final.content[0].text == "Không có khuyến mãi riêng cho ĐT-018 [S1]."


def test_continued_output_is_joined_not_just_last_message(fake_cli):
    client, _ = fake_cli([], messages=["| Ngành hàng | Thời hạn |\n| Điện máy | 30 ", "ngày |"])
    msg = client.messages.create(model="m", max_tokens=10, messages=[{"role": "user", "content": "?"}])
    assert msg.content[0].text == "| Ngành hàng | Thời hạn |\n| Điện máy | 30 ngày |"


def test_stream_with_tools_flushes_held_text_when_not_a_tool_call(fake_cli):
    client, _ = fake_cli(["Giá là ", "`1.290.000đ`", " nhé"])
    tools = [{"name": "get_price", "input_schema": {"type": "object"}}]
    with client.messages.stream(model="m", max_tokens=100, messages=[{"role": "user", "content": "?"}], tools=tools) as s:
        chunks = list(s.text_stream)
    assert chunks == ["Giá là ", "`1.290.000đ` nhé"]


def test_error_result_raises_and_llm_client_reports_unavailable(fake_cli):
    from core.llm import LLMClient, LLMUnavailable

    client, _ = fake_cli(["Not logged in · Please run /login"], is_error=True)
    with pytest.raises(ClaudeCLIError) as ei:
        client.messages.create(model="m", max_tokens=10, messages=[{"role": "user", "content": "?"}])
    assert ei.value.retryable is False
    calls = []
    llm = LLMClient(client=client, usage_sink=calls.append, sleep=lambda _s: None)
    with pytest.raises(LLMUnavailable):
        llm.complete("router", system=None, messages=[{"role": "user", "content": "?"}])
    assert len(calls) == 1 and calls[0].status == "error"


def test_llm_client_records_zero_cost_for_subscription(fake_cli):
    from core.llm import LLMClient

    client, _ = fake_cli(["ok"])
    calls = []
    res = LLMClient(client=client, usage_sink=calls.append).complete("router", system=None, messages=[{"role": "user", "content": "?"}])
    assert res.text == "ok" and calls[0].input_tokens == 11 and calls[0].cost_usd == 0.0
