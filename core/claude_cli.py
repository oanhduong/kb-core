"""Client tương thích anthropic.Anthropic (.messages.create / .messages.stream) chạy qua Claude Code CLI
đã đăng nhập bằng subscription (`claude -p`) — cho máy dev không có API credit (LLM_PROVIDER=claude_cli).

- Mỗi lượt gọi = 1 tiến trình `claude -p` không tool/settings/MCP, chạy ở thư mục tạm (không đọc CLAUDE.md
  của repo). ANTHROPIC_API_KEY bị gỡ khỏi env để CLI dùng đăng nhập OAuth thay vì key.
- CLI chỉ nhận lượt user -> hội thoại nhiều lượt + tool_use/tool_result được dựng thành 1 user message
  dạng transcript; block ảnh/PDF giữ nguyên.
- Tool use giả lập bằng prompt: model trả {"tool_calls": [...]} -> block tool_use (stop_reason=tool_use).
  Model hay viết tiếp cả [tool_result] tự bịa sau JSON, hoặc tự diễn lượt USER mới -> đọc stream, JSON
  tool_calls vừa đủ / gặp header lượt là kill tiến trình và cắt text tại đó.
- temperature + max_tokens bị bỏ qua (CLI tự viết tiếp khi chạm giới hạn output — text được ghép từ mọi
  message, `result` của CLI chỉ chứa message cuối). Usage là token thật nhưng không tính tiền
  (subscription=True -> LLMClient ghi cost 0).
- KHÔNG dùng cho production: subscription dành cho cá nhân dùng tương tác, không phải backend phục vụ người khác.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from types import SimpleNamespace
from typing import Any, Iterator

_STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
_RETRYABLE_RE = re.compile(r"overloaded|rate.?limit|timed? ?out|5\d\d|529|ECONNRESET|network|socket", re.IGNORECASE)
_TOOL_CALLS_RE = re.compile(r'\{\s*"tool_calls"\s*:')
# model hay "diễn" tiếp transcript: tự viết lượt USER/ASSISTANT mới sau câu trả lời -> cắt tại đó
_TURN_RE = re.compile(r"\s*=== (?:USER|ASSISTANT) ===")

_TOOL_PROTOCOL = """\
To call one or more tools, reply with ONLY a JSON object and nothing else:
{"tool_calls": [{"name": "<tool name>", "input": {...}}]}
Then STOP. Never write tool results yourself: the system runs the tools and sends the real results back
in the next USER turn as [tool_result ...] blocks.
When you have enough information, reply with the final answer as normal text (no JSON wrapper)."""


class ClaudeCLIError(Exception):
    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def _as_dict(b: Any) -> dict[str, Any]:
    if isinstance(b, dict):
        return b
    if hasattr(b, "model_dump"):
        return b.model_dump()
    return dict(vars(b))


def _text(t: str) -> dict[str, Any]:
    return {"type": "text", "text": t}


def system_text(system: Any) -> str:
    if not system:
        return ""
    if isinstance(system, str):
        return system
    return "\n\n".join(b.get("text", "") for b in map(_as_dict, system) if b.get("type") == "text")


def tools_prompt(tools: list[dict[str, Any]]) -> str:
    parts = ["# Tools", "You can call these tools (input must match input_schema):"]
    for t in map(_as_dict, tools):
        parts.append(f"## {t['name']}\n{t.get('description', '')}\n"
                     f"input_schema: {json.dumps(t.get('input_schema', {}), ensure_ascii=False)}")
    parts.append(_TOOL_PROTOCOL)
    return "\n\n".join(parts)


def _blocks(content: Any) -> list[dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [_text(content)]
    out: list[dict[str, Any]] = []
    for b in map(_as_dict, content):
        t = b.get("type")
        if t == "text":
            out.append(_text(b.get("text", "")))
        elif t in ("image", "document"):
            out.append({k: v for k, v in b.items() if k != "cache_control"})
        elif t == "tool_use":
            out.append(_text(f"[tool_call id={b.get('id')} name={b.get('name')}] "
                             f"{json.dumps(b.get('input') or {}, ensure_ascii=False)}"))
        elif t == "tool_result":
            flag = " is_error=true" if b.get("is_error") else ""
            out.append(_text(f"[tool_result id={b.get('tool_use_id')}{flag}]"))
            out.extend(_blocks(b.get("content")))
    return out


def _merge_text(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for b in blocks:
        if b["type"] == "text" and out and out[-1]["type"] == "text":
            out[-1] = _text(out[-1]["text"] + "\n" + b["text"])
        else:
            out.append(b)
    return out


def build_user_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gộp hội thoại thành nội dung 1 lượt user (CLI không nhận lượt assistant)."""
    if len(messages) == 1 and messages[0]["role"] == "user":
        return _merge_text(_blocks(messages[0]["content"]))
    blocks = [_text("Conversation so far is below. Write ONLY the assistant's next turn — never write "
                    "=== USER === or === ASSISTANT === headers or invent further turns.")]
    for m in messages:
        blocks.append(_text(f"\n=== {m['role'].upper()} ==="))
        blocks.extend(_blocks(m["content"]))
    return _merge_text(blocks)


def complete_tool_call(text: str) -> str | None:
    """text tới hết JSON {"tool_calls": ...} đầu tiên nếu JSON đó đã đủ, ngược lại None."""
    m = _TOOL_CALLS_RE.search(text)
    if not m:
        return None
    try:
        _, end = json.JSONDecoder().raw_decode(text[m.start():])
    except json.JSONDecodeError:
        return None
    return text[:m.start() + end]


def parse_tool_calls(text: str, tool_names: set[str]) -> tuple[str, list[dict[str, Any]]]:
    """-> (văn bản trước JSON, [{name, input}]); không phải lời gọi tool hợp lệ -> (text, [])."""
    m = _TOOL_CALLS_RE.search(text)
    if not m:
        return text, []
    try:
        data, _ = json.JSONDecoder().raw_decode(text[m.start():])
    except json.JSONDecodeError:
        return text, []
    calls = [c for c in data.get("tool_calls") or [] if isinstance(c, dict) and c.get("name") in tool_names]
    if not calls:
        return text, []
    prefix = re.sub(r"```[a-zA-Z]*\s*$", "", text[:m.start()]).strip()
    return prefix, [{"name": c["name"], "input": c.get("input") or {}} for c in calls]


class ClaudeCLIClient:
    subscription = True

    def __init__(self, binary: str | None = None, timeout: float = 180.0) -> None:
        self.binary = binary or shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
        self.timeout = timeout
        self.messages = _Messages(self)

    def _events(self, *, model: str, messages: list[dict[str, Any]], system: Any = None,
                tools: list[dict[str, Any]] | None = None, **_: Any) -> Iterator[str]:
        """Yield các đoạn text khi model viết; return Message giống SDK qua StopIteration.value."""
        sys_prompt = system_text(system)
        if tools:
            sys_prompt = (sys_prompt + "\n\n" + tools_prompt(tools)).strip()
        payload = json.dumps({"type": "user", "message": {"role": "user", "content": build_user_content(messages)}},
                             ensure_ascii=False) + "\n"
        env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}
        env["MAX_THINKING_TOKENS"] = "0"
        with tempfile.TemporaryDirectory(prefix="claude-cli-") as cwd:
            sys_file = os.path.join(cwd, "system.md")
            with open(sys_file, "w", encoding="utf-8") as f:
                f.write(sys_prompt or "You are a helpful assistant.")
            cmd = [self.binary, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                   "--tools", "", "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence",
                   "--disable-slash-commands", "--include-partial-messages", "--model", model,
                   "--system-prompt-file", sys_file]
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding="utf-8", env=env, cwd=cwd)
            except OSError as e:
                raise ClaudeCLIError(f"Không chạy được Claude CLI ({self.binary}): {e}") from e
            timed_out = threading.Event()

            def _kill() -> None:
                timed_out.set()
                proc.kill()

            timer = threading.Timer(self.timeout, _kill)
            timer.start()
            result: dict[str, Any] | None = None
            streamed, messages_text, stopped_text = "", [], None
            try:
                assert proc.stdin and proc.stdout and proc.stderr
                proc.stdin.write(payload)
                proc.stdin.close()
                for line in proc.stdout:
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if d.get("type") == "stream_event":
                        ev = d.get("event") or {}
                        delta = ev.get("delta") or {}
                        if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                            streamed += delta.get("text", "")
                            yield delta.get("text", "")
                            if (turn := _TURN_RE.search(streamed)) or (tools and complete_tool_call(streamed)):
                                stopped_text = streamed[:turn.start()] if turn else streamed
                                if tools:
                                    stopped_text = complete_tool_call(stopped_text) or stopped_text
                                proc.kill()  # phần sau chỉ là kết quả tool / lượt hội thoại model tự bịa
                                break
                    elif d.get("type") == "assistant":
                        content = (d.get("message") or {}).get("content") or []
                        messages_text.append("".join(b.get("text", "") for b in content if b.get("type") == "text"))
                    elif d.get("type") == "result":
                        result = d
                stderr = proc.stderr.read()
                proc.wait()
            finally:
                timer.cancel()
                if proc.poll() is None:
                    proc.kill()
        if timed_out.is_set():
            raise ClaudeCLIError(f"Claude CLI quá {self.timeout:.0f}s", retryable=True)
        if stopped_text is not None:
            text, result = stopped_text, {}
        elif result is None:
            msg = (stderr or "").strip()[-500:] or f"exit code {proc.returncode}"
            raise ClaudeCLIError(f"Claude CLI không trả kết quả: {msg}", retryable=bool(_RETRYABLE_RE.search(msg)))
        else:
            text = "".join(messages_text) or result.get("result") or ""
            if turn := _TURN_RE.search(text):
                text = text[:turn.start()]
        if os.environ.get("CLAUDE_CLI_DEBUG_DIR"):  # dev: ghi input/output thô để soi model làm sai gì
            dump = os.path.join(os.environ["CLAUDE_CLI_DEBUG_DIR"], f"{uuid.uuid4().hex[:12]}.json")
            with open(dump, "w", encoding="utf-8") as f:
                json.dump({"model": model, "system": sys_prompt, "payload": json.loads(payload), "result": text},
                          f, ensure_ascii=False, indent=1)
        if result.get("is_error"):
            msg = text or str(result.get("subtype"))
            raise ClaudeCLIError(f"Claude CLI lỗi: {msg}", retryable=bool(_RETRYABLE_RE.search(msg)))
        return self._message(model, text, result, tools)

    @staticmethod
    def _message(model: str, text: str, result: dict[str, Any], tools: list[dict[str, Any]] | None) -> Any:
        u = result.get("usage") or {}
        usage = SimpleNamespace(input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
                                cache_read_input_tokens=u.get("cache_read_input_tokens", 0),
                                cache_creation_input_tokens=u.get("cache_creation_input_tokens", 0))
        calls: list[dict[str, Any]] = []
        if tools:
            text, calls = parse_tool_calls(text, {_as_dict(t)["name"] for t in tools})
        content: list[Any] = [SimpleNamespace(type="text", text=text)] if text else []
        content += [SimpleNamespace(type="tool_use", id=f"toolu_cli_{uuid.uuid4().hex[:20]}", name=c["name"], input=c["input"])
                    for c in calls]
        return SimpleNamespace(role="assistant", model=model, content=content, usage=usage,
                               stop_reason="tool_use" if calls else (result.get("stop_reason") or "end_turn"))


class _Messages:
    def __init__(self, client: ClaudeCLIClient) -> None:
        self._client = client

    def create(self, **kwargs: Any) -> Any:
        gen = self._client._events(**kwargs)
        while True:
            try:
                next(gen)
            except StopIteration as stop:
                return stop.value

    def stream(self, **kwargs: Any) -> "_Stream":
        return _Stream(self._client, kwargs)


class _Stream:
    """Như MessageStream của SDK: text_stream + get_final_message(). Khi có tools hoặc hội thoại nhiều lượt,
    phần text từ ký tự '{', '`' hoặc '=' đầu tiên được giữ lại tới cuối — nếu đó là lời gọi tool hay lượt
    hội thoại tự bịa thì không lộ ra cho user (message cuối đã được cắt sạch)."""

    def __init__(self, client: ClaudeCLIClient, kwargs: dict[str, Any]) -> None:
        self._gen = client._events(**kwargs)
        self._hold = bool(kwargs.get("tools")) or len(kwargs.get("messages") or []) > 1
        self._final: Any = None

    def __enter__(self) -> "_Stream":
        return self

    def __exit__(self, *exc: Any) -> None:
        self._gen.close()

    @property
    def text_stream(self) -> Iterator[str]:
        sent, holding = "", False
        while True:
            try:
                chunk = next(self._gen)
            except StopIteration as stop:
                self._final = stop.value
                break
            if holding:
                continue
            cut = min((i for i in map(chunk.find, "{`=") if i >= 0), default=-1) if self._hold else -1
            if cut >= 0:
                chunk, holding = chunk[:cut], True
            if chunk:
                sent += chunk
                yield chunk
        # phần còn lại (bị giữ, hoặc CLI không gửi partial) lấy từ message cuối — lời gọi tool đã bị tách khỏi text
        final_text = "".join(b.text for b in self._final.content if b.type == "text")
        if final_text.startswith(sent) and final_text[len(sent):]:
            yield final_text[len(sent):]

    def get_final_message(self) -> Any:
        if self._final is None:
            for _ in self.text_stream:
                pass
        return self._final
