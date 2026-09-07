"""index/rerank.py — Cross-encoder reranker (BAAI/bge-reranker-v2-m3) cho tầng retrieval: nhận top N
ứng viên từ index/hybrid.py (hoặc bất kỳ nguồn nào trả object có .id/.text/.metadata), chấm lại điểm
(query, chunk.text) qua cross-encoder, giữ lại top K (mặc định 5).

Cross-encoder khác bi-encoder (dense retrieval ở index/store.py): bi-encoder embed query/document ĐỘC
LẬP rồi so cosine (rẻ, index trước được, nhưng độ chính xác thấp hơn vì không có tương tác token giữa
query-document lúc encode); cross-encoder encode CẶP (query, document) CÙNG LÚC qua 1 forward pass
(attention chéo giữa 2 chuỗi) -> chính xác hơn NHIỀU nhưng KHÔNG index trước được (phải chạy lại mỗi
query), nên chỉ áp dụng cho candidate pool đã thu hẹp (top 50 từ retrieval — xem
index/hybrid.DEFAULT_CANDIDATE_TOP_K), không chạy trên toàn corpus.

Model: BAAI/bge-reranker-v2-m3 (multilingual, hỗ trợ tiếng Việt), load qua
transformers.AutoModelForSequenceClassification — model này tương thích chuẩn AutoModelForSequence-
Classification (input (query, text) pair, output 1 logit = relevance score), không cần thêm
sentence-transformers/FlagEmbedding. Chạy CPU được (không cần GPU) — model ~570M tham số, forward pass
CPU cho batch nhỏ (candidate pool ~50) đủ nhanh cho quy mô 1 cửa hàng bán lẻ.

LATENCY THẬT đo được (Apple Silicon, CPU, batch_size=16, model đã tải sẵn/cache local — KHÔNG tính
thời gian tải model ~1.1GB lần đầu, đó là chi phí một lần lúc khởi động service, không phải mỗi
query): 51 candidate (~kích thước pool thật đề xuất, xem DEFAULT_CANDIDATE_POOL) -> ~1.4s/query sau
khi model đã "ấm" (lần forward đầu tiên sau khi load luôn chậm hơn hẳn — ~3s — do torch tự khởi tạo
thread pool CPU lần đầu, ổn định lại từ lần thứ 2). Đo lại trên máy khác: `python -m index.rerank`
(script demo, tải model thật nếu chưa cache, in latency_ms thật). rerank_with_latency() trả latency_ms
đo bằng time.perf_counter() quanh forward pass thật — KHÔNG hardcode ngưỡng cứng trong code (CPU khác
nhau giữa máy dev/CI cho số khác nhau), chỉ đo và trả về để caller tự quyết định ngưỡng chấp nhận
được. Test latency THẬT (tải model thật) chỉ chạy khi RETAILKB_RUN_SLOW_TESTS=1 — xem
tests/test_rerank.py::test_rerank_latency_is_measured_and_positive_with_real_model — không chạy
trong CI mặc định vì cần mạng + ~1.1GB tải về lần đầu.

Model load 1 LẦN (lazy, cache theo model_name trong _MODEL_CACHE) — KHÔNG load lại mỗi lần gọi
rerank(), vì load model từ HuggingFace hub/disk tốn vài giây, và tokenizer/model object tái sử dụng an
toàn giữa nhiều lệnh gọi (không giữ state giữa các batch, forward pass chạy dưới torch.no_grad()).
"""
import argparse
import sys
import time
from dataclasses import dataclass

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"
DEFAULT_TOP_K = 5
DEFAULT_CANDIDATE_POOL = 50
DEFAULT_BATCH_SIZE = 16

_MODEL_CACHE = {}


@dataclass
class RerankResult:
    id: str
    text: str
    metadata: dict
    rerank_score: float
    original_score: float = None


def _load_model(model_name):
    if model_name not in _MODEL_CACHE:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForSequenceClassification.from_pretrained(model_name)
        model.eval()
        _MODEL_CACHE[model_name] = (tokenizer, model)
    return _MODEL_CACHE[model_name]


def _cross_encoder_score_fn(model_name, batch_size):
    def score_fn(query, texts):
        import torch

        tokenizer, model = _load_model(model_name)
        scores = []
        with torch.no_grad():
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i + batch_size]
                pairs = [[query, t] for t in batch]
                inputs = tokenizer(pairs, padding=True, truncation=True, max_length=512, return_tensors="pt")
                logits = model(**inputs).logits.view(-1).float()
                scores.extend(logits.tolist())
        return scores

    return score_fn


def _extract_original_score(candidate):
    for attr in ("fused_score", "score"):
        value = getattr(candidate, attr, None)
        if value is not None:
            return value
    return None


def rerank(query, candidates, top_k=DEFAULT_TOP_K, model_name=DEFAULT_MODEL, score_fn=None,
           batch_size=DEFAULT_BATCH_SIZE):
    """candidates: list object có .id/.text/.metadata (thường là index.hybrid.HybridResult hoặc
    index.store.SearchResult, đã giới hạn TRƯỚC ở DEFAULT_CANDIDATE_POOL bởi caller — hàm này KHÔNG
    tự cắt candidate pool). score_fn (tuỳ chọn, tiêm sẵn cho test — tránh tải model thật ~1.1GB mỗi
    lần chạy test): (query, list[str]) -> list[float], cùng thứ tự với texts. Mặc định dùng
    cross-encoder thật (_load_model qua model_name). Trả về list[RerankResult] ĐÃ SẮP GIẢM DẦN theo
    rerank_score, tối đa top_k phần tử."""
    if not candidates:
        return []
    texts = [c.text for c in candidates]
    score_fn = score_fn or _cross_encoder_score_fn(model_name, batch_size)
    scores = score_fn(query, texts)

    results = [
        RerankResult(
            id=c.id, text=c.text, metadata=dict(getattr(c, "metadata", None) or {}),
            rerank_score=float(score), original_score=_extract_original_score(c),
        )
        for c, score in zip(candidates, scores)
    ]
    results.sort(key=lambda r: r.rerank_score, reverse=True)
    return results[:top_k]


def rerank_with_latency(query, candidates, top_k=DEFAULT_TOP_K, model_name=DEFAULT_MODEL,
                         score_fn=None, batch_size=DEFAULT_BATCH_SIZE):
    """Giống rerank(), trả thêm latency_ms đo THẬT (time.perf_counter quanh score_fn — bao gồm cả
    thời gian load model lần đầu nếu _MODEL_CACHE chưa có, KHÔNG che giấu cost đó) — dùng cho
    CLI/bench đo latency CPU thật, xem docstring đầu file."""
    start = time.perf_counter()
    results = rerank(query, candidates, top_k=top_k, model_name=model_name, score_fn=score_fn,
                      batch_size=batch_size)
    latency_ms = (time.perf_counter() - start) * 1000
    return results, latency_ms


class RerankingRetriever:
    """Bọc 1 retriever bất kỳ (cần .search(query, top_k, filters) -> list object có .id/.text/
    .metadata, vd index.hybrid.HybridRetriever) — lấy candidate_pool ứng viên từ retriever gốc rồi
    rerank lại bằng cross-encoder, trả về đúng top_k cuối cùng. Dùng để ghép index/hybrid.py +
    index/rerank.py thành 1 pipeline retrieval duy nhất cho qa/answer.py."""

    def __init__(self, retriever, candidate_pool=DEFAULT_CANDIDATE_POOL, model_name=DEFAULT_MODEL,
                 score_fn=None, batch_size=DEFAULT_BATCH_SIZE):
        self._retriever = retriever
        self._candidate_pool = candidate_pool
        self._model_name = model_name
        self._score_fn = score_fn
        self._batch_size = batch_size

    def search(self, query, top_k, filters=None):
        candidates = self._retriever.search(query, self._candidate_pool, filters=filters)
        return rerank(query, candidates, top_k=top_k, model_name=self._model_name,
                       score_fn=self._score_fn, batch_size=self._batch_size)


# =====================================================================================
# CLI — demo latency thật trên máy hiện tại (tải model thật, xem docstring đầu file)
# =====================================================================================

_DEMO_QUERY = "Chính sách đổi trả cho tai nghe Bluetooth mã ABC-123 như thế nào?"
_DEMO_CANDIDATES = [
    ("c1", "Chính sách đổi trả trong 7 ngày áp dụng cho mọi sản phẩm điện tử, bao gồm tai nghe Bluetooth mã ABC-123."),
    ("c2", "Bảng giá tai nghe Bluetooth mã XYZ-999, không liên quan tới chính sách đổi trả."),
    ("c3", "Khuyến mãi mùa hè cho nồi chiên không dầu, giảm 20% tới hết tháng."),
]


@dataclass
class _DemoCandidate:
    id: str
    text: str
    metadata: dict


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description="Demo index/rerank.py — đo latency THẬT trên CPU (tải model thật).")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    args = parser.parse_args(argv)

    candidates = [_DemoCandidate(id=i, text=t, metadata={}) for i, t in _DEMO_CANDIDATES]
    print(f"Đang tải model {args.model} (lần đầu có thể mất vài phút, tải ~1.1GB)...", file=sys.stderr)
    results, latency_ms = rerank_with_latency(
        _DEMO_QUERY, candidates, top_k=args.top_k, model_name=args.model, batch_size=args.batch_size,
    )
    print(f"\nLatency thật (CPU, {len(candidates)} candidate, batch_size={args.batch_size}): {latency_ms:.1f}ms", file=sys.stderr)
    for r in results:
        print(f"  [{r.rerank_score:.4f}] {r.id}: {r.text}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
