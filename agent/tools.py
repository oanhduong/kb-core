"""Tool cho AgentLoop, bọc quanh ingest/parsers.py.

Mỗi tool nhận tham số phạm vi (sheet_name, page_range, start_sec/end_sec, ...) để model tự
quyết định gọi bao nhiêu lần thay vì luôn phải đọc trọn file trong một lần gọi. Sau mỗi lần
gọi parser trong ingest/parsers.py, kết quả được chạy qua ingest.invariants.check_result()
trước khi cắt theo phạm vi yêu cầu và trả về cho model.

Lỗi do tham số tool sai (path không đúng loại, sheet/trang không tồn tại, KBRecord không hợp lệ...)
raise ToolError với message actionable — model có thể tự sửa và gọi lại. Lỗi ingest.invariants.ParserError
KHÔNG được bắt ở đây — để agent/loop.py xử lý (dừng hẳn, không retry, vì đó là bug ở ingest/parsers.py).
"""
import base64
import functools
import io
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime

from pydantic import ValidationError

from ingest import parsers
from ingest.invariants import check_result
from schemas import KBRecord

OUTPUT_DIR = os.path.join("data", "output")
VISION_MODEL = "claude-sonnet-4-6"


class ToolError(Exception):
    """Lỗi actionable do tham số tool sai — model có thể tự sửa input và gọi lại."""


# =====================================================================================
# detect_file_type
# =====================================================================================

_SCHEMA_DETECT_FILE_TYPE = {
    "description": (
        "Nhận diện loại file thật qua magic bytes (không dựa vào đuôi file). Luôn gọi tool này "
        "ĐẦU TIÊN cho mỗi file trước khi quyết định dùng parse_document / extract_image_content / "
        "transcribe_media. Trả về một trong: 'docx', 'xlsx', 'pdf', 'image', 'audio', 'video'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Đường dẫn tới file cần nhận diện."},
        },
        "required": ["path"],
    },
}


def detect_file_type(path):
    kind = parsers.detect_file_type(path)
    return {"path": path, "kind": kind}


# =====================================================================================
# parse_document
# =====================================================================================

_SCHEMA_PARSE_DOCUMENT = {
    "description": (
        "Parse file docx/xlsx/pdf thành chunk nội dung + stats. CHỈ dùng cho docx/xlsx/pdf — "
        "KHÔNG dùng cho image (dùng extract_image_content) hay audio/video (dùng transcribe_media).\n"
        "- docx: không có tham số phạm vi, luôn trả về toàn bộ file (thường vài trang, không tràn context).\n"
        "- xlsx: dùng sheet_name để lấy đúng 1 sheet một lần — với workbook nhiều sheet/nhiều dòng, "
        "nên gọi lần lượt từng sheet thay vì bỏ trống sheet_name (dễ tràn context). stats.total_sheets "
        "và stats.sheet_names luôn phản ánh toàn bộ workbook để biết còn sheet nào chưa đọc.\n"
        "- pdf: dùng page_range (ví dụ '1-3' hoặc '5') để lấy một khoảng trang — với pdf nhiều trang "
        "nên chia nhỏ theo vài trang một lần. stats.total_pages và stats.needs_ocr_pages luôn phản ánh "
        "toàn bộ tài liệu; nếu một trang nằm trong needs_ocr_pages thì trang đó không có text layer, "
        "dùng extract_image_content(path_or_page=\"<file>.pdf#trang=<N>\") để đọc bằng vision thay vì "
        "trông chờ parse_document trả nội dung cho trang đó."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Đường dẫn file .docx/.xlsx/.pdf cần parse."},
            "sheet_name": {
                "type": "string",
                "description": "Chỉ áp dụng cho .xlsx — tên sheet cụ thể muốn lấy. Bỏ trống để lấy toàn bộ workbook.",
            },
            "page_range": {
                "type": "string",
                "description": "Chỉ áp dụng cho .pdf — phạm vi trang dạng 'N' hoặc 'N-M' (1-indexed). Bỏ trống để lấy toàn bộ tài liệu.",
            },
        },
        "required": ["path"],
    },
}

_PAGE_RANGE_RE = re.compile(r"^\s*(\d+)(?:-(\d+))?\s*$")
_PDF_LOC_PAGE_RE = re.compile(r"^Trang (\d+)")


def _parse_page_range(page_range, total_pages):
    m = _PAGE_RANGE_RE.match(page_range)
    if not m:
        raise ToolError(f"page_range {page_range!r} sai định dạng, cần dạng 'N' hoặc 'N-M' (ví dụ '1-3').")
    lo = int(m.group(1))
    hi = int(m.group(2)) if m.group(2) else lo
    if lo < 1 or hi < lo:
        raise ToolError(f"page_range {page_range!r} không hợp lệ (trang phải >= 1 và trang cuối >= trang đầu).")
    if lo > total_pages:
        raise ToolError(f"page_range {page_range!r} vượt quá số trang thực tế của tài liệu ({total_pages} trang).")
    return lo, min(hi, total_pages)


def _chunk_in_page_range(chunk, lo, hi):
    m = _PDF_LOC_PAGE_RE.match(chunk["location"])
    if not m:
        return False  # "Không rõ trang" -> không thuộc phạm vi trang cụ thể nào
    return lo <= int(m.group(1)) <= hi


def parse_document(path, sheet_name=None, page_range=None):
    kind = parsers.detect_file_type(path)

    if kind == "image":
        raise ToolError(
            f"'{path}' là file ảnh — parse_document không xử lý ảnh. "
            "Dùng tool extract_image_content(path_or_page=...) để đọc nội dung ảnh qua Claude vision."
        )
    if kind in ("audio", "video"):
        raise ToolError(
            f"'{path}' là file {kind} — parse_document không xử lý audio/video. "
            "Dùng tool transcribe_media(path=...) để phiên âm."
        )

    if kind == "docx":
        if sheet_name is not None or page_range is not None:
            raise ToolError(
                "File .docx không có khái niệm sheet/trang trong pipeline này — "
                "gọi parse_document(path) không kèm sheet_name/page_range."
            )
        result = parsers.parse_docx(path)
        check_result("docx", path, result)
        return {"chunks": result["chunks"], "stats": result["stats"]}

    if kind == "xlsx":
        if page_range is not None:
            raise ToolError("File .xlsx không có khái niệm trang — dùng sheet_name thay vì page_range.")
        result = parsers.parse_xlsx(path)
        check_result("xlsx", path, result)
        stats = result["stats"]
        sheet_names = list(stats["sheets"].keys())

        if sheet_name is not None:
            if sheet_name not in stats["sheets"]:
                raise ToolError(f"Sheet {sheet_name!r} không tồn tại trong '{path}'. Các sheet có sẵn: {sheet_names}.")
            prefix = f"{sheet_name}!"
            chunks = [c for c in result["chunks"] if c["location"].startswith(prefix)]
        else:
            chunks = result["chunks"]

        return {
            "chunks": chunks,
            "stats": {
                "total_sheets": stats["sheet_count"],
                "sheet_names": sheet_names,
                "scoped_chunk_count": len(chunks),
            },
        }

    if kind == "pdf":
        if sheet_name is not None:
            raise ToolError("File .pdf không có khái niệm sheet — dùng page_range thay vì sheet_name.")
        result = parsers.parse_pdf(path)
        check_result("pdf", path, result)
        stats = result["stats"]

        if page_range is not None:
            lo, hi = _parse_page_range(page_range, stats["page_count"])
            chunks = [c for c in result["chunks"] if _chunk_in_page_range(c, lo, hi)]
        else:
            chunks = result["chunks"]

        return {
            "chunks": chunks,
            "stats": {
                "total_pages": stats["page_count"],
                "needs_ocr_pages": stats["pages_needing_ocr"],
                "scoped_chunk_count": len(chunks),
            },
        }

    raise ToolError(f"Loại file '{kind}' chưa được hỗ trợ trong parse_document.")


# =====================================================================================
# extract_image_content
# =====================================================================================

_SCHEMA_EXTRACT_IMAGE_CONTENT = {
    "description": (
        "Đọc nội dung ảnh bằng Claude vision — dùng khi cần OCR/mô tả nội dung không có text layer sẵn. "
        "Hai trường hợp:\n"
        "1. File ảnh độc lập (png/jpg/...): truyền path_or_page là đường dẫn file ảnh.\n"
        "2. Một trang PDF dạng scan (không có text layer): truyền path_or_page dạng "
        "'<đường_dẫn.pdf>#trang=<N>' — chỉ dùng khi parse_document đã báo trang đó nằm trong "
        "stats.needs_ocr_pages, KHÔNG dùng tràn lan vì render + gọi vision tốn kém.\n"
        "KHÔNG dùng tool này cho docx/xlsx/pdf có text layer sẵn — dùng parse_document sẽ rẻ và chính xác hơn."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path_or_page": {
                "type": "string",
                "description": "Đường dẫn file ảnh, hoặc '<file>.pdf#trang=<N>' để OCR một trang PDF dạng scan.",
            },
            "region_hint": {
                "type": "string",
                "description": "Gợi ý vùng cần chú ý trong ảnh, ví dụ 'bảng giá ở giữa ảnh'. Bỏ trống để đọc toàn bộ ảnh.",
            },
        },
        "required": ["path_or_page"],
    },
}

_PDF_PAGE_REF_RE = re.compile(r"^(.*\.pdf)#trang=(\d+)$", re.IGNORECASE)


def _render_pdf_page_image(pdf_path, page_no):
    parsers._require_file(pdf_path)
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as e:
        raise ImportError("Thiếu thư viện docling. Cài đặt: pip install docling") from e

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = False
    pipeline_options.generate_page_images = True
    pipeline_options.images_scale = 2.0
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)})

    try:
        result = converter.convert(pdf_path, page_range=(page_no, page_no))
    except Exception as e:
        raise ToolError(f"Không render được trang {page_no} của '{pdf_path}': {e}") from e

    page_item = result.document.pages.get(page_no)
    if page_item is None or page_item.image is None:
        raise ToolError(
            f"Không lấy được ảnh trang {page_no} của '{pdf_path}' — kiểm tra lại số trang "
            "(dùng parse_document để xem stats.total_pages)."
        )

    buf = io.BytesIO()
    page_item.image.pil_image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii"), "image/png"


def extract_image_content(path_or_page, region_hint=None, client=None):
    if client is None:
        raise RuntimeError("extract_image_content chưa được gắn Anthropic client (lỗi cấu hình nội bộ ở build_registry).")

    m = _PDF_PAGE_REF_RE.match(path_or_page.strip())
    if m:
        pdf_path, page_no = m.group(1), int(m.group(2))
        image_b64, media_type = _render_pdf_page_image(pdf_path, page_no)
        source_desc = f"trang {page_no} của {pdf_path}"
    else:
        kind = parsers.detect_file_type(path_or_page)
        if kind != "image":
            raise ToolError(
                f"'{path_or_page}' không phải file ảnh (nhận diện: {kind}). "
                "Dùng path tới file ảnh, hoặc '<file>.pdf#trang=<N>' để OCR một trang PDF dạng scan."
            )
        result = parsers.parse_image(path_or_page)
        check_result("image", path_or_page, result)
        chunk = result["chunks"][0]
        image_b64, media_type = chunk["base64"], chunk["media_type"]
        source_desc = path_or_page

    prompt = (
        "Đọc kỹ ảnh này và trích xuất toàn bộ nội dung có thể đọc được: văn bản (giữ nguyên dấu tiếng Việt), "
        "số liệu trong bảng nếu có, và mô tả ngắn các yếu tố phi văn bản quan trọng (logo, hình sản phẩm, bố cục). "
        "Trình bày văn bản trích xuất đúng theo thứ tự đọc tự nhiên."
    )
    if region_hint:
        prompt += f"\n\nChú ý đặc biệt vào vùng: {region_hint}."

    response = client.messages.create(
        model=VISION_MODEL,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    text = "\n".join(b.text for b in response.content if b.type == "text")
    size_bytes = len(base64.b64decode(image_b64, validate=True))
    return {"source": source_desc, "extracted_text": text, "size_bytes": size_bytes}


# =====================================================================================
# transcribe_media
# =====================================================================================

_SCHEMA_TRANSCRIBE_MEDIA = {
    "description": (
        "Phiên âm file audio/video bằng faster-whisper. Với file dài (>1 phút), nên chia nhỏ theo "
        "start_sec/end_sec (ví dụ từng đoạn 30-60s) thay vì phiên âm cả file trong một lần gọi. "
        "Bỏ trống cả hai để phiên âm toàn bộ file. location trả về là mốc thời gian tuyệt đối "
        "(HH:MM:SS) tính từ đầu file gốc, kể cả khi chỉ phiên âm một đoạn."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Đường dẫn file audio/video."},
            "start_sec": {
                "type": "number",
                "description": "Giây bắt đầu đoạn cần phiên âm (tính từ đầu file gốc). Bỏ trống để bắt đầu từ đầu file.",
            },
            "end_sec": {
                "type": "number",
                "description": "Giây kết thúc đoạn cần phiên âm. Bỏ trống để lấy tới hết file.",
            },
        },
        "required": ["path"],
    },
}


def _transcribe_range(path, start_sec, end_sec):
    ffmpeg_bin = parsers._find_ffmpeg_binary()
    if not ffmpeg_bin:
        raise ToolError(
            "Không tìm thấy ffmpeg trong PATH — cần để cắt đoạn audio/video theo start_sec/end_sec. "
            "Cài đặt bằng 'brew install ffmpeg' hoặc set biến môi trường FFMPEG_BIN."
        )

    fd, out_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    cmd = [ffmpeg_bin, "-y", "-ss", str(start_sec)]
    if end_sec is not None:
        cmd += ["-to", str(end_sec)]
    cmd += ["-i", path, "-vn", "-ac", "1", "-ar", "16000", out_path]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        if os.path.exists(out_path):
            os.remove(out_path)
        raise ToolError(f"Không cắt được đoạn {start_sec}-{end_sec}s từ '{path}' (ffmpeg lỗi): {e.stderr}") from e

    try:
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise ImportError("Thiếu thư viện faster-whisper. Cài đặt: pip install faster-whisper") from e

        try:
            model = WhisperModel("small", device="auto", compute_type="default")
            segments, _info = model.transcribe(out_path, language="vi")
        except Exception as e:
            raise ToolError(f"Không thể nhận dạng giọng nói, đoạn {start_sec}-{end_sec}s có thể bị hỏng: {e}") from e

        chunks = []
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            chunks.append({
                "text": text,
                "location": parsers._format_timestamp(start_sec + seg.start),
                "kind": "segment",
                "end": parsers._format_timestamp(start_sec + seg.end),
            })
        return chunks
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)


def transcribe_media(path, start_sec=None, end_sec=None):
    kind = parsers.detect_file_type(path)
    if kind not in ("audio", "video"):
        raise ToolError(
            f"'{path}' không phải audio/video (nhận diện: {kind}). "
            "Dùng parse_document cho docx/xlsx/pdf, extract_image_content cho ảnh."
        )
    if start_sec is not None and end_sec is not None and end_sec <= start_sec:
        raise ToolError(f"end_sec ({end_sec}) phải lớn hơn start_sec ({start_sec}).")

    if start_sec is None and end_sec is None:
        result = parsers.parse_media(path)
        check_result(kind, path, result)
        chunks, stats = result["chunks"], result["stats"]
    else:
        chunks = _transcribe_range(path, start_sec or 0.0, end_sec)
        stats = {"segment_count": len(chunks), "range_start_sec": start_sec or 0.0, "range_end_sec": end_sec}
        check_result(kind, path, {"chunks": chunks, "stats": stats})

    return {"chunks": chunks, "stats": stats}


# =====================================================================================
# write_kb_record
# =====================================================================================

_SCHEMA_WRITE_KB_RECORD = {
    "description": (
        "Validate một KBRecord theo schemas.KBRecord rồi ghi ra data/output/<record_id>.json. "
        "Gọi tool này cho mỗi đoạn nội dung có ý nghĩa nghiệp vụ đã trích xuất được (từ parse_document, "
        "extract_image_content hoặc transcribe_media). Nếu ghi lỗi vì field sai định dạng, sửa đúng field "
        "đó theo message trả về rồi gọi lại — không lặp lại y hệt input cũ."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "record": {
                "type": "object",
                "description": "Một KBRecord hoàn chỉnh, khớp schemas.KBRecord.",
                "properties": {
                    "record_id": {"type": "string", "description": "ID duy nhất cho record, không rỗng."},
                    "source_file": {"type": "string", "description": "Đường dẫn file nguồn."},
                    "source_type": {"type": "string", "enum": ["docx", "xlsx", "pdf", "image", "audio", "video"]},
                    "doc_type": {
                        "type": "string",
                        "enum": ["policy", "price_list", "promotion", "catalog", "training", "contract", "unknown"],
                    },
                    "content": {"type": "string", "description": "Nội dung gốc của đoạn, không rỗng."},
                    "location": {
                        "type": "string",
                        "description": "Vị trí trong file nguồn, ví dụ 'p.12', 'Sheet1!A5:D5', '00:02:15'.",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "store_code, sku, effective_date (định dạng YYYY-MM-DD nếu có), version, ...",
                    },
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                },
                "required": [
                    "record_id", "source_file", "source_type", "doc_type",
                    "content", "location", "metadata", "confidence",
                ],
            },
        },
        "required": ["record"],
    },
}

_EFFECTIVE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _check_effective_date(record):
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return
    effective_date = metadata.get("effective_date")
    if effective_date is None:
        return
    if not isinstance(effective_date, str) or not _EFFECTIVE_DATE_RE.match(effective_date):
        raise ToolError(f"field effective_date sai format, cần YYYY-MM-DD (nhận: {effective_date!r})")
    try:
        datetime.strptime(effective_date, "%Y-%m-%d")
    except ValueError:
        raise ToolError(f"field effective_date sai format, cần YYYY-MM-DD (ngày không hợp lệ: {effective_date!r})")


def _vietnamese_error_hint(err):
    err_type = err.get("type", "")
    if err_type == "missing":
        return "bị thiếu, cần cung cấp giá trị"
    if err_type in ("string_type", "string_too_short"):
        return f"phải là chuỗi không rỗng ({err.get('msg')})"
    if err_type in ("greater_than_equal", "less_than_equal"):
        return f"nằm ngoài khoảng cho phép ({err.get('msg')})"
    if err_type == "literal_error":
        return f"giá trị không hợp lệ, {err.get('msg')}"
    return err.get("msg", "không hợp lệ")


def _format_validation_errors(e):
    lines = []
    for err in e.errors():
        field = ".".join(str(p) for p in err["loc"]) or "(root)"
        lines.append(f"field {field} {_vietnamese_error_hint(err)}")
    return "KBRecord không hợp lệ:\n" + "\n".join(lines)


def write_kb_record(record):
    if not isinstance(record, dict):
        raise ToolError("record phải là object JSON khớp schemas.KBRecord.")

    _check_effective_date(record)

    try:
        kb_record = KBRecord(**record)
    except ValidationError as e:
        raise ToolError(_format_validation_errors(e)) from e

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", kb_record.record_id)
    out_path = os.path.join(OUTPUT_DIR, f"{safe_id}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(kb_record.model_dump(), f, ensure_ascii=False, indent=2)

    return {"status": "written", "path": out_path, "record_id": kb_record.record_id}


# =====================================================================================
# REGISTRY
# =====================================================================================

def build_registry(client):
    """Trả về dict tên tool -> {"function": callable, "schema": {"description","input_schema"}}."""
    return {
        "detect_file_type": {"function": detect_file_type, "schema": _SCHEMA_DETECT_FILE_TYPE},
        "parse_document": {"function": parse_document, "schema": _SCHEMA_PARSE_DOCUMENT},
        "extract_image_content": {
            "function": functools.partial(extract_image_content, client=client),
            "schema": _SCHEMA_EXTRACT_IMAGE_CONTENT,
        },
        "transcribe_media": {"function": transcribe_media, "schema": _SCHEMA_TRANSCRIBE_MEDIA},
        "write_kb_record": {"function": write_kb_record, "schema": _SCHEMA_WRITE_KB_RECORD},
    }
