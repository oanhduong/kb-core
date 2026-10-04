"""Vector pipeline (B1): hybrid (dense + BM25, chạy song song) + metadata pre-filter + RRF + rerank.

Mỗi bước là 1 span Langfuse riêng (E1): retrieval.dense, retrieval.bm25, retrieval.fusion,
retrieval.rerank. Graceful degradation (B9):
- dense lỗi (embedding/DB) -> chỉ BM25; BM25 lỗi -> chỉ dense; cả hai lỗi -> RetrievalUnavailable.
- reranker lỗi/timeout/tắt -> giữ thứ tự hybrid (RRF), abstention chuyển sang ngưỡng dense dự phòng.
Mọi lần dự phòng được ghi vào outcome.degraded -> hiện trong decision trace.
"""
from __future__ import annotations

import contextvars
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from core.config import AppConfig
from core.errors import UpstreamUnavailable
from core.logging import log_event
from index.hybrid import reciprocal_rank_fusion
from index.store import Filters, VectorStore
from observability import tracing
from qa.abstention import sigmoid

logger = logging.getLogger(__name__)

ScoreFn = Callable[[str, list[str]], list[float]]
_RERANK_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rerank")


class RetrievalUnavailable(UpstreamUnavailable):
    code = "retrieval_unavailable"


@dataclass
class Candidate:
    chunk_id: str
    text: str
    metadata: dict[str, Any]
    dense_score: float | None = None
    bm25_score: float | None = None
    fused_score: float = 0.0
    rerank_logit: float | None = None
    rerank_score: float | None = None


@dataclass
class RetrievalOutcome:
    candidates: list[Candidate] = field(default_factory=list)
    dense_count: int = 0
    bm25_count: int = 0
    dense_top: float | None = None
    rerank_used: bool = False
    degraded: list[str] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)

    @property
    def rerank_top(self) -> float | None:
        return self.candidates[0].rerank_score if self.rerank_used and self.candidates else None


class VectorRetriever:
    def __init__(self, cfg: AppConfig, vector_store: VectorStore, bm25_provider: Callable[[], Any], embedder: Any,
                 rerank_fn: ScoreFn | None = None) -> None:
        self.cfg = cfg
        self.vector_store = vector_store
        self.bm25_provider = bm25_provider
        self.embedder = embedder
        self.rerank_fn = rerank_fn
        self._rerank_ready = threading.Event()
        if rerank_fn is not None:
            self._rerank_ready.set()

    # ------------------------------------------------------------------ reranker
    @classmethod
    def default_rerank_fn(cls, cfg: AppConfig) -> ScoreFn:
        from index.rerank import _cross_encoder_score_fn

        return _cross_encoder_score_fn(cfg.retrieval.rerank.model, cfg.retrieval.rerank.batch_size)

    def warmup_reranker(self) -> None:
        """Tải model rerank ở nền lúc khởi động (lần tải đầu ~1GB) — trong lúc chưa xong, request đi
        nhánh dự phòng (hybrid) thay vì treo chờ."""
        if not self.cfg.retrieval.rerank.enabled:
            return

        def _load() -> None:
            try:
                fn = self.default_rerank_fn(self.cfg)
                fn("khởi động", ["kiểm tra"])
                self.rerank_fn = fn
                self._rerank_ready.set()
                log_event(logger, "reranker_ready", model=self.cfg.retrieval.rerank.model)
            except Exception as e:
                log_event(logger, "reranker_load_failed", level=logging.ERROR, error=f"{type(e).__name__}: {e}")

        threading.Thread(target=_load, daemon=True, name="rerank-warmup").start()

    # ------------------------------------------------------------------ retrieve
    def retrieve(self, query: str, filters: Filters) -> RetrievalOutcome:
        rc = self.cfg.retrieval
        out = RetrievalOutcome(filters={k: v for k, v in asdict(filters).items() if v is not None})
        with tracing.span("retrieval", input={"query": query, "filters": out.filters}) as root:
            dense: list[Any] = []
            bm25: list[tuple[str, float]] = []
            bm25_index = None
            with ThreadPoolExecutor(max_workers=2) as ex:
                # mỗi nhánh 1 bản copy contextvars riêng -> span Langfuse/log context theo được sang thread
                f_dense = ex.submit(contextvars.copy_context().run, self._dense, query, filters)
                f_bm25 = ex.submit(contextvars.copy_context().run, self._bm25, query, filters)
                try:
                    dense = f_dense.result()
                except Exception as e:
                    out.degraded.append(f"Tìm kiếm ngữ nghĩa (dense) lỗi: {type(e).__name__} -> chỉ dùng từ khoá (BM25)")
                    log_event(logger, "retrieval_dense_failed", level=logging.ERROR, error=f"{type(e).__name__}: {e}")
                try:
                    bm25, bm25_index = f_bm25.result()
                except Exception as e:
                    out.degraded.append(f"Tìm kiếm từ khoá (BM25) lỗi: {type(e).__name__} -> chỉ dùng ngữ nghĩa")
                    log_event(logger, "retrieval_bm25_failed", level=logging.ERROR, error=f"{type(e).__name__}: {e}")
            if len(out.degraded) == 2:
                raise RetrievalUnavailable("Hệ thống tìm kiếm tài liệu đang gặp sự cố, vui lòng thử lại sau.")
            out.dense_count, out.bm25_count = len(dense), len(bm25)
            out.dense_top = dense[0].score if dense else None

            with tracing.span("retrieval.fusion", input={"dense": len(dense), "bm25": len(bm25)}) as sp:
                fused = reciprocal_rank_fusion([[r.id for r in dense], [i for i, _ in bm25]], k=rc.rrf_k)
                by_id = {r.id: r for r in dense}
                dense_scores = {r.id: r.score for r in dense}
                bm25_scores = dict(bm25)
                cands: list[Candidate] = []
                for cid, fscore in fused[: rc.rerank.candidate_pool]:
                    src = by_id.get(cid) or (bm25_index.get(cid) if bm25_index is not None else None)
                    if src is None:
                        continue
                    meta = dict(src.metadata or {})
                    text = meta.pop("raw_text", None) or src.text
                    cands.append(Candidate(cid, text, meta, dense_scores.get(cid), bm25_scores.get(cid), fscore))
                sp.update(output={"fused": len(cands)})

            cands = self._rerank(query, cands, out)
            out.candidates = cands[: rc.rerank.top_n]
            root.update(output={"candidates": len(out.candidates), "rerank_used": out.rerank_used,
                                "top": out.candidates[0].chunk_id if out.candidates else None, "degraded": out.degraded})
        return out

    def _dense(self, query: str, filters: Filters) -> list[Any]:
        with tracing.span("retrieval.dense", model=getattr(self.embedder, "model_name", "?")) as sp:
            vec = self.embedder.embed_query(query)
            res = self.vector_store.search(vec, self.cfg.retrieval.candidate_top_k, filters=filters)
            sp.update(output={"hits": len(res), "top_score": round(res[0].score, 4) if res else None})
            return res

    def _bm25(self, query: str, filters: Filters) -> tuple[list[tuple[str, float]], Any]:
        with tracing.span("retrieval.bm25") as sp:
            index = self.bm25_provider()
            res = index.search(query, self.cfg.retrieval.candidate_top_k, filters=filters)
            sp.update(output={"hits": len(res)})
            return res, index

    def _rerank(self, query: str, cands: list[Candidate], out: RetrievalOutcome) -> list[Candidate]:
        rr = self.cfg.retrieval.rerank
        if not cands:
            return cands
        if not rr.enabled:
            out.degraded.append("Reranker tắt trong config -> dùng thứ tự hybrid")
            return cands
        if self.rerank_fn is None or not self._rerank_ready.is_set():
            out.degraded.append("Reranker chưa sẵn sàng (đang tải model) -> dùng thứ tự hybrid")
            return cands
        with tracing.span("retrieval.rerank", model=rr.model, candidates=len(cands)) as sp:
            texts = [(c.metadata.get("context_prefix") + "\n" + c.text) if c.metadata.get("context_prefix") else c.text
                     for c in cands]
            try:
                scores = _RERANK_POOL.submit(self.rerank_fn, query, texts).result(timeout=rr.timeout_seconds)
            except FutureTimeout:
                out.degraded.append(f"Reranker quá {rr.timeout_seconds:.0f}s -> dùng thứ tự hybrid")
                sp.update(level="WARNING", error="timeout")
                log_event(logger, "rerank_timeout", level=logging.WARNING, timeout=rr.timeout_seconds)
                return cands
            except Exception as e:
                out.degraded.append(f"Reranker lỗi ({type(e).__name__}) -> dùng thứ tự hybrid")
                sp.update(level="ERROR", error=f"{type(e).__name__}: {e}")
                log_event(logger, "rerank_failed", level=logging.ERROR, error=f"{type(e).__name__}: {e}")
                return cands
            for c, s in zip(cands, scores):
                c.rerank_logit = float(s)
                c.rerank_score = sigmoid(float(s))
            cands.sort(key=lambda c: c.rerank_score or 0.0, reverse=True)
            out.rerank_used = True
            sp.update(output={"top_score": round(cands[0].rerank_score or 0, 4)})
        return cands
