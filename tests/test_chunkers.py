"""Test cho ingest/chunkers.py.

Input là schemas.KBRecord dựng trực tiếp (không phụ thuộc file fixture thật) — chunkers.py hoạt
động ở tầng sau parser, nên test tách biệt khỏi ingest/parsers.py."""
import pytest

from ingest.chunkers import Chunk, ChunkerConfig, _char_window_spans, chunk_record, chunk_records
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
