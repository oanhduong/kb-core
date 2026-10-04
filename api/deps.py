"""Dependency dùng chung cho router: DB session, services, audit hành động người dùng."""
from __future__ import annotations

import logging
from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from core.db import get_sessionmaker
from core.principal import Principal
from security.audit import AppendOnlyAuditLog

logger = logging.getLogger(__name__)


def get_db() -> Iterator[Session]:
    s = get_sessionmaker()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_services(request: Request) -> Any:
    return request.app.state.services


@lru_cache(maxsize=1)
def _audit_log() -> AppendOnlyAuditLog:
    from observability.retention import RotatingAuditLog

    return RotatingAuditLog()


def audit_action(request: Request | None, principal: Principal, action: str, target: str | None, **details: Any) -> None:
    """Ghi audit append-only cho thao tác của người (duyệt review, sửa/xoá record, xoá file, tạo key...)."""
    try:
        _audit_log().append(
            run_id=getattr(getattr(request, "state", None), "request_id", None) if request else None,
            file_id=target, tool_name=f"api:{action}", arguments=details, result={"ok": True},
            policy_decision="committed", approved_by=principal.subject, latency_seconds=0.0)
    except Exception:  # audit lỗi phải hiện rõ trong log nhưng không phá request đã thành công
        logger.exception("ghi audit log thất bại action=%s target=%s", action, target)
