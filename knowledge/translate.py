"""Nội dung AI sinh theo ngôn ngữ giao diện (vi | en).

Hồ sơ tài liệu, mô tả bảng, câu hỏi gợi ý, mô tả thực thể, nhãn quan hệ... được sinh 1 lần theo ngôn ngữ của
tài liệu (chính xác nhất), rồi bước `translate` của job knowledge_extract dịch sang ngôn ngữ còn lại và lưu vào
content_translations (khoá: workspace + ngôn ngữ đích + hash chuỗi gốc — chuỗi lặp giữa tài liệu chỉ dịch 1 lần).
Khi trả API, `localize()` duyệt JSON, tra cache 1 lần và thay chuỗi nào có bản dịch sang ngôn ngữ người gọi.
Chỉ chuỗi AI sinh mới có bản dịch -> id, mã, số, tên file không bao giờ bị thay.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from core.config import AppConfig
from core.db import session_scope
from core.i18n import detect_lang, get_lang, language_name
from core.logging import log_event
from core.models import ContentTranslation, DataTable, Document, Entity, Relation

logger = logging.getLogger(__name__)

BATCH = 40
BATCH_CHARS = 6000
_SYSTEM = (
    "You translate text written by an AI about a company's documents and data, for display in a business app. "
    "Translate each string of the input list into {target}. Keep proper names, product and company names, codes "
    "(SKU, site or protocol IDs), numbers, units, file names and Markdown exactly as they are. Keep it concise and natural; "
    "do not add explanations. Return ONLY JSON: {{\"t\": [ ...translations in the same order, same count... ]}}"
)


class _Out(BaseModel):
    t: list[str]


def text_hash(s: str) -> str:
    return hashlib.sha1(s.strip().encode("utf-8")).hexdigest()


def _other(lang: str) -> str:
    return "en" if lang == "vi" else "vi"


def _strings(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for v in values:
        if isinstance(v, str) and len(v.strip()) >= 2:
            out.append(v.strip())
        elif isinstance(v, (list, tuple)):
            out.extend(_strings(v))
    return out


def document_strings(session: Session, document_id: str) -> tuple[str, str, list[str]]:
    """-> (workspace_id, ngôn ngữ chính của tài liệu, các chuỗi AI sinh cần có bản dịch)."""
    doc = session.get(Document, document_id)
    if doc is None:
        return "", "vi", []
    prof = doc.semantic_profile or {}
    texts = _strings([doc.summary, prof.get("kind"), prof.get("audience"), prof.get("structure"), prof.get("quality_notes"),
                      prof.get("topics"), prof.get("suggested_questions"), (prof.get("time_range") or {}).get("note")])
    for t in session.execute(select(DataTable).where(DataTable.document_id == document_id, DataTable.is_active.is_(True))).scalars():
        sem = t.semantic or {}
        texts += _strings([t.description, sem.get("suggested_questions"), [c.get("label") for c in (sem.get("columns") or {}).values()]])
    texts += _strings(e.description for e in session.execute(
        select(Entity).where(Entity.workspace_id == doc.workspace_id, Entity.document_ids.contains([document_id]))).scalars())
    texts += _strings(r.label for r in session.execute(select(Relation).where(Relation.document_id == document_id)).scalars())
    doc_lang = (prof.get("language") or "").lower()
    doc_lang = doc_lang if doc_lang in ("vi", "en") else detect_lang(" ".join(texts[:30]))
    return doc.workspace_id, doc_lang, list(dict.fromkeys(texts))


def _batches(strings: list[str]) -> Iterable[list[str]]:
    """Lô tối đa BATCH chuỗi và ~BATCH_CHARS ký tự (câu trả lời hỏi đáp dài cả nghìn ký tự)."""
    batch: list[str] = []
    size = 0
    for x in strings:
        if batch and (len(batch) >= BATCH or size + len(x) > BATCH_CHARS):
            yield batch
            batch, size = [], 0
        batch.append(x)
        size += len(x)
    if batch:
        yield batch


def translate_strings(llm: Any, strings: list[str], target: str) -> dict[str, str]:
    """Dịch theo lô; lô nào lỗi/sai số lượng thì bỏ qua (chuỗi đó giữ ngôn ngữ gốc, lần chạy sau dịch tiếp)."""
    from core.llm import LLMOutputInvalid, LLMUnavailable

    out: dict[str, str] = {}
    for batch in _batches(strings):
        chars = sum(len(x) for x in batch)
        try:
            res = llm.complete_json("translate", system=_SYSTEM.format(target=language_name(target)),
                                    user=json.dumps({"items": batch}, ensure_ascii=False), schema=_Out,
                                    max_tokens=min(16000, 300 + 120 * len(batch) + chars), max_retries=1)
        except (LLMOutputInvalid, LLMUnavailable) as e:
            log_event(logger, "translate_batch_failed", level=logging.WARNING, target=target, size=len(batch), error=str(e)[:200])
            continue
        if len(res.t) != len(batch):
            log_event(logger, "translate_batch_mismatch", level=logging.WARNING, expected=len(batch), got=len(res.t))
            continue
        out.update({src: dst.strip() for src, dst in zip(batch, res.t) if dst and dst.strip()})
    return out


def translate_document(cfg: AppConfig, sm: sessionmaker, llm: Any, document_id: str, force: bool = False) -> dict[str, Any]:
    if llm is None:
        return {"skipped": "no_llm"}
    with session_scope() as s:
        ws, doc_lang, texts = document_strings(s, document_id)
        if not ws:
            return {"skipped": "no_document"}
        # chuỗi ngắn không đủ chữ để nhận diện -> theo ngôn ngữ tài liệu (chuỗi có dấu tiếng Việt -> vi)
        by_target = _missing(s, ws, texts, doc_lang, force=force)
    done = _translate_and_store(llm, ws, by_target)
    log_event(logger, "translate_done", document_id=document_id, doc_lang=doc_lang,
              pending={k: len(v) for k, v in by_target.items()}, translated=done)
    return {"doc_lang": doc_lang, "translated": done, "pending": {k: len(v) for k, v in by_target.items()}}


def _missing(s: Session, workspace_id: str, texts: Iterable[str], default_lang: str, force: bool = False) -> dict[str, list[str]]:
    """-> {ngôn ngữ đích: các chuỗi chưa có bản dịch sang ngôn ngữ đó}."""
    by_target: dict[str, list[str]] = {}
    for t in dict.fromkeys(x.strip() for x in texts if isinstance(x, str) and len(x.strip()) >= 2):
        by_target.setdefault(_other(detect_lang(t, default=default_lang)), []).append(t)
    if not force:
        for target, items in by_target.items():
            have = set(s.execute(select(ContentTranslation.source_hash).where(
                ContentTranslation.workspace_id == workspace_id, ContentTranslation.lang == target,
                ContentTranslation.source_hash.in_([text_hash(x) for x in items]))).scalars())
            by_target[target] = [x for x in items if text_hash(x) not in have]
    return by_target


def _translate_and_store(llm: Any, workspace_id: str, by_target: dict[str, list[str]]) -> int:
    done = 0
    for target, items in by_target.items():
        if not items:
            continue
        translated = translate_strings(llm, items, target)
        if translated:
            with session_scope() as s:
                rows = [{"workspace_id": workspace_id, "lang": target, "source_hash": text_hash(src), "text": dst} for src, dst in translated.items()]
                stmt = insert(ContentTranslation).values(rows)
                s.execute(stmt.on_conflict_do_update(index_elements=["workspace_id", "lang", "source_hash"], set_={"text": stmt.excluded.text}))
        done += len(translated)
    return done


# ------------------------------------------------------------------ hỏi đáp
# Câu hỏi người dùng gõ và câu trả lời được lưu nguyên văn (qa_traces, chat_messages, review_queue...). Ngay sau khi
# trả lời, các chuỗi đó được dịch sang ngôn ngữ còn lại ở nền; câu cũ chưa có bản dịch thì được dịch bù khi có
# người mở (localize(..., ensure=...)). Câu ngắn không dấu được coi là tiếng Anh (người Việt gõ có dấu).
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="translate")
_RETRY_AFTER_S = 600
_recent: dict[tuple[str, str], float] = {}
_recent_lock = threading.Lock()


def qa_strings(question: str | None, answer: str | None = None, decision_trace: dict[str, Any] | None = None) -> list[str]:
    """Chuỗi hiển thị của 1 lượt hỏi đáp: câu hỏi (+ tiêu đề phiên = 80 ký tự đầu), câu trả lời, phần giải thích."""
    dt = decision_trace or {}
    conf = dt.get("confidence") or {}
    return _strings([question, (question or "")[:80], answer, dt.get("summary"), dt.get("degraded"), dt.get("basis"),
                     conf.get("explanation"), conf.get("reasons")])


def translate_texts(llm: Any, workspace_id: str, texts: Iterable[str]) -> int:
    """Dịch (đồng bộ) các chuỗi chưa có bản dịch sang ngôn ngữ còn lại, lưu vào content_translations."""
    if llm is None or not workspace_id:
        return 0
    with session_scope() as s:
        by_target = _missing(s, workspace_id, texts, "en")
    return _translate_and_store(llm, workspace_id, by_target)


def translate_later(llm: Any, workspace_id: str | None, texts: Iterable[str]) -> None:
    """Như translate_texts nhưng chạy nền; chuỗi vừa thử trong 10 phút thì bỏ qua (không dịch lặp khi trang poll)."""
    if llm is None or not workspace_id:
        return
    now = time.monotonic()
    with _recent_lock:
        fresh = [t for t in dict.fromkeys(texts) if now - _recent.get((workspace_id, text_hash(t)), -1e9) > _RETRY_AFTER_S]
        for t in fresh:
            _recent[(workspace_id, text_hash(t))] = now
    if not fresh:
        return

    def _run() -> None:
        try:
            n = translate_texts(llm, workspace_id, fresh)
            log_event(logger, "translate_qa_done", workspace_id=workspace_id, strings=len(fresh), translated=n)
        except Exception as e:
            log_event(logger, "translate_qa_failed", level=logging.WARNING, workspace_id=workspace_id, error=f"{type(e).__name__}: {e}"[:300])
    _POOL.submit(_run)


def _collect(obj: Any, acc: set[str]) -> None:
    if isinstance(obj, str):
        if len(obj.strip()) >= 2:
            acc.add(obj.strip())
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect(v, acc)


def _replace(obj: Any, table: dict[str, str]) -> Any:
    if isinstance(obj, str):
        return table.get(text_hash(obj), obj) if len(obj.strip()) >= 2 else obj
    if isinstance(obj, dict):
        return {k: _replace(v, table) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace(v, table) for v in obj]
    return obj


def localize(session: Session, workspace_id: str, payload: Any, lang: str | None = None, ensure: Iterable[str] | None = None) -> Any:
    """Thay mọi chuỗi trong payload có bản dịch sang ngôn ngữ người gọi (mặc định ngôn ngữ của request).
    ensure: chuỗi (hỏi đáp) phải có bản dịch — chuỗi nào khác ngôn ngữ người gọi mà chưa có thì dịch bù ở nền."""
    lang = lang or get_lang()
    strings: set[str] = set()
    _collect(payload, strings)
    if not strings:
        return payload
    hashes = list({text_hash(s) for s in strings})
    table: dict[str, str] = {}
    for i in range(0, len(hashes), 1000):
        table.update(dict(session.execute(select(ContentTranslation.source_hash, ContentTranslation.text).where(
            ContentTranslation.workspace_id == workspace_id, ContentTranslation.lang == lang,
            ContentTranslation.source_hash.in_(hashes[i:i + 1000]))).all()))
    if ensure is not None:
        todo = [t for t in _strings(list(ensure)) if text_hash(t) not in table and detect_lang(t, default="en") != lang]
        if todo:
            from core.llm import get_llm

            translate_later(get_llm(), workspace_id, todo)
    return _replace(payload, table) if table else payload
