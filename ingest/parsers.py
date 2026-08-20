"""Parser cho pipeline ingest tài liệu retail: docx, xlsx, pdf, image, audio/video.

Mỗi hàm parse_* nhận đường dẫn file, trả về {"chunks": [...], "stats": {...}}.
Mỗi chunk tối thiểu có {"text", "location", "kind"}, có thể kèm thêm key riêng
theo loại file (ví dụ "base64"/"media_type" ở parse_image).
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

# =====================================================================================
# NHẬN DIỆN LOẠI FILE THEO MAGIC BYTES (không dựa vào đuôi file)
# =====================================================================================

FORMAT_TO_KIND = {
    "docx": "docx",
    "xlsx": "xlsx",
    "pdf": "pdf",
    "png": "image", "jpg": "image", "gif": "image", "bmp": "image", "webp": "image",
    "mp3": "audio", "wav": "audio", "flac": "audio", "ogg": "audio", "m4a": "audio",
    "mp4": "video", "mov": "video", "avi": "video", "mkv": "video",
}

IMAGE_MEDIA_TYPES = {
    "png": "image/png", "jpg": "image/jpeg", "gif": "image/gif",
    "bmp": "image/bmp", "webp": "image/webp",
}


def _require_file(file_path):
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Không tìm thấy file: {file_path}")


def _read_head(file_path, n=64):
    with open(file_path, "rb") as f:
        return f.read(n)


def _sniff_format(file_path):
    """Nhận diện định dạng thật của file qua magic bytes. Raise ValueError nếu không nhận diện được."""
    _require_file(file_path)
    head = _read_head(file_path, 64)
    if not head:
        raise ValueError(f"File rỗng, không thể nhận diện định dạng: {file_path}")

    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "gif"
    if head.startswith(b"BM"):
        return "bmp"
    if head[0:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[0:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[0:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "avi"
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"OggS"):
        return "ogg"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "mkv"
    if head[0:3] == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand.startswith(b"M4A"):
            return "m4a"
        if brand == b"qt  ":
            return "mov"
        return "mp4"
    if head[0:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        try:
            with zipfile.ZipFile(file_path) as zf:
                names = set(zf.namelist())
        except zipfile.BadZipFile as e:
            raise ValueError(f"File zip/office bị hỏng: {file_path} ({e})") from e
        if "word/document.xml" in names:
            return "docx"
        if "xl/workbook.xml" in names:
            return "xlsx"
        raise ValueError(f"File dạng zip nhưng không phải .docx/.xlsx được hỗ trợ: {file_path}")

    raise ValueError(
        f"Không nhận diện được định dạng file từ magic bytes: {file_path} "
        f"(16 byte đầu: {head[:16].hex()})"
    )


def detect_file_type(file_path):
    """Trả về loại file ở mức dispatch: 'docx' | 'xlsx' | 'pdf' | 'image' | 'audio' | 'video'."""
    fmt = _sniff_format(file_path)
    kind = FORMAT_TO_KIND.get(fmt)
    if kind is None:
        raise ValueError(f"Định dạng '{fmt}' được nhận diện nhưng chưa được hỗ trợ: {file_path}")
    return kind


# =====================================================================================
# PARSE DOCX
# =====================================================================================

_HEADING_STYLE_RE = re.compile(r"^Heading (\d+)$")


def parse_docx(file_path):
    _require_file(file_path)
    try:
        from docx import Document
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as e:
        raise ImportError("Thiếu thư viện python-docx. Cài đặt: pip install python-docx") from e

    try:
        doc = Document(file_path)
    except Exception as e:
        raise ValueError(f"File .docx bị hỏng hoặc không đúng định dạng: {file_path} ({e})") from e

    def iter_block_items(document):
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                yield "p", Paragraph(child, document)
            elif child.tag == qn("w:tbl"):
                yield "tbl", Table(child, document)

    def heading_level(paragraph):
        style_name = paragraph.style.name if paragraph.style else ""
        if style_name == "Title":
            return 0
        m = _HEADING_STYLE_RE.match(style_name or "")
        return int(m.group(1)) if m else None

    heading_stack = []  # list[(level, text)]
    table_counters = {}
    chunks = []
    paragraph_count = 0
    heading_count = 0
    table_count = 0
    char_count = 0

    for block_kind, item in iter_block_items(doc):
        if block_kind == "p":
            text = item.text.strip()
            if not text:
                continue
            level = heading_level(item)
            if level is not None:
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, text))
                heading_count += 1
                chunks.append({"text": text, "location": " > ".join(t for _, t in heading_stack), "kind": "heading"})
            else:
                paragraph_count += 1
                chunks.append({"text": text, "location": " > ".join(t for _, t in heading_stack), "kind": "text"})
            char_count += len(text)
        else:
            table_text = "\n".join(" | ".join(cell.text.strip() for cell in row.cells) for row in item.rows)
            if not table_text.strip():
                continue
            loc_key = " > ".join(t for _, t in heading_stack)
            table_counters[loc_key] = table_counters.get(loc_key, 0) + 1
            location = f"{loc_key} > Bảng {table_counters[loc_key]}" if loc_key else f"Bảng {table_counters[loc_key]}"
            chunks.append({"text": table_text, "location": location, "kind": "table"})
            table_count += 1
            char_count += len(table_text)

    stats = {
        "paragraph_count": paragraph_count,
        "heading_count": heading_count,
        "table_count": table_count,
        "character_count": char_count,
        "chunk_count": len(chunks),
    }
    return {"chunks": chunks, "stats": stats}


# =====================================================================================
# PARSE XLSX
# =====================================================================================

def _detect_header_row_count(ws, max_row, max_col, max_scan=6, bold_ratio_threshold=0.6):
    header_rows = 0
    for row in ws.iter_rows(min_row=1, max_row=min(max_row, max_scan), max_col=max_col):
        non_empty = [cell for cell in row if cell.value not in (None, "")]
        if not non_empty:
            if header_rows == 0:
                continue
            break
        bold_ratio = sum(1 for c in non_empty if c.font and c.font.bold) / len(non_empty)
        if bold_ratio >= bold_ratio_threshold:
            header_rows += 1
        else:
            break
    return header_rows or 1


def parse_xlsx(file_path):
    _require_file(file_path)
    try:
        from openpyxl import load_workbook
    except ImportError as e:
        raise ImportError("Thiếu thư viện openpyxl. Cài đặt: pip install openpyxl") from e

    try:
        wb = load_workbook(file_path, data_only=True)
    except Exception as e:
        raise ValueError(f"File .xlsx bị hỏng hoặc không đúng định dạng: {file_path} ({e})") from e

    chunks = []
    sheet_stats = {}
    total_data_rows = 0

    for ws in wb.worksheets:
        max_row, max_col = ws.max_row or 0, ws.max_column or 0
        if max_row == 0 or max_col == 0:
            sheet_stats[ws.title] = {"row_count": 0, "header": []}
            continue

        grid = [[cell.value for cell in row] for row in ws.iter_rows(min_row=1, max_row=max_row, max_col=max_col)]
        for merged_range in ws.merged_cells.ranges:
            fill_value = grid[merged_range.min_row - 1][merged_range.min_col - 1]
            for r in range(merged_range.min_row, merged_range.max_row + 1):
                for c in range(merged_range.min_col, merged_range.max_col + 1):
                    grid[r - 1][c - 1] = fill_value

        header_end = min(_detect_header_row_count(ws, max_row, max_col), max_row)

        headers = []
        for c in range(max_col):
            parts = []
            for r in range(header_end):
                val = grid[r][c]
                if val not in (None, "") and str(val) not in parts:
                    parts.append(str(val))
            headers.append(" - ".join(parts) if parts else f"cột {c + 1}")

        sheet_row_count = 0
        for r in range(header_end, max_row):
            row_values = grid[r]
            non_empty = [(headers[c], row_values[c]) for c in range(max_col) if row_values[c] not in (None, "")]
            if not non_empty:
                continue
            excel_row_number = r + 1
            distinct_values = {v for _, v in non_empty}
            if len(distinct_values) == 1:
                # merged cell chiếm trọn dòng (ví dụ dòng tiêu đề nhóm) -> đây không phải dữ liệu dạng bảng
                chunks.append({
                    "text": str(non_empty[0][1]),
                    "location": f"{ws.title}!A{excel_row_number}",
                    "kind": "text",
                })
            else:
                sentence = ", ".join(f"{name} là {value}" for name, value in non_empty)
                chunks.append({
                    "text": sentence,
                    "location": f"{ws.title}!A{excel_row_number}",
                    "kind": "row",
                })
            sheet_row_count += 1

        sheet_stats[ws.title] = {
            "header_row_count": header_end,
            "header": headers,
            "row_count": sheet_row_count,
        }
        total_data_rows += sheet_row_count

    stats = {
        "sheet_count": len(wb.worksheets),
        "sheets": sheet_stats,
        "row_count": total_data_rows,
        "chunk_count": len(chunks),
    }
    return {"chunks": chunks, "stats": stats}


# =====================================================================================
# PARSE PDF (Docling, không tự OCR)
# =====================================================================================

def _table_item_text(table_item, doc):
    try:
        df = table_item.export_to_dataframe(doc=doc)
        rows = [list(df.columns)] + df.astype(str).values.tolist()
        return "\n".join(" | ".join(str(v) for v in row) for row in rows)
    except Exception:
        pass
    try:
        return table_item.export_to_markdown(doc=doc)
    except Exception:
        pass
    cells = getattr(table_item.data, "table_cells", None) or []
    rows_by_idx = {}
    for c in cells:
        rows_by_idx.setdefault(c.start_row_offset_idx, []).append(c.text)
    return "\n".join(" | ".join(rows_by_idx[idx]) for idx in sorted(rows_by_idx))


def parse_pdf(file_path, ocr_threshold_chars_per_page=20):
    _require_file(file_path)
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as e:
        raise ImportError("Thiếu thư viện docling. Cài đặt: pip install docling") from e

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = False  # chỉ đo text layer sẵn có, không tự chạy OCR
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)})

    try:
        result = converter.convert(file_path)
    except Exception as e:
        raise ValueError(f"File .pdf bị hỏng hoặc không đọc được: {file_path} ({e})") from e

    doc = result.document
    page_count = len(doc.pages) if doc.pages else 1
    chars_per_page = {p: 0 for p in range(1, page_count + 1)}
    table_counters = {}
    chunks = []

    for text_item in doc.texts:
        text = text_item.text.strip()
        if not text:
            continue
        page_no = text_item.prov[0].page_no if text_item.prov else None
        if page_no is not None:
            chars_per_page[page_no] = chars_per_page.get(page_no, 0) + len(text)
        location = f"Trang {page_no}" if page_no is not None else "Không rõ trang"
        chunks.append({"text": text, "location": location, "kind": "text"})

    for table_item in doc.tables:
        table_text = _table_item_text(table_item, doc)
        if not table_text.strip():
            continue
        page_no = table_item.prov[0].page_no if table_item.prov else None
        table_counters[page_no] = table_counters.get(page_no, 0) + 1
        location = f"Trang {page_no} > Bảng {table_counters[page_no]}" if page_no is not None else \
            f"Không rõ trang > Bảng {table_counters[page_no]}"
        chunks.append({"text": table_text, "location": location, "kind": "table"})

    pages_needing_ocr = sorted(p for p, c in chars_per_page.items() if c < ocr_threshold_chars_per_page)
    stats = {
        "page_count": page_count,
        "character_count": sum(chars_per_page.values()),
        "chars_per_page": chars_per_page,
        "needs_ocr": bool(pages_needing_ocr),
        "pages_needing_ocr": pages_needing_ocr,
        "ocr_threshold_chars_per_page": ocr_threshold_chars_per_page,
        "table_count": len(doc.tables),
        "chunk_count": len(chunks),
    }
    return {"chunks": chunks, "stats": stats}


# =====================================================================================
# PARSE IMAGE (chỉ đọc raw bytes -> base64, không xử lý nội dung)
# =====================================================================================

def parse_image(file_path):
    _require_file(file_path)
    fmt = _sniff_format(file_path)
    if FORMAT_TO_KIND.get(fmt) != "image":
        raise ValueError(f"File không phải định dạng ảnh được hỗ trợ (nhận diện: {fmt}): {file_path}")

    with open(file_path, "rb") as f:
        raw = f.read()

    media_type = IMAGE_MEDIA_TYPES[fmt]
    chunk = {
        "text": "",
        "location": os.path.basename(file_path),
        "kind": "image",
        "base64": base64.b64encode(raw).decode("ascii"),
        "media_type": media_type,
    }
    stats = {"size_bytes": len(raw), "media_type": media_type, "format": fmt}
    return {"chunks": [chunk], "stats": stats}


# =====================================================================================
# PARSE AUDIO/VIDEO (faster-whisper, ffmpeg để tách audio từ video)
# =====================================================================================

def _format_timestamp(seconds):
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _find_ffmpeg_binary():
    for candidate in (
        os.environ.get("FFMPEG_BIN"),
        "ffmpeg",
        "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
    ):
        if not candidate:
            continue
        if os.path.isabs(candidate):
            if os.path.isfile(candidate):
                return candidate
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _extract_audio_with_ffmpeg(video_path):
    ffmpeg_bin = _find_ffmpeg_binary()
    if not ffmpeg_bin:
        raise RuntimeError(
            "Không tìm thấy ffmpeg trong PATH. Cài đặt bằng 'brew install ffmpeg' hoặc set biến môi trường FFMPEG_BIN."
        )
    fd, out_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    cmd = [ffmpeg_bin, "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000", out_path]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        if os.path.exists(out_path):
            os.remove(out_path)
        raise ValueError(f"Không thể tách audio từ video (ffmpeg lỗi): {video_path}\n{e.stderr}") from e
    return out_path


def parse_media(file_path, model_size="small", language="vi", device="auto", compute_type="default"):
    _require_file(file_path)
    fmt = _sniff_format(file_path)
    kind = FORMAT_TO_KIND.get(fmt)
    if kind not in ("audio", "video"):
        raise ValueError(f"File không phải định dạng audio/video được hỗ trợ (nhận diện: {fmt}): {file_path}")

    temp_audio_path = None
    used_ffmpeg = False
    try:
        if kind == "video":
            temp_audio_path = _extract_audio_with_ffmpeg(file_path)
            audio_path = temp_audio_path
            used_ffmpeg = True
        else:
            audio_path = file_path

        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise ImportError("Thiếu thư viện faster-whisper. Cài đặt: pip install faster-whisper") from e

        try:
            model = WhisperModel(model_size, device=device, compute_type=compute_type)
            segments, info = model.transcribe(audio_path, language=language)
        except Exception as e:
            raise ValueError(f"Không thể nhận dạng giọng nói, file có thể bị hỏng: {file_path} ({e})") from e

        chunks = []
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            chunks.append({
                "text": text,
                "location": _format_timestamp(seg.start),
                "kind": "segment",
                "end": _format_timestamp(seg.end),
            })

        stats = {
            "duration_seconds": getattr(info, "duration", None),
            "language": getattr(info, "language", language),
            "language_probability": getattr(info, "language_probability", None),
            "segment_count": len(chunks),
            "used_ffmpeg_extraction": used_ffmpeg,
            "source_format": fmt,
        }
        return {"chunks": chunks, "stats": stats}
    finally:
        if temp_audio_path and os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)


# =====================================================================================
# DISPATCH + CLI
# =====================================================================================

_PARSERS = {
    "docx": parse_docx,
    "xlsx": parse_xlsx,
    "pdf": parse_pdf,
    "image": parse_image,
    "audio": parse_media,
    "video": parse_media,
}


def parse(file_path):
    """Nhận diện loại file rồi gọi parser tương ứng."""
    kind = detect_file_type(file_path)
    return _PARSERS[kind](file_path)


def _for_display(result):
    chunks = []
    for chunk in result.get("chunks", []):
        chunk = dict(chunk)
        if "base64" in chunk:
            chunk["base64"] = f"<{len(chunk['base64'])} ký tự base64, đã lược bớt>"
        chunks.append(chunk)
    return {"chunks": chunks, "stats": result.get("stats", {})}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("Cách dùng: python -m ingest.parsers <file>", file=sys.stderr)
        return 2

    file_path = argv[0]
    try:
        kind = detect_file_type(file_path)
        print(f"Loại file nhận diện: {kind}", file=sys.stderr)
        result = parse(file_path)
    except Exception as e:
        print(f"Lỗi: {e}", file=sys.stderr)
        return 1

    print(json.dumps(_for_display(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
