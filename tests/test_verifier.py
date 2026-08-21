"""Pytest cho agent/verifier.py.

Không chạy agent thật — mọi test tự dựng record/tool_calls giả rồi gọi thẳng verifier.verify().
Mỗi check có case pass và case fail; ngưỡng tỉ lệ doc_type=unknown test đúng biên (30% pass,
31% fail). Cuối file có test tính chất: mọi issue message phải chứa ít nhất một con số hoặc một
định danh cụ thể được trích dẫn trong dấu nháy đơn — nếu không, message chỉ nêu triệu chứng chung
chung mà không actionable.
"""
import re

import pytest

from agent.verifier import (
    LARGE_IMAGE_MIN_BYTES,
    MEDIA_COVERAGE_MAX_GAP_SECONDS,
    UNKNOWN_DOC_TYPE_MAX_RATIO,
    VISION_SANITY_MIN_CHARS,
    ToolCallEvidence,
    verify,
)

FILE = "data/raw/xlsx/01_bang_gia_sku.xlsx"
PDF_FILE = "data/raw/pdf/05_scan_bien_ban_ban_giao_hang_hoa.pdf"
IMG_FILE = "data/raw/image/02_bang_gia_viet_tay.png"
MEDIA_FILE = "data/raw/video/01_video_quy_trinh_thanh_toan_quay_thu_ngan.mp4"


def make_record(**overrides):
    record = {
        "record_id": "rec-1",
        "source_file": FILE,
        "source_type": "xlsx",
        "doc_type": "price_list",
        "content": "sku SKU001 giá 100000",
        "location": "Sheet1!A2",
        "metadata": {},
        "confidence": 0.9,
    }
    record.update(overrides)
    return record


def tc(tool_name, arguments, output=None, is_error=False):
    return ToolCallEvidence(tool_name=tool_name, arguments=arguments, output=output, is_error=is_error)


# =====================================================================================
# 1. SCOPE COMPLETENESS
# =====================================================================================

def _xlsx_parse_call(sheet_name, sheet_names, total_sheets=None):
    return tc(
        "parse_document",
        {"path": FILE, "sheet_name": sheet_name},
        output={
            "chunks": [{"text": "x", "location": f"{sheet_name}!A2", "kind": "row"}],
            "stats": {
                "total_sheets": total_sheets or len(sheet_names),
                "sheet_names": sheet_names,
                "scoped_chunk_count": 1,
            },
        },
    )


def test_scope_completeness_xlsx_all_sheets_covered_passes():
    sheet_names = [f"Sheet{i}" for i in range(1, 6)]
    calls = [_xlsx_parse_call(s, sheet_names) for s in sheet_names]
    result = verify(FILE, [], calls)
    assert result.passed
    assert not result.issues


def test_scope_completeness_xlsx_missing_sheets_fails_and_names_them():
    sheet_names = [f"Sheet{i}" for i in range(1, 6)]
    calls = [_xlsx_parse_call(s, sheet_names) for s in sheet_names[:3]]
    result = verify(FILE, [], calls)
    assert not result.passed
    msg = next(i.message for i in result.issues if i.check == "scope_completeness")
    assert "5 sheet" in msg
    assert "Sheet4" in msg and "Sheet5" in msg


def _pdf_parse_call(page_range, pages_with_chunks, total_pages, needs_ocr_pages=()):
    chunks = [{"text": "nội dung", "location": f"Trang {p}", "kind": "text"} for p in pages_with_chunks]
    return tc(
        "parse_document",
        {"path": PDF_FILE, "page_range": page_range},
        output={
            "chunks": chunks,
            "stats": {
                "total_pages": total_pages,
                "needs_ocr_pages": list(needs_ocr_pages),
                "scoped_chunk_count": len(chunks),
            },
        },
    )


def test_scope_completeness_pdf_all_pages_covered_passes():
    calls = [_pdf_parse_call("1-5", [1, 2, 3, 4, 5], total_pages=5)]
    result = verify(PDF_FILE, [], calls)
    assert result.passed


def test_scope_completeness_pdf_missing_pages_fails_and_lists_page_numbers():
    calls = [_pdf_parse_call("1-3", [1, 2, 3], total_pages=5)]
    result = verify(PDF_FILE, [], calls)
    assert not result.passed
    msg = next(i.message for i in result.issues if i.check == "scope_completeness")
    assert "5 trang" in msg
    assert "4" in msg and "5" in msg


def test_scope_completeness_pdf_excludes_ocr_pages_from_required_coverage():
    calls = [_pdf_parse_call("1-5", [1, 2, 4, 5], total_pages=5, needs_ocr_pages=[3])]
    result = verify(PDF_FILE, [], calls)
    assert not any(i.check == "scope_completeness" for i in result.issues)


# =====================================================================================
# 2. ROUTING
# =====================================================================================

def test_routing_ocr_page_handled_passes():
    calls = [
        _pdf_parse_call("1-5", [1, 2, 4, 5], total_pages=5, needs_ocr_pages=[3]),
        tc("extract_image_content", {"path_or_page": f"{PDF_FILE}#trang=3"}, output={"extracted_text": "..."}),
    ]
    result = verify(PDF_FILE, [], calls)
    assert not any(i.check == "routing" for i in result.issues)


def test_routing_ocr_page_not_handled_fails_and_names_page():
    calls = [_pdf_parse_call("1-5", [1, 2, 4, 5], total_pages=5, needs_ocr_pages=[3])]
    result = verify(PDF_FILE, [], calls)
    msg = next(i.message for i in result.issues if i.check == "routing")
    assert "trang 3" in msg
    assert PDF_FILE in msg


# =====================================================================================
# 3. CLASSIFICATION
# =====================================================================================

def test_classification_unknown_ratio_at_30_percent_passes():
    records = [make_record(record_id=f"r{i}", doc_type="unknown") for i in range(30)]
    records += [make_record(record_id=f"r{i}", doc_type="policy") for i in range(30, 100)]
    result = verify(FILE, records, [])
    assert not any(i.check == "classification_unknown_ratio" for i in result.issues)


def test_classification_unknown_ratio_at_31_percent_fails():
    records = [make_record(record_id=f"r{i}", doc_type="unknown") for i in range(31)]
    records += [make_record(record_id=f"r{i}", doc_type="policy") for i in range(31, 100)]
    result = verify(FILE, records, [])
    assert not result.passed
    msg = next(i.message for i in result.issues if i.check == "classification_unknown_ratio")
    assert "31/100" in msg


def test_classification_price_list_without_any_sku_fails():
    records = [
        make_record(record_id="p1", doc_type="price_list", metadata={}),
        make_record(record_id="p2", doc_type="price_list", metadata={"store_code": "HN01"}),
    ]
    result = verify(FILE, records, [])
    msg = next(i.message for i in result.issues if i.check == "classification_price_list_sku")
    assert "p1" in msg and "p2" in msg


def test_classification_price_list_with_at_least_one_sku_passes():
    records = [
        make_record(record_id="p1", doc_type="price_list", metadata={}),
        make_record(record_id="p2", doc_type="price_list", metadata={"sku": "SKU001"}),
    ]
    result = verify(FILE, records, [])
    assert not any(i.check == "classification_price_list_sku" for i in result.issues)


def test_classification_promotion_missing_effective_date_fails():
    records = [make_record(record_id="promo1", doc_type="promotion", metadata={})]
    result = verify(FILE, records, [])
    msg = next(i.message for i in result.issues if i.check == "classification_promotion_effective_date")
    assert "promo1" in msg


def test_classification_promotion_with_effective_date_passes():
    records = [make_record(record_id="promo1", doc_type="promotion", metadata={"effective_date": "2026-07-01"})]
    result = verify(FILE, records, [])
    assert not any(i.check == "classification_promotion_effective_date" for i in result.issues)


@pytest.mark.parametrize("bad_value", ["01-07-2026", "2026/07/01", "2026-13-40", "not-a-date"])
def test_classification_effective_date_bad_format_fails(bad_value):
    records = [make_record(record_id="r1", metadata={"effective_date": bad_value})]
    result = verify(FILE, records, [])
    msg = next(i.message for i in result.issues if i.check == "classification_effective_date_format")
    assert bad_value in msg


def test_classification_effective_date_valid_format_passes():
    records = [make_record(record_id="r1", metadata={"effective_date": "2026-07-01"})]
    result = verify(FILE, records, [])
    assert not any(i.check == "classification_effective_date_format" for i in result.issues)


# =====================================================================================
# 4. VISION SANITY
# =====================================================================================

def test_vision_sanity_short_content_from_large_image_fails():
    records = [make_record(
        record_id="img1", source_type="image", source_file=IMG_FILE, content="giá", doc_type="catalog",
    )]
    calls = [tc(
        "extract_image_content", {"path_or_page": IMG_FILE},
        output={"extracted_text": "...", "size_bytes": LARGE_IMAGE_MIN_BYTES + 1},
    )]
    result = verify(IMG_FILE, records, calls)
    msg = next(i.message for i in result.issues if i.check == "vision_sanity")
    assert "img1" in msg
    assert "region_hint" in msg


def test_vision_sanity_short_content_from_small_image_passes():
    records = [make_record(
        record_id="img1", source_type="image", source_file=IMG_FILE, content="giá", doc_type="catalog",
    )]
    calls = [tc(
        "extract_image_content", {"path_or_page": IMG_FILE},
        output={"extracted_text": "...", "size_bytes": LARGE_IMAGE_MIN_BYTES - 1},
    )]
    result = verify(IMG_FILE, records, calls)
    assert not any(i.check == "vision_sanity" for i in result.issues)


def test_vision_sanity_sufficient_content_length_passes():
    records = [make_record(
        record_id="img1", source_type="image", source_file=IMG_FILE,
        content="x" * VISION_SANITY_MIN_CHARS, doc_type="catalog",
    )]
    calls = [tc(
        "extract_image_content", {"path_or_page": IMG_FILE},
        output={"extracted_text": "...", "size_bytes": LARGE_IMAGE_MIN_BYTES + 1},
    )]
    result = verify(IMG_FILE, records, calls)
    assert not any(i.check == "vision_sanity" for i in result.issues)


# =====================================================================================
# 5. MEDIA COVERAGE
# =====================================================================================

def _media_call(end_str, duration_seconds=None):
    stats = {"segment_count": 1}
    if duration_seconds is not None:
        stats["duration_seconds"] = duration_seconds
    return tc(
        "transcribe_media", {"path": MEDIA_FILE},
        output={"chunks": [{"text": "...", "location": "00:00:00", "kind": "segment", "end": end_str}], "stats": stats},
    )


def test_media_coverage_transcript_stops_far_before_duration_fails():
    calls = [_media_call("00:02:30", duration_seconds=312)]  # 05:12 = 312s
    result = verify(MEDIA_FILE, [], calls)
    msg = next(i.message for i in result.issues if i.check == "media_coverage")
    assert "05:12" in msg
    assert "02:30" in msg


def test_media_coverage_transcript_covers_full_duration_passes():
    calls = [_media_call("00:05:10", duration_seconds=312)]  # gap 2s < threshold
    result = verify(MEDIA_FILE, [], calls)
    assert not any(i.check == "media_coverage" for i in result.issues)


def test_media_coverage_gap_exactly_at_threshold_passes():
    calls = [_media_call(_hms(312 - MEDIA_COVERAGE_MAX_GAP_SECONDS), duration_seconds=312)]
    result = verify(MEDIA_FILE, [], calls)
    assert not any(i.check == "media_coverage" for i in result.issues)


def _hms(seconds):
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def test_media_coverage_no_duration_evidence_skips_check():
    calls = [_media_call("00:02:30", duration_seconds=None)]
    result = verify(MEDIA_FILE, [], calls)
    assert not any(i.check == "media_coverage" for i in result.issues)


# =====================================================================================
# 6. CONFLICT
# =====================================================================================

def test_conflict_same_sku_different_price_without_effective_date_fails():
    records = [
        make_record(record_id="r1", metadata={"sku": "SKU001", "price": 100000}),
        make_record(record_id="r2", metadata={"sku": "SKU001", "price": 120000}),
    ]
    result = verify(FILE, records, [])
    msg = next(i.message for i in result.issues if i.check == "conflict_price_no_effective_date")
    assert "SKU001" in msg and "r1" in msg and "r2" in msg


def test_conflict_same_sku_different_price_with_effective_date_passes():
    records = [
        make_record(record_id="r1", metadata={"sku": "SKU001", "price": 100000, "effective_date": "2026-01-01"}),
        make_record(record_id="r2", metadata={"sku": "SKU001", "price": 120000, "effective_date": "2026-07-01"}),
    ]
    result = verify(FILE, records, [])
    assert not any(i.check == "conflict_price_no_effective_date" for i in result.issues)


def test_conflict_same_sku_same_price_passes():
    records = [
        make_record(record_id="r1", metadata={"sku": "SKU001", "price": 100000}),
        make_record(record_id="r2", metadata={"sku": "SKU001", "price": 100000}),
    ]
    result = verify(FILE, records, [])
    assert not any(i.check == "conflict_price_no_effective_date" for i in result.issues)


# =====================================================================================
# TÍNH CHẤT: mọi issue message phải actionable — chứa con số hoặc định danh cụ thể trong nháy đơn
# =====================================================================================

_HAS_NUMBER_OR_QUOTED_IDENTIFIER = re.compile(r"\d|'[^']+'")


def _all_failing_scenarios():
    sheet_names = [f"Sheet{i}" for i in range(1, 6)]
    yield FILE, [], [_xlsx_parse_call(s, sheet_names) for s in sheet_names[:3]]
    yield PDF_FILE, [], [_pdf_parse_call("1-3", [1, 2, 3], total_pages=5)]
    yield PDF_FILE, [], [_pdf_parse_call("1-5", [1, 2, 4, 5], total_pages=5, needs_ocr_pages=[3])]

    unknown_records = [make_record(record_id=f"r{i}", doc_type="unknown") for i in range(31)]
    unknown_records += [make_record(record_id=f"r{i}", doc_type="policy") for i in range(31, 100)]
    yield FILE, unknown_records, []

    yield FILE, [
        make_record(record_id="p1", doc_type="price_list", metadata={}),
    ], []
    yield FILE, [make_record(record_id="promo1", doc_type="promotion", metadata={})], []
    yield FILE, [make_record(record_id="r1", metadata={"effective_date": "not-a-date"})], []

    yield IMG_FILE, [
        make_record(record_id="img1", source_type="image", source_file=IMG_FILE, content="giá", doc_type="catalog"),
    ], [tc(
        "extract_image_content", {"path_or_page": IMG_FILE},
        output={"extracted_text": "...", "size_bytes": LARGE_IMAGE_MIN_BYTES + 1},
    )]

    yield MEDIA_FILE, [], [_media_call("00:02:30", duration_seconds=312)]

    yield FILE, [
        make_record(record_id="r1", metadata={"sku": "SKU001", "price": 100000}),
        make_record(record_id="r2", metadata={"sku": "SKU001", "price": 120000}),
    ], []


def test_every_issue_message_is_actionable_with_a_number_or_quoted_identifier():
    all_issues = []
    for file_path, records, tool_calls in _all_failing_scenarios():
        result = verify(file_path, records, tool_calls)
        all_issues.extend(result.issues)

    assert len(all_issues) >= 9  # bao phủ đủ 6 loại check (một số check phát nhiều issue)
    for issue in all_issues:
        assert _HAS_NUMBER_OR_QUOTED_IDENTIFIER.search(issue.message), (
            f"issue [{issue.check}] không chứa số hay định danh cụ thể: {issue.message!r}"
        )
