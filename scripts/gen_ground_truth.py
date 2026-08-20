"""Sinh data/ground_truth.json: doc lai 25 file da sinh trong data/raw/ va ghi nhan METADATA THAT
(khong phai gia dinh) cho tung file - so trang, so sheet, so dong, so ky tu text that, thoi luong media.

Phuong phap do dac (tat ca deu do tren file that tren dia, khong hardcode):
- docx : so trang -> KHONG the render bang Word/LibreOffice that (khong co san). Dung pipeline
         mammoth (docx->html) + weasyprint (html->pdf, kho A4, font/margin xap xi Word mac dinh)
         + pypdf (dem trang tren PDF vua render). Day la proxy do dac, GHI RO la uoc luong qua
         renderer thay the, khong phai Word/LibreOffice that -> pages_method field.
         So ky tu: doc that tu python-docx (toan bo paragraph + text trong table).
- xlsx : so sheet, so dong/sheet, tong so dong -> doc that qua openpyxl.
- pdf  : so trang, so ky tu text trich duoc -> doc that qua pypdf. has_text_layer = it hon 20 ky tu
         trich duoc thi coi la KHONG co text layer (anh scan).
- png  : khong co OCR engine nao chay that trong pipeline nay -> dung "reference_text" = noi dung
         chu THAT da duoc ve len anh luc sinh (lay tu chinh script gen_images.py, khong bia dat).
         Ghi ro reference_text_source de nguoi dung biet day la ground truth tu nguon sinh anh,
         khong phai OCR output.
- audio/video: thoi luong -> do that qua ffprobe tren file that. reference_transcript = kich ban
         that da doc qua TTS (lay tu gen_audio_video.py), khong phai ASR.
"""
import glob
import json
import os
import subprocess
import sys

os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib")

sys.path.insert(0, os.path.dirname(__file__))

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "ground_truth.json")

FFPROBE_FULL = "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"
FFPROBE = FFPROBE_FULL if os.path.exists(FFPROBE_FULL) else "ffprobe"

_DOCX_RENDER_CSS = """
@page { size: A4; margin: 2cm 1.8cm; }
body { font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.15; }
h1 { font-size: 20pt; } h2 { font-size: 15pt; } h3 { font-size: 12pt; }
"""


def _rel(path):
    return os.path.relpath(path, os.path.join(os.path.dirname(__file__), "..")).replace(os.sep, "/")


def docx_metadata(path):
    import docx
    import mammoth
    import weasyprint
    from pypdf import PdfReader

    d = docx.Document(path)
    chars = sum(len(p.text) for p in d.paragraphs)
    for table in d.tables:
        for row in table.rows:
            for cell in row.cells:
                chars += len(cell.text)

    with open(path, "rb") as fh:
        html = mammoth.convert_to_html(fh).value
    full_html = f"<html><head><style>{_DOCX_RENDER_CSS}</style></head><body>{html}</body></html>"
    pdf_bytes = weasyprint.HTML(string=full_html).write_pdf()
    import io
    pages = len(PdfReader(io.BytesIO(pdf_bytes)).pages)

    return {
        "type": "docx",
        "characters": chars,
        "pages": pages,
        "pages_method": "rendered via mammoth(docx->html)+weasyprint(html->pdf, A4, Arial 11pt) "
                        "then counted with pypdf — proxy for MS Word pagination (no Word/LibreOffice "
                        "available in this environment), not exact Word page count",
    }


def xlsx_metadata(path):
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    sheets = {}
    total_rows = 0
    total_chars = 0
    for name in wb.sheetnames:
        ws = wb[name]
        rows = ws.max_row
        sheets[name] = rows
        total_rows += rows
        for row in ws.iter_rows(values_only=True):
            for cell in row:
                if isinstance(cell, str):
                    total_chars += len(cell)
    return {
        "type": "xlsx",
        "sheet_count": len(wb.sheetnames),
        "sheet_names": wb.sheetnames,
        "rows_per_sheet": sheets,
        "total_rows_incl_header": total_rows,
        "characters_in_string_cells": total_chars,
    }


def pdf_metadata(path):
    from pypdf import PdfReader
    r = PdfReader(path)
    text = "".join(p.extract_text() or "" for p in r.pages)
    has_text_layer = len(text.strip()) >= 20
    meta = {
        "type": "pdf",
        "pages": len(r.pages),
        "has_text_layer": has_text_layer,
        "characters_extracted": len(text),
    }
    return meta


def png_metadata(path, reference_text):
    from PIL import Image
    with Image.open(path) as im:
        w, h = im.size
    return {
        "type": "png",
        "width_px": w,
        "height_px": h,
        "has_text_layer": False,
        "reference_text": reference_text,
        "reference_text_source": "known ground truth: exact text drawn onto the image by scripts/gen_images.py "
                                  "(no OCR was run — this is the true source text, not an OCR hypothesis)",
        "reference_text_characters": len(reference_text),
    }


def _ffprobe_duration(path):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True)
    return round(float(out.stdout.strip()), 2)


def media_metadata(path, kind, reference_transcript):
    return {
        "type": kind,
        "duration_seconds": _ffprobe_duration(path),
        "reference_transcript": reference_transcript,
        "reference_transcript_source": "known ground truth: exact script text fed to the TTS engine "
                                        "(gTTS/pyttsx3) by scripts/gen_audio_video.py — not an ASR transcript",
        "reference_transcript_characters": len(reference_transcript),
    }


def main():
    result = {}

    for path in sorted(glob.glob(os.path.join(RAW_DIR, "docx", "*.docx"))):
        result[_rel(path)] = docx_metadata(path)

    for path in sorted(glob.glob(os.path.join(RAW_DIR, "xlsx", "*.xlsx"))):
        result[_rel(path)] = xlsx_metadata(path)

    for path in sorted(glob.glob(os.path.join(RAW_DIR, "pdf", "*.pdf"))):
        result[_rel(path)] = pdf_metadata(path)

    # PNG can dung lai reference_text that duoc tra ve boi chinh cac ham sinh anh (khong bia dat).
    import gen_images
    png_texts = {
        "01_poster_khuyen_mai.png": gen_images.poster_khuyen_mai,
        "02_bang_gia_viet_tay.png": gen_images.bang_gia_viet_tay,
        "03_anh_chup_man_hinh_pos.png": gen_images.anh_chup_man_hinh_pos,
        "04_bien_hieu_quay.png": gen_images.bien_hieu_quay,
        "05_phieu_bao_hanh.png": gen_images.phieu_bao_hanh,
    }
    for fname, fn in png_texts.items():
        ref_text = fn()  # deterministic (seed co dinh) -> ghi de file PNG giong het, chi de lay text that
        path = os.path.join(RAW_DIR, "image", fname)
        result[_rel(path)] = png_metadata(path, ref_text)

    import gen_audio_video
    for fname, text in gen_audio_video.AUDIO_SCRIPTS.items():
        path = os.path.join(RAW_DIR, "audio", fname)
        result[_rel(path)] = media_metadata(path, "audio", text)
    for fname, spec in gen_audio_video.VIDEO_SCRIPTS.items():
        path = os.path.join(RAW_DIR, "video", fname)
        result[_rel(path)] = media_metadata(path, "video", spec["narration"])

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Done: ground_truth.json written for {len(result)} files -> {os.path.abspath(OUT_PATH)}")


if __name__ == "__main__":
    main()
