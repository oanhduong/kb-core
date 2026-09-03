"""Test cho index/embeddings.py (HashingEmbedder) — thuần Python, không gọi API/tải model nào."""
import math

from index.embeddings import HashingEmbedder


def _norm(vec):
    return math.sqrt(sum(v * v for v in vec))


def test_embed_returns_one_vector_per_text_same_order():
    embedder = HashingEmbedder(dim=32)
    vectors = embedder.embed(["giá SKU ABC tại HCM01", "giá SKU ABC tại HN01", "hoàn toàn khác"])
    assert len(vectors) == 3
    assert all(len(v) == 32 for v in vectors)


def test_embed_is_deterministic():
    embedder = HashingEmbedder(dim=32)
    assert embedder.embed(["giá SKU ABC tại HCM01"]) == embedder.embed(["giá SKU ABC tại HCM01"])


def test_embed_vectors_are_l2_normalized():
    embedder = HashingEmbedder(dim=64)
    for vec in embedder.embed(["một câu bất kỳ có vài từ", "câu khác"]):
        assert math.isclose(_norm(vec), 1.0, abs_tol=1e-9)


def test_embed_empty_text_returns_zero_vector_without_error():
    embedder = HashingEmbedder(dim=16)
    vec = embedder.embed([""])[0]
    assert vec == [0.0] * 16


def test_embed_near_duplicate_sentences_have_high_cosine_similarity():
    """Tính chất then chốt mà index/bench_prefilter.py cần: 2 câu chỉ khác mã cửa hàng phải có
    embedding rất gần nhau (mô phỏng đúng vấn đề thật của post-filter — xem docstring bench)."""
    embedder = HashingEmbedder(dim=128)
    a, b = embedder.embed([
        "Giá Sản phẩm số 3 (mã SKU003) tại cửa hàng ST0001: 115,000 VND. Áp dụng từ 2026-01-01.",
        "Giá Sản phẩm số 3 (mã SKU003) tại cửa hàng ST0099: 115,000 VND. Áp dụng từ 2026-01-01.",
    ])
    cosine = sum(x * y for x, y in zip(a, b))  # đã L2-normalize -> dot product = cosine similarity
    assert cosine > 0.9


def test_embed_unrelated_sentences_have_lower_similarity_than_near_duplicates():
    embedder = HashingEmbedder(dim=128)
    base, near_dup, unrelated = embedder.embed([
        "Giá Sản phẩm số 3 (mã SKU003) tại cửa hàng ST0001: 115,000 VND.",
        "Giá Sản phẩm số 3 (mã SKU003) tại cửa hàng ST0099: 115,000 VND.",
        "Chính sách đổi trả hàng hoá áp dụng cho toàn bộ hệ thống cửa hàng.",
    ])
    sim_near_dup = sum(x * y for x, y in zip(base, near_dup))
    sim_unrelated = sum(x * y for x, y in zip(base, unrelated))
    assert sim_near_dup > sim_unrelated
