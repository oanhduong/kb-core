"""Verifier chạy sau mỗi lượt agent muốn dừng (end_turn), TRƯỚC KHI agent/loop.py chấp nhận dừng thật.

Code thuần, không gọi LLM. Verifier KHÔNG kiểm chất lượng parser — invariant tầng đó đã có
ingest/invariants.py và tests/test_parsers.py lo. Verifier chỉ kiểm những quyết định MODEL đưa ra
trong lượt chạy: đọc đủ phạm vi file chưa (scope), có route đúng tool cho nội dung cần OCR chưa
(routing), phân loại/điền metadata có hợp lý không (classification), nội dung trích từ ảnh có vá»
hay không (vision sanity), phiên âm audio/video có phủ hết thời lượng không (media coverage), và có
record nào mâu thuẫn nhau không được giải thích bằng effective_date (conflict).

Input của verify() hoàn toàn là dữ liệu — không đọc file, không gọi API — nên test được bằng cách
tự dựng record/tool_calls giả (xem tests/test_verifier.py), không cần chạy agent thật.
"""
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

# =====================================================================================
# NGƯỠNG (documented, có thể chỉnh nếu ground truth corpus đổi)
# =====================================================================================

UNKNOWN_DOC_TYPE_MAX_RATIO = 0.30  # >30% record doc_type=unknown thì fail
VISION_SANITY_MIN_CHARS = 20  # record ảnh có content ngắn hơn ngưỡng này bị coi là "vá»"
LARGE_IMAGE_MIN_BYTES = 20_000  # ảnh nhỏ hơn ngưỡng này có thể hợp lệ chỉ có content ngắn (icon, logo...)
MEDIA_COVERAGE_MAX_GAP_SECONDS = 20  # transcript dừng cách duration quá ngưỡng này thì fail

_EFFECTIVE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PDF_LOC_PAGE_RE = re.compile(r"^Trang (\d+)")
_TIME_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})$")


# =====================================================================================
# KIỂU DỮ LIỆU
# =====================================================================================

@dataclass(frozen=True)
class ToolCallEvidence:
    """Bằng chứng một lần gọi tool trong lượt chạy — verifier dùng để suy ra phạm vi đã đọc.

    output là kết quả gốc (dict) của tool khi thành công, None khi lỗi. Đây là kiểu "duck-typed":
    verify() chỉ cần các thuộc tính tool_name/arguments/output/is_error, không yêu cầu đúng class
    này — agent/loop.py có thể truyền thẳng ToolCallRecord của nó miễn có đủ 4 thuộc tính.
    """
    tool_name: str
    arguments: dict
    output: object = None
    is_error: bool = False


@dataclass(frozen=True)
class Issue:
    check: str
    message: str


@dataclass(frozen=True)
class VerifyResult:
    passed: bool
    issues: list


# =====================================================================================
# HELPER
# =====================================================================================

def _hms_to_seconds(hms):
    m = _TIME_RE.match(hms or "")
    if not m:
        return None
    h, mi, s = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + s


def _seconds_to_hms(seconds):
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _is_valid_effective_date(value):
    if not isinstance(value, str) or not _EFFECTIVE_DATE_RE.match(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _record_id(record):
    return record.get("record_id", "(không có record_id)")


def _successful_calls(tool_calls, tool_name, path_key="path", path_value=None):
    for tc in tool_calls:
        if tc.tool_name != tool_name or tc.is_error or not isinstance(tc.output, dict):
            continue
        if path_value is not None and tc.arguments.get(path_key) != path_value:
            continue
        yield tc


# =====================================================================================
# 1. SCOPE COMPLETENESS
# =====================================================================================

def _check_scope_completeness(file_path, tool_calls):
    issues = []
    xlsx_calls = list(_successful_calls(tool_calls, "parse_document", path_value=file_path))
    xlsx_calls = [tc for tc in xlsx_calls if "total_sheets" in tc.output.get("stats", {})]
    if xlsx_calls:
        stats = xlsx_calls[0].output["stats"]
        total_sheets, sheet_names = stats["total_sheets"], stats["sheet_names"]
        covered = set()
        for tc in xlsx_calls:
            requested = tc.arguments.get("sheet_name")
            if requested:
                covered.add(requested)
            for chunk in tc.output.get("chunks", []):
                loc = chunk.get("location", "")
                if "!" in loc:
                    covered.add(loc.split("!", 1)[0])
        missing = [s for s in sheet_names if s not in covered]
        if missing:
            issues.append(Issue(
                "scope_completeness",
                f"file {file_path} có {total_sheets} sheet, chưa gọi parse_document cho "
                f"{', '.join(missing)}.",
            ))

    pdf_calls = list(_successful_calls(tool_calls, "parse_document", path_value=file_path))
    pdf_calls = [tc for tc in pdf_calls if "total_pages" in tc.output.get("stats", {})]
    if pdf_calls:
        stats = pdf_calls[0].output["stats"]
        total_pages = stats["total_pages"]
        needs_ocr = set(stats.get("needs_ocr_pages", []))
        covered = set()
        for tc in pdf_calls:
            for chunk in tc.output.get("chunks", []):
                m = _PDF_LOC_PAGE_RE.match(chunk.get("location", ""))
                if m:
                    covered.add(int(m.group(1)))
        missing = sorted(p for p in range(1, total_pages + 1) if p not in covered and p not in needs_ocr)
        if missing:
            missing_str = ", ".join(str(p) for p in missing)
            issues.append(Issue(
                "scope_completeness",
                f"file {file_path} có {total_pages} trang, chưa gọi parse_document cho trang "
                f"{missing_str}.",
            ))

    return issues


# =====================================================================================
# 2. ROUTING (needs_ocr_pages -> extract_image_content)
# =====================================================================================

def _check_routing(file_path, tool_calls):
    issues = []
    pdf_calls = [
        tc for tc in _successful_calls(tool_calls, "parse_document", path_value=file_path)
        if "needs_ocr_pages" in tc.output.get("stats", {})
    ]
    if not pdf_calls:
        return issues

    needs_ocr = set()
    for tc in pdf_calls:
        needs_ocr |= set(tc.output["stats"].get("needs_ocr_pages", []))
    if not needs_ocr:
        return issues

    ocr_called_pages = set()
    for tc in tool_calls:
        if tc.tool_name != "extract_image_content" or tc.is_error:
            continue
        m = re.match(r"^(.*)#trang=(\d+)$", (tc.arguments.get("path_or_page") or "").strip())
        if m and m.group(1) == file_path:
            ocr_called_pages.add(int(m.group(2)))

    for page in sorted(needs_ocr - ocr_called_pages):
        issues.append(Issue(
            "routing",
            f"trang {page} của file {file_path} có needs_ocr=True nhưng chưa gọi "
            f"extract_image_content(path_or_page='{file_path}#trang={page}') — trang này không có "
            "text layer nên parse_document sẽ không lấy được nội dung.",
        ))
    return issues


# =====================================================================================
# 3. CLASSIFICATION
# =====================================================================================

def _check_classification(records):
    issues = []
    total = len(records)

    if total:
        unknown = [r for r in records if r.get("doc_type") == "unknown"]
        ratio = len(unknown) / total
        if ratio > UNKNOWN_DOC_TYPE_MAX_RATIO:
            ids = ", ".join(_record_id(r) for r in unknown)
            issues.append(Issue(
                "classification_unknown_ratio",
                f"{len(unknown)}/{total} record ({ratio:.0%}) có doc_type='unknown', vượt ngưỡng "
                f"{UNKNOWN_DOC_TYPE_MAX_RATIO:.0%} cho phép — phân loại lại các record: {ids}.",
            ))

    price_list_records = [r for r in records if r.get("doc_type") == "price_list"]
    if price_list_records and not any((r.get("metadata") or {}).get("sku") for r in price_list_records):
        ids = ", ".join(_record_id(r) for r in price_list_records)
        issues.append(Issue(
            "classification_price_list_sku",
            f"{len(price_list_records)} record có doc_type='price_list' nhưng không record nào có "
            f"metadata.sku — bổ sung sku rồi ghi lại các record: {ids}.",
        ))

    for r in records:
        if r.get("doc_type") == "promotion" and not (r.get("metadata") or {}).get("effective_date"):
            issues.append(Issue(
                "classification_promotion_effective_date",
                f"record '{_record_id(r)}' có doc_type='promotion' nhưng metadata thiếu "
                "effective_date — bổ sung effective_date (định dạng YYYY-MM-DD) rồi ghi lại record.",
            ))

    for r in records:
        ed = (r.get("metadata") or {}).get("effective_date")
        if ed is not None and not _is_valid_effective_date(ed):
            issues.append(Issue(
                "classification_effective_date_format",
                f"record '{_record_id(r)}' có metadata.effective_date={ed!r} sai định dạng, cần "
                "YYYY-MM-DD — sửa lại giá trị rồi ghi lại record.",
            ))

    return issues


# =====================================================================================
# 4. VISION SANITY
# =====================================================================================

def _check_vision_sanity(records, tool_calls):
    issues = []
    image_sizes = {}
    for tc in _successful_calls(tool_calls, "extract_image_content"):
        path = tc.arguments.get("path_or_page")
        size = tc.output.get("size_bytes")
        if path and size is not None:
            image_sizes[path] = max(size, image_sizes.get(path, 0))

    for r in records:
        if r.get("source_type") != "image":
            continue
        content = (r.get("content") or "").strip()
        if len(content) >= VISION_SANITY_MIN_CHARS:
            continue
        size = image_sizes.get(r.get("source_file"))
        if size is not None and size >= LARGE_IMAGE_MIN_BYTES:
            issues.append(Issue(
                "vision_sanity",
                f"record '{_record_id(r)}' trích từ ảnh '{r.get('source_file')}' ({size} byte) chỉ "
                f"có {len(content)} ký tự nội dung — gọi lại extract_image_content với region_hint "
                "để trích xuất kỹ hơn thay vì chấp nhận kết quả này.",
            ))
    return issues


# =====================================================================================
# 5. MEDIA COVERAGE
# =====================================================================================

def _check_media_coverage(file_path, tool_calls):
    issues = []
    media_calls = list(_successful_calls(tool_calls, "transcribe_media", path_value=file_path))
    if not media_calls:
        return issues

    duration = None
    last_end_seconds = -1
    last_end_str = None
    for tc in media_calls:
        stats = tc.output.get("stats", {})
        if stats.get("duration_seconds") is not None:
            duration = stats["duration_seconds"]
        for chunk in tc.output.get("chunks", []):
            secs = _hms_to_seconds(chunk.get("end"))
            if secs is not None and secs > last_end_seconds:
                last_end_seconds = secs
                last_end_str = chunk["end"]

    if duration is None or last_end_str is None:
        return issues

    gap = duration - last_end_seconds
    if gap > MEDIA_COVERAGE_MAX_GAP_SECONDS:
        issues.append(Issue(
            "media_coverage",
            f"file {file_path} dài {_seconds_to_hms(duration)}, transcript dừng ở {last_end_str} "
            f"— còn thiếu khoảng {gap:.0f}s chưa phiên âm, gọi transcribe_media(path='{file_path}', "
            f"start_sec={last_end_seconds}) để tiếp tục.",
        ))
    return issues


# =====================================================================================
# 6. CONFLICT (cùng sku, khác giá, không có effective_date phân biệt)
# =====================================================================================

def _check_conflict(records):
    issues = []
    by_sku = defaultdict(list)
    for r in records:
        sku = (r.get("metadata") or {}).get("sku")
        if sku:
            by_sku[sku].append(r)

    for sku, recs in sorted(by_sku.items(), key=lambda kv: str(kv[0])):
        priced = [r for r in recs if (r.get("metadata") or {}).get("price") is not None]
        distinct_prices = {(r["metadata"]["price"]) for r in priced}
        if len(distinct_prices) <= 1:
            continue
        no_date = [r for r in priced if not (r.get("metadata") or {}).get("effective_date")]
        if no_date:
            ids = ", ".join(_record_id(r) for r in no_date)
            prices_str = ", ".join(str(p) for p in sorted(distinct_prices, key=str))
            issues.append(Issue(
                "conflict_price_no_effective_date",
                f"sku '{sku}' có nhiều giá khác nhau ({prices_str}) giữa các record nhưng thiếu "
                f"effective_date để phân biệt — bổ sung effective_date rồi ghi lại record: {ids}.",
            ))
    return issues


# =====================================================================================
# ENTRYPOINT
# =====================================================================================

def verify(file_path, records, tool_calls):
    """Chạy toàn bộ check trên trạng thái hiện tại của lượt agent.

    file_path: đường dẫn file đang ingest (để lọc bằng chứng đúng file, tránh nhiễu từ tool_calls
        của file khác trong cùng batch nếu lỡ truyền chung).
    records: list[dict] các KBRecord đã ghi thành công (argument gốc truyền cho write_kb_record).
    tool_calls: list các ToolCallEvidence (hoặc bất kỳ object nào có .tool_name/.arguments/.output/
        .is_error) — bằng chứng các lần gọi tool trong lượt chạy.
    """
    issues = []
    issues += _check_scope_completeness(file_path, tool_calls)
    issues += _check_routing(file_path, tool_calls)
    issues += _check_classification(records)
    issues += _check_vision_sanity(records, tool_calls)
    issues += _check_media_coverage(file_path, tool_calls)
    issues += _check_conflict(records)
    return VerifyResult(passed=not issues, issues=issues)


def format_issues(issues):
    """Định dạng issues thành nội dung tin nhắn user để agent/loop.py append vào messages."""
    lines = ["Tự kiểm tra (verifier) phát hiện vấn đề cần xử lý trước khi hoàn tất, KHÔNG được bỏ qua:"]
    for i, issue in enumerate(issues, 1):
        lines.append(f"{i}. [{issue.check}] {issue.message}")
    lines.append("Hãy xử lý từng vấn đề trên (gọi thêm tool nếu cần) rồi mới trả lời tóm tắt và dừng.")
    return "\n".join(lines)
