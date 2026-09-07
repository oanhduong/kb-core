"""qa/answer.py — QA đơn giản: câu hỏi + filter (store_code) -> câu trả lời kèm chunk nguồn.

Pipeline: retriever (index/hybrid.HybridRetriever, tuỳ chọn bọc thêm index/rerank.RerankingRetriever
— xem build_pipeline()) tìm CHILD chunk liên quan (chunk_role="child" của chiến lược parent_child, xem
ingest/chunkers.py — child ngắn nên semantic/lexical search chính xác hơn) -> tra PARENT chunk tương
ứng qua ChunkLookup (theo child.parent_id) -> PARENT (dài hơn, đủ ngữ cảnh) được đưa vào context cho
model, KHÔNG dùng child để trả lời (dùng child dễ trả lời cụt/thiếu ngữ cảnh xung quanh).

ChunkLookup cần thiết vì index.store.VectorRecord.from_chunk KHÔNG lưu chunk.parent_id (chỉ lưu
id/text/metadata, xem docstring VectorRecord) — muốn từ 1 child search result tra ra chunk_id của
PARENT thì phải tra lại toàn bộ Chunk gốc (parent lẫn child) theo chunk_id, nạp từ CÙNG file
chunks.jsonl mà index/reindex.py đã dùng để nạp VectorStore.

Provenance: nội dung parent chunk (trích từ tài liệu khách hàng, xem security/provenance.py) được bọc
<untrusted_document> trước khi đưa vào prompt — model được dạy (PROVENANCE_SYSTEM_INSTRUCTIONS) coi
đây là DỮ LIỆU cần trả lời dựa vào, không phải chỉ dẫn để thực thi (cùng ranh giới trust/untrust đã
dùng ở agent/loop.py cho pipeline ingest).

CHƯA làm citation format đẹp (trích dẫn [1], link vị trí p.x/Sheet/timestamp trong câu trả lời) — Phase
8 sẽ làm; answer_question() ở đây trả list chunk nguồn THÔ (chunk_id, location, text) kèm câu trả lời,
để phase sau tự quyết định format hiển thị.

FastAPI: POST /ask {question, store_code} -> {answer, sources}.
    uvicorn qa.answer:app --reload          # cần configure_app() chạy trước (xem main())
    python -m qa.answer --chunks data/chunks/parent_child/chunks.jsonl --backend pgvector
"""
import argparse
import json
import logging
import sys

import anthropic
from fastapi import Depends, FastAPI
from pydantic import BaseModel

from ingest.chunkers import Chunk
from index.embeddings import DEFAULT_DIM, HashingEmbedder
from index.hybrid import BM25Index, HybridRetriever
from index.rerank import RerankingRetriever
from index.store import Filters, PgVectorStore, QdrantStore, VectorRecord

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"  # cùng model với agent/loop.py — chất lượng trả lời quan trọng hơn cost cho QA
MAX_TOKENS = 1024
DEFAULT_TOP_K = 5

SYSTEM_PROMPT_TEMPLATE = """Bạn là trợ lý CSKH của RetailKB Copilot, trả lời câu hỏi dựa HOÀN TOÀN vào
các đoạn tài liệu nội bộ được cung cấp bên dưới (chính sách, bảng giá, khuyến mãi, ...). Không bịa
thông tin không có trong tài liệu — nếu tài liệu không đủ để trả lời, nói rõ là không tìm thấy thông
tin thay vì đoán. Trả lời ngắn gọn, đúng trọng tâm câu hỏi, bằng tiếng Việt.

{provenance_instructions}"""

NO_ANSWER_TEXT = "Xin lỗi, tôi không tìm thấy thông tin liên quan trong tài liệu hiện có."


# =====================================================================================
# ChunkLookup — tra Chunk gốc (parent lẫn child) theo chunk_id, xem docstring đầu file
# =====================================================================================

class ChunkLookup:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._by_id = {c.chunk_id: c for c in self._chunks}

    def get(self, chunk_id):
        return self._by_id.get(chunk_id)

    def all(self):
        return list(self._chunks)

    @classmethod
    def from_jsonl(cls, path):
        chunks = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    chunks.append(Chunk(**json.loads(line)))
        return cls(chunks)


def _is_retrievable_chunk(chunk):
    """Chunk nên đưa vào index/hybrid (embed + BM25) — loại chunk vai trò "parent" của chiến lược
    parent_child (chỉ dùng để tra context, không tự search — xem ingest/chunkers.py); chunk của 3
    chiến lược còn lại (không có chunk_role) đều retrievable."""
    return chunk.metadata.get("chunk_role") != "parent"


# =====================================================================================
# QA
# =====================================================================================

class Source(BaseModel):
    chunk_id: str
    location: str
    text: str


class AnswerResult(BaseModel):
    answer: str
    sources: list[Source]


def _resolve_parent_chunks(child_results, chunk_lookup):
    """child_results: list object có .id (đã retrieve trên CHILD chunk) -> list[Chunk] PARENT tương
    ứng, GIỮ THỨ TỰ, khử trùng lặp (nhiều child cùng 1 parent chỉ giữ 1 lần). Child/parent không tra
    được (không nên xảy ra với dữ liệu đã qua chunk_records strategy='parent_child') bị BỎ QUA, không
    raise — QA vẫn trả lời được bằng các parent tra được, chỉ log warning để biết dữ liệu thiếu."""
    seen_parent_ids = set()
    parents = []
    for result in child_results:
        child_chunk = chunk_lookup.get(result.id)
        if child_chunk is None:
            logger.warning("qa.answer: không tra được chunk gốc cho id=%s, bỏ qua", result.id)
            continue
        parent_chunk = chunk_lookup.get(child_chunk.parent_id)
        if parent_chunk is None:
            logger.warning(
                "qa.answer: không tra được parent chunk cho child=%s (parent_id=%s), bỏ qua",
                result.id, child_chunk.parent_id,
            )
            continue
        if parent_chunk.chunk_id in seen_parent_ids:
            continue
        seen_parent_ids.add(parent_chunk.chunk_id)
        parents.append(parent_chunk)
    return parents


def _build_system_prompt():
    from security.provenance import PROVENANCE_SYSTEM_INSTRUCTIONS

    return SYSTEM_PROMPT_TEMPLATE.format(provenance_instructions=PROVENANCE_SYSTEM_INSTRUCTIONS)


def _build_user_prompt(question, parent_chunks):
    from security.provenance import wrap_untrusted

    context_blocks = "\n\n".join(
        wrap_untrusted(chunk.text, source=chunk.location) for chunk in parent_chunks
    )
    return f"Câu hỏi: {question}\n\nTài liệu liên quan:\n{context_blocks}"


def answer_question(question, store_code, retriever, chunk_lookup, client=None, model=MODEL,
                     max_tokens=MAX_TOKENS, top_k=DEFAULT_TOP_K):
    """question (str), store_code (str | None — None nghĩa là không giới hạn cửa hàng nào) -> trả về
    AnswerResult. retriever: object có .search(query, top_k, filters) -> list object có .id (thường
    là index.hybrid.HybridRetriever, tuỳ chọn bọc index.rerank.RerankingRetriever — xem
    build_pipeline()). client: anthropic.Anthropic tiêm sẵn (test) hoặc None (tự tạo)."""
    filters = Filters(store_code=store_code, is_active=True) if store_code else Filters(is_active=True)
    child_results = retriever.search(question, top_k=top_k, filters=filters)
    parent_chunks = _resolve_parent_chunks(child_results, chunk_lookup)

    if not parent_chunks:
        return AnswerResult(answer=NO_ANSWER_TEXT, sources=[])

    client = client or anthropic.Anthropic()
    response = client.messages.create(
        model=model, max_tokens=max_tokens, system=_build_system_prompt(),
        messages=[{"role": "user", "content": _build_user_prompt(question, parent_chunks)}],
    )
    answer_text = "\n".join(b.text for b in response.content if b.type == "text").strip()

    sources = [Source(chunk_id=c.chunk_id, location=c.location, text=c.text) for c in parent_chunks]
    return AnswerResult(answer=answer_text, sources=sources)


# =====================================================================================
# PIPELINE MẶC ĐỊNH — ghép index/hybrid.py + index/rerank.py + index/store.py thành 1 retriever
# =====================================================================================

def build_pipeline(chunks_path, backend="pgvector", use_rerank=True, embed_dim=DEFAULT_DIM, **store_kwargs):
    """Nạp chunks_path (jsonl từ ingest/chunkers.py --strategy parent_child), dựng
    (retriever, chunk_lookup) sẵn sàng cho answer_question(). VectorStore/BM25Index CHỈ nạp lại (đọc)
    — dữ liệu phải đã được index/reindex.py upsert vào backend từ TRƯỚC (build_pipeline không tự
    upsert). embedder mặc định HashingEmbedder (index/embeddings.py) — placeholder, thay bằng
    embedding model thật trước production, xem docstring index/embeddings.py."""
    chunk_lookup = ChunkLookup.from_jsonl(chunks_path)
    retrievable_chunks = [c for c in chunk_lookup.all() if _is_retrievable_chunk(c)]
    # BM25Index cần object có .id (VectorRecord dùng "id", Chunk dùng "chunk_id") -> chuyển đổi, KHÔNG
    # cần vector (BM25 không dùng) — cùng chunk_id với VectorRecord mà index/reindex.py đã upsert vào
    # VectorStore, để id trả về từ 2 nhánh dense/BM25 khớp nhau (HybridRetriever hợp nhất theo id).
    bm25_records = [
        VectorRecord(id=c.chunk_id, vector=None, text=c.text, metadata=c.metadata)
        for c in retrievable_chunks
    ]

    embedder = HashingEmbedder(dim=embed_dim)
    if backend == "pgvector":
        vector_store = PgVectorStore(dim=embed_dim, **store_kwargs)
    elif backend == "qdrant":
        vector_store = QdrantStore(dim=embed_dim, **store_kwargs)
    else:
        raise ValueError(f"Backend không hợp lệ: {backend!r} (hỗ trợ: pgvector, qdrant)")

    bm25_index = BM25Index(bm25_records)
    retriever = HybridRetriever(vector_store, bm25_index, embedder)
    if use_rerank:
        retriever = RerankingRetriever(retriever)
    return retriever, chunk_lookup


# =====================================================================================
# FASTAPI
# =====================================================================================

app = FastAPI(title="RetailKB Copilot QA")

_state = {"retriever": None, "chunk_lookup": None, "client": None}


def configure_app(retriever, chunk_lookup, client=None):
    """Tiêm dependency cho app (production: gọi từ main() sau build_pipeline(); test: gọi với
    Fake retriever/chunk_lookup/client, hoặc dùng app.dependency_overrides trực tiếp)."""
    _state["retriever"] = retriever
    _state["chunk_lookup"] = chunk_lookup
    _state["client"] = client


def get_retriever():
    if _state["retriever"] is None:
        raise RuntimeError("qa.answer: retriever chưa được cấu hình — gọi configure_app() trước.")
    return _state["retriever"]


def get_chunk_lookup():
    if _state["chunk_lookup"] is None:
        raise RuntimeError("qa.answer: chunk_lookup chưa được cấu hình — gọi configure_app() trước.")
    return _state["chunk_lookup"]


def get_client():
    return _state["client"]  # None hợp lệ — answer_question() tự tạo anthropic.Anthropic() nếu None


class AskRequest(BaseModel):
    question: str
    store_code: str | None = None


@app.post("/ask", response_model=AnswerResult)
def ask(
    request: AskRequest,
    retriever=Depends(get_retriever),
    chunk_lookup=Depends(get_chunk_lookup),
    client=Depends(get_client),
):
    return answer_question(request.question, request.store_code, retriever, chunk_lookup, client=client)


# =====================================================================================
# CLI
# =====================================================================================

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description="Chạy FastAPI QA server (POST /ask).")
    parser.add_argument("--chunks", required=True, help="File chunks.jsonl (strategy parent_child, xem ingest/chunkers.py --out).")
    parser.add_argument("--backend", choices=["pgvector", "qdrant"], default="pgvector")
    parser.add_argument("--no-rerank", action="store_true", help="Tắt cross-encoder rerank (index/rerank.py) — chỉ dùng hybrid retrieval thô.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    retriever, chunk_lookup = build_pipeline(args.chunks, backend=args.backend, use_rerank=not args.no_rerank)
    configure_app(retriever, chunk_lookup)

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
