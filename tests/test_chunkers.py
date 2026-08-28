"""Test cho ingest/chunkers.py.

Input là schemas.KBRecord dựng trực tiếp (không phụ thuộc file fixture thật) — chunkers.py hoạt
động ở tầng sau parser, nên test tách biệt khỏi ingest/parsers.py.

Phần contextual retrieval / metadata LLM fallback KHÔNG gọi Anthropic API thật: dùng FakeClient/
FakeResponse tự dựng (cùng phong cách tests/test_loop.py), mô phỏng đúng response.content[].type/.text
và response.usage.*."""
from types import SimpleNamespace

import pytest

from ingest.chunkers import (
    Chunk,
    ChunkerConfig,
    _char_window_spans,
    _find_all_skus,
    _owning_record_id,
    add_contextual_retrieval,
    chunk_record,
    chunk_records,
    normalize_chunk_metadata,
)
from schemas import KBRecord

ALL_STRATEGIES = ("fixed", "recursive", "structure_aware")


def _kb_record(record_id="r1", content="nội dung", location="p.1", source_type="docx",
                doc_type="policy", source_file="data/raw/docx/test.docx", metadata=None):
    return KBRecord(
        record_id=record_id,
        source_file=source_file,
        source_type=source_type,
        doc_type=doc_type,
        content=content,
        location=location,
        metadata=metadata or {},
        confidence=0.9,
    )


PRICE_TABLE_HEADERS = ["SKU", "Tên", "Giá", "Category", "Store"]
PRICE_TABLE_CONTENT = "\n".join(
    [" | ".join(PRICE_TABLE_HEADERS)]
    + [f"SKU{i:03d} | Tai nghe {i} | {100000 + i * 1000} | Điện tử | HCM01" for i in range(1, 15)]
)

DIEU_FILLER = " ".join(["Nội dung điều khoản chi tiết."] * 40)
DIEU_UNITS = [f"Điều {i}. Quy định số {i}\n{DIEU_FILLER}" for i in range(1, 8)]
DIEU_CONTENT = "\n\n".join(DIEU_UNITS)


# =====================================================================================
# Tabular data — áp dụng cho mọi chiến lược
# =====================================================================================

def test_row_chunk_has_all_column_names():
    record = _kb_record(content=PRICE_TABLE_CONTENT, doc_type="price_list", source_type="xlsx")
    for strategy in ALL_STRATEGIES:
        chunks = chunk_record(record, ChunkerConfig(strategy=strategy))
        assert len(chunks) == 14  # 14 dòng dữ liệu, bảng > small_table_max_rows nên tách theo dòng
        for chunk in chunks:
            for header in PRICE_TABLE_HEADERS:
                assert header in chunk.text, f"Thiếu tên cột '{header}' trong chunk: {chunk.text!r}"


def test_small_table_kept_whole():
    small_content = "\n".join(
        [" | ".join(PRICE_TABLE_HEADERS)]
        + [f"SKU{i} | Tên {i} | {i} | Điện tử | HCM01" for i in range(1, 4)]
    )
    record = _kb_record(content=small_content, doc_type="price_list", source_type="xlsx")
    chunks = chunk_record(record, ChunkerConfig())
    assert len(chunks) == 1
    assert chunks[0].text == small_content


# =====================================================================================
# structure_aware không cắt giữa một Điều
# =====================================================================================

def test_structure_aware_does_not_split_mid_dieu():
    record = _kb_record(content=DIEU_CONTENT, doc_type="policy", location="Chương 1")
    config = ChunkerConfig(strategy="structure_aware", recursive_chunk_chars=500)
    chunks = chunk_record(record, config)

    assert len(chunks) >= 2  # content dài hơn nhiều lần budget nên phải tách thành nhiều chunk
    for unit in DIEU_UNITS:
        matches = [c for c in chunks if unit in c.text]
        assert len(matches) == 1, f"Điều bị cắt khúc hoặc trùng lặp giữa các chunk: {unit[:20]}..."


def test_structure_aware_single_oversized_dieu_kept_whole():
    oversized_unit = "Điều 1. Quy định duy nhất\n" + (" ".join(["nội dung dài"] * 200))
    record = _kb_record(content=oversized_unit, doc_type="policy")
    config = ChunkerConfig(strategy="structure_aware", recursive_chunk_chars=100)
    chunks = chunk_record(record, config)

    assert len(chunks) == 1
    assert chunks[0].text == oversized_unit


# =====================================================================================
# Chunk không rỗng
# =====================================================================================

def test_chunks_never_empty():
    records = [
        _kb_record(record_id="r1", content=("Xin chào, đây là đoạn văn bản đầu tiên. " * 40).strip(), location="p.1"),
        _kb_record(record_id="r2", content=PRICE_TABLE_CONTENT, doc_type="price_list", source_type="xlsx",
                    location="Sheet1!A2:E15"),
        _kb_record(record_id="r3", content=DIEU_CONTENT, doc_type="policy", location="Chương 1"),
    ]
    for strategy in ALL_STRATEGIES:
        chunks = chunk_records(records, ChunkerConfig(strategy=strategy, recursive_chunk_chars=500))
        assert chunks
        for chunk in chunks:
            assert chunk.text.strip() != ""
            assert isinstance(chunk, Chunk)


def test_media_cluster_chunks_never_empty():
    records = [
        _kb_record(record_id="seg1", content="xin chào quý khách", location="00:00:00",
                    source_type="audio", doc_type="training", metadata={"end": "00:00:03"}),
        _kb_record(record_id="seg2", content="hôm nay cửa hàng có khuyến mãi", location="00:00:03",
                    source_type="audio", doc_type="training", metadata={"end": "00:00:07"}),
    ]
    chunks = chunk_records(records, ChunkerConfig())
    assert chunks
    for chunk in chunks:
        assert chunk.text.strip() != ""


# =====================================================================================
# chunk_id unique
# =====================================================================================

def test_chunk_ids_unique_across_strategies_and_records():
    records = [
        _kb_record(record_id="r1", content=("Xin chào, đây là đoạn văn bản đầu tiên. " * 40).strip(), location="p.1"),
        _kb_record(record_id="r2", content=PRICE_TABLE_CONTENT, doc_type="price_list", source_type="xlsx",
                    location="Sheet1!A2:E15"),
        _kb_record(record_id="r3", content=DIEU_CONTENT, doc_type="policy", location="Chương 1"),
        _kb_record(record_id="seg1", content="xin chào quý khách", location="00:00:00",
                    source_type="audio", doc_type="training", metadata={"end": "00:00:03"}),
        _kb_record(record_id="seg2", content="hôm nay cửa hàng có khuyến mãi", location="00:00:03",
                    source_type="audio", doc_type="training", metadata={"end": "00:00:07"}),
    ]
    for strategy in ALL_STRATEGIES:
        chunks = chunk_records(records, ChunkerConfig(strategy=strategy, recursive_chunk_chars=500))
        ids = [c.chunk_id for c in chunks]
        assert len(ids) == len(set(ids)), f"chunk_id trùng lặp với strategy={strategy}: {ids}"


# =====================================================================================
# Media: gom cụm segment liền kề, giữ timestamp start/end
# =====================================================================================

def test_media_segments_cluster_by_time_gap_and_keep_start_end():
    records = [
        _kb_record(record_id="seg1", content="xin chào quý khách", location="00:00:00",
                    source_type="audio", doc_type="training", metadata={"end": "00:00:03"}),
        _kb_record(record_id="seg2", content="hôm nay cửa hàng có khuyến mãi", location="00:00:03",
                    source_type="audio", doc_type="training", metadata={"end": "00:00:07"}),
        _kb_record(record_id="seg3", content="giảm giá 20 phần trăm toàn bộ giày dép", location="00:05:00",
                    source_type="audio", doc_type="training", metadata={"end": "00:05:04"}),
    ]
    chunks = chunk_records(records, ChunkerConfig(media_max_gap_seconds=5))

    assert len(chunks) == 2  # seg1+seg2 liền nhau -> 1 cụm; seg3 cách xa -> cụm riêng
    assert chunks[0].location == "00:00:00-00:00:07"
    assert "xin chào" in chunks[0].text and "khuyến mãi" in chunks[0].text
    assert chunks[1].location == "00:05:00-00:05:04"
    assert chunks[1].parent_id == "seg3"


# =====================================================================================
# fixed — cửa sổ trượt cơ bản
# =====================================================================================

def test_fixed_strategy_splits_long_content_with_overlap():
    long_text = ("Chính sách đổi trả áp dụng trong 30 ngày kể từ ngày mua hàng. " * 100).strip()
    record = _kb_record(record_id="r1", content=long_text)
    config = ChunkerConfig(strategy="fixed", fixed_chunk_tokens=50, fixed_overlap_tokens=10)
    chunks = chunk_record(record, config)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.text.strip() != ""
        assert chunk.parent_id == "r1"


def _assert_no_content_lost(original, chunk_texts):
    """Mỗi chunk phải là substring NGUYÊN VĂN (kể cả khoảng trắng) của content gốc, và mọi ký tự
    không phải khoảng trắng trong content gốc phải được ít nhất 1 chunk phủ tới.

    Tìm theo con trỏ tăng dần (không phải original.find(text) từ đầu mỗi lần) — cần thiết vì
    content test có đoạn lặp lại giống hệt nhau, nếu tìm từ đầu mỗi lần sẽ luôn khớp nhầm về
    lần lặp ĐẦU TIÊN thay vì đúng vị trí của chunk đang xét."""
    covered = bytearray(len(original))
    cursor = 0
    for text in chunk_texts:
        idx = original.find(text, cursor)
        assert idx != -1, f"Chunk không phải substring nguyên văn (từ vị trí {cursor}) của content gốc: {text[:60]!r}..."
        for i in range(idx, idx + len(text)):
            covered[i] = 1
        cursor = idx
    gaps = [i for i in range(len(original)) if not covered[i] and not original[i].isspace()]
    assert not gaps, f"Mất {len(gaps)} ký tự nội dung, vị trí đầu tiên {gaps[0]}: " \
                      f"{original[max(0, gaps[0] - 20):gaps[0] + 20]!r}"


# Content thực tế hơn DIEU_CONTENT: khoảng trắng giữa các Điều KHÔNG đều nhau (khi thì 1 \n, khi
# thì 2, khi thì có khoảng trắng cuối dòng) — để bắt được lỗi kiểu "gộp lại bằng join() cố định
# thay vì cắt nguyên văn theo vị trí" (đã từng xảy ra ở _chunk_structure_aware). Câu lặp trong mỗi
# Điều được đánh số (không lặp y hệt) để substring-match trong _assert_no_content_lost không bị
# nhầm giữa các lần lặp.
_filler_counter = iter(range(1000))


def _filler(n=30):
    return " ".join(f"Chi tiết dòng {next(_filler_counter)}." for _ in range(n))


IRREGULAR_DIEU_CONTENT = (
    "Lời mở đầu, không thuộc Điều nào.\n"
    "Điều 1. Mục đích\nNội dung điều 1. " + _filler() + "\n"
    "Điều 2. Phạm vi\nNội dung điều 2. " + _filler() + "\n\n"
    "Điều 3. Định nghĩa\nNội dung điều 3. " + _filler() + "\n\n\n"
    "Điều 4. Hiệu lực\nNội dung điều 4. " + _filler()
)


def test_no_content_lost_or_duplicated():
    # câu có đánh số i để KHÔNG lặp lại giống hệt nhau — nội dung lặp nguyên văn (period cố định)
    # làm substring-match trong assert bị nhầm lẫn giữa các lần lặp, không phản ánh bug thật.
    free_text = " ".join(f"Đoạn văn tự do số {i}, minh hoạ nội dung không có cấu trúc rõ ràng." for i in range(80))
    records = [
        _kb_record(record_id="r1", content=IRREGULAR_DIEU_CONTENT, doc_type="policy"),
        _kb_record(record_id="r2", content=free_text),
    ]
    for strategy in ALL_STRATEGIES:
        for record in records:
            chunks = chunk_record(record, ChunkerConfig(strategy=strategy, recursive_chunk_chars=200))
            _assert_no_content_lost(record.content, [c.text for c in chunks])


def test_fixed_strategy_never_cuts_mid_word():
    # nhiều từ dài, không đều nhau, để step không tình cờ luôn rơi đúng khoảng trắng
    long_text = " ".join(f"từthứ{i}khôngchia" for i in range(400))
    for window, overlap in [(2048, 200), (300, 50), (137, 41), (2048, 2000)]:
        spans = _char_window_spans(long_text, window, overlap)
        assert spans[-1][1] == len(long_text)  # phủ hết text, không rơi ký tự cuối
        for start, end in spans:
            if start > 0:
                assert long_text[start - 1] == " ", (
                    f"start={start} cắt giữa từ (window={window}, overlap={overlap}): "
                    f"...{long_text[max(0, start - 10):start + 10]!r}..."
                )
            if end < len(long_text):
                assert long_text[end] == " ", (
                    f"end={end} cắt giữa từ (window={window}, overlap={overlap}): "
                    f"...{long_text[end - 10:end + 10]!r}..."
                )


# =====================================================================================
# parent_child — parent lớn (model đọc) chứa nhiều child nhỏ (embed/search)
# =====================================================================================

def test_parent_child_links_child_to_parent_chunk_id_not_record_id():
    long_text = ("Chính sách đổi trả áp dụng trong 30 ngày kể từ ngày mua hàng. " * 200).strip()
    record = _kb_record(record_id="r1", content=long_text)
    config = ChunkerConfig(strategy="parent_child", parent_chunk_tokens=100, child_chunk_tokens=20, child_overlap_tokens=5)
    chunks = chunk_record(record, config)

    parents = [c for c in chunks if c.metadata["chunk_role"] == "parent"]
    children = [c for c in chunks if c.metadata["chunk_role"] == "child"]
    assert len(parents) >= 2
    assert len(children) > len(parents)

    parent_ids = {p.chunk_id for p in parents}
    for child in children:
        assert child.parent_id in parent_ids, "child.parent_id phải trỏ tới chunk_id của parent, không phải record_id"
        assert child.parent_id != "r1"
        assert child.metadata["source_record_id"] == "r1"  # record_id gốc vẫn truy ngược được qua metadata
    for parent in parents:
        assert parent.parent_id == "r1"
        assert parent.metadata["source_record_id"] == "r1"


def test_parent_child_chunk_ids_unique():
    long_text = ("Nội dung minh hoạ cho parent child. " * 300).strip()
    record = _kb_record(record_id="r1", content=long_text)
    config = ChunkerConfig(strategy="parent_child", parent_chunk_tokens=80, child_chunk_tokens=20, child_overlap_tokens=5)
    chunks = chunk_record(record, config)
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))


def test_parent_child_small_record_has_single_parent():
    record = _kb_record(record_id="r1", content="Nội dung ngắn, không cần chia nhiều parent.")
    chunks = chunk_record(record, ChunkerConfig(strategy="parent_child"))
    parents = [c for c in chunks if c.metadata["chunk_role"] == "parent"]
    children = [c for c in chunks if c.metadata["chunk_role"] == "child"]
    assert len(parents) == 1
    assert len(children) >= 1
    assert all(c.parent_id == parents[0].chunk_id for c in children)


# =====================================================================================
# normalize_chunk_metadata — rule trước, LLM (Haiku) chỉ cho field rule không ra
# =====================================================================================

def text_block(text):
    return SimpleNamespace(type="text", text=text)


def fake_usage(input_tokens=500, output_tokens=20, cache_created=0, cache_read=0):
    return SimpleNamespace(
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_creation_input_tokens=cache_created, cache_read_input_tokens=cache_read,
    )


def fake_response(text, **usage_kwargs):
    return SimpleNamespace(content=[text_block(text)], usage=fake_usage(**usage_kwargs))


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeMessages hết response đã script.")
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def test_normalize_metadata_rule_only_no_llm_call():
    record = _kb_record(
        record_id="r1",
        content="Giá niêm yết SKU MB-2201 áp dụng từ 01/03/2026 tại HN01.",
        doc_type="price_list", source_type="pdf", source_file="data/raw/pdf/gia.pdf",
        metadata={"store_code": "HN01", "category": "Điện tử", "version": "v1.2"},
    )
    chunks = chunk_record(record, ChunkerConfig(strategy="fixed"))
    fake_client = FakeClient([])  # không có response nào script sẵn -> lỗi ngay nếu code lỡ gọi LLM

    result = normalize_chunk_metadata([record], chunks, client=fake_client)

    assert len(fake_client.messages.calls) == 0
    for chunk in result:
        assert chunk.metadata["source_file"] == "data/raw/pdf/gia.pdf"
        assert chunk.metadata["doc_type"] == "price_list"
        assert chunk.metadata["store_code"] == "HN01"
        assert chunk.metadata["category"] == "Điện tử"
        assert chunk.metadata["version"] == "v1.2"
        assert chunk.metadata["effective_date"] == "2026-03-01"
        assert chunk.metadata["is_active"] is True
        assert "MB-2201" in chunk.metadata["sku"]


def test_normalize_metadata_llm_fallback_only_for_missing_fields():
    record = _kb_record(
        record_id="r1", content="Chính sách bảo hành áp dụng cho toàn bộ sản phẩm.",
        doc_type="policy", metadata={},  # không có category/version/store_code -> rule không ra được
    )
    chunks = chunk_record(record, ChunkerConfig(strategy="fixed"))
    fake_client = FakeClient([
        fake_response('{"category": "Bảo hành", "version": null, "store_code": null}'),
    ])

    result = normalize_chunk_metadata([record], chunks, client=fake_client)

    assert len(fake_client.messages.calls) == 1  # 1 lệnh gọi DUY NHẤT cho cả record, không phải mỗi chunk
    for chunk in result:
        assert chunk.metadata["category"] == "Bảo hành"


def test_normalize_metadata_llm_called_once_per_record_not_per_chunk():
    long_text = ("Chính sách bảo hành sản phẩm điện tử. " * 100).strip()
    record = _kb_record(record_id="r1", content=long_text, doc_type="policy", metadata={})
    config = ChunkerConfig(strategy="fixed", fixed_chunk_tokens=50, fixed_overlap_tokens=5)
    chunks = chunk_record(record, config)
    assert len(chunks) > 1

    fake_client = FakeClient([
        fake_response('{"category": "Bảo hành", "version": null, "store_code": null}'),
    ])
    normalize_chunk_metadata([record], chunks, client=fake_client)
    assert len(fake_client.messages.calls) == 1


def test_find_all_skus_matches_vietnamese_dieu_prefix():
    # SKU thật trong data/raw/xlsx/01_bang_gia_sku.xlsx dùng tiền tố tiếng Việt viết tắt category
    # ("ĐT" = Điện Tử, "ĐM" = Điện Máy, "ĐCT" = Đồ Chơi Trẻ em) — chữ "Đ" PHẢI khớp, không chỉ A-Z.
    text = "sku là ĐT-018, ten_san_pham là Loa bluetooth di động 30W, category là Điện tử"
    assert _find_all_skus(text) == ["ĐT-018"]


def test_store_code_rule_does_not_false_positive_on_bare_acronyms():
    # Row-chunk bảng giá thật (data/raw/xlsx/01_bang_gia_sku.xlsx) luôn có chữ "SKU" ở đầu text —
    # store_code KHÔNG được suy ra nhầm thành "SKU" (từng là bug: regex cũ cho phép 0 chữ số nên
    # khớp bừa mọi từ viết hoa 2-4 ký tự, kể cả tên cột).
    record = _kb_record(
        record_id="r1", content="SKU ĐT-018, Tên sản phẩm Loa bluetooth, Giá 1440000, Category Điện tử",
        doc_type="price_list", source_type="xlsx", metadata={},
    )
    chunks = [Chunk(chunk_id="r1#0", parent_id="r1", text=record.content, location="p.1", metadata={})]
    fake_client = FakeClient([fake_response('{"category": null, "version": null, "store_code": null}')])

    result = normalize_chunk_metadata([record], chunks, client=fake_client)
    assert result[0].metadata["store_code"] != "SKU"


def test_store_code_rule_still_matches_codes_with_digits():
    record = _kb_record(record_id="r1", content="Áp dụng tại cửa hàng HCM01 từ đầu tháng.", doc_type="policy", metadata={})
    chunks = [Chunk(chunk_id="r1#0", parent_id="r1", text=record.content, location="p.1", metadata={})]
    fake_client = FakeClient([fake_response('{"category": null, "version": null}')])

    result = normalize_chunk_metadata([record], chunks, client=fake_client)
    assert result[0].metadata["store_code"] == "HCM01"


def test_normalize_metadata_sku_derived_per_chunk():
    content = "SKU MB-2201 giá 100. SKU MB-3300 giá 200."
    record = _kb_record(record_id="r1", content=content, doc_type="price_list")
    chunk_a = Chunk(chunk_id="r1#a", parent_id="r1", text="SKU MB-2201 giá 100.", location="p.1", metadata={})
    chunk_b = Chunk(chunk_id="r1#b", parent_id="r1", text="SKU MB-3300 giá 200.", location="p.1", metadata={})
    fake_client = FakeClient([fake_response('{"category": null, "version": null, "store_code": null}')])

    result = normalize_chunk_metadata([record], [chunk_a, chunk_b], client=fake_client)

    assert result[0].metadata["sku"] == ["MB-2201"]
    assert result[1].metadata["sku"] == ["MB-3300"]


def test_normalize_metadata_works_with_parent_child_source_record_id():
    record = _kb_record(record_id="r1", content=("Nội dung dài. " * 100).strip(), doc_type="policy",
                         metadata={"store_code": "HN01", "category": "Điện tử", "version": "v1",
                                   "effective_date": "2026-01-01"})
    config = ChunkerConfig(strategy="parent_child", parent_chunk_tokens=50, child_chunk_tokens=20, child_overlap_tokens=5)
    chunks = chunk_record(record, config)
    fake_client = FakeClient([])  # mọi field rule đã ra hết từ record.metadata -> không cần LLM

    result = normalize_chunk_metadata([record], chunks, client=fake_client)

    assert len(fake_client.messages.calls) == 0
    for chunk in result:
        assert _owning_record_id(chunk) == "r1"
        assert chunk.metadata["store_code"] == "HN01"
        assert chunk.metadata["source_file"] == record.source_file


# =====================================================================================
# add_contextual_retrieval — document cached, chunk text động; bỏ qua table/parent
# =====================================================================================

def test_contextual_retrieval_prepends_context_and_uses_cache_control():
    record = _kb_record(record_id="r1", content=("Điều 5. Chính sách đổi trả. " * 50).strip(), doc_type="policy")
    chunks = chunk_record(record, ChunkerConfig(strategy="fixed", fixed_chunk_tokens=50, fixed_overlap_tokens=5))
    fake_client = FakeClient([
        fake_response("Thuộc Điều 5 chính sách đổi trả:", cache_created=200) for _ in chunks
    ])

    cost_log = []
    result = add_contextual_retrieval(chunks, [record], client=fake_client, model="claude-haiku-4-5", cost_log=cost_log)

    assert len(cost_log) == len(chunks)
    for original, new_chunk in zip(chunks, result):
        assert new_chunk.text.startswith("Thuộc Điều 5 chính sách đổi trả:")
        assert original.text in new_chunk.text
        assert new_chunk.metadata["contextual_retrieval"] is True

    first_call = fake_client.messages.calls[0]
    document_block = first_call["messages"][0]["content"][0]
    assert document_block["cache_control"] == {"type": "ephemeral"}
    assert "Điều 5" in document_block["text"]
    chunk_block = first_call["messages"][0]["content"][1]
    assert "cache_control" not in chunk_block  # chỉ document được cache, phần chunk phải "động"


def test_contextual_retrieval_skips_table_and_parent_chunks():
    table_headers = ["SKU", "Giá"]
    table_content = "\n".join([" | ".join(table_headers)] + [f"SKU{i} | {i}" for i in range(1, 15)])
    table_record = _kb_record(record_id="rt", content=table_content, doc_type="price_list", source_type="xlsx")
    table_chunks = chunk_record(table_record, ChunkerConfig())

    pc_record = _kb_record(record_id="rp", content=("Nội dung dài. " * 100).strip(), doc_type="policy")
    pc_chunks = chunk_record(pc_record, ChunkerConfig(strategy="parent_child", parent_chunk_tokens=50, child_chunk_tokens=20))
    parent_chunks = [c for c in pc_chunks if c.metadata["chunk_role"] == "parent"]

    fake_client = FakeClient([])  # không script response nào -> lỗi ngay nếu code lỡ gọi LLM cho các chunk này

    result_table = add_contextual_retrieval(table_chunks, [table_record], client=fake_client, cost_log=[])
    result_parent = add_contextual_retrieval(parent_chunks, [pc_record], client=fake_client, cost_log=[])

    assert len(fake_client.messages.calls) == 0
    assert [c.text for c in result_table] == [c.text for c in table_chunks]
    assert [c.text for c in result_parent] == [c.text for c in parent_chunks]
