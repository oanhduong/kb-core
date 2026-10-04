"""Embedder dùng cho index/reindex.py và index/bench_prefilter.py.

Chỉ định nghĩa 1 interface tối giản (Embedder.embed) + 1 implementation placeholder
(HashingEmbedder) — KHÔNG dùng model ngữ nghĩa thật (không gọi API, không tải model, không cần
mạng/GPU), để index/reindex.py và bench chạy được độc lập trên máy bất kỳ. Thay bằng embedding
model thật (Voyage AI, OpenAI, sentence-transformers local, ...) trước khi đưa vào production —
việc đó nằm ngoài phạm vi tầng lưu trữ (index/store.py) và không ảnh hưởng tới interface VectorStore.

HashingEmbedder dùng "hashing trick" (băm token vào 1 vector chiều cố định, có dấu ngẫu nhiên theo
hash để giảm thiên lệch) — tính chất quan trọng nó tạo ra và bench (index/bench_prefilter.py) cần:
hai câu gần giống hệt nhau (chỉ khác 1-2 token, ví dụ chỉ khác mã cửa hàng) cho ra vector có cosine
similarity rất cao, đúng như một embedding model ngữ nghĩa thật sẽ làm — nên vẫn mô phỏng đúng vấn đề
thực tế mà bench muốn đo (nhiều chunk gần giống nhau về nội dung nhưng khác store/thời điểm).
"""
import hashlib
import math
import re
from abc import ABC, abstractmethod

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)

DEFAULT_DIM = 256


class Embedder(ABC):
    dim: int
    model_name: str = "hashing"

    @abstractmethod
    def embed(self, texts):
        """list[str] -> list[list[float]], cùng thứ tự, mỗi vector độ dài self.dim."""
        raise NotImplementedError

    def embed_query(self, text):
        """Vector cho câu hỏi — model như e5 cần tiền tố "query: " khác với "passage: " của tài liệu."""
        return self.embed([text])[0]

    def embed_passages(self, texts):
        return self.embed(texts)


class HashingEmbedder(Embedder):
    def __init__(self, dim=DEFAULT_DIM):
        self.dim = dim
        self.model_name = f"hashing-{dim}"

    def embed(self, texts):
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text):
        vec = [0.0] * self.dim
        for tok in _TOKEN_RE.findall(text.lower()):
            digest = int(hashlib.blake2b(tok.encode("utf-8"), digest_size=8).hexdigest(), 16)
            idx = digest % self.dim
            sign = 1.0 if (digest // self.dim) % 2 == 0 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


class SentenceTransformerEmbedder(Embedder):
    """Embedding model thật (sentence-transformers, chạy CPU được) — model mặc định trong config:
    intfloat/multilingual-e5-base (768 chiều, hỗ trợ tiếng Việt). e5 yêu cầu tiền tố "query: " cho câu
    hỏi và "passage: " cho tài liệu — cấu hình qua embedding.query_prefix/passage_prefix. Model load
    1 lần/tiến trình (lazy) và dùng lại; vector đã L2-normalize (cosine = dot)."""

    _models = {}

    def __init__(self, model_name, dim, query_prefix="", passage_prefix="", batch_size=32, device="cpu"):
        self.model_name = model_name
        self.dim = dim
        self.query_prefix = query_prefix
        self.passage_prefix = passage_prefix
        self.batch_size = batch_size
        self.device = device

    def _model(self):
        key = (self.model_name, self.device)
        if key not in self._models:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(self.model_name, device=self.device)
            actual = model.get_sentence_embedding_dimension()
            if actual != self.dim:
                raise ValueError(f"embedding.dim={self.dim} nhưng model {self.model_name} trả {actual} chiều — sửa config")
            self._models[key] = model
        return self._models[key]

    def embed(self, texts):
        vecs = self._model().encode(list(texts), batch_size=self.batch_size, normalize_embeddings=True,
                                    show_progress_bar=False, convert_to_numpy=True)
        return [v.tolist() for v in vecs]

    def embed_query(self, text):
        return self.embed([f"{self.query_prefix}{text}"])[0]

    def embed_passages(self, texts):
        return self.embed([f"{self.passage_prefix}{t}" for t in texts])


def get_embedder(cfg=None):
    """Embedder theo config (embedding.provider/model/dim). Cache theo (provider, model)."""
    if cfg is None:
        from core.config import get_config

        cfg = get_config()
    e = cfg.embedding
    key = (e.provider, e.model, e.dim)
    if key not in _EMBEDDER_CACHE:
        if e.provider == "hashing":
            _EMBEDDER_CACHE[key] = HashingEmbedder(dim=e.dim)
        else:
            _EMBEDDER_CACHE[key] = SentenceTransformerEmbedder(e.model, e.dim, e.query_prefix, e.passage_prefix,
                                                               e.batch_size, e.device)
    return _EMBEDDER_CACHE[key]


_EMBEDDER_CACHE = {}


def effective_embedding_model(cfg=None):
    """Tên model THỰC SỰ dùng để embed (khớp cột chunk_embeddings.model) — provider hashing cho ra
    'hashing-<dim>' chứ không phải embedding.model trong config."""
    if cfg is None:
        from core.config import get_config

        cfg = get_config()
    return f"hashing-{cfg.embedding.dim}" if cfg.embedding.provider == "hashing" else cfg.embedding.model
