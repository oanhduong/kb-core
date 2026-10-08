"""LLM client dùng chung (Anthropic SDK) cho mọi vai trò: answer, router, entity/graph extraction,
compaction, phân loại tài liệu.

- Model theo VAI TRÒ đọc từ config (models.<role>) — đổi model = đổi config, không sửa code.
- Timeout + retry (B9): lỗi tạm thời (timeout, mất kết nối, 429, 5xx/529 overloaded) retry tối đa
  llm.max_retries lần với exponential backoff + jitter, hết lượt -> LLMUnavailable (message rõ ràng,
  API đổi thành thông báo thân thiện, không bao giờ 500 trần).
- Streaming: chỉ retry khi lỗi xảy ra TRƯỚC token đầu tiên (đã stream ra cho user thì không thể
  "rút lại" — báo lỗi rõ ràng thay vì lặp nội dung).
- Mỗi lần gọi: tính cost USD từ usage thật (llm.pricing), ghi generation lên Langfuse (E1) và gửi
  sang usage_sink (mặc định ghi bảng llm_usage — nguồn cho alert chi phí ngày, E3).
- complete_json(): parse + validate pydantic, retry tối đa N lần khi model trả JSON sai (A2).
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from core.claude_cli import ClaudeCLIClient, ClaudeCLIError
from core.config import AppConfig, Settings, get_config, get_settings
from core.errors import UpstreamUnavailable
from core.i18n import L
from core.logging import get_log_context, log_event
from observability import tracing

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
UsageSink = Callable[["LLMCall"], None]


class LLMUnavailable(UpstreamUnavailable):
    code = "llm_unavailable"


class LLMOutputInvalid(Exception):
    """Model trả output không parse/validate được sau khi đã retry hết."""


@dataclass
class LLMCall:
    role: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    status: str = "ok"
    trace_id: str | None = None
    job_id: str | None = None


@dataclass
class LLMResult:
    text: str
    model: str
    stop_reason: str | None
    call: LLMCall


@dataclass
class StreamHandle:
    """Iterator các đoạn text; sau khi duyệt hết, .result có LLMResult đầy đủ (usage/cost)."""

    _gen: Iterator[str]
    result: LLMResult | None = None
    chunks: list[str] = field(default_factory=list)

    def __iter__(self) -> Iterator[str]:
        for piece in self._gen:
            self.chunks.append(piece)
            yield piece


def compute_cost(cfg: AppConfig, model: str, input_tokens: int, output_tokens: int,
                 cache_read: int = 0, cache_write: int = 0) -> float:
    price = cfg.price_for(model)
    if price is None:
        return 0.0
    cost = (input_tokens * price.input + output_tokens * price.output
            + cache_read * price.input * price.cache_read_multiplier
            + cache_write * price.input * price.cache_write_multiplier) / 1_000_000
    return round(cost, 6)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, ClaudeCLIError):
        return exc.retryable
    if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError, anthropic.RateLimitError,
                        anthropic.InternalServerError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code in (408, 409, 429) or exc.status_code >= 500
    return False


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json_object(text: str) -> Any:
    """Lấy JSON object đầu tiên trong text (model đôi khi bọc ```json ... ```)."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_OBJECT_RE.search(text)
        if not m:
            raise
        return json.loads(m.group(0))


class LLMClient:
    def __init__(self, cfg: AppConfig | None = None, settings: Settings | None = None,
                 client: Any = None, usage_sink: UsageSink | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.cfg = cfg or get_config()
        settings = settings or get_settings()
        if client is None and settings.llm_provider == "claude_cli":
            # CLI tốn thêm vài giây khởi động mỗi lượt -> nới timeout
            client = ClaudeCLIClient(binary=settings.claude_cli_path, timeout=self.cfg.llm.timeout_seconds * 3)
        self._client = client or anthropic.Anthropic(
            api_key=settings.anthropic_api_key, timeout=self.cfg.llm.timeout_seconds, max_retries=0,
        )
        # subscription (Claude CLI) không tính tiền theo token -> cost 0, không kích hoạt alert chi phí
        self._billed = not getattr(self._client, "subscription", False)
        self._usage_sink = usage_sink
        self._sleep = sleep

    def model_for(self, role: str) -> str:
        model = getattr(self.cfg.models, role, None)
        if not model:
            raise ValueError(f"Không có model cấu hình cho vai trò {role!r} (config.models)")
        return model

    def _backoff(self, attempt: int) -> float:
        base = self.cfg.llm.backoff_base_seconds * (2 ** attempt)
        return min(self.cfg.llm.backoff_max_seconds, base) * (0.75 + random.random() * 0.5)

    def _finish_call(self, role: str, model: str, usage: Any, latency_ms: int, status: str,
                     input_summary: Any, output: Any) -> LLMCall:
        ctx = get_log_context()
        in_tok = int(getattr(usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(usage, "output_tokens", 0) or 0)
        c_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        c_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        call = LLMCall(
            role=role, model=model, input_tokens=in_tok, output_tokens=out_tok, cache_read_tokens=c_read,
            cache_write_tokens=c_write, cost_usd=compute_cost(self.cfg, model, in_tok, out_tok, c_read, c_write) if self._billed else 0.0,
            latency_ms=latency_ms, status=status, trace_id=ctx.get("trace_id"), job_id=ctx.get("job_id"),
        )
        tracing.record_generation(
            name=f"llm.{role}", model=model, input=input_summary, output=output,
            input_tokens=in_tok + c_read + c_write, output_tokens=out_tok, cost_usd=call.cost_usd,
            latency_ms=latency_ms, metadata={"cache_read": c_read, "cache_write": c_write, "status": status},
            level=None if status == "ok" else "ERROR",
        )
        log_event(logger, "llm_call", role=role, model=model, input_tokens=in_tok, output_tokens=out_tok,
                  cost_usd=call.cost_usd, latency_ms=latency_ms, status=status)
        if self._usage_sink is not None:
            try:
                self._usage_sink(call)
            except Exception as e:  # ghi usage lỗi không được làm hỏng request
                logger.warning("usage_sink lỗi (bỏ qua): %s", e)
        return call

    @staticmethod
    def _summarize_input(system: Any, messages: list[dict[str, Any]]) -> dict[str, Any]:
        last = messages[-1]["content"] if messages else ""
        if isinstance(last, list):
            last = " ".join(b.get("text", "") for b in last if isinstance(b, dict))
        return {"messages": len(messages), "last_user_chars": len(str(last)), "last_user_preview": str(last)[:500]}

    def complete(self, role: str, *, system: str | list[dict[str, Any]] | None, messages: list[dict[str, Any]],
                 max_tokens: int = 1024, temperature: float = 0.0, model: str | None = None) -> LLMResult:
        model = model or self.model_for(role)
        attempts = self.cfg.llm.max_retries + 1
        last_exc: BaseException | None = None
        for attempt in range(attempts):
            start = time.perf_counter()
            try:
                kwargs: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "temperature": temperature,
                                          "messages": messages}
                if system:
                    kwargs["system"] = system
                resp = self._client.messages.create(**kwargs)
            except Exception as e:
                latency = int((time.perf_counter() - start) * 1000)
                last_exc = e
                retryable = _is_retryable(e)
                log_event(logger, "llm_call_failed", level=logging.WARNING, role=role, model=model,
                          attempt=attempt + 1, max_attempts=attempts, retryable=retryable,
                          error=f"{type(e).__name__}: {e}", latency_ms=latency)
                if not retryable or attempt == attempts - 1:
                    self._finish_call(role, model, None, latency, "error", self._summarize_input(system, messages),
                                      f"{type(e).__name__}: {e}")
                    break
                self._sleep(self._backoff(attempt))
                continue
            latency = int((time.perf_counter() - start) * 1000)
            text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
            call = self._finish_call(role, model, resp.usage, latency, "ok", self._summarize_input(system, messages), text)
            return LLMResult(text=text, model=model, stop_reason=getattr(resp, "stop_reason", None), call=call)
        raise LLMUnavailable(
            L(f"Mô hình AI ({role}) không phản hồi sau {attempts} lần thử: {type(last_exc).__name__}",
              f"The AI model ({role}) did not respond after {attempts} attempts: {type(last_exc).__name__}"),
            context={"role": role, "model": model, "error": str(last_exc)},
        ) from last_exc

    def stream(self, role: str, *, system: str | list[dict[str, Any]], messages: list[dict[str, Any]],
               max_tokens: int = 1024, temperature: float = 0.0, model: str | None = None) -> StreamHandle:
        model = model or self.model_for(role)
        handle = StreamHandle(_gen=iter(()))

        def gen() -> Iterator[str]:
            attempts = self.cfg.llm.max_retries + 1
            last_exc: BaseException | None = None
            for attempt in range(attempts):
                start = time.perf_counter()
                emitted = False
                try:
                    kwargs: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "temperature": temperature,
                                              "messages": messages}
                    if system:
                        kwargs["system"] = system
                    with self._client.messages.stream(**kwargs) as s:
                        for text in s.text_stream:
                            emitted = True
                            yield text
                        final = s.get_final_message()
                    latency = int((time.perf_counter() - start) * 1000)
                    text_all = "".join(getattr(b, "text", "") for b in final.content if getattr(b, "type", None) == "text")
                    call = self._finish_call(role, model, final.usage, latency, "ok",
                                             self._summarize_input(system, messages), text_all)
                    handle.result = LLMResult(text=text_all, model=model, stop_reason=final.stop_reason, call=call)
                    return
                except Exception as e:
                    latency = int((time.perf_counter() - start) * 1000)
                    last_exc = e
                    retryable = _is_retryable(e) and not emitted
                    log_event(logger, "llm_stream_failed", level=logging.WARNING, role=role, model=model,
                              attempt=attempt + 1, retryable=retryable, emitted=emitted,
                              error=f"{type(e).__name__}: {e}")
                    if not retryable or attempt == attempts - 1:
                        self._finish_call(role, model, None, latency, "error",
                                          self._summarize_input(system, messages), f"{type(e).__name__}: {e}")
                        break
                    self._sleep(self._backoff(attempt))
            raise LLMUnavailable(
                L(f"Mô hình AI ({role}) lỗi khi stream câu trả lời: {type(last_exc).__name__}",
                  f"The AI model ({role}) failed while streaming the answer: {type(last_exc).__name__}"),
                context={"role": role, "model": model, "error": str(last_exc)},
            ) from last_exc

        handle._gen = gen()
        return handle

    def complete_json(self, role: str, *, system: str, user: str, schema: type[T], max_tokens: int = 1024,
                      max_retries: int = 2) -> T:
        """Gọi model, parse JSON + validate pydantic. Output sai -> gửi lại lỗi cho model, tối đa
        max_retries lần sửa. Hết lượt -> LLMOutputInvalid (caller quyết định bỏ qua hay báo lỗi)."""
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        last_error = ""
        for attempt in range(max_retries + 1):
            result = self.complete(role, system=system, messages=messages, max_tokens=max_tokens)
            try:
                data = extract_json_object(result.text)
                return schema.model_validate(data)
            except (json.JSONDecodeError, ValidationError, ValueError) as e:
                last_error = str(e)[:800]
                log_event(logger, "llm_json_invalid", level=logging.WARNING, role=role, attempt=attempt + 1,
                          error=last_error)
                messages = messages + [
                    {"role": "assistant", "content": result.text or "(rỗng)"},
                    {"role": "user", "content": "Output trên không hợp lệ theo schema yêu cầu: "
                                                f"{last_error}\nHãy trả lại DUY NHẤT 1 JSON hợp lệ."},
                ]
        raise LLMOutputInvalid(f"{role}: output JSON không hợp lệ sau {max_retries + 1} lần: {last_error}")


_default_client: LLMClient | None = None


def get_llm(usage_sink: UsageSink | None = None) -> LLMClient:
    """Client mặc định cho api/worker (ghi usage vào DB). Test tự tạo LLMClient(client=fake)."""
    global _default_client
    if _default_client is None:
        if usage_sink is None:
            from observability.costs import record_llm_usage

            usage_sink = record_llm_usage
        _default_client = LLMClient(usage_sink=usage_sink)
    return _default_client


def set_llm(client: LLMClient | None) -> None:
    global _default_client
    _default_client = client


class AnthropicCompatAdapter:
    """Bọc LLMClient thành object có .messages.create(model=..., max_tokens=..., messages=...) như
    anthropic.Anthropic — để tool cũ (agent/tools.extract_image_content) đi qua cùng retry/backoff,
    tính cost, ghi llm_usage + Langfuse mà không phải sửa tool."""

    def __init__(self, llm: LLMClient, role: str) -> None:
        self._llm = llm
        self._role = role
        self.messages = self

    def create(self, *, model: str, max_tokens: int, messages: list[dict[str, Any]], system: Any = None, **_: Any) -> Any:
        from types import SimpleNamespace

        res = self._llm.complete(self._role, system=system, messages=messages, max_tokens=max_tokens, model=model)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=res.text)], stop_reason=res.stop_reason,
                               usage=SimpleNamespace(input_tokens=res.call.input_tokens, output_tokens=res.call.output_tokens))
