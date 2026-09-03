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

    @abstractmethod
    def embed(self, texts):
        """list[str] -> list[list[float]], cùng thứ tự, mỗi vector độ dài self.dim."""
        raise NotImplementedError


class HashingEmbedder(Embedder):
    def __init__(self, dim=DEFAULT_DIM):
        self.dim = dim

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
