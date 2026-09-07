"""index/hybrid.py — Hybrid retrieval: BM25 (lexical) + dense (semantic, qua index/store.VectorStore)
chạy SONG SONG, hợp nhất bằng Reciprocal Rank Fusion (RRF, k=60 mặc định).

Vấn đề dense thuần không giải quyết được: catalog bán lẻ có RẤT NHIỀU chunk mô tả gần giống hệt nhau
(cùng category, cùng khuôn câu) nhưng khác SKU/mã cửa hàng. Dense đo similarity NGỮ NGHĨA tổng thể —
token SKU (chuỗi chữ+số hiếm gặp) đóng góp rất nhỏ vào "ý nghĩa" so với các từ mô tả chung ("tai nghe
Bluetooth chống ồn", ...), nên khi corpus có nhiều sản phẩm cùng dòng, dense rất dễ xếp hạng cao 1
chunk ĐÚNG CATEGORY nhưng SAI SKU lên trên chunk đúng. BM25 khớp từ vựng CHÍNH XÁC — SKU là token hiếm
(IDF cao), khớp đúng SKU tạo điểm áp đảo — nên CẢI THIỆN RÕ RỆT điểm yếu này (không phải "chìa khoá
vạn năng" — RRF chỉ dùng RANK, không dùng độ lớn điểm thô, nên trên pool rất nhỏ/rất giống nhau vẫn có
thể lọt case sai — xem giới hạn thật của RRF ở docstring bench/hybrid_sku_case.py). Bằng chứng cụ thể
(golden set 10 câu hỏi có SKU, kết quả THẬT: dense recall@1=0.20, hybrid recall@1=0.60): xem
tests/test_hybrid.py và bench/hybrid_sku_case.py.

Tokenize tiếng Việt (tokenize_vi): pyvi.ViTokenizer.tokenize ghép từ ghép ("cửa hàng" -> 1 token
"cửa_hàng", tránh "cửa" một mình loãng IDF vì xuất hiện trong quá nhiều câu không liên quan) — NHƯNG
pyvi tách vụn chuỗi SKU/mã cửa hàng dạng "ABC-123" thành 3 token rời ("abc", "-", "123"), làm mất tính
"token hiếm nguyên khối" mà BM25 cần để phân biệt SKU. Nên SKU-shaped substring được trích riêng bằng
regex TRƯỚC khi đưa phần còn lại của câu qua pyvi, giữ nguyên thành 1 token duy nhất (cùng tinh thần
_SKU_RE của ingest/chunkers.py, không dùng chung hằng số để 2 module không phụ thuộc lẫn nhau).

RRF: score(doc) = sum(1 / (k + rank)) trên MỌI ranking chứa doc (rank bắt đầu từ 1, doc không xuất
hiện trong 1 ranking thì ranking đó không đóng góp gì cho doc đó). k=60 là hằng số phổ biến trong
literature (Cormack et al. 2009) — làm giảm ảnh hưởng của rank thấp/outlier mà KHÔNG cần chuẩn hoá
điểm thô giữa BM25 (không chặn trên) và dense (cosine, [-1, 1]) — hai thang đo không so sánh trực
tiếp được, RRF né hẳn vấn đề đó bằng cách chỉ dùng RANK, không dùng điểm thô.
"""
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from rank_bm25 import BM25Okapi

from index.store import Filters, _matches_filters
   
DEFAULT_RRF_K = 60
DEFAULT_CANDIDATE_TOP_K = 50  # số ứng viên lấy từ MỖI nhánh (dense/BM25) trước khi fuse

# =====================================================================================
# TOKENIZE — tiếng Việt qua pyvi, SKU/mã cửa hàng giữ nguyên khối (xem docstring đầu file)
# =====================================================================================

# Hình dạng gần giống ingest/chunkers._SKU_RE (2-6 chữ + 2-6 số, có thể nối gạch ngang) nhưng rộng
# hơn 1 chút (không giới hạn chữ hoa) vì câu hỏi người dùng gõ SKU/mã cửa hàng không nhất quán hoa/
# thường (vd "abc123" vs "ABC123") — tokenize_vi luôn lowercase kết quả nên không cần phân biệt.
_SKU_LIKE_RE = re.compile(r"\b[A-Za-zĐđ]{2,6}-?\d{2,6}(?:-[A-Za-z0-9]+)?\b")
_WORD_RE = re.compile(r"[^\s]+", re.UNICODE)
_PUNCT_ONLY_RE = re.compile(r"^[^\w]+$", re.UNICODE)


def tokenize_vi(text):
    """str -> list[str] token đã lowercase. Rỗng/None -> []. Xem docstring đầu file cho lý do trích
    SKU-shaped substring riêng trước khi đưa qua pyvi."""
    if not text or not text.strip():
        return []
    try:
        from pyvi import ViTokenizer
    except ImportError as e:
        raise ImportError("Thiếu thư viện pyvi. Cài đặt: pip install pyvi") from e

    sku_like_tokens = [m.group(0).lower() for m in _SKU_LIKE_RE.finditer(text)]
    remaining_text = _SKU_LIKE_RE.sub(" ", text)

    segmented = ViTokenizer.tokenize(remaining_text.strip())
    word_tokens = [t for t in _WORD_RE.findall(segmented.lower()) if not _PUNCT_ONLY_RE.match(t)]
    return sku_like_tokens + word_tokens


# =====================================================================================
# BM25 — rank_bm25.BM25Okapi, IDF tính 1 LẦN trên toàn corpus lúc khởi tạo (không rebuild mỗi query)
# =====================================================================================

class BM25Index:
    """Index BM25 thuần Python trên corpus cố định (documents: iterable object có .id/.text/.metadata,
    vd index.store.VectorRecord hoặc ingest.chunkers.Chunk). IDF tính MỘT LẦN lúc __init__ trên TOÀN
    BỘ documents — search() với filters chỉ MASK kết quả sau khi có điểm (không rebuild BM25Okapi mỗi
    query, vốn tốn kém vì phải tính lại IDF). Đánh đổi: IDF không đặc thù riêng theo từng tập con đã
    filter (vd riêng theo từng store) — chấp nhận được, giống cách Postgres tsvector/Elasticsearch
    thường dùng IDF toàn corpus bất kể filter."""

    def __init__(self, documents):
        self._documents = list(documents)
        self._by_id = {d.id: d for d in self._documents}
        tokenized = [tokenize_vi(d.text) for d in self._documents]
        self._token_sets = [set(toks) for toks in tokenized]
        self._bm25 = BM25Okapi(tokenized) if tokenized else None

    def get(self, doc_id):
        return self._by_id.get(doc_id)

    def search(self, query, top_k, filters=None):
        """-> list[(id, score)] sắp giảm dần theo score, tối đa top_k, CHỈ gồm document khớp filters
        (nếu có) VÀ có ít nhất 1 token trùng query (kiểm tra TRỰC TIẾP bằng giao token set, KHÔNG dùng
        điều kiện score > 0 — rank_bm25.BM25Okapi dùng công thức IDF cổ điển (Robertson-Sparck-Jones),
        có thể ra 0 hoặc ÂM khi token xuất hiện ở TỪ MỘT NỬA corpus trở lên — corpus nhỏ/degenerate
        (vd chỉ 2 document, cùng chứa 1 token) rất dễ gặp trường hợp này; loại theo score > 0 sẽ loại
        NHẦM cả match thật)."""
        if self._bm25 is None:
            return []
        filters = filters or Filters()
        query_tokens = tokenize_vi(query)
        if not query_tokens:
            return []
        query_token_set = set(query_tokens)
        scores = self._bm25.get_scores(query_tokens)

        candidates = [
            i for i in range(len(self._documents))
            if self._token_sets[i] & query_token_set and _matches_filters(self._documents[i].metadata, filters)
        ]
        candidates.sort(key=lambda i: scores[i], reverse=True)
        return [(self._documents[i].id, float(scores[i])) for i in candidates[:top_k]]


# =====================================================================================
# RECIPROCAL RANK FUSION
# =====================================================================================

def reciprocal_rank_fusion(rankings, k=DEFAULT_RRF_K):
    """rankings: list[list[str]] — mỗi ranking là list id đã sắp best->worst (độ dài không cần bằng
    nhau, ranking rỗng hợp lệ). -> list[(id, fused_score)] sắp giảm dần theo fused_score."""
    scores = defaultdict(float)
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


# =====================================================================================
# HYBRID RETRIEVER — dense (VectorStore) + BM25Index, chạy song song, hợp nhất bằng RRF
# =====================================================================================

@dataclass
class HybridResult:
    id: str
    text: str
    metadata: dict = field(default_factory=dict)
    fused_score: float = 0.0
    dense_score: float = None
    bm25_score: float = None


class HybridRetriever:
    """Kết hợp index.store.VectorStore (dense) + BM25Index (lexical) qua reciprocal_rank_fusion.
    Chạy 2 truy vấn SONG SONG (ThreadPoolExecutor 2 worker) — dense có thể đi qua network (Postgres/
    Qdrant), BM25 thuần CPU trong tiến trình hiện tại; chạy đồng thời giảm latency tổng so với chạy
    tuần tự (dense_latency + bm25_latency -> max(dense_latency, bm25_latency))."""

    def __init__(self, vector_store, bm25_index, embedder, rrf_k=DEFAULT_RRF_K,
                 candidate_top_k=DEFAULT_CANDIDATE_TOP_K):
        self._vector_store = vector_store
        self._bm25_index = bm25_index
        self._embedder = embedder
        self._rrf_k = rrf_k
        self._candidate_top_k = candidate_top_k

    def search(self, query, top_k, filters=None):
        with ThreadPoolExecutor(max_workers=2) as executor:
            dense_future = executor.submit(self._dense_search, query, filters)
            bm25_future = executor.submit(self._bm25_index.search, query, self._candidate_top_k, filters)
            dense_results = dense_future.result()
            bm25_results = bm25_future.result()

        dense_by_id = {r.id: r for r in dense_results}
        dense_score_by_id = {r.id: r.score for r in dense_results}
        bm25_score_by_id = dict(bm25_results)

        fused = reciprocal_rank_fusion(
            [[r.id for r in dense_results], [doc_id for doc_id, _ in bm25_results]], k=self._rrf_k,
        )

        results = []
        for doc_id, fused_score in fused[:top_k]:
            source = dense_by_id.get(doc_id) or self._bm25_index.get(doc_id)
            if source is None:
                continue  # không nên xảy ra (id chỉ đến từ 1 trong 2 nhánh trên), giữ phòng thủ
            results.append(HybridResult(
                id=doc_id, text=source.text, metadata=dict(source.metadata or {}),
                fused_score=fused_score,
                dense_score=dense_score_by_id.get(doc_id),
                bm25_score=bm25_score_by_id.get(doc_id),
            ))
        return results

    def _dense_search(self, query, filters):
        query_vec = self._embedder.embed([query])[0]
        return self._vector_store.search(query_vec, self._candidate_top_k, filters=filters)
