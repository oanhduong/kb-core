"""Exception có ngữ cảnh dùng chung cho API/worker.

Mọi lỗi nghiệp vụ đi lên tầng API đều là AppError (hoặc lớp con) — mang `code` máy đọc được,
`message` tiếng Việt người dùng đọc được, `http_status`, và `context` (dict) để log/trace. API có
exception handler chung (api/errors.py) chuyển AppError -> JSON {error: {code, message, trace_id}},
và chuyển mọi exception KHÔNG lường trước thành 1 thông báo thân thiện + log đầy đủ stacktrace —
không bao giờ trả lỗi 500 trần (yêu cầu B9).
"""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    code: str = "app_error"
    http_status: int = 500

    def __init__(self, message: str, *, code: str | None = None, http_status: int | None = None,
                 context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        self.context: dict[str, Any] = dict(context or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}

    def __str__(self) -> str:  # pragma: no cover - chỉ phục vụ log
        if self.context:
            return f"{self.message} (context={self.context})"
        return self.message


class NotFoundError(AppError):
    code = "not_found"
    http_status = 404


class ForbiddenError(AppError):
    code = "forbidden"
    http_status = 403


class UnauthorizedError(AppError):
    code = "unauthorized"
    http_status = 401


class ValidationFailed(AppError):
    code = "validation_failed"
    http_status = 422


class ConflictError(AppError):
    code = "conflict"
    http_status = 409


class RateLimited(AppError):
    code = "rate_limited"
    http_status = 429


class UpstreamUnavailable(AppError):
    """Dịch vụ phía sau (LLM, reranker, graph) lỗi/timeout sau khi đã retry hết."""

    code = "upstream_unavailable"
    http_status = 503


class ConfigError(AppError):
    code = "config_error"
    http_status = 500
