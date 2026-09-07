"""Test cho index/hybrid.py — BM25Index/reciprocal_rank_fusion/HybridRetriever test bằng object thuần
Python (VectorRecord, FakeVectorStore/FakeEmbedder tiêm sẵn — cùng phong cách dependency-injection với
tests/test_reindex.py), không cần Postgres/Qdrant thật. Phần cuối file là test case RIÊNG cho hiện
tượng "dense thuần trả về sản phẩm gần nghĩa nhưng sai SKU" (xem docstring index/hybrid.py và
bench/hybrid_sku_case.py) — golden set 10 câu hỏi, kết quả so sánh dense-only vs hybrid được GHI LẠI
qua bench/hybrid_sku_case.py::run() (số liệu tất định, xem test_golden_sku_case_* bên dưới)."""
import pytest

from index.hybrid import (
    BM25Index,
    HybridRetriever,
    reciprocal_rank_fusion,
    tokenize_vi,
)
from index.store import Filters, SearchResult, VectorRecord, VectorStore

pytest.importorskip("pyvi")
pytest.importorskip("rank_bm25")


# =====================================================================================
# tokenize_vi
# =====================================================================================

def test_tokenize_vi_empty_string_returns_empty_list():
    assert tokenize_vi("") == []
    assert tokenize_vi("   ") == []


def test_tokenize_vi_joins_compound_words():
    tokens = tokenize_vi("cửa hàng gần nhất")
    assert "cửa_hàng" in tokens


def test_tokenize_vi_keeps_sku_shaped_token_whole():
    tokens = tokenize_vi("Giá sản phẩm mã ABC-123 hôm nay")
    assert "abc-123" in tokens
    assert "abc" not in tokens
    assert "123" not in tokens


def test_tokenize_vi_lowercases_everything():
    tokens = tokenize_vi("SKU ABC-123")
    assert all(t == t.lower() for t in tokens)


def test_tokenize_vi_drops_punctuation_only_tokens():
    tokens = tokenize_vi("Giá bao nhiêu?")
    assert "?" not in tokens


# =====================================================================================
# BM25Index
# =====================================================================================

def _doc(doc_id, text, **metadata):
    return VectorRecord(id=doc_id, vector=None, text=text, metadata=metadata)


def test_bm25index_ranks_exact_lexical_match_highest():
    docs = [
        _doc("a", "Tai nghe Bluetooth chống ồn, mã ABC-123"),
        _doc("b", "Tai nghe Bluetooth chống ồn, mã XYZ-999"),
        _doc("c", "Nồi chiên không dầu, mã DEF-456"),
    ]
    index = BM25Index(docs)
    results = index.search("mã ABC-123", top_k=3)
    assert results[0][0] == "a"


def test_bm25index_empty_corpus_returns_empty():
    assert BM25Index([]).search("bất kỳ", top_k=5) == []


def test_bm25index_no_lexical_overlap_returns_empty():
    docs = [_doc("a", "Tai nghe Bluetooth")]
    index = BM25Index(docs)
    assert index.search("nồi chiên không dầu", top_k=5) == []


def test_bm25index_respects_filters():
    docs = [
        _doc("a", "Giá sản phẩm ABC-123", store_code="HCM01"),
        _doc("b", "Giá sản phẩm ABC-123", store_code="HN01"),
    ]
    index = BM25Index(docs)
    results = index.search("ABC-123", top_k=5, filters=Filters(store_code="HN01"))
    assert [doc_id for doc_id, _ in results] == ["b"]


def test_bm25index_get_returns_document_by_id():
    docs = [_doc("a", "nội dung a"), _doc("b", "nội dung b")]
    index = BM25Index(docs)
    assert index.get("a").text == "nội dung a"
    assert index.get("missing") is None


def test_bm25index_search_caps_at_top_k():
    docs = [_doc(str(i), f"sản phẩm khuyến mãi số {i}") for i in range(10)]
    index = BM25Index(docs)
    results = index.search("sản phẩm khuyến mãi", top_k=3)
    assert len(results) == 3


# =====================================================================================
# reciprocal_rank_fusion
# =====================================================================================

def test_rrf_doc_present_in_both_rankings_scores_higher_than_single_ranking():
    fused = dict(reciprocal_rank_fusion([["a", "b"], ["b", "c"]]))
    assert fused["b"] > fused["a"]
    assert fused["b"] > fused["c"]


def test_rrf_empty_rankings_returns_empty():
    assert reciprocal_rank_fusion([[], []]) == []


def test_rrf_single_ranking_preserves_order():
    fused = reciprocal_rank_fusion([["a", "b", "c"]])
    assert [doc_id for doc_id, _ in fused] == ["a", "b", "c"]


def test_rrf_matches_manual_formula():
    fused = dict(reciprocal_rank_fusion([["a", "b"]], k=10))
    assert fused["a"] == pytest.approx(1 / 11)
    assert fused["b"] == pytest.approx(1 / 12)


# =====================================================================================
# HybridRetriever — FakeVectorStore/FakeEmbedder tiêm sẵn
# =====================================================================================

class FakeVectorStore(VectorStore):
    """Trả về đúng 1 ranking đã script sẵn, bỏ qua query_vec thật — chỉ để kiểm tra HybridRetriever
    hợp nhất đúng, không kiểm tra chất lượng dense thật (đó là việc của bench/hybrid_sku_case.py)."""

    def __init__(self, ranking):
        self._ranking = ranking  # list[SearchResult], đã sắp sẵn

    def upsert(self, records):
        raise NotImplementedError

    def search(self, query_vec, top_k, filters=None):
        return self._ranking[:top_k]

    def delete(self, ids):
        raise NotImplementedError

    def count(self):
        return len(self._ranking)


class FakeEmbedder:
    def embed(self, texts):
        return [[0.0] for _ in texts]


def test_hybrid_retriever_fuses_dense_and_bm25_rankings():
    # "decoy" không có bất kỳ token nào trùng query -> dense (rank1, nhưng KHÔNG có căn cứ từ vựng
    # nào) không được BM25 xác nhận; "correct" có mặt ở CẢ HAI ranking (bm25 rank1 nhờ khớp đúng SKU,
    # dense rank2) -> RRF phải cộng dồn 2 nguồn tín hiệu và xếp "correct" lên đầu, đúng tinh thần
    # "đồng thuận 2 nguồn thắng 1 nguồn đơn lẻ" của RRF.
    dense_store = FakeVectorStore([
        SearchResult(id="decoy", score=0.9, text="chuyện hoàn toàn không liên quan", metadata={}),
        SearchResult(id="correct", score=0.85, text="sản phẩm đúng mã ABC-123", metadata={}),
    ])
    bm25_docs = [
        _doc("correct", "sản phẩm đúng mã ABC-123"),
        _doc("decoy", "chuyện hoàn toàn không liên quan"),
    ]
    retriever = HybridRetriever(dense_store, BM25Index(bm25_docs), FakeEmbedder())

    results = retriever.search("mã ABC-123", top_k=2)

    assert results[0].id == "correct"
    assert results[0].bm25_score is not None
    assert results[0].dense_score is not None


def test_hybrid_retriever_includes_bm25_only_doc_missing_from_dense():
    dense_store = FakeVectorStore([SearchResult(id="a", score=0.5, text="a", metadata={})])
    bm25_docs = [_doc("a", "sản phẩm A"), _doc("b", "sản phẩm B mã ABC-123")]
    retriever = HybridRetriever(dense_store, BM25Index(bm25_docs), FakeEmbedder())

    results = retriever.search("ABC-123", top_k=5)

    ids = [r.id for r in results]
    assert "b" in ids  # chỉ BM25 tìm ra (dense không trả về) vẫn phải có mặt trong kết quả hợp nhất
    b_result = next(r for r in results if r.id == "b")
    assert b_result.dense_score is None
    assert b_result.bm25_score is not None


def test_hybrid_retriever_respects_top_k():
    dense_store = FakeVectorStore([SearchResult(id=str(i), score=1.0, text=f"sản phẩm {i}", metadata={}) for i in range(5)])
    bm25_docs = [_doc(str(i), f"sản phẩm {i}") for i in range(5)]
    retriever = HybridRetriever(dense_store, BM25Index(bm25_docs), FakeEmbedder())

    assert len(retriever.search("sản phẩm", top_k=2)) == 2


def test_hybrid_retriever_passes_filters_to_both_branches():
    dense_calls = []

    class RecordingStore(VectorStore):
        def upsert(self, records):
            raise NotImplementedError

        def search(self, query_vec, top_k, filters=None):
            dense_calls.append(filters)
            return []

        def delete(self, ids):
            raise NotImplementedError

        def count(self):
            return 0

    retriever = HybridRetriever(RecordingStore(), BM25Index([]), FakeEmbedder())
    filters = Filters(store_code="HCM01")
    retriever.search("bất kỳ", top_k=5, filters=filters)

    assert dense_calls == [filters]


# =====================================================================================
# GOLDEN SET — hiện tượng "dense thuần trả sản phẩm gần nghĩa nhưng SAI SKU" (xem docstring đầu file
# index/hybrid.py). Corpus/query dùng chung với bench/hybrid_sku_case.py để không định nghĩa 2 lần.
# Kết quả TẤT ĐỊNH (không phụ thuộc PYTHONHASHSEED, xem bench/hybrid_sku_case.py::_noise_vector) —
# GHI LẠI ở đây bằng assertion cứng (không phải threshold mơ hồ) để đối chiếu lại được khi code đổi.
# =====================================================================================

def test_golden_sku_case_dense_only_fails_on_most_queries():
    from bench.hybrid_sku_case import run

    result = run(progress_fn=lambda msg: None)

    assert result["n_queries"] == 10
    # Dense thuần: đúng 2/10 (0.20) — xác nhận hiện tượng "dense trả sản phẩm gần nghĩa nhưng sai SKU"
    # xảy ra trên ĐA SỐ (8/10) câu hỏi của golden set.
    assert result["dense_recall_at_1"] == pytest.approx(0.20)


def test_golden_sku_case_hybrid_recovers_most_of_the_gap():
    from bench.hybrid_sku_case import run

    result = run(progress_fn=lambda msg: None)

    # Hybrid: đúng 6/10 (0.60) — GẤP 3 lần dense thuần, nhưng KHÔNG đạt 100% (giới hạn thật của RRF
    # k=60 trên pool nhỏ/rất giống nhau — xem docstring bench/hybrid_sku_case.py, không che giấu).
    assert result["hybrid_recall_at_1"] == pytest.approx(0.60)
    assert result["hybrid_recall_at_1"] > result["dense_recall_at_1"]
