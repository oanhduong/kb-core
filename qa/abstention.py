"""Abstention (B3): dưới ngưỡng căn cứ -> KHÔNG trả lời, đề nghị chuyển người phụ trách; ghi lại mọi
lần abstain (bảng abstentions — admin xem KB đang thiếu gì, KB health C6, alert đột biến E3).

Ngưỡng nằm trong config (abstention.*):
- có rerank: sigmoid(logit) của ứng viên top-1 < rerank_min_score -> abstain (low_score);
- reranker lỗi/tắt: dense cosine top-1 < fallback_min_dense_score VÀ không có kết quả BM25 khớp từ
  khoá -> abstain (low_score) — thang điểm RRF không hiệu chỉnh nên không dùng làm ngưỡng;
- không có ứng viên nào -> no_results; graph không ra đường đi nào (sau khi đã fallback vector) ->
  quyết định theo vector.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from core.config import AppConfig
from core.logging import log_event

logger = logging.getLogger(__name__)


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


@dataclass
class AbstainDecision:
    abstain: bool
    reason: str | None
    top_score: float | None
    score_kind: str  # rerank | dense | graph | none


def decide(cfg: AppConfig, *, candidates: int, rerank_top: float | None, dense_top: float | None,
           bm25_hits: int, graph_sources: int = 0) -> AbstainDecision:
    ab = cfg.abstention
    if graph_sources >= ab.graph_min_paths and candidates == 0:
        return AbstainDecision(False, None, None, "graph")
    if candidates == 0 and graph_sources == 0:
        return AbstainDecision(True, "no_results", None, "none")
    if rerank_top is not None:
        if rerank_top < ab.rerank_min_score and graph_sources < ab.graph_min_paths:
            return AbstainDecision(True, "low_score", rerank_top, "rerank")
        return AbstainDecision(False, None, rerank_top, "rerank")
    if graph_sources >= ab.graph_min_paths:
        return AbstainDecision(False, None, dense_top, "graph")
    if (dense_top is None or dense_top < ab.fallback_min_dense_score) and bm25_hits == 0:
        return AbstainDecision(True, "low_score", dense_top, "dense")
    return AbstainDecision(False, None, dense_top, "dense")


def record_abstention(session: Any, *, trace_id: str, question: str, reason: str, top_score: float | None,
                      question_type: str | None, store_code: str | None, user_id: str | None,
                      workspace_id: str | None = None) -> None:
    from sqlalchemy.dialects.postgresql import insert

    from core.models import Abstention

    session.execute(insert(Abstention).values(
        trace_id=trace_id, workspace_id=workspace_id, question=question, reason=reason, top_score=top_score,
        question_type=question_type, store_code=store_code, user_id=user_id).on_conflict_do_nothing(index_elements=["trace_id"]))
    log_event(logger, "qa_abstained", reason=reason, top_score=top_score, question_type=question_type)
