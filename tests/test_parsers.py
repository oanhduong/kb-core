"""Pytest cho ingest/parsers.py.

Fixture nằm ở tests/fixtures/ — file nhỏ, cố định, commit vào repo (khác data/raw
vốn sinh ngẫu nhiên mỗi lần chạy scripts/gen_*.py). Xem tests/fixtures/generate_fixtures.py
nếu cần sinh lại.

Test dùng model faster-whisper "tiny" (thay vì "small" mặc định của parse_media) và
docling ở chế độ mặc định để cả suite chạy nhanh — model chỉ được load 1-2 lần vì
pytest chạy toàn bộ file trong cùng 1 process.
"""
import builtins
import os
import re
import shutil
import types

import pytest

from ingest import parsers

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def fx(name):
    return os.path.join(FIXTURES_DIR, name)


LOC_RE = {
    "xlsx": re.compile(r"^[^!]+!A\d+$"),
    "pdf": re.compile(r"^Trang \d+$"),
    "media": re.compile(r"^\d{2}:\d{2}:\d{2}$"),
}


# =====================================================================================
# detect_file_type — magic bytes, không dựa vào đuôi file
# =====================================================================================

@pytest.mark.parametrize("filename,expected_kind", [
    ("tiny.docx", "docx"),
    ("tiny.xlsx", "xlsx"),
    ("tiny_text.pdf", "pdf"),
    ("tiny_scan.pdf", "pdf"),
    ("tiny.png", "image"),
    ("tiny.wav", "audio"),
])
def test_detect_file_type_by_magic_bytes(filename, expected_kind):
    assert parsers.detect_file_type(fx(filename)) == expected_kind


@pytest.mark.parametrize("real_file,wrong_ext,expected_kind", [
    ("tiny.xlsx", "fake.pdf", "xlsx"),
    ("tiny.docx", "fake.png", "docx"),
    ("tiny.png", "fake.docx", "image"),
])
def test_detect_file_type_ignores_wrong_extension(tmp_path, real_file, wrong_ext, expected_kind):
    dest = tmp_path / wrong_ext
    shutil.copyfile(fx(real_file), dest)
    assert parsers.detect_file_type(str(dest)) == expected_kind


def test_detect_file_type_missing_file_raises_with_path():
    with pytest.raises(FileNotFoundError, match="khong_ton_tai"):
        parsers.detect_file_type(fx("khong_ton_tai.pdf"))


def test_detect_file_type_unrecognized_raises_value_error(tmp_path):
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"\x00\x01\x02 khong phai dinh dang nao ca")
    with pytest.raises(ValueError):
        parsers.detect_file_type(str(junk))


def test_detect_file_type_raises_for_recognized_but_unmapped_format(monkeypatch):
    monkeypatch.setattr(parsers, "_sniff_format", lambda p: "dinh_dang_la")
    with pytest.raises(ValueError, match="chưa được hỗ trợ"):
        parsers.detect_file_type(fx("tiny.png"))


def test_detect_file_type_empty_file_raises(tmp_path):
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    with pytest.raises(ValueError, match="rỗng"):
        parsers.detect_file_type(str(empty))


def test_sniff_format_zip_that_is_neither_docx_nor_xlsx_raises(tmp_path):
    import zipfile

    z = tmp_path / "plain.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("hello.txt", "khong phai docx/xlsx")
    with pytest.raises(ValueError, match="docx/.xlsx"):
        parsers._sniff_format(str(z))


def test_sniff_format_corrupted_zip_header_raises(tmp_path):
    # Header 4 byte dau khop signature PK\x03\x04 nhung phan con lai khong phai zip that.
    bad_zip = tmp_path / "bad.zip"
    bad_zip.write_bytes(b"PK\x03\x04" + b"\x00" * 60)
    with pytest.raises(ValueError, match="hỏng"):
        parsers._sniff_format(str(bad_zip))


@pytest.mark.parametrize("head_bytes,expected_fmt", [
    (b"\xff\xd8\xff\xe0" + b"\x00" * 10, "jpg"),
    (b"GIF89a" + b"\x00" * 10, "gif"),
    (b"BM" + b"\x00" * 10, "bmp"),
    (b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 10, "webp"),
    (b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 10, "wav"),
    (b"RIFF\x00\x00\x00\x00AVI \x00" + b"\x00" * 10, "avi"),
    (b"fLaC" + b"\x00" * 10, "flac"),
    (b"OggS" + b"\x00" * 10, "ogg"),
    (b"\x1a\x45\xdf\xa3" + b"\x00" * 10, "mkv"),
    (b"ID3" + b"\x00" * 10, "mp3"),
    (bytes([0xFF, 0xFB]) + b"\x00" * 10, "mp3"),
    (b"\x00\x00\x00\x18ftypM4A \x00" + b"\x00" * 10, "m4a"),
    (b"\x00\x00\x00\x18ftypqt  \x00" + b"\x00" * 10, "mov"),
    (b"\x00\x00\x00\x18ftypisom\x00" + b"\x00" * 10, "mp4"),
])
def test_sniff_format_recognizes_all_magic_byte_signatures(tmp_path, head_bytes, expected_fmt):
    f = tmp_path / "sample.bin"
    f.write_bytes(head_bytes)
    assert parsers._sniff_format(str(f)) == expected_fmt


# =====================================================================================
# parse_docx — heading hierarchy, bảng tách riêng
# =====================================================================================

def test_parse_docx_heading_hierarchy():
    result = parsers.parse_docx(fx("tiny.docx"))
    headings = [c for c in result["chunks"] if c["kind"] == "heading"]
    assert [h["text"] for h in headings] == ["CHƯƠNG I. QUY ĐỊNH CHUNG", "Điều 1. Mục đích"]
    assert headings[0]["location"] == "CHƯƠNG I. QUY ĐỊNH CHUNG"
    assert headings[1]["location"] == "CHƯƠNG I. QUY ĐỊNH CHUNG > Điều 1. Mục đích"


def test_parse_docx_paragraph_inherits_deepest_heading_location():
    result = parsers.parse_docx(fx("tiny.docx"))
    paragraphs = [c for c in result["chunks"] if c["kind"] == "text"]
    assert len(paragraphs) == 1
    assert paragraphs[0]["location"] == "CHƯƠNG I. QUY ĐỊNH CHUNG > Điều 1. Mục đích"


def test_parse_docx_table_is_separated_with_kind_table():
    result = parsers.parse_docx(fx("tiny.docx"))
    tables = [c for c in result["chunks"] if c["kind"] == "table"]
    assert len(tables) == 1
    table = tables[0]
    assert table["location"] == "CHƯƠNG I. QUY ĐỊNH CHUNG > Điều 1. Mục đích > Bảng 1"
    rows = table["text"].split("\n")
    assert len(rows) == 3
    assert rows[0] == "Cột A | Cột B | Cột C"
    assert result["stats"]["table_count"] == 1


def test_parse_docx_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        parsers.parse_docx(fx("khong_ton_tai.docx"))


def test_parse_docx_corrupted_raises_specific_message_not_empty_dict():
    with pytest.raises(ValueError) as exc_info:
        parsers.parse_docx(fx("corrupted.docx"))
    message = str(exc_info.value)
    assert "corrupted.docx" in message
    assert "hỏng" in message


def test_parse_docx_title_style_is_heading_level_0(tmp_path):
    from docx import Document

    doc = Document()
    doc.add_heading("TÀI LIỆU THỬ NGHIỆM", level=0)  # style "Title"
    doc.add_heading("Mục 1", level=1)
    path = tmp_path / "with_title.docx"
    doc.save(path)

    result = parsers.parse_docx(str(path))
    headings = [c for c in result["chunks"] if c["kind"] == "heading"]
    assert headings[0]["location"] == "TÀI LIỆU THỬ NGHIỆM"
    assert headings[1]["location"] == "TÀI LIỆU THỬ NGHIỆM > Mục 1"


def test_parse_docx_skips_blank_paragraphs_dedents_headings_and_skips_empty_tables(tmp_path):
    from docx import Document

    doc = Document()
    doc.add_heading("A", level=1)
    doc.add_heading("B", level=2)
    doc.add_paragraph("")  # đoạn văn trống -> phải bị bỏ qua
    doc.add_heading("C", level=1)  # quay lại level 1 -> phải pop "B" ra khỏi breadcrumb
    empty_table = doc.add_table(rows=1, cols=1)
    empty_table.cell(0, 0).text = "   "  # bảng chỉ có khoảng trắng -> phải bị bỏ qua
    path = tmp_path / "edge_cases.docx"
    doc.save(path)

    result = parsers.parse_docx(str(path))
    headings = [c for c in result["chunks"] if c["kind"] == "heading"]
    assert [h["text"] for h in headings] == ["A", "B", "C"]
    assert headings[2]["location"] == "C"  # "B" đã bị pop khỏi breadcrumb
    assert result["stats"]["table_count"] == 0
    assert all(c["text"].strip() for c in result["chunks"])  # không có chunk rỗng


@pytest.mark.parametrize("blocked_module,parse_fn,fixture_name", [
    ("docx", parsers.parse_docx, "tiny.docx"),
    ("openpyxl", parsers.parse_xlsx, "tiny.xlsx"),
    ("docling.document_converter", parsers.parse_pdf, "tiny_text.pdf"),
    ("faster_whisper", parsers.parse_media, "tiny.wav"),
])
def test_missing_optional_dependency_raises_clear_import_error(monkeypatch, blocked_module, parse_fn, fixture_name):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == blocked_module or name.startswith(blocked_module + "."):
            raise ImportError(f"mô phỏng thiếu {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError):
        parse_fn(fx(fixture_name))


# =====================================================================================
# parse_xlsx — header row detect, merged cell fill xuống ô con, location "sheet!A5"
# =====================================================================================

def test_parse_xlsx_header_row_detection():
    result = parsers.parse_xlsx(fx("tiny.xlsx"))
    sheets = result["stats"]["sheets"]
    assert sheets["Sheet1"]["header_row_count"] == 1
    assert sheets["Sheet1"]["header"] == ["sku", "ten"]
    assert sheets["Sheet2"]["header_row_count"] == 2
    assert sheets["Sheet2"]["header"] == ["NHÓM A - Cột 1", "NHÓM A - Cột 2"]


def test_parse_xlsx_merged_cell_fills_down_into_child_cells():
    result = parsers.parse_xlsx(fx("tiny.xlsx"))
    by_location = {c["location"]: c for c in result["chunks"]}
    # A3:A4 merged ("Danh mục X") -> phải xuất hiện ở CẢ dòng A3 lẫn dòng A4 sau khi fill.
    assert "Danh mục X" in by_location["Sheet2!A3"]["text"]
    assert "Danh mục X" in by_location["Sheet2!A4"]["text"]
    assert "Giá trị 1" in by_location["Sheet2!A3"]["text"]
    assert "Giá trị 2" in by_location["Sheet2!A4"]["text"]


def test_parse_xlsx_full_row_merge_becomes_text_note_not_table_row():
    result = parsers.parse_xlsx(fx("tiny.xlsx"))
    by_location = {c["location"]: c for c in result["chunks"]}
    note = by_location["Sheet2!A5"]
    assert note["kind"] == "text"
    assert note["text"] == "Ghi chú áp dụng cho cả nhóm"


def test_parse_xlsx_location_format():
    result = parsers.parse_xlsx(fx("tiny.xlsx"))
    assert result["chunks"], "cần có ít nhất 1 chunk để kiểm tra location"
    for chunk in result["chunks"]:
        assert LOC_RE["xlsx"].match(chunk["location"]), chunk["location"]


def test_detect_header_row_count_stops_at_blank_row():
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.append(["A", "B"])
    for c in range(1, 3):
        ws.cell(row=1, column=c).font = Font(bold=True)
    ws.append([None, None])  # dòng trống ngay sau header
    ws.append(["x", "y"])
    assert parsers._detect_header_row_count(ws, ws.max_row, ws.max_column) == 1


def test_parse_xlsx_skips_fully_blank_data_row(tmp_path):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.append(["a", "b"])
    ws.cell(row=1, column=1).font = Font(bold=True)
    ws.cell(row=1, column=2).font = Font(bold=True)
    ws.append([None, None])  # dòng dữ liệu hoàn toàn trống -> phải bị bỏ qua
    ws.append(["x", "y"])
    path = tmp_path / "blank_row.xlsx"
    wb.save(path)

    result = parsers.parse_xlsx(str(path))
    assert len(result["chunks"]) == 1
    assert result["chunks"][0]["location"] == "Sheet!A3"


def test_parse_xlsx_empty_workbook_returns_no_chunks_without_raising():
    result = parsers.parse_xlsx(fx("empty.xlsx"))
    assert result["chunks"] == []
    assert result["stats"]["row_count"] == 0


def test_parse_xlsx_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        parsers.parse_xlsx(fx("khong_ton_tai.xlsx"))


def test_parse_xlsx_corrupted_input_raises_specific_message():
    with pytest.raises(ValueError) as exc_info:
        parsers.parse_xlsx(fx("corrupted.docx"))
    assert "hỏng" in str(exc_info.value)


# =====================================================================================
# parse_pdf — Docling, không tự OCR, location "Trang N"
# =====================================================================================

def test_table_item_text_uses_dataframe_when_available():
    class FakeValues(list):
        def tolist(self):
            return list(self)

    class FakeDataFrame:
        def __init__(self, columns, rows):
            self.columns = columns
            self.values = FakeValues(rows)

        def astype(self, _to_type):
            return self

    class FakeTableItem:
        def export_to_dataframe(self, doc=None):
            return FakeDataFrame(["A", "B"], [["1", "2"]])

    assert parsers._table_item_text(FakeTableItem(), doc=None) == "A | B\n1 | 2"


def test_table_item_text_falls_back_to_markdown_when_dataframe_fails():
    class FakeTableItem:
        def export_to_dataframe(self, doc=None):
            raise RuntimeError("không hỗ trợ dataframe")

        def export_to_markdown(self, doc=None):
            return "| A | B |\n| 1 | 2 |"

    assert parsers._table_item_text(FakeTableItem(), doc=None) == "| A | B |\n| 1 | 2 |"


def test_table_item_text_falls_back_to_raw_cells_when_both_exports_fail():
    cells = [
        types.SimpleNamespace(start_row_offset_idx=0, text="A"),
        types.SimpleNamespace(start_row_offset_idx=0, text="B"),
        types.SimpleNamespace(start_row_offset_idx=1, text="1"),
    ]

    class FakeTableItem:
        data = types.SimpleNamespace(table_cells=cells)

        def export_to_dataframe(self, doc=None):
            raise RuntimeError("không hỗ trợ dataframe")

        def export_to_markdown(self, doc=None):
            raise RuntimeError("không hỗ trợ markdown")

    assert parsers._table_item_text(FakeTableItem(), doc=None) == "A | B\n1"


def test_parse_pdf_wraps_docling_convert_failure_as_value_error(monkeypatch):
    pytest.importorskip("docling")
    from docling.document_converter import DocumentConverter

    def fake_convert(self, file_path):
        raise RuntimeError("mô phỏng lỗi docling")

    monkeypatch.setattr(DocumentConverter, "convert", fake_convert)
    with pytest.raises(ValueError, match="không đọc được"):
        parsers.parse_pdf(fx("tiny_text.pdf"))


def test_parse_pdf_skips_blank_text_items_and_handles_unknown_page_for_tables(monkeypatch):
    pytest.importorskip("docling")
    from docling.document_converter import DocumentConverter

    class FakeProv:
        def __init__(self, page_no):
            self.page_no = page_no

    class FakeTextItem:
        def __init__(self, text, prov=None):
            self.text, self.prov = text, prov or []

    class FakeTableItem:
        def __init__(self):
            self.prov = [FakeProv(None)]
            self.data = types.SimpleNamespace(
                table_cells=[types.SimpleNamespace(start_row_offset_idx=0, text="X")]
            )

        def export_to_dataframe(self, doc=None):
            raise RuntimeError("no")

        def export_to_markdown(self, doc=None):
            raise RuntimeError("no")

    class FakeDoc:
        pages = {1: object()}
        texts = [FakeTextItem("   "), FakeTextItem("Nội dung thật", prov=[FakeProv(None)])]
        tables = [FakeTableItem()]

    class FakeResult:
        document = FakeDoc()

    def fake_convert(self, file_path):
        return FakeResult()

    monkeypatch.setattr(DocumentConverter, "convert", fake_convert)
    result = parsers.parse_pdf(fx("tiny_text.pdf"))
    assert [c["text"] for c in result["chunks"]] == ["Nội dung thật", "X"]
    text_chunk, table_chunk = result["chunks"]
    assert text_chunk["location"] == "Không rõ trang"
    assert table_chunk["location"] == "Không rõ trang > Bảng 1"


def test_parse_pdf_text_layer_no_ocr_needed():
    pytest.importorskip("docling")
    result = parsers.parse_pdf(fx("tiny_text.pdf"))
    assert result["stats"]["needs_ocr"] is False
    assert result["stats"]["page_count"] == 1
    assert result["stats"]["character_count"] > 20
    assert result["chunks"], "PDF có text layer phải trích được ít nhất 1 chunk"
    for chunk in result["chunks"]:
        assert LOC_RE["pdf"].match(chunk["location"]), chunk["location"]


def test_parse_pdf_scan_flags_needs_ocr_without_raising():
    pytest.importorskip("docling")
    result = parsers.parse_pdf(fx("tiny_scan.pdf"))
    assert result["stats"]["needs_ocr"] is True
    assert result["stats"]["pages_needing_ocr"] == [1]
    assert result["stats"]["character_count"] < result["stats"]["ocr_threshold_chars_per_page"]
    assert result["chunks"] == []


def test_parse_pdf_missing_file_raises():
    pytest.importorskip("docling")
    with pytest.raises(FileNotFoundError):
        parsers.parse_pdf(fx("khong_ton_tai.pdf"))


# =====================================================================================
# parse_image — chỉ đọc raw bytes -> base64, không xử lý nội dung
# =====================================================================================

def test_parse_image_base64_roundtrips_and_media_type():
    import base64

    result = parsers.parse_image(fx("tiny.png"))
    assert len(result["chunks"]) == 1
    chunk = result["chunks"][0]
    assert chunk["kind"] == "image"
    assert chunk["media_type"] == "image/png"
    assert chunk["location"] == "tiny.png"
    with open(fx("tiny.png"), "rb") as f:
        raw = f.read()
    assert base64.b64decode(chunk["base64"]) == raw
    assert result["stats"]["size_bytes"] == len(raw)


def test_parse_image_rejects_non_image_file():
    with pytest.raises(ValueError):
        parsers.parse_image(fx("tiny.docx"))


def test_parse_image_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        parsers.parse_image(fx("khong_ton_tai.png"))


# =====================================================================================
# parse_media — faster-whisper, location "00:02:15", ffmpeg cho video
# =====================================================================================

FFMPEG_MISSING = parsers._find_ffmpeg_binary() is None


def test_parse_media_audio_wav():
    pytest.importorskip("faster_whisper")
    result = parsers.parse_media(fx("tiny.wav"), model_size="tiny")
    assert 3.0 < result["stats"]["duration_seconds"] < 9.0
    assert result["stats"]["language"] == "vi"
    assert result["stats"]["used_ffmpeg_extraction"] is False
    assert isinstance(result["chunks"], list)
    for chunk in result["chunks"]:
        assert chunk["kind"] == "segment"
        assert LOC_RE["media"].match(chunk["location"]), chunk["location"]
        assert LOC_RE["media"].match(chunk["end"]), chunk["end"]


def test_parse_media_skips_blank_segments_using_fake_model(monkeypatch):
    pytest.importorskip("faster_whisper")
    import faster_whisper

    class FakeInfo:
        duration = 1.23
        language = "vi"
        language_probability = 0.99

    class FakeSegment:
        def __init__(self, start, end, text):
            self.start, self.end, self.text = start, end, text

    class FakeModel:
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, *a, **kw):
            segments = [FakeSegment(0.0, 1.0, "   "), FakeSegment(1.0, 2.0, "Xin chào")]
            return segments, FakeInfo()

    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)
    result = parsers.parse_media(fx("tiny.wav"), model_size="tiny")
    assert len(result["chunks"]) == 1
    assert result["chunks"][0]["text"] == "Xin chào"
    assert result["stats"]["segment_count"] == 1


def test_parse_media_wraps_whisper_failure_as_value_error(monkeypatch):
    pytest.importorskip("faster_whisper")
    import faster_whisper

    class FailingModel:
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, *a, **kw):
            raise RuntimeError("mô phỏng lỗi whisper")

    monkeypatch.setattr(faster_whisper, "WhisperModel", FailingModel)
    with pytest.raises(ValueError, match="Không thể nhận dạng"):
        parsers.parse_media(fx("tiny.wav"), model_size="tiny")


@pytest.mark.skipif(FFMPEG_MISSING, reason="ffmpeg không có trong PATH")
def test_parse_media_video_branch_extracts_audio_with_ffmpeg(monkeypatch):
    pytest.importorskip("faster_whisper")
    # tiny.wav khong phai video that, nhung ffmpeg van doc duoc noi dung audio cua no
    # -> du de kiem tra nhanh du code path "video" (trich audio qua ffmpeg) ma khong
    # can them 1 fixture video rieng.
    monkeypatch.setattr(parsers, "_sniff_format", lambda p: "mp4")
    result = parsers.parse_media(fx("tiny.wav"), model_size="tiny")
    assert result["stats"]["used_ffmpeg_extraction"] is True
    assert result["stats"]["source_format"] == "mp4"
    assert result["stats"]["duration_seconds"] > 0


@pytest.mark.skipif(FFMPEG_MISSING, reason="ffmpeg không có trong PATH")
def test_extract_audio_with_ffmpeg_produces_wav_then_is_cleaned_up_by_caller():
    out_path = parsers._extract_audio_with_ffmpeg(fx("tiny.wav"))
    try:
        assert os.path.isfile(out_path)
        assert out_path.endswith(".wav")
        assert os.path.getsize(out_path) > 0
    finally:
        os.remove(out_path)


def test_extract_audio_with_ffmpeg_raises_runtime_error_when_binary_missing(monkeypatch):
    monkeypatch.setattr(parsers, "_find_ffmpeg_binary", lambda: None)
    with pytest.raises(RuntimeError, match="Không tìm thấy ffmpeg"):
        parsers._extract_audio_with_ffmpeg(fx("tiny.wav"))


@pytest.mark.skipif(not os.path.isfile("/usr/bin/false"), reason="cần /usr/bin/false để mô phỏng ffmpeg lỗi")
def test_extract_audio_with_ffmpeg_wraps_process_failure(monkeypatch):
    monkeypatch.setenv("FFMPEG_BIN", "/usr/bin/false")
    with pytest.raises(ValueError, match="ffmpeg lỗi"):
        parsers._extract_audio_with_ffmpeg(fx("tiny.wav"))


def test_find_ffmpeg_binary_prefers_env_var(monkeypatch, tmp_path):
    fake_bin = tmp_path / "ffmpeg"
    fake_bin.write_text("fake binary, chi can ton tai la du cho _find_ffmpeg_binary")
    monkeypatch.setenv("FFMPEG_BIN", str(fake_bin))
    assert parsers._find_ffmpeg_binary() == str(fake_bin)


def test_find_ffmpeg_binary_returns_none_when_nothing_found(monkeypatch):
    monkeypatch.delenv("FFMPEG_BIN", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(os.path, "isfile", lambda _: False)
    assert parsers._find_ffmpeg_binary() is None


def test_parse_media_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        parsers.parse_media(fx("khong_ton_tai.mp3"))


def test_parse_media_rejects_totally_unrecognized_input():
    with pytest.raises(ValueError):
        parsers.parse_media(fx("corrupted.docx"))


def test_parse_media_rejects_recognized_but_non_audio_video_format():
    # tiny.png nhận diện đúng là "image" -> parse_media phải từ chối ở bước kiểm tra
    # kind (không phải ở bước sniff format như file rác), khác voi test ở trên.
    with pytest.raises(ValueError, match="audio/video"):
        parsers.parse_media(fx("tiny.png"))


# =====================================================================================
# Dispatch (parse) + CLI (main)
# =====================================================================================

def test_parse_dispatches_by_detected_type():
    result = parsers.parse(fx("tiny.docx"))
    assert result["stats"]["heading_count"] == 2
    result_xlsx = parsers.parse(fx("tiny.xlsx"))
    assert result_xlsx["stats"]["sheet_count"] == 2


def test_cli_main_prints_json_for_valid_file(capsys):
    exit_code = parsers.main([fx("tiny.docx")])
    assert exit_code == 0
    out, err = capsys.readouterr()
    assert "Loại file nhận diện: docx" in err
    assert '"kind": "heading"' in out


def test_cli_main_truncates_base64_in_output(capsys):
    exit_code = parsers.main([fx("tiny.png")])
    assert exit_code == 0
    out, _ = capsys.readouterr()
    assert "ký tự base64" in out
    assert "iVBOR" not in out  # khong in full base64 PNG ra terminal


def test_cli_main_wrong_arg_count(capsys):
    exit_code = parsers.main([])
    assert exit_code == 2
    _, err = capsys.readouterr()
    assert "Cách dùng" in err


def test_cli_main_reports_error_for_corrupted_file(capsys):
    exit_code = parsers.main([fx("corrupted.docx")])
    assert exit_code == 1
    _, err = capsys.readouterr()
    assert "Lỗi:" in err
