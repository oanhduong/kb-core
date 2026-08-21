"""Invariant check ở tầng code, chạy ngay sau mỗi lần gọi parser trong ingest/parsers.py.

Đây KHÔNG phải validate input người dùng — parser input đã hợp lệ (qua được ingest/parsers.py)
tới bước này. check_result() bắt các lỗi lẽ ra không bao giờ được xảy ra nếu code parser đúng:
- chunk rỗng trong khi stats báo có nội dung
- location sai định dạng đã quy ước cho từng loại file
- chunk bị mất ký tự/byte so với khi đọc trực tiếp lại từ file gốc

Fail ở đây luôn là bug trong ingest/parsers.py, không phải lỗi tham số của agent/tools.py —
raise ParserError và KHÔNG retry (agent/loop.py dừng hẳn file đang xử lý khi gặp lỗi này).
"""
import base64
import os
import re

from ingest import parsers


class ParserError(Exception):
    """Vi phạm invariant tầng code sau khi parse — luôn là bug ở ingest/parsers.py, không retry."""


# =====================================================================================
# CHECK CHUNG CHO MỌI LOẠI FILE
# =====================================================================================

def _check_common(file_path, kind, chunks):
    if not isinstance(chunks, list):
        raise ParserError(f"'chunks' phải là list, nhận {type(chunks).__name__} cho {file_path}")

    for c in chunks:
        if not isinstance(c, dict) or "text" not in c or "location" not in c or "kind" not in c:
            raise ParserError(f"Chunk thiếu key bắt buộc (text/location/kind): {c!r} trong {file_path}")
        if not isinstance(c["text"], str):
            raise ParserError(f"Chunk có text không phải string: {c!r} trong {file_path}")
        if not isinstance(c["location"], str):
            raise ParserError(f"Chunk có location không phải string: {c!r} trong {file_path}")

        # parse_image cố tình để text="" (không OCR ảnh) — hợp lệ, mọi loại khác thì không.
        if kind != "image":
            if not c["text"].strip():
                raise ParserError(f"Chunk rỗng (chỉ toàn khoảng trắng) không hợp lệ: {c!r} trong {file_path}")
            if c["text"] != c["text"].strip():
                raise ParserError(f"Chunk text chưa được strip khoảng trắng đầu/cuối: {c!r} trong {file_path}")


# =====================================================================================
# DOCX
# =====================================================================================

def _raw_docx_char_count(file_path):
    """Đếm ký tự trực tiếp từ file docx, độc lập với vòng lặp trong parse_docx."""
    from docx import Document

    doc = Document(file_path)
    total = 0
    for p in doc.paragraphs:
        text = p.text.strip()
        if text:
            total += len(text)
    for table in doc.tables:
        table_text = "\n".join(" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows)
        if table_text.strip():
            total += len(table_text)
    return total


def _check_docx(file_path, chunks, stats):
    if stats.get("chunk_count") != len(chunks):
        raise ParserError(
            f"stats.chunk_count ({stats.get('chunk_count')}) khác số chunk thực tế ({len(chunks)}) trong {file_path}"
        )
    if stats.get("character_count", 0) > 0 and not chunks:
        raise ParserError(
            f"stats báo có {stats['character_count']} ký tự nhưng parse_docx trả về 0 chunk cho {file_path}"
        )

    for c in chunks:
        # Chunk kind=text hợp lệ có location rỗng khi đứng trước heading đầu tiên trong file.
        if c["kind"] in ("heading", "table") and not c["location"].strip():
            raise ParserError(f"Chunk kind={c['kind']} có location rỗng: {c!r} trong {file_path}")

    raw_chars = _raw_docx_char_count(file_path)
    chunk_chars = sum(len(c["text"]) for c in chunks)
    if chunk_chars != raw_chars:
        raise ParserError(
            f"Mất ký tự khi parse docx {file_path}: đọc trực tiếp từ file được {raw_chars} ký tự "
            f"nhưng chunks trả về chỉ có {chunk_chars} ký tự. Đây là bug ở parse_docx, không retry."
        )


# =====================================================================================
# XLSX
# =====================================================================================

_XLSX_LOC_RE = re.compile(r"^([^!]+)!A(\d+)$")


def _check_xlsx(file_path, chunks, stats):
    if stats.get("chunk_count") != len(chunks):
        raise ParserError(
            f"stats.chunk_count ({stats.get('chunk_count')}) khác số chunk thực tế ({len(chunks)}) trong {file_path}"
        )
    if stats.get("row_count", 0) > 0 and not chunks:
        raise ParserError(
            f"stats báo có {stats['row_count']} dòng dữ liệu nhưng parse_xlsx trả về 0 chunk cho {file_path}"
        )

    from openpyxl import load_workbook

    wb = load_workbook(file_path, data_only=True)
    sheet_names = set(wb.sheetnames)

    for c in chunks:
        loc = c["location"]
        m = _XLSX_LOC_RE.match(loc)
        if not m:
            raise ParserError(f"Location xlsx sai định dạng (cần 'TênSheet!A<số dòng>'): {loc!r} trong {file_path}")
        sheet, row_no = m.group(1), int(m.group(2))
        if sheet not in sheet_names:
            raise ParserError(f"Chunk tham chiếu sheet '{sheet}' không tồn tại trong {file_path}")

        ws = wb[sheet]
        raw_values = [
            str(cell.value).strip()
            for cell in ws[row_no]
            if cell.value not in (None, "") and str(cell.value).strip()
        ]
        missing = [v for v in raw_values if v not in c["text"]]
        if missing:
            raise ParserError(
                f"Mất nội dung khi parse xlsx {file_path} tại {loc}: giá trị cell {missing} "
                f"không xuất hiện trong text chunk {c['text']!r}. Đây là bug ở parse_xlsx, không retry."
            )


# =====================================================================================
# PDF
# =====================================================================================

_PDF_LOC_RE = re.compile(r"^(?:Trang \d+|Không rõ trang)(?: > Bảng \d+)?$")


def _check_pdf(file_path, chunks, stats):
    if stats.get("chunk_count") != len(chunks):
        raise ParserError(
            f"stats.chunk_count ({stats.get('chunk_count')}) khác số chunk thực tế ({len(chunks)}) trong {file_path}"
        )
    if stats.get("character_count", 0) > 0 and not chunks:
        raise ParserError(
            f"stats báo có {stats['character_count']} ký tự nhưng parse_pdf trả về 0 chunk cho {file_path}"
        )

    table_chunks = [c for c in chunks if c["kind"] == "table"]
    if stats.get("table_count") != len(table_chunks):
        raise ParserError(
            f"stats.table_count ({stats.get('table_count')}) khác số chunk kind=table thực tế "
            f"({len(table_chunks)}) trong {file_path}"
        )

    text_char_total = sum(len(c["text"]) for c in chunks if c["kind"] == "text")
    if text_char_total != stats.get("character_count"):
        raise ParserError(
            f"Mất ký tự khi parse pdf {file_path}: stats.character_count={stats.get('character_count')} "
            f"nhưng tổng ký tự các chunk kind=text chỉ có {text_char_total}. Đây là bug ở parse_pdf, không retry."
        )

    for c in chunks:
        if not _PDF_LOC_RE.match(c["location"]):
            raise ParserError(f"Location pdf sai định dạng: {c['location']!r} trong {file_path}")


# =====================================================================================
# IMAGE
# =====================================================================================

def _check_image(file_path, chunks, stats):
    if len(chunks) != 1:
        raise ParserError(f"parse_image phải trả về đúng 1 chunk, nhận được {len(chunks)} cho {file_path}")

    chunk = chunks[0]
    if chunk.get("kind") != "image":
        raise ParserError(f"Chunk ảnh sai kind (nhận '{chunk.get('kind')}', cần 'image') cho {file_path}")

    expected_location = os.path.basename(file_path)
    if chunk["location"] != expected_location:
        raise ParserError(
            f"Location ảnh sai định dạng, cần đúng tên file '{expected_location}', "
            f"nhận được {chunk['location']!r} cho {file_path}"
        )

    b64 = chunk.get("base64", "")
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as e:
        raise ParserError(f"base64 của ảnh {file_path} không giải mã được: {e}") from e

    if len(raw) != stats.get("size_bytes"):
        raise ParserError(
            f"Mất dữ liệu khi encode base64 ảnh {file_path}: file gốc {stats.get('size_bytes')} byte "
            f"nhưng base64 giải mã ra {len(raw)} byte. Đây là bug ở parse_image, không retry."
        )

    if chunk.get("media_type") not in parsers.IMAGE_MEDIA_TYPES.values():
        raise ParserError(f"media_type ảnh không hợp lệ: {chunk.get('media_type')!r} cho {file_path}")


# =====================================================================================
# AUDIO / VIDEO
# =====================================================================================

_TIME_LOC_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})$")


def _hms_to_seconds(hms):
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s


def _check_media(file_path, chunks, stats):
    if stats.get("segment_count") != len(chunks):
        raise ParserError(
            f"stats.segment_count ({stats.get('segment_count')}) khác số chunk thực tế "
            f"({len(chunks)}) khi phiên âm {file_path}"
        )

    prev_start = -1
    for c in chunks:
        loc, end = c["location"], c.get("end", "")
        if not _TIME_LOC_RE.match(loc):
            raise ParserError(f"Location audio/video sai định dạng (cần HH:MM:SS): {loc!r} trong {file_path}")
        if not _TIME_LOC_RE.match(end):
            raise ParserError(f"Trường 'end' của segment sai định dạng (cần HH:MM:SS): {end!r} trong {file_path}")

        start_s, end_s = _hms_to_seconds(loc), _hms_to_seconds(end)
        if end_s < start_s:
            raise ParserError(f"Segment có end ({end}) sớm hơn start ({loc}) trong {file_path}")
        if start_s < prev_start:
            raise ParserError(f"Các segment audio/video không theo thứ tự thời gian tăng dần trong {file_path}")
        prev_start = start_s

    duration = stats.get("duration_seconds")
    if duration is not None and chunks:
        last_end_s = _hms_to_seconds(chunks[-1]["end"])
        if last_end_s > round(duration) + 2:  # dung sai làm tròn giây
            raise ParserError(
                f"Segment cuối ({chunks[-1]['end']}) vượt quá thời lượng file ({duration}s) "
                f"trong {file_path} — có thể bug tính timestamp."
            )


# =====================================================================================
# DISPATCH
# =====================================================================================

_CHECKERS = {
    "docx": _check_docx,
    "xlsx": _check_xlsx,
    "pdf": _check_pdf,
    "image": _check_image,
    "audio": _check_media,
    "video": _check_media,
}


def check_result(kind, file_path, result):
    """Chạy invariant check cho kết quả {"chunks": [...], "stats": {...}} của một lần parse.

    Raise ParserError nếu vi phạm — luôn là bug code, không retry, dừng xử lý file đó.
    """
    if not isinstance(result, dict) or "chunks" not in result or "stats" not in result:
        raise ParserError(f"Kết quả parse thiếu 'chunks' hoặc 'stats' cho {file_path} (kind={kind})")

    chunks, stats = result["chunks"], result["stats"]
    if not isinstance(stats, dict):
        raise ParserError(f"'stats' phải là dict, nhận {type(stats).__name__} cho {file_path}")

    _check_common(file_path, kind, chunks)

    checker = _CHECKERS.get(kind)
    if checker is None:
        raise ParserError(f"Không có invariant check cho loại file '{kind}': {file_path}")
    checker(file_path, chunks, stats)
