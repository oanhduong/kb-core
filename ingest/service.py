"""Nhận file upload (POST /ingest, make ingest) -> lưu file + tạo Document (pending) + job ingest_file.

C1 incremental: mỗi file 1 job riêng, chỉ xử lý đúng file đó. Idempotent theo sha256 nội dung file:
upload lại ĐÚNG file đã có (chưa xoá) -> trả document/job cũ, không xử lý lại. File đã bị xoá mềm mà
upload lại -> tạo document mới (bản xoá vẫn giữ để audit/khôi phục).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import AppConfig
from core.errors import ValidationFailed
from core.logging import log_event
from core.models import Document, Job, new_id
from kb.ids import file_sha256
from kb.lifecycle import derive_series_key, validate_replace_mode
from worker.queue import enqueue_job

logger = logging.getLogger(__name__)

_SAFE_NAME_RE = re.compile(r"[^\w.\- ]+", re.UNICODE)
EXT_SOURCE_TYPE = {".docx": "docx", ".xlsx": "xlsx", ".pdf": "pdf", ".png": "image", ".jpg": "image",
                   ".jpeg": "image", ".webp": "image", ".mp3": "audio", ".wav": "audio", ".m4a": "audio",
                   ".mp4": "video", ".mov": "video"}
DOC_TYPES = ("policy", "price_list", "promotion", "catalog", "training", "contract", "inventory", "supplier", "unknown")


@dataclass
class UploadOptions:
    title: str | None = None
    doc_type: str | None = None
    store_code: str | None = None
    effective_date: date | None = None
    version: str | None = None
    series_key: str | None = None
    replace_mode: str = "document"


@dataclass
class UploadResult:
    document_id: str
    job_id: str
    filename: str
    duplicate_of_upload: bool
    status: str


def safe_filename(name: str) -> str:
    base = os.path.basename(unicodedata.normalize("NFC", name or "file")).strip()
    base = _SAFE_NAME_RE.sub("_", base)[:180]
    return base or "file"


def humanize_title(filename: str) -> str:
    stem = os.path.splitext(filename)[0]
    stem = re.sub(r"^\d{1,3}[_\- ]+", "", stem)
    return re.sub(r"[_]+", " ", stem).strip() or filename


def register_upload(session: Session, cfg: AppConfig, *, src: BinaryIO | str, filename: str, opts: UploadOptions,
                    uploaded_by: str | None, batch_id: str | None = None) -> UploadResult:
    fname = safe_filename(filename)
    ext = os.path.splitext(fname)[1].lower()
    if ext not in cfg.ingest.allowed_extensions or ext not in EXT_SOURCE_TYPE:
        raise ValidationFailed(f"Định dạng {ext or '(không có đuôi)'} không được hỗ trợ. Hỗ trợ: {', '.join(cfg.ingest.allowed_extensions)}",
                               context={"filename": fname})
    if opts.doc_type and opts.doc_type not in DOC_TYPES:
        raise ValidationFailed(f"doc_type không hợp lệ: {opts.doc_type}. Hợp lệ: {', '.join(DOC_TYPES)}")
    validate_replace_mode(opts.replace_mode)

    doc_id = new_id()
    folder = os.path.join(cfg.ingest.storage_dir, doc_id)
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, fname)
    max_bytes = cfg.ingest.max_upload_mb * 1024 * 1024
    written = 0
    with open(dest, "wb") as out:
        if isinstance(src, str):
            with open(src, "rb") as f:
                shutil.copyfileobj(f, out)
            written = os.path.getsize(dest)
        else:
            while True:
                block = src.read(1 << 20)
                if not block:
                    break
                written += len(block)
                if written > max_bytes:
                    out.close()
                    shutil.rmtree(folder, ignore_errors=True)
                    raise ValidationFailed(f"File vượt giới hạn {cfg.ingest.max_upload_mb}MB", context={"filename": fname})
                out.write(block)
    if written == 0:
        shutil.rmtree(folder, ignore_errors=True)
        raise ValidationFailed("File rỗng", context={"filename": fname})
    digest = file_sha256(dest)

    existing = session.execute(select(Document).where(Document.content_hash == digest, Document.deleted_at.is_(None))
                               .order_by(Document.created_at)).scalars().first()
    if existing is not None:
        shutil.rmtree(folder, ignore_errors=True)
        job = session.execute(select(Job).where(Job.document_id == existing.id, Job.type == "ingest_file")
                              .order_by(Job.created_at.desc())).scalars().first()
        if job is None:
            job = enqueue_job(session, "ingest_file", {"document_id": existing.id}, document_id=existing.id,
                              created_by=uploaded_by, idempotency_key=f"ingest:{existing.id}")
        log_event(logger, "upload_duplicate_file", document_id=existing.id, filename=fname)
        return UploadResult(existing.id, job.id, existing.filename, True, existing.status)

    doc = Document(
        id=doc_id, filename=fname, title=(opts.title or humanize_title(fname)).strip(),
        series_key=opts.series_key or derive_series_key(fname), source_type=EXT_SOURCE_TYPE[ext],
        doc_type=opts.doc_type or "unknown", store_code=opts.store_code, effective_date=opts.effective_date,
        version=opts.version, content_hash=digest, storage_path=dest, size_bytes=written, status="pending",
        is_active=False, replace_mode=opts.replace_mode, uploaded_by=uploaded_by,
        meta={"doc_type_source": "user" if opts.doc_type else "auto",
              "effective_date_source": "user" if opts.effective_date else "auto"},
    )
    session.add(doc)
    session.flush()
    job = enqueue_job(session, "ingest_file", {"document_id": doc.id}, document_id=doc.id, batch_id=batch_id,
                      created_by=uploaded_by, idempotency_key=f"ingest:{doc.id}")
    log_event(logger, "upload_registered", document_id=doc.id, filename=fname, size=written, job_id=job.id)
    return UploadResult(doc.id, job.id, fname, False, doc.status)
