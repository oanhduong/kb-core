"""Tracing Langfuse (E1/E2) — 1 trace / request QA, 1 trace / file ingest.

Thiết kế:
- start_trace(...) tạo trace gốc và đặt làm "trace hiện tại" trong contextvar; span(...) ở BẤT KỲ
  đâu trong cùng ngữ cảnh (router, retrieval, rerank, graph...) tự gắn làm con của span đang mở —
  không phải truyền handle qua từng hàm.
- generation(...) cho mỗi lần gọi model: model, input tóm tắt, usage token + cost USD.
- Metadata bắt buộc trên trace (E2): config_version, config_hash, store_code, question_type,
  user_id, session_id — để tìm lại request cụ thể khi user báo lỗi (Langfuse UI lọc theo
  user/session/metadata; trace_id của Langfuse = trace_id của hệ thống, in trong response API).
- Observability KHÔNG BAO GIỜ làm hỏng request: mọi lỗi SDK/mạng tới Langfuse bị nuốt có log debug.
  Langfuse tắt (thiếu key / config tắt / SDK không cài) -> dùng no-op, code gọi không cần biết.
"""
from __future__ import annotations

import contextvars
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from core.config import get_config, get_settings

logger = logging.getLogger(__name__)

_current_trace: contextvars.ContextVar["TraceHandle | None"] = contextvars.ContextVar("retailkb_trace", default=None)
_current_parent: contextvars.ContextVar[Any] = contextvars.ContextVar("retailkb_span_parent", default=None)

_langfuse_client: Any = None
_langfuse_init_done = False


def _get_langfuse() -> Any:
    global _langfuse_client, _langfuse_init_done
    if _langfuse_init_done:
        return _langfuse_client
    _langfuse_init_done = True
    try:
        cfg = get_config()
        settings = get_settings()
        if not cfg.observability.langfuse_enabled:
            return None
        if not (settings.langfuse_public_key and settings.langfuse_secret_key and settings.langfuse_host):
            logger.info("Langfuse chưa cấu hình (LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST) — tracing chạy no-op")
            return None
        from langfuse import Langfuse

        _langfuse_client = Langfuse(
            public_key=settings.langfuse_public_key, secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host, release=cfg.config_version,
        )
    except Exception as e:  # SDK thiếu hoặc lỗi khởi tạo -> no-op
        logger.warning("Không khởi tạo được Langfuse, tracing chạy no-op: %s", e)
        _langfuse_client = None
    return _langfuse_client


def _safe(fn, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # pragma: no cover - lỗi mạng/SDK Langfuse
        logger.debug("langfuse call lỗi (bỏ qua): %s", e)
        return None


@dataclass
class SpanHandle:
    name: str
    _obj: Any = None
    started: float = field(default_factory=time.perf_counter)
    output: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    level: str | None = None

    def update(self, *, output: Any = None, level: str | None = None, **metadata: Any) -> None:
        if output is not None:
            self.output = output
        if level is not None:
            self.level = level
        self.metadata.update(metadata)

    @property
    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)


@dataclass
class TraceHandle:
    trace_id: str
    name: str
    _obj: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return self._obj is not None

    def update(self, **kwargs: Any) -> None:
        if "metadata" in kwargs:
            self.metadata.update(kwargs["metadata"] or {})
            kwargs["metadata"] = self.metadata
        if self._obj is not None:
            _safe(self._obj.update, **kwargs)

    def url(self) -> str | None:
        return trace_url(self.trace_id) if self._obj is not None else None


def current_trace() -> TraceHandle | None:
    return _current_trace.get()


def trace_url(trace_id: str | None) -> str | None:
    """Link mở trace trên Langfuse UI (LANGFUSE_PUBLIC_URL; có LANGFUSE_PROJECT_ID thì trỏ thẳng trang
    trace của project, không thì dùng đường dẫn /trace/<id> — Langfuse tự chuyển tới project)."""
    settings = get_settings()
    if not trace_id or not settings.langfuse_public_url:
        return None
    base = settings.langfuse_public_url.rstrip("/")
    if settings.langfuse_project_id:
        return f"{base}/project/{settings.langfuse_project_id}/traces/{trace_id}"
    return f"{base}/trace/{trace_id}"


@contextmanager
def start_trace(name: str, *, trace_id: str, user_id: str | None = None, session_id: str | None = None,
                input: Any = None, metadata: dict[str, Any] | None = None,
                tags: list[str] | None = None) -> Iterator[TraceHandle]:
    cfg = get_config()
    meta = {"config_version": cfg.config_version, "config_hash": cfg.config_hash, **(metadata or {})}
    client = _get_langfuse()
    obj = None
    if client is not None:
        obj = _safe(client.trace, id=trace_id, name=name, user_id=user_id, session_id=session_id,
                    input=input, metadata=meta, tags=tags, release=cfg.config_version)
    handle = TraceHandle(trace_id=trace_id, name=name, _obj=obj, metadata=meta)
    token_t = _current_trace.set(handle)
    token_p = _current_parent.set(obj)
    try:
        yield handle
    finally:
        _current_parent.reset(token_p)
        _current_trace.reset(token_t)
        if client is not None:
            _safe(client.flush)


@contextmanager
def span(name: str, *, input: Any = None, **metadata: Any) -> Iterator[SpanHandle]:
    """Span con của span/trace đang mở. Dùng được cả khi không có trace (no-op)."""
    parent = _current_parent.get()
    obj = _safe(parent.span, name=name, input=input, metadata=metadata or None) if parent is not None else None
    handle = SpanHandle(name=name, _obj=obj, metadata=dict(metadata))
    token = _current_parent.set(obj if obj is not None else parent)
    try:
        yield handle
    except Exception as e:
        handle.update(level="ERROR", error=f"{type(e).__name__}: {e}")
        raise
    finally:
        _current_parent.reset(token)
        handle.metadata["elapsed_ms"] = handle.elapsed_ms
        if obj is not None:
            kwargs: dict[str, Any] = {"output": handle.output, "metadata": handle.metadata}
            if handle.level:
                kwargs["level"] = handle.level
                kwargs["status_message"] = str(handle.metadata.get("error", ""))[:500]
            _safe(obj.end, **kwargs)


def record_generation(*, name: str, model: str, input: Any, output: Any, input_tokens: int, output_tokens: int,
                      cost_usd: float, latency_ms: int, metadata: dict[str, Any] | None = None,
                      level: str | None = None) -> None:
    """Ghi 1 generation (lần gọi model) vào span/trace hiện tại — kèm token + cost (E1)."""
    parent = _current_parent.get()
    if parent is None:
        return
    kwargs: dict[str, Any] = dict(
        name=name, model=model, input=input, output=output,
        usage={"input": input_tokens, "output": output_tokens, "total": input_tokens + output_tokens,
               "unit": "TOKENS", "total_cost": cost_usd},
        metadata={**(metadata or {}), "latency_ms": latency_ms, "cost_usd": cost_usd},
    )
    if level:
        kwargs["level"] = level
    _safe(parent.generation, **kwargs)


def flush() -> None:
    client = _get_langfuse()
    if client is not None:
        _safe(client.flush)
