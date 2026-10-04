"""Job ingest_file (C1) — xử lý ĐÚNG 1 file, không chạy lại toàn bộ KB.

    detect type -> parse (sandbox không network nếu bật) / vision cho ảnh & trang PDF scan
    -> đơn vị record (ingest/units.py) -> metadata (rule; LLM chỉ để phân loại doc_type khi rule bó tay)
    -> kb_write_records qua gateway (dry-run validate -> commit: upsert record + chunk + replace/dedup)
    -> embed chunk (progress) -> phát hiện mâu thuẫn giá -> đẩy job graph_sync (A6, chỉ phần graph
    của file này) -> done | blocked.

Mọi đọc file/ghi KB đi qua security.gateway (cùng policy + audit append-only với agent — F6). Nội dung
có dấu hiệu prompt injection -> file BLOCKED, record vào trạng thái quarantined (không phục vụ QA) chờ
admin duyệt (POST /kb/documents/{id}/approve). Chế độ ingest.mode=agent dùng agent/loop.py (verifier
+ bounded retry) thay cho bước unit/metadata xác định.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import date
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import sessionmaker

from core.config import AppConfig, read_prompt
from core.db import session_scope
from core.llm import AnthropicCompatAdapter, LLMClient, LLMOutputInvalid, LLMUnavailable
from core.logging import log_context, log_event
from core.models import Document, KBRecordRow, new_id, utcnow
from graph.sync import units_cache_path
from ingest.invariants import ParserError, check_result
from ingest.metadata import classify_doc_type_by_rules, extract_effective_date, extract_version, find_skus, record_fields
from ingest.units import RecordUnit, make_units, save_units
from kb.chunking import embed_chunks, rebuild_chunks
from kb.ids import content_hash, record_id_for
from kb.lifecycle import activate_document
from kb.repository import active_price_records_for_skus, bump_kb_version
from observability import tracing
from qa.citation import format_citation
from qa.conflicts import detect_conflicts, extract_price_facts, persist_conflicts
from schemas import KBRecord
from observability.retention import monthly_gateway_log_path
from security.gateway import build_ingest_tools, build_pipeline_gateway
from security.provenance import scan_for_suspicious_content, unwrap_untrusted, wrap_untrusted
from worker.queue import JobBlocked, enqueue_job, update_progress

logger = logging.getLogger(__name__)

SUPPORTED_KINDS = ("docx", "xlsx", "pdf", "image", "audio", "video")
MAX_OCR_PAGES = 20


class _DocTypeOut(BaseModel):
    doc_type: str
    confidence: float = Field(ge=0, le=1)


class IngestPipeline:
    def __init__(self, cfg: AppConfig, sm: sessionmaker, llm: LLMClient | None, embedder: Any,
                 audit_log: Any = None) -> None:
        self.cfg = cfg
        self.sm = sm
        self.llm = llm
        self.embedder = embedder
        if audit_log is None:
            from observability.retention import RotatingAuditLog

            audit_log = RotatingAuditLog()
        self.audit_log = audit_log

    # ------------------------------------------------------------------ gateway impls
    def _parse_raw(self, path: str, kind: str) -> dict[str, Any]:
        """READ tool: parse + invariant check. Sandbox: container không network, rootfs read-only."""
        mode = self.cfg.ingest.sandbox_mode
        if mode == "sidecar":
            from security.parser_sidecar import request_parse

            result = request_parse(self.cfg.ingest.sidecar_work_dir, path, kind, self.cfg.ingest.sandbox_timeout_seconds)
        elif mode == "docker":
            from security.sandbox import run_parser

            with tempfile.TemporaryDirectory(prefix="retailkb-parse-") as out_dir:
                fname = os.path.basename(path)
                res = run_parser(["python", "-m", "ingest.parsers", f"/input/{fname}", "--json-out", "/output/parsed.json"],
                                 input_dir=os.path.dirname(path), output_dir=out_dir, image=self.cfg.ingest.sandbox_image,
                                 timeout=self.cfg.ingest.sandbox_timeout_seconds)
                if res["exit_code"] != 0:
                    raise ParserError(f"Parser trong sandbox lỗi (exit={res['exit_code']}): {res['logs'][-800:]}")
                with open(os.path.join(out_dir, "parsed.json"), encoding="utf-8") as f:
                    result = json.load(f)
                result.pop("kind", None)
        else:
            from ingest import parsers

            if kind in ("audio", "video"):
                result = parsers.parse_media(path, model_size=self.cfg.ingest.whisper_model_size)
            else:
                result = parsers._PARSERS[kind](path)  # noqa: SLF001
        check_result(kind, path, result)
        return result

    def _kb_dry_run(self, document_id: str, records: list[dict[str, Any]], quarantine: bool = False) -> dict[str, Any]:
        for r in records:
            KBRecord(**{k: r[k] for k in ("record_id", "source_file", "source_type", "doc_type", "content", "location",
                                          "metadata", "confidence", "suspicious_content")})
        return {"document_id": document_id, "records": len(records), "quarantine": quarantine,
                "record_ids_preview": [r["record_id"] for r in records[:5]]}

    def _kb_commit(self, document_id: str, records: list[dict[str, Any]], quarantine: bool = False) -> dict[str, Any]:
        with session_scope() as s:
            doc = s.get(Document, document_id, with_for_update=True)
            if doc is None:
                raise JobBlocked("Document đã bị xoá trong lúc ingest")
            rows: list[KBRecordRow] = []
            for r in records:
                row = s.get(KBRecordRow, r["record_id"])
                if row is not None and row.manually_edited:
                    rows.append(row)  # giữ bản admin đã sửa tay, không ghi đè khi ingest lại
                    continue
                if row is None:
                    row = KBRecordRow(record_id=r["record_id"], document_id=doc.id)
                    s.add(row)
                row.source_file, row.source_type, row.doc_type = r["source_file"], r["source_type"], r["doc_type"]
                row.content, row.location, row.meta = r["content"], r["location"], r["metadata"]
                row.confidence, row.suspicious_content = r["confidence"], r["suspicious_content"]
                row.store_code, row.skus, row.category = r["store_code"], r["skus"], r["category"]
                row.effective_date = date.fromisoformat(r["effective_date"]) if r["effective_date"] else None
                row.version, row.entity_key, row.content_hash = r["version"], r["entity_key"], r["content_hash"]
                row.status, row.is_active, row.deleted_at, row.valid_to = ("quarantined", False, None, None) if quarantine else ("active", True, None, None)
                rows.append(row)
            s.flush()
            chunks = rebuild_chunks(s, self.cfg, doc, rows)
            doc.record_count = len(rows)
            summary: dict[str, Any] = {"records": len(rows), "chunks": len(chunks)}
            if quarantine:
                doc.status, doc.is_active = "blocked", False
                bump_kb_version(s)
            else:
                summary.update(activate_document(s, doc))
            return summary

    # ------------------------------------------------------------------ helpers
    def _vision_text(self, gateway: Any, ref: str, run_id: str, file_id: str) -> str:
        out = gateway.call_tool("extract_image_content", {"path_or_page": ref}, requested_by="ingest-pipeline",
                                run_id=run_id, file_id=file_id)
        return unwrap_untrusted(out.get("extracted_text", "")).strip()

    def _classify(self, doc: Document, units: list[RecordUnit]) -> tuple[str, str]:
        head = "\n".join(u.content for u in units[:8])[:3000]
        dt = classify_doc_type_by_rules(doc.filename, head, doc.source_type)
        if dt:
            return dt, "rule"
        if self.llm is None:
            return "unknown", "none"
        try:
            out = self.llm.complete_json("doc_classification", system=read_prompt("doc_classify", self.cfg),
                                         user=f"Tên file: {doc.filename}\n" + wrap_untrusted(head, source=doc.filename),
                                         schema=_DocTypeOut, max_tokens=80, max_retries=1)
            from ingest.service import DOC_TYPES

            if out.doc_type in DOC_TYPES and out.confidence >= 0.5:
                return out.doc_type, "llm"
        except (LLMOutputInvalid, LLMUnavailable) as e:
            log_event(logger, "doc_classify_failed", level=logging.WARNING, error=str(e)[:200])
        return "unknown", "none"

    def _build_records(self, doc: Document, units: list[RecordUnit]) -> list[dict[str, Any]]:
        out, seen = [], {}
        doc_eff = doc.effective_date.isoformat() if doc.effective_date else None
        for u in units:
            content = u.content.strip()
            if not content:
                continue
            n = seen.get(u.location, 0)
            seen[u.location] = n + 1
            rid = record_id_for(doc.id, u.location, n + 1 if n else None)
            rf = record_fields(u, doc_type=doc.doc_type, doc_store=doc.store_code)
            eff = rf.get("effective_date") or doc_eff
            meta = {"source_file": doc.filename, "title": doc.title, "doc_type": doc.doc_type, "store_code": rf["store_code"],
                    "sku": rf["skus"], "category": rf["category"], "effective_date": eff, "version": doc.version,
                    "is_active": True, "unit_kind": u.kind, **{k: v for k, v in u.meta.items() if v is not None}}
            if rf.get("end_date"):
                meta["end_date"] = rf["end_date"]
            out.append({
                "record_id": rid, "source_file": doc.filename, "source_type": doc.source_type, "doc_type": doc.doc_type,
                "content": content, "location": u.location, "metadata": meta, "confidence": 1.0,
                "suspicious_content": scan_for_suspicious_content(content), "store_code": rf["store_code"],
                "skus": rf["skus"], "category": rf["category"], "effective_date": eff, "version": doc.version,
                "entity_key": rf.get("entity_key"), "content_hash": content_hash(content),
            })
        return out

    def _agent_records(self, doc: Document, job_id: str) -> list[dict[str, Any]]:
        """ingest.mode=agent: agent/loop.py (verifier + bounded retry) qua gateway, rồi chuẩn hoá id."""
        from agent.loop import SYSTEM_PROMPT, AgentLoop

        client = AnthropicCompatAdapter(self.llm, "ingest_agent") if self.llm else None
        _gw, tools = build_ingest_tools(client, file_id=doc.id, run_id=job_id, audit_log=self.audit_log)
        loop = AgentLoop(system_prompt=SYSTEM_PROMPT, tools=tools, client=client, model=self.cfg.models.ingest_agent,
                         max_turns=self.cfg.ingest.agent_max_turns, max_verify_retries=self.cfg.ingest.agent_max_verify_retries)
        result = loop.run(f"Hãy ingest file '{doc.storage_path}' vào knowledge base theo đúng quy tắc.", doc.storage_path)
        if result.status != "OK":
            raise JobBlocked(f"Agent ingest dừng ở trạng thái {result.status}: {result.blocked_reason or result.final_text[:300]}",
                             {"issues": result.issues or []})
        units = [RecordUnit(location=r.get("location") or f"record-{i}", content=r.get("content", ""), kind="agent",
                            meta={"agent_record_id": r.get("record_id"), **(r.get("metadata") or {})})
                 for i, r in enumerate(result.records)]
        return self._build_records(doc, units)

    # ------------------------------------------------------------------ run
    def run(self, job_id: str, document_id: str) -> dict[str, Any]:
        cfg = self.cfg
        trace_id = new_id()
        with session_scope() as s:
            doc = s.get(Document, document_id)
            if doc is None or doc.deleted_at is not None:
                raise JobBlocked("Document không tồn tại hoặc đã bị xoá")
            doc.status = "processing"
            filename, path = doc.filename, doc.storage_path
        with log_context(job_id=job_id), tracing.start_trace(
                "ingest.file", trace_id=trace_id, input={"filename": filename},
                metadata={"document_id": document_id, "job_id": job_id, "filename": filename}, tags=["ingest"]) as th:
            gateway = build_pipeline_gateway(
                parse_file_raw=self._parse_raw, kb_write_dry_run=self._kb_dry_run, kb_write_commit=self._kb_commit,
                client=AnthropicCompatAdapter(self.llm, "vision") if self.llm else None, audit_log=self.audit_log,
                audit_log_path=monthly_gateway_log_path())
            call = lambda tool, args: gateway.call_tool(tool, args, requested_by="ingest-pipeline", run_id=job_id, file_id=document_id)  # noqa: E731
            warnings: list[str] = []
            with tracing.span("ingest.detect"):
                kind = call("detect_file_type", {"path": path})["kind"]
            if kind not in SUPPORTED_KINDS:
                self._set_status(document_id, "blocked")
                raise JobBlocked(f"Định dạng file không hỗ trợ (nhận diện: {kind})")

            with session_scope() as s:
                doc = s.get(Document, document_id)
                s.expunge(doc)
            if cfg.ingest.mode == "agent":
                with tracing.span("ingest.agent"):
                    records = self._agent_records(doc, job_id)
                units = None
            else:
                with tracing.span("ingest.parse", kind=kind) as sp:
                    try:
                        if kind == "image":
                            if not cfg.ingest.image_extraction or self.llm is None:
                                self._set_status(document_id, "blocked")
                                raise JobBlocked("Ảnh cần Claude vision nhưng đang tắt (ingest.image_extraction) hoặc thiếu API key")
                            text = self._vision_text(gateway, path, job_id, document_id)
                            units = [RecordUnit(location=filename, content=text, kind="image")] if text else []
                        else:
                            parsed = call("parse_file_raw", {"path": path, "kind": kind})
                            units = make_units(kind, parsed)
                            ocr_pages = (parsed.get("stats") or {}).get("pages_needing_ocr") or []
                            if kind == "pdf" and ocr_pages:
                                if cfg.ingest.image_extraction and self.llm is not None:
                                    for p in ocr_pages[:MAX_OCR_PAGES]:
                                        text = self._vision_text(gateway, f"{path}#trang={p}", job_id, document_id)
                                        if text:
                                            units.append(RecordUnit(location=f"Trang {p}", content=text, kind="page", meta={"ocr": True}))
                                    if len(ocr_pages) > MAX_OCR_PAGES:
                                        warnings.append(f"PDF có {len(ocr_pages)} trang scan, chỉ OCR {MAX_OCR_PAGES} trang đầu")
                                else:
                                    warnings.append(f"{len(ocr_pages)} trang PDF dạng scan chưa được OCR (vision đang tắt)")
                    except ParserError as e:
                        self._set_status(document_id, "blocked")
                        raise JobBlocked(f"File không parse được đáng tin cậy: {e}") from e
                    sp.update(output={"units": len(units)})
                if not units:
                    self._set_status(document_id, "blocked")
                    raise JobBlocked("Không trích được nội dung nào từ file (file rỗng/ảnh không có chữ?)")
                save_units(units_cache_path(doc), units)
                # metadata cấp document: doc_type / ngày hiệu lực / phiên bản (người upload khai báo thì ưu tiên)
                head_text = "\n".join(u.content for u in units[:10])
                updates: dict[str, Any] = {}
                if doc.doc_type == "unknown":
                    dt, how = self._classify(doc, units)
                    updates["doc_type"], updates["doc_type_source"] = dt, how
                if doc.effective_date is None:
                    eff = extract_effective_date(head_text, filename)
                    updates["effective_date"] = date.fromisoformat(eff) if eff else None
                if not doc.version:
                    updates["version"] = extract_version(head_text)
                doc = self._apply_doc_updates(document_id, updates)
                records = self._build_records(doc, units)

            suspicious = [r["record_id"] for r in records if r["suspicious_content"]]
            with tracing.span("ingest.write", records=len(records), quarantine=bool(suspicious)) as sp:
                summary = call("kb_write_records", {"document_id": document_id, "records": records, "quarantine": bool(suspicious)})
                sp.update(output=summary)
            with tracing.span("ingest.embed", model=self.embedder.model_name) as sp:
                emb = embed_chunks(self.sm, cfg, self.embedder, document_ids=[document_id],
                                   progress=lambda d, t: update_progress(job_id, d, t))
                sp.update(output=emb)
            conflicts = self._detect_conflicts(document_id) if not suspicious else []

            with session_scope() as s:
                to_sync = [document_id, *summary.get("superseded_documents", [])]
                for did in to_sync:
                    enqueue_job(s, "graph_sync", {"document_id": did}, document_id=did, created_by="ingest",
                                idempotency_key=f"graph_sync:{did}:{job_id}")
            result = {"trace_id": trace_id, "document_id": document_id, "kind": kind, **summary, "embedding": emb,
                      "conflicts": conflicts, "warnings": warnings, "suspicious_records": suspicious[:20]}
            th.update(output={k: v for k, v in result.items() if k != "suspicious_records"})
            if suspicious:
                raise JobBlocked(f"{len(suspicious)} record có dấu hiệu prompt injection — file bị chặn chờ admin duyệt "
                                 "(POST /kb/documents/{id}/approve sau khi kiểm tra).", result)
            log_event(logger, "ingest_done", document_id=document_id, records=summary.get("records"),
                      chunks=summary.get("chunks"), superseded=len(summary.get("superseded_documents", [])))
            return result

    def _set_status(self, document_id: str, status: str) -> None:
        with session_scope() as s:
            doc = s.get(Document, document_id)
            if doc is not None:
                doc.status = status

    def _apply_doc_updates(self, document_id: str, updates: dict[str, Any]) -> Document:
        with session_scope() as s:
            doc = s.get(Document, document_id)
            src = updates.pop("doc_type_source", None)
            for k, v in updates.items():
                setattr(doc, k, v)
            if src:
                doc.meta = {**(doc.meta or {}), "doc_type_source": src}
            doc.updated_at = utcnow()
            s.flush()
            s.expunge(doc)
            return doc

    def _detect_conflicts(self, document_id: str) -> list[str]:
        with session_scope() as s:
            doc = s.get(Document, document_id)
            if doc is None or not doc.is_active:
                return []
            recs = [r for r in s.query(KBRecordRow).filter(KBRecordRow.document_id == document_id,
                                                           KBRecordRow.is_active.is_(True)).all()]
            skus = sorted({k for r in recs for k in (r.skus or [])})
            if not skus:
                return []
            others = active_price_records_for_skus(s, skus, None)
            docs = {d.id: d for d in s.query(Document).filter(Document.id.in_({r.document_id for r in others} | {document_id})).all()}
            facts = []
            for r in {r.record_id: r for r in [*recs, *others]}.values():
                d = docs.get(r.document_id)
                facts.extend(extract_price_facts(r, format_citation(d.title, d.source_type, r.location) if d else r.source_file))
            conflicts = detect_conflicts(facts)
            return persist_conflicts(s, conflicts) if conflicts else []


def approve_blocked_document(session: Any, doc: Document, actor: str) -> dict[str, Any]:
    """Admin đã kiểm tra file bị chặn (nghi injection) -> cho record vào phục vụ."""
    recs = session.query(KBRecordRow).filter(KBRecordRow.document_id == doc.id, KBRecordRow.status == "quarantined").all()
    for r in recs:
        r.status, r.is_active = "active", True
        r.meta = {**(r.meta or {}), "approved_by": actor}
    session.flush()
    summary = activate_document(session, doc)
    enqueue_job(session, "graph_sync", {"document_id": doc.id}, document_id=doc.id, created_by=actor)
    for did in summary.get("superseded_documents", []):
        enqueue_job(session, "graph_sync", {"document_id": did}, document_id=did, created_by=actor)
    return {"document_id": doc.id, "approved_records": len(recs), **summary}


__all__ = ["IngestPipeline", "approve_blocked_document", "find_skus"]
