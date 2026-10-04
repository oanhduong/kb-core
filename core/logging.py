"""Structured logging (JSON 1 dòng/event) dùng stdlib logging — không thêm dependency.

- Ngữ cảnh request/job (request_id, trace_id, user_id, store_code, session_id, job_id) giữ trong
  contextvars, tự gắn vào MỌI dòng log phát ra trong cùng ngữ cảnh (kể cả log của module cũ như
  index/hybrid.py dùng logging.getLogger thường) — không cần truyền tay qua từng hàm.
- log_event(logger, "event_name", **fields) là cách khuyến nghị để log có field máy đọc được.
- configure_logging() gọi 1 lần ở entrypoint (api, worker, CLI). LOG_FORMAT=text để đọc bằng mắt
  khi dev local.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from typing import Any, Iterator

_CONTEXT_KEYS = ("request_id", "trace_id", "user_id", "store_code", "session_id", "job_id", "config_version")
_log_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("retailkb_log_context", default={})


def get_log_context() -> dict[str, Any]:
    return dict(_log_context.get())


def bind_log_context(**fields: Any) -> contextvars.Token:
    """Gắn thêm field vào ngữ cảnh log hiện tại. Trả về token để reset_log_context()."""
    current = dict(_log_context.get())
    current.update({k: v for k, v in fields.items() if v is not None})
    return _log_context.set(current)


def reset_log_context(token: contextvars.Token) -> None:
    _log_context.reset(token)


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    token = bind_log_context(**fields)
    try:
        yield
    finally:
        reset_log_context(token)


class JsonFormatter(logging.Formatter):
    _RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(_log_context.get())
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        ctx = _log_context.get()
        extras = {k: v for k, v in record.__dict__.items()
                  if k not in JsonFormatter._RESERVED and not k.startswith("_")}
        merged = {**ctx, **extras}
        if merged:
            base += " | " + " ".join(f"{k}={v}" for k, v in merged.items())
        return base


_configured = False


def configure_logging(level: str | None = None, fmt: str | None = None) -> None:
    global _configured
    if _configured:
        return
    level = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    fmt = (fmt or os.environ.get("LOG_FORMAT", "json")).lower()
    handler = logging.StreamHandler(sys.stdout)
    if fmt == "text":
        handler.setFormatter(TextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    else:
        handler.setFormatter(JsonFormatter())
    handler._retailkb = True  # type: ignore[attr-defined]
    root = logging.getLogger()
    # chỉ thay handler do chính module này cài (không đụng handler của uvicorn/pytest caplog...)
    root.handlers[:] = [h for h in root.handlers if not getattr(h, "_retailkb", False)] + [handler]
    root.setLevel(level)
    # Thư viện ồn ào — giữ WARNING để log vận hành không bị chìm.
    for noisy in ("httpx", "httpcore", "urllib3", "anthropic", "sentence_transformers", "transformers",
                  "filelock", "huggingface_hub", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Log 1 event có cấu trúc. `fields` thành key riêng trong JSON (không nhồi vào message)."""
    safe = {k: v for k, v in fields.items() if k not in JsonFormatter._RESERVED}
    logger.log(level, event, extra={"event": event, **safe})
