"""Test cho index/rerank.py — score_fn TIÊM SẴN (dependency injection, cùng phong cách client=None
của ingest/chunkers.py) cho mọi test mặc định, KHÔNG tải model BAAI/bge-reranker-v2-m3 thật (~1.1GB).
Test latency THẬT (tải model thật, đo CPU thật) chỉ chạy khi RETAILKB_RUN_SLOW_TESTS=1 (cùng quy ước
skip-if với tests/test_parsers.py cho ffmpeg) — mặc định SKIP để CI không phụ thuộc mạng/tốn thời gian."""
import os
from dataclasses import dataclass

import pytest

from index.rerank import DEFAULT_TOP_K, RerankingRetriever, rerank, rerank_with_latency

RUN_SLOW = bool(os.environ.get("RETAILKB_RUN_SLOW_TESTS"))


@dataclass
class Candidate:
    id: str
    text: str
    metadata: dict = None
    score: float = None
    fused_score: float = None


def _score_by_length_fn(query, texts):
    """score_fn giả — điểm = độ dài text, đảo ngược thứ tự gốc để test chắc chắn rerank() có SẮP LẠI
    (không phải giữ nguyên thứ tự candidates đưa vào)."""
    return [float(len(t)) for t in texts]


def test_rerank_empty_candidates_returns_empty():
    assert rerank("query", [], score_fn=_score_by_length_fn) == []


def test_rerank_sorts_by_score_descending():
    candidates = [
        Candidate(id="short", text="ab", metadata={}),
        Candidate(id="long", text="abcdefghij", metadata={}),
        Candidate(id="mid", text="abcde", metadata={}),
    ]
    results = rerank("query", candidates, score_fn=_score_by_length_fn)
    assert [r.id for r in results] == ["long", "mid", "short"]


def test_rerank_caps_at_top_k():
    candidates = [Candidate(id=str(i), text="x" * i, metadata={}) for i in range(1, 11)]
    results = rerank("query", candidates, top_k=3, score_fn=_score_by_length_fn)
    assert len(results) == 3
    assert results[0].id == "10"  # dài nhất


def test_rerank_default_top_k_is_5():
    candidates = [Candidate(id=str(i), text="x" * i, metadata={}) for i in range(1, 11)]
    results = rerank("query", candidates, score_fn=_score_by_length_fn)
    assert len(results) == DEFAULT_TOP_K


def test_rerank_preserves_metadata_and_text():
    candidates = [Candidate(id="a", text="nội dung a", metadata={"store_code": "HCM01"})]
    results = rerank("query", candidates, score_fn=_score_by_length_fn)
    assert results[0].text == "nội dung a"
    assert results[0].metadata == {"store_code": "HCM01"}


def test_rerank_captures_original_score_from_fused_score():
    candidates = [Candidate(id="a", text="x", metadata={}, fused_score=0.42)]
    results = rerank("query", candidates, score_fn=_score_by_length_fn)
    assert results[0].original_score == 0.42


def test_rerank_captures_original_score_from_score_attribute():
    candidates = [Candidate(id="a", text="x", metadata={}, score=0.77)]
    results = rerank("query", candidates, score_fn=_score_by_length_fn)
    assert results[0].original_score == 0.77


def test_rerank_score_fn_called_with_query_and_all_texts():
    calls = []

    def recording_score_fn(query, texts):
        calls.append((query, texts))
        return [0.0 for _ in texts]

    candidates = [Candidate(id="a", text="văn bản a", metadata={})]
    rerank("câu hỏi", candidates, score_fn=recording_score_fn)

    assert calls == [("câu hỏi", ["văn bản a"])]


def test_rerank_with_latency_returns_positive_latency_ms():
    candidates = [Candidate(id="a", text="x", metadata={})]
    results, latency_ms = rerank_with_latency("query", candidates, score_fn=_score_by_length_fn)
    assert len(results) == 1
    assert latency_ms >= 0


# =====================================================================================
# RerankingRetriever — bọc 1 retriever bất kỳ + rerank
# =====================================================================================

class FakeRetriever:
    def __init__(self, candidates):
        self._candidates = candidates
        self.calls = []

    def search(self, query, top_k, filters=None):
        self.calls.append((query, top_k, filters))
        return self._candidates[:top_k]


def test_reranking_retriever_reranks_candidate_pool_from_inner_retriever():
    inner = FakeRetriever([
        Candidate(id="short", text="ab", metadata={}),
        Candidate(id="long", text="abcdefghij", metadata={}),
    ])
    retriever = RerankingRetriever(inner, candidate_pool=10, score_fn=_score_by_length_fn)

    results = retriever.search("query", top_k=2)

    assert [r.id for r in results] == ["long", "short"]
    assert inner.calls == [("query", 10, None)]


def test_reranking_retriever_passes_filters_through():
    from index.store import Filters

    inner = FakeRetriever([Candidate(id="a", text="x", metadata={})])
    retriever = RerankingRetriever(inner, score_fn=_score_by_length_fn)
    filters = Filters(store_code="HCM01")

    retriever.search("query", top_k=5, filters=filters)

    assert inner.calls[0][2] == filters


# =====================================================================================
# LATENCY THẬT — tải model BAAI/bge-reranker-v2-m3 thật (~1.1GB, cần mạng lần đầu), CHỈ chạy khi
# RETAILKB_RUN_SLOW_TESTS=1 (không chạy trong CI mặc định, xem docstring đầu file).
# =====================================================================================

@pytest.mark.skipif(not RUN_SLOW, reason="Cần RETAILKB_RUN_SLOW_TESTS=1 (tải model thật ~1.1GB, đo latency CPU thật)")
def test_rerank_latency_is_measured_and_positive_with_real_model():
    candidates = [
        Candidate(id="c1", text="Chính sách đổi trả trong 7 ngày cho hàng điện tử."),
        Candidate(id="c2", text="Bảng giá nồi chiên không dầu, không liên quan chính sách đổi trả."),
    ]
    results, latency_ms = rerank_with_latency("Chính sách đổi trả hàng điện tử là gì?", candidates, top_k=2)
    assert len(results) == 2
    assert latency_ms > 0
    print(f"\n[rerank latency thật] {latency_ms:.1f}ms cho {len(candidates)} candidate (CPU)")
