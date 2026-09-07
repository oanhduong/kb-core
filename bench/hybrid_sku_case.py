"""bench/hybrid_sku_case.py — chứng minh CỤ THỂ hiện tượng nêu ở đầu index/hybrid.py: catalog bán lẻ
có nhiều sản phẩm cùng dòng (cùng category, mô tả gần giống hệt nhau) chỉ khác SKU — dense THUẦN dễ
trả về đúng category nhưng SAI SKU; hybrid (BM25 + dense qua RRF, xem index/hybrid.py) cải thiện ĐÁNG
KỂ vì BM25 khớp từ vựng CHÍNH XÁC lên SKU (token hiếm, IDF cao).

Golden set: 10 category, mỗi category N_VARIANTS=5 chunk gần như GIỐNG HỆT nhau (chỉ khác SKU) — 10
câu hỏi, mỗi câu hỏi nêu rõ 1 SKU cụ thể (chunk đúng = "target variant" của category đó, luôn là variant
thứ 3 để không trùng vị trí đầu/cuối danh sách). recall@1 = tỷ lệ câu hỏi mà top-1 kết quả ĐÚNG chunk_id
mong đợi.

KẾT QUẢ THẬT (chạy `python -m bench.hybrid_sku_case`, tất định — xem SemanticOnlyEmbedder/_noise_vector,
không đổi giữa các lần chạy): dense recall@1 = 0.20 (2/10), hybrid recall@1 = 0.60 (6/10) — hybrid gấp
3 lần dense, nhưng KHÔNG đạt 100%. Lý do trung thực (không che giấu): RRF chỉ dùng RANK, không dùng độ
lớn điểm thô — khi BM25 cho correct rank=1 áp đảo (điểm cao hơn hẳn) nhưng ứng viên SAI gần đó (rank=2,
điểm thấp hơn RẤT NHIỀU) LẠI được dense (do nhiễu ngẫu nhiên, xem _noise_vector) xếp rank=1, RRF k=60
(hằng số tối ưu cho danh sách kết quả DÀI, ~hàng trăm/nghìn — xem docstring index/hybrid.py) làm phẳng
chênh lệch rank 1 so với rank 2-3 trên một pool CHỈ 5 ứng viên gần giống hệt nhau, nên đôi khi vẫn để
lọt ứng viên sai. Đây là giới hạn THẬT của RRF (không phải bug), đáng ghi nhận: hybrid tốt hơn dense
RÕ RỆT trên case này nhưng không phải "chìa khoá vạn năng" — production có thể cần thêm boost khớp
chính xác (exact-match) cho token dạng SKU nếu cần recall cao hơn nữa, nằm NGOÀI phạm vi RRF thuần.

Dense-only dùng SemanticOnlyEmbedder (bọc index.embeddings.HashingEmbedder, loại token dạng SKU khỏi
text TRƯỚC khi hash, cộng thêm nhiễu nhỏ tất định — xem _noise_vector) — mô phỏng TẤT ĐỊNH (không cần
tải model thật, chạy lại được, không phụ thuộc PYTHONHASHSEED) tính chất THẬT của nhiều embedding
model ngữ nghĩa: token mã sản phẩm (hiếm gặp, không mang nghĩa từ vựng thông thường) đóng góp rất ít
vào không gian embedding so với từ mô tả chung, và 2 văn bản gần giống hệt nhau không cho vector CHÍNH
XÁC bằng nhau trong thực tế — đây là nguyên nhân well-known khiến dense-only retrieval nhầm giữa các
sản phẩm cùng dòng. index/rerank.py, index/store.py (dense thật qua Postgres/Qdrant) không dùng
embedder này — nó CHỈ tồn tại ở bench này để tái hiện vấn đề một cách reproducible.

Cách dùng:
    python -m bench.hybrid_sku_case
    python -m bench.hybrid_sku_case --out bench/hybrid_sku_case.md
"""
import argparse
import math
import os
import random
import re
import sys
from dataclasses import dataclass

from index.embeddings import HashingEmbedder
from index.hybrid import BM25Index, HybridRetriever
from index.store import Filters, SearchResult, VectorRecord, VectorStore, _matches_filters

DEFAULT_OUT_MD = os.path.join("bench", "hybrid_sku_case.md")
N_VARIANTS = 5
TARGET_VARIANT_INDEX = 2  # variant thứ 3 (0-based) — không phải đầu/cuối danh sách, tránh thiên vị

CATEGORIES = [
    ("Tai nghe Bluetooth chống ồn", "TN"),
    ("Nồi chiên không dầu", "NC"),
    ("Bàn phím cơ RGB", "BP"),
    ("Sạc dự phòng 20000mAh", "SD"),
    ("Loa di động chống nước", "LD"),
    ("Ấm siêu tốc 1.8L", "AS"),
    ("Chuột không dây", "CK"),
    ("Máy xay sinh tố đa năng", "MX"),
    ("Đèn bàn LED chống cận", "DB"),
    ("Ổ cắm điện thông minh wifi", "OC"),
]


@dataclass
class GoldenQuery:
    question: str
    expected_chunk_id: str
    category: str


def _sku(prefix, variant_index):
    return f"{prefix}-{100 + variant_index}"


def _chunk_id(prefix, variant_index):
    return f"{prefix}-chunk-{variant_index}"


def _chunk_text(category, sku):
    return f"{category}, mã sản phẩm {sku}, hàng chính hãng, bảo hành 12 tháng, giao nhanh trong ngày."


def build_corpus():
    """-> list[VectorRecord] (vector=None — bench tự embed riêng theo từng embedder, xem run())."""
    corpus = []
    for category, prefix in CATEGORIES:
        for i in range(N_VARIANTS):
            sku = _sku(prefix, i)
            corpus.append(VectorRecord(
                id=_chunk_id(prefix, i), vector=None, text=_chunk_text(category, sku),
                metadata={"doc_type": "catalog", "sku": [sku], "is_active": True},
            ))
    return corpus


def build_golden_queries():
    """-> list[GoldenQuery], 10 câu (1/category), mỗi câu nêu rõ SKU của variant mục tiêu."""
    queries = []
    for category, prefix in CATEGORIES:
        sku = _sku(prefix, TARGET_VARIANT_INDEX)
        queries.append(GoldenQuery(
            question=f"Cho tôi thông tin sản phẩm {category} mã {sku}",
            expected_chunk_id=_chunk_id(prefix, TARGET_VARIANT_INDEX),
            category=category,
        ))
    return queries


# =====================================================================================
# SemanticOnlyEmbedder — xem docstring đầu file
# =====================================================================================

_SKU_LIKE_RE = re.compile(r"\b[A-Za-zĐđ]{2,6}-?\d{2,6}(?:-[A-Za-z0-9]+)?\b")


def _noise_vector(seed_key, dim, magnitude):
    """Nhiễu nhỏ, TẤT ĐỊNH (seed = hash nội dung gốc, không dùng random.random()/time — reproducible
    giữa các lần chạy) mô phỏng imprecision THẬT của embedding model ngữ nghĩa: 2 văn bản gần giống
    hệt nhau (chỉ khác SKU đã bị loại khỏi vector) vẫn không cho ra vector CHÍNH XÁC bằng nhau trong
    thực tế — luôn có sai khác nhỏ, không tương quan với thứ tự BM25 xếp hạng theo SKU. Không có
    nhiễu này, mọi variant "giống hệt sau khi bỏ SKU" sẽ cho vector TRÙNG TUYỆT ĐỐI -> tie-break rơi
    về thứ tự chèn corpus (dict) — thứ tự đó TRÙNG với thứ tự BM25 dùng để phá tie (cùng list gốc),
    khiến 2 nhánh dense/BM25 "giả vờ" độc lập nhưng thật ra cùng chọn 1 ứng viên sai — không phản ánh
    đúng thực tế (2 model độc lập không có lý do gì để tie-break giống hệt nhau)."""
    rng = random.Random(seed_key)
    return [rng.uniform(-magnitude, magnitude) for _ in range(dim)]


class SemanticOnlyEmbedder:
    def __init__(self, dim=64, noise_magnitude=0.15):
        self._inner = HashingEmbedder(dim=dim)
        self._dim = dim
        self._noise_magnitude = noise_magnitude

    def embed(self, texts):
        stripped = [_SKU_LIKE_RE.sub(" ", t) for t in texts]
        vectors = self._inner.embed(stripped)
        return [
            [v + n for v, n in zip(vec, _noise_vector(text, self._dim, self._noise_magnitude))]
            for text, vec in zip(texts, vectors)
        ]


# =====================================================================================
# InMemoryVectorStore — cosine similarity thuần Python, CHỈ dùng cho bench/test (không cần
# Postgres/Qdrant thật) — pre-filter giống PgVectorStore/QdrantStore.
# =====================================================================================

def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class InMemoryVectorStore(VectorStore):
    def __init__(self):
        self._records = {}

    def upsert(self, records):
        for r in records:
            self._records[r.id] = r

    def search(self, query_vec, top_k, filters=None):
        filters = filters or Filters()
        pool = [r for r in self._records.values() if _matches_filters(r.metadata, filters)]
        scored = sorted(pool, key=lambda r: _cosine(query_vec, r.vector), reverse=True)
        return [
            SearchResult(id=r.id, score=_cosine(query_vec, r.vector), text=r.text, metadata=r.metadata)
            for r in scored[:top_k]
        ]

    def delete(self, ids):
        for i in ids:
            self._records.pop(i, None)

    def count(self):
        return len(self._records)


# =====================================================================================
# CHẠY
# =====================================================================================

def run(progress_fn=None):
    """-> dict {n_queries, dense_hits, hybrid_hits, dense_recall_at_1, hybrid_recall_at_1, rows}."""
    progress_fn = progress_fn or (lambda msg: print(msg, file=sys.stderr))
    corpus = build_corpus()
    queries = build_golden_queries()

    embedder = SemanticOnlyEmbedder()
    dense_store = InMemoryVectorStore()
    vectors = embedder.embed([c.text for c in corpus])
    dense_store.upsert([
        VectorRecord(id=c.id, vector=v, text=c.text, metadata=c.metadata) for c, v in zip(corpus, vectors)
    ])
    bm25_index = BM25Index(corpus)
    # candidate_top_k = toàn bộ corpus (50 chunk) — đảm bảo nhánh dense KHÔNG loại chunk đúng ra khỏi
    # ranking của nó chỉ vì rank thấp (dense vẫn xếp mọi variant CÙNG category lên trên các category
    # khác — chỉ SAI thứ tự nội bộ NHÓM đó, xem docstring _FLAVOR_TEXTS); nếu candidate_top_k quá nhỏ,
    # RRF mất hẳn tín hiệu BM25 cho chunk không lọt top dense — không phản ánh đúng hành vi hybrid
    # thật (production dùng candidate_top_k=50 trên corpus lớn hơn NHIỀU, tỷ lệ oversample tương tự).
    hybrid = HybridRetriever(dense_store, bm25_index, embedder, candidate_top_k=len(corpus))

    dense_hits, hybrid_hits, rows = 0, 0, []
    for q in queries:
        query_vec = embedder.embed([q.question])[0]
        dense_top1 = dense_store.search(query_vec, top_k=1)
        dense_correct = bool(dense_top1) and dense_top1[0].id == q.expected_chunk_id

        hybrid_top1 = hybrid.search(q.question, top_k=1)
        hybrid_correct = bool(hybrid_top1) and hybrid_top1[0].id == q.expected_chunk_id

        dense_hits += int(dense_correct)
        hybrid_hits += int(hybrid_correct)
        rows.append({
            "category": q.category, "question": q.question, "expected_id": q.expected_chunk_id,
            "dense_top1_id": dense_top1[0].id if dense_top1 else None, "dense_correct": dense_correct,
            "hybrid_top1_id": hybrid_top1[0].id if hybrid_top1 else None, "hybrid_correct": hybrid_correct,
        })
        progress_fn(
            f"  [{q.category}] dense={'ĐÚNG' if dense_correct else 'SAI (' + str(dense_top1[0].id if dense_top1 else None) + ')'} "
            f"hybrid={'ĐÚNG' if hybrid_correct else 'SAI'}"
        )

    result = {
        "n_queries": len(queries), "dense_hits": dense_hits, "hybrid_hits": hybrid_hits,
        "dense_recall_at_1": dense_hits / len(queries), "hybrid_recall_at_1": hybrid_hits / len(queries),
        "rows": rows,
    }
    progress_fn(
        f"[hybrid_sku_case] dense recall@1={result['dense_recall_at_1']:.2f} "
        f"hybrid recall@1={result['hybrid_recall_at_1']:.2f} ({len(queries)} câu)"
    )
    return result


def _write_report(result, out_path):
    lines = [
        "# Hybrid retrieval — case SKU trong catalog nhiều sản phẩm cùng dòng",
        "",
        f"Golden set {result['n_queries']} câu hỏi (1/category, {N_VARIANTS} variant/category gần "
        "giống hệt nhau, chỉ khác SKU) — recall@1 = tỷ lệ câu hỏi mà top-1 kết quả đúng chunk_id "
        "mong đợi. Xem bench/hybrid_sku_case.py cho chi tiết corpus/query.",
        "",
        "| Mode | Recall@1 | Đúng / Tổng |",
        "|---|---:|---:|",
        f"| Dense thuần (SemanticOnlyEmbedder) | {result['dense_recall_at_1']:.2f} | {result['dense_hits']}/{result['n_queries']} |",
        f"| Hybrid (BM25 + dense, RRF k=60) | {result['hybrid_recall_at_1']:.2f} | {result['hybrid_hits']}/{result['n_queries']} |",
        "",
        "| Category | Câu hỏi | Expected id | Dense top-1 | Hybrid top-1 |",
        "|---|---|---|---|---|",
    ]
    for row in result["rows"]:
        dense_mark = "✅" if row["dense_correct"] else "❌"
        hybrid_mark = "✅" if row["hybrid_correct"] else "❌"
        lines.append(
            f"| {row['category']} | {row['question']} | `{row['expected_id']}` | "
            f"{dense_mark} `{row['dense_top1_id']}` | {hybrid_mark} `{row['hybrid_top1_id']}` |"
        )
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description="Bench: dense thuần vs hybrid trên golden set SKU.")
    parser.add_argument("--out", default=DEFAULT_OUT_MD)
    args = parser.parse_args(argv)

    result = run()
    _write_report(result, args.out)
    print(f"\nĐã ghi báo cáo vào {args.out}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
