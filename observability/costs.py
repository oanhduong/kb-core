"""Ghi usage/cost của từng lần gọi LLM vào bảng llm_usage — nguồn số liệu cho alert chi phí ngày
(E3) và dashboard admin. Độc lập với Langfuse: Langfuse tắt/hỏng vẫn kiểm soát được chi phí."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.db import session_scope
from core.models import LLMUsage

logger = logging.getLogger(__name__)


def record_llm_usage(call) -> None:  # call: core.llm.LLMCall (tránh import vòng)
    with session_scope() as s:
        s.add(LLMUsage(
            role=call.role, model=call.model, trace_id=call.trace_id, job_id=call.job_id,
            input_tokens=call.input_tokens, output_tokens=call.output_tokens,
            cache_read_tokens=call.cache_read_tokens, cache_write_tokens=call.cache_write_tokens,
            cost_usd=call.cost_usd, latency_ms=call.latency_ms, status=call.status,
        ))


def cost_since(session: Session, since: datetime) -> float:
    return float(session.execute(select(func.coalesce(func.sum(LLMUsage.cost_usd), 0.0))
                                 .where(LLMUsage.created_at >= since)).scalar_one())


def cost_today(session: Session, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return cost_since(session, start)


def cost_by_role(session: Session, days: int = 1) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = session.execute(
        select(LLMUsage.role, LLMUsage.model, func.count(), func.sum(LLMUsage.cost_usd),
               func.sum(LLMUsage.input_tokens), func.sum(LLMUsage.output_tokens))
        .where(LLMUsage.created_at >= since).group_by(LLMUsage.role, LLMUsage.model)
    ).all()
    return [{"role": r, "model": m, "calls": c, "cost_usd": round(float(cost or 0), 4),
             "input_tokens": int(i or 0), "output_tokens": int(o or 0)} for r, m, c, cost, i, o in rows]
