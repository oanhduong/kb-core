"""index/reindex.py — nạp toàn bộ chunk (data/chunks/<strategy>/chunks.jsonl, xem ingest/chunkers.py)
vào 1 VectorStore (index/store.py: pgvector hoặc qdrant).

Idempotent: upsert theo chunk_id — PgVectorStore dùng ON CONFLICT DO UPDATE, QdrantStore băm
chunk_id sang point id ổn định (_qdrant_point_id) rồi upsert đè — chạy lại nhiều lần trên CÙNG input
không tạo bản ghi trùng, không cần tự dedup.

Progress: in tiến độ theo batch ra stderr. Resume (--resume): đọc file trạng thái
'<input>.reindex_state.<backend>.json' (ghi qua file tạm + os.replace, cùng kỹ thuật với
security/gateway.py::QueueApprovalBackend._save) để bỏ qua các chunk đã upsert ở lần chạy trước —
GIẢ ĐỊNH input không đổi thứ tự/nội dung giữa các lần chạy (chunks.jsonl do ingest/chunkers.py ghi
1 lần, không sửa tay).

Cách dùng:
    python -m index.reindex --backend pgvector --input data/chunks/fixed/chunks.jsonl
    python -m index.reindex --backend qdrant --input data/chunks/fixed/chunks.jsonl --batch-size 200
    python -m index.reindex --backend pgvector --input data/chunks/fixed/chunks.jsonl --resume
"""
import argparse
import json
import os
import sys

from ingest.chunkers import Chunk
from index.embeddings import DEFAULT_DIM, HashingEmbedder
from index.store import PgVectorStore, QdrantStore, VectorRecord

DEFAULT_BATCH_SIZE = 100
DEFAULT_BACKEND = "pgvector"


def _load_chunks(input_path):
    chunks = []
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(Chunk(**json.loads(line)))
    return chunks


def _state_path(input_path, backend):
    return f"{input_path}.reindex_state.{backend}.json"


def _load_state(path):
    if not os.path.isfile(path):
        return {"done": 0}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_state(path, state):
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp_path, path)


def _build_store(backend, dim, **kwargs):
    if backend == "pgvector":
        return PgVectorStore(dim=dim, **kwargs)
    if backend == "qdrant":
        return QdrantStore(dim=dim, **kwargs)
    raise ValueError(f"Backend không hợp lệ: {backend!r} (hỗ trợ: pgvector, qdrant)")


def reindex(
    input_path, backend=DEFAULT_BACKEND, store=None, embedder=None,
    batch_size=DEFAULT_BATCH_SIZE, resume=False, dim=DEFAULT_DIM, progress_fn=None,
):
    """Đọc toàn bộ chunk từ input_path, embed + upsert theo batch. store/embedder có thể tiêm sẵn
    (test, hoặc tái dùng store đã mở) — mặc định tự tạo theo backend/dim. Trả về
    {"total": ..., "upserted": ..., "skipped": ...} (skipped = số chunk bỏ qua nhờ --resume)."""
    progress_fn = progress_fn or (lambda msg: print(msg, file=sys.stderr))
    chunks = _load_chunks(input_path)
    embedder = embedder or HashingEmbedder(dim=dim)
    store = store or _build_store(backend, dim=embedder.dim)

    state_path = _state_path(input_path, backend)
    state = _load_state(state_path) if resume else {"done": 0}
    start_index = min(state.get("done", 0), len(chunks))
    if start_index >= len(chunks) and chunks:
        progress_fn(
            f"[reindex] {input_path}: đã xong toàn bộ {len(chunks)} chunk từ lần chạy trước, "
            "không có gì để làm."
        )
        return {"total": len(chunks), "upserted": 0, "skipped": start_index}

    remaining = chunks[start_index:]
    total_batches = (len(remaining) + batch_size - 1) // batch_size if remaining else 0
    upserted = 0
    for batch_index in range(total_batches):
        batch = remaining[batch_index * batch_size: (batch_index + 1) * batch_size]
        vectors = embedder.embed([c.text for c in batch])
        records = [VectorRecord.from_chunk(c, vec) for c, vec in zip(batch, vectors)]
        store.upsert(records)
        upserted += len(records)

        state["done"] = min(len(chunks), start_index + (batch_index + 1) * batch_size)
        _save_state(state_path, state)
        progress_fn(
            f"[reindex] {input_path} -> {backend}: batch {batch_index + 1}/{total_batches} "
            f"({len(records)} chunk) — tổng đã upsert {state['done']}/{len(chunks)}"
        )

    progress_fn(
        f"[reindex] Hoàn tất: {upserted} chunk mới upsert, {start_index} chunk bỏ qua (resume), "
        f"tổng {len(chunks)}."
    )
    return {"total": len(chunks), "upserted": upserted, "skipped": start_index}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        description="Reindex toàn bộ chunk (jsonl từ ingest/chunkers.py) vào pgvector hoặc qdrant."
    )
    parser.add_argument("--backend", choices=["pgvector", "qdrant"], default=DEFAULT_BACKEND)
    parser.add_argument("--input", required=True, help="File chunks.jsonl (xem ingest/chunkers.py --out).")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--embed-dim", type=int, default=DEFAULT_DIM)
    parser.add_argument(
        "--resume", action="store_true",
        help="Bỏ qua chunk đã upsert ở lần chạy trước (đọc file trạng thái cạnh --input).",
    )
    parser.add_argument("--pg-dsn", default=None, help="Chỉ dùng khi --backend pgvector (mặc định RETAILKB_PG_DSN hoặc localhost).")
    parser.add_argument("--qdrant-url", default=None, help="Chỉ dùng khi --backend qdrant (mặc định RETAILKB_QDRANT_URL hoặc localhost).")
    parser.add_argument("--table", default=None, help="Tên bảng pgvector (mặc định 'chunks').")
    parser.add_argument("--collection", default=None, help="Tên collection qdrant (mặc định 'chunks').")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.input):
        print(f"Không tìm thấy file: {args.input}", file=sys.stderr)
        return 1

    kwargs = {}
    if args.backend == "pgvector":
        if args.pg_dsn:
            kwargs["dsn"] = args.pg_dsn
        if args.table:
            kwargs["table"] = args.table
    else:
        if args.qdrant_url:
            kwargs["url"] = args.qdrant_url
        if args.collection:
            kwargs["collection"] = args.collection

    try:
        store = _build_store(args.backend, dim=args.embed_dim, **kwargs)
    except Exception as e:
        print(f"Lỗi khi kết nối {args.backend}: {e}", file=sys.stderr)
        return 1

    result = reindex(
        args.input, args.backend, store=store, embedder=HashingEmbedder(dim=args.embed_dim),
        batch_size=args.batch_size, resume=args.resume, dim=args.embed_dim,
    )
    print(f"\nXong: {result}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
