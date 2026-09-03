"""index/bench_prefilter.py — so sánh pre-filter (WHERE SQL / Qdrant payload filter chạy TRƯỚC ANN
search) với post-filter (ANN search KHÔNG filter, lấy candidate pool lớn hơn top_k rồi lọc lại bằng
metadata SAU) trên CÙNG một query set, đo recall@top_k và p95 latency — xem index/store.py (tham số
`prefilter` của search(), chỉ tồn tại để phục vụ bench này).

Case cụ thể theo đúng đặc thù retail (câu hỏi luôn gắn với 1 store + 1 thời điểm): sinh synthetic
corpus N_STORES cửa hàng x N_SKUS_PER_STORE SKU/cửa hàng (mặc định 150 x 20 = 3000 chunk — "vài
nghìn"). Text mỗi chunk dạng "Giá {tên SKU} tại cửa hàng {store_code}: ...". CÙNG 1 SKU ở CÁC cửa
hàng khác nhau có văn bản gần như giống hệt nhau (chỉ khác mã cửa hàng) -> embedding của chúng rất
gần nhau. Đây CHÍNH LÀ tình huống khiến post-filter kém: hỏi giá 1 SKU cụ thể tại 1 store cụ thể,
top-K gần nhất theo similarity thuần rất dễ toàn là "đúng SKU nhưng SAI store" (tới N_STORES cửa
hàng cùng bán SKU đó) — filter store_code SAU khi đã search xong dễ lọc mất luôn candidate đúng nếu
candidate pool (oversample_factor * top_k) không đủ lớn so với N_STORES. Pre-filter tránh hẳn vấn đề
này: WHERE store_code=... chạy TRƯỚC, ANN chỉ cần tìm trong ~N_SKUS_PER_STORE (vài chục) chunk của
đúng cửa hàng đó, KHÔNG cạnh tranh với N_STORES-1 cửa hàng khác cùng bán SKU giống hệt.

Query set: n_queries cặp (store, sku) ngẫu nhiên (seed cố định để tái lập); mỗi query có ĐÚNG 1
chunk đúng (ground truth), nên recall@top_k = tỷ lệ query mà chunk đúng nằm trong top_k trả về.

Dùng HashingEmbedder (index/embeddings.py) — placeholder không cần model/API/mạng, nhưng vẫn tạo ra
đúng tính chất "câu gần giống nhau -> vector gần nhau" mà bench cần (bench đo CƠ CHẾ filter, không
đo chất lượng embedding).

Cách dùng (cần ops/docker-compose.yml đang chạy):
    python -m index.bench_prefilter
    python -m index.bench_prefilter --backend pgvector --n-stores 200 --n-skus 30 --n-queries 50
    python -m index.bench_prefilter --out bench/prefilter_vs_postfilter.md
"""
import argparse
import os
import random
import sys
import time
from dataclasses import dataclass

from index.embeddings import DEFAULT_DIM, HashingEmbedder
from index.store import DEFAULT_OVERSAMPLE_FACTOR, Filters, PgVectorStore, QdrantStore, VectorRecord

DEFAULT_N_STORES = 150
DEFAULT_N_SKUS_PER_STORE = 20
DEFAULT_N_QUERIES = 50
DEFAULT_TOP_K = 5
DEFAULT_SEED = 42
BENCH_TABLE = "bench_prefilter"
BENCH_COLLECTION = "bench_prefilter"
DEFAULT_OUT_MD = "bench/prefilter_vs_postfilter.md"


# =====================================================================================
# SYNTHETIC CORPUS
# =====================================================================================

@dataclass
class BenchQuery:
    text: str
    target_store: str
    target_sku: str
    expected_id: str


def _store_code(i):
    return f"ST{i:04d}"


def _sku_code(j):
    return f"SKU{j:03d}"


def _sku_name(j):
    return f"Sản phẩm số {j}"


def _chunk_id(store_code, sku_code):
    return f"{store_code}-{sku_code}"


def _make_corpus(n_stores, n_skus_per_store):
    """list[dict] (chưa embed) — mỗi item {id, text, metadata}. Metadata đúng shape mà
    ingest.chunkers.normalize_chunk_metadata sinh ra (store_code, sku list, doc_type, is_active,
    effective_date), để bench phản ánh đúng dữ liệu thật sẽ đi qua VectorStore."""
    items = []
    for i in range(n_stores):
        store_code = _store_code(i)
        for j in range(n_skus_per_store):
            sku_code = _sku_code(j)
            price = 100_000 + j * 5_000 + i * 37
            text = (
                f"Giá {_sku_name(j)} (mã {sku_code}) tại cửa hàng {store_code}: {price:,} VND. "
                f"Áp dụng từ 2026-01-01."
            )
            items.append({
                "id": _chunk_id(store_code, sku_code),
                "text": text,
                "metadata": {
                    "doc_type": "price_list",
                    "store_code": store_code,
                    "sku": [sku_code],
                    "is_active": True,
                    "effective_date": "2026-01-01",
                },
            })
    return items


def _make_queries(n_stores, n_skus_per_store, n_queries, seed):
    rng = random.Random(seed)
    queries = []
    for _ in range(n_queries):
        i = rng.randrange(n_stores)
        j = rng.randrange(n_skus_per_store)
        store_code, sku_code = _store_code(i), _sku_code(j)
        text = f"Giá {_sku_name(j)} tại cửa hàng {store_code} bao nhiêu tiền?"
        queries.append(BenchQuery(
            text=text, target_store=store_code, target_sku=sku_code,
            expected_id=_chunk_id(store_code, sku_code),
        ))
    return queries


# =====================================================================================
# CHẠY 1 BACKEND
# =====================================================================================

def _percentile(values, p):
    if not values:
        return float("nan")
    xs = sorted(values)
    idx = min(len(xs) - 1, int(round(p * (len(xs) - 1))))
    return xs[idx]


def _build_store(backend, dim, **kwargs):
    if backend == "pgvector":
        return PgVectorStore(dim=dim, table=BENCH_TABLE, **kwargs)
    if backend == "qdrant":
        return QdrantStore(dim=dim, collection=BENCH_COLLECTION, **kwargs)
    raise ValueError(f"Backend không hợp lệ: {backend!r} (hỗ trợ: pgvector, qdrant)")


def _run_mode(store, queries, embedder, top_k, oversample_factor, prefilter, progress_fn):
    hits, latencies = 0, []
    for q in queries:
        qvec = embedder.embed([q.text])[0]
        filters = Filters(store_code=q.target_store, is_active=True)
        start = time.perf_counter()
        results = store.search(
            qvec, top_k, filters=filters, prefilter=prefilter, oversample_factor=oversample_factor,
        )
        latencies.append((time.perf_counter() - start) * 1000)
        if any(r.id == q.expected_id for r in results):
            hits += 1
    mode = "pre-filter" if prefilter else "post-filter"
    progress_fn(f"  [{mode}] recall@{top_k}={hits / len(queries):.3f} p95={_percentile(latencies, 0.95):.2f}ms")
    return {
        "mode": mode,
        "recall": hits / len(queries),
        "hits": hits,
        "n_queries": len(queries),
        "p50_ms": _percentile(latencies, 0.50),
        "p95_ms": _percentile(latencies, 0.95),
        "mean_ms": sum(latencies) / len(latencies) if latencies else float("nan"),
    }


def run_backend(
    backend, n_stores, n_skus_per_store, n_queries, top_k, oversample_factor, seed, dim, progress_fn, **store_kwargs,
):
    progress_fn(f"[{backend}] khởi tạo corpus {n_stores}x{n_skus_per_store}={n_stores * n_skus_per_store} chunk...")
    embedder = HashingEmbedder(dim=dim)
    store = _build_store(backend, dim=dim, **store_kwargs)
    store.drop_schema()
    store.close()
    store = _build_store(backend, dim=dim, **store_kwargs)  # tạo lại schema sạch sau khi drop

    corpus = _make_corpus(n_stores, n_skus_per_store)
    vectors = embedder.embed([item["text"] for item in corpus])
    records = [
        VectorRecord(id=item["id"], vector=vec, text=item["text"], metadata=item["metadata"])
        for item, vec in zip(corpus, vectors)
    ]
    store.upsert(records)
    progress_fn(f"[{backend}] đã nạp {store.count()} chunk. Chạy {n_queries} query...")

    queries = _make_queries(n_stores, n_skus_per_store, n_queries, seed)
    pre = _run_mode(store, queries, embedder, top_k, oversample_factor, True, progress_fn)
    post = _run_mode(store, queries, embedder, top_k, oversample_factor, False, progress_fn)
    store.close()
    return {
        "backend": backend, "n_stores": n_stores, "n_skus_per_store": n_skus_per_store,
        "n_chunks": len(corpus), "top_k": top_k, "oversample_factor": oversample_factor,
        "pre_filter": pre, "post_filter": post,
    }


# =====================================================================================
# BÁO CÁO
# =====================================================================================

def _write_report(all_results, out_path):
    lines = ["# Pre-filter vs. post-filter — bench", ""]
    lines.append(
        "Case: hỏi giá 1 SKU tại 1 store cụ thể — filter (store_code) thu hẹp candidate còn "
        "`n_skus_per_store` chunk trong tổng `n_chunks` chunk. Mỗi query có đúng 1 chunk đúng "
        "(ground truth); recall@top_k = tỷ lệ query tìm thấy đúng chunk đó trong top_k kết quả."
    )
    lines.append("")
    lines.append("| Backend | n_chunks | top_k | oversample | Mode | Recall@k | p50 (ms) | p95 (ms) |")
    lines.append("|---|---:|---:|---:|---|---:|---:|---:|")
    for r in all_results:
        for mode_result in (r["pre_filter"], r["post_filter"]):
            lines.append(
                f"| {r['backend']} | {r['n_chunks']} | {r['top_k']} | {r['oversample_factor']} "
                f"| {mode_result['mode']} | {mode_result['recall']:.3f} "
                f"| {mode_result['p50_ms']:.2f} | {mode_result['p95_ms']:.2f} |"
            )
    lines.append("")
    for r in all_results:
        delta = r["pre_filter"]["recall"] - r["post_filter"]["recall"]
        lines.append(
            f"- **{r['backend']}**: pre-filter recall {r['pre_filter']['recall']:.3f} vs. "
            f"post-filter {r['post_filter']['recall']:.3f} (chênh lệch {delta:+.3f}); "
            f"p95 latency pre-filter {r['pre_filter']['p95_ms']:.2f}ms vs. "
            f"post-filter {r['post_filter']['p95_ms']:.2f}ms."
        )
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# =====================================================================================
# CLI
# =====================================================================================

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description="So sánh pre-filter vs post-filter trên pgvector/qdrant.")
    parser.add_argument("--backend", default="pgvector,qdrant", help="Danh sách backend, phân cách bởi dấu phẩy.")
    parser.add_argument("--n-stores", type=int, default=DEFAULT_N_STORES)
    parser.add_argument("--n-skus", type=int, default=DEFAULT_N_SKUS_PER_STORE)
    parser.add_argument("--n-queries", type=int, default=DEFAULT_N_QUERIES)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--oversample-factor", type=int, default=DEFAULT_OVERSAMPLE_FACTOR)
    parser.add_argument("--embed-dim", type=int, default=DEFAULT_DIM)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--pg-dsn", default=None)
    parser.add_argument("--qdrant-url", default=None)
    parser.add_argument("--out", default=DEFAULT_OUT_MD)
    args = parser.parse_args(argv)

    def progress_fn(msg):
        print(msg, file=sys.stderr)

    backends = [b.strip() for b in args.backend.split(",") if b.strip()]
    all_results = []
    for backend in backends:
        kwargs = {}
        if backend == "pgvector" and args.pg_dsn:
            kwargs["dsn"] = args.pg_dsn
        if backend == "qdrant" and args.qdrant_url:
            kwargs["url"] = args.qdrant_url
        try:
            result = run_backend(
                backend, args.n_stores, args.n_skus, args.n_queries, args.top_k,
                args.oversample_factor, args.seed, args.embed_dim, progress_fn, **kwargs,
            )
        except Exception as e:
            print(f"Lỗi khi chạy backend {backend}: {e}", file=sys.stderr)
            continue
        all_results.append(result)

    if not all_results:
        print("Không có backend nào chạy thành công.", file=sys.stderr)
        return 1

    _write_report(all_results, args.out)
    print(f"\nĐã ghi báo cáo vào {args.out}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
