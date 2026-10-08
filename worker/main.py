"""Worker nền — mọi tác vụ chậm (ingest, reindex, graph sync/rebuild) chạy ở đây, không block request.

    python -m worker.main                 # chạy vòng lặp (container `worker`)
    python -m worker.main --once          # xử lý hết job đang chờ rồi thoát (CLI/test)

Vòng lặp: claim job (FOR UPDATE SKIP LOCKED) -> handler -> done | blocked | retry/error. Song song đó
chạy tác vụ định kỳ: khôi phục job treo, kiểm tra alert (E3), dọn dữ liệu quá hạn (E5).
Nhiều worker chạy cùng lúc an toàn (scale ngang: `docker compose up --scale worker=3`).
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import time
from typing import Any, Callable

from core.config import get_config
from core.db import get_sessionmaker, session_scope
from core.llm import get_llm
from core.logging import configure_logging, log_context, log_event
from core.models import Document, Job
from worker import queue

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, worker_id: str | None = None) -> None:
        self.cfg = get_config()
        self.sm = get_sessionmaker()
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self._stop = False
        self._llm = None
        self._embedder = None
        self._pipeline = None
        self._last_periodic: dict[str, float] = {}

    # lazy để worker khởi động nhanh, chỉ tải model khi có job cần
    @property
    def llm(self) -> Any:
        if self._llm is None:
            from core.config import get_settings

            self._llm = get_llm() if get_settings().anthropic_api_key else None
        return self._llm

    @property
    def embedder(self) -> Any:
        if self._embedder is None:
            from index.embeddings import get_embedder

            self._embedder = get_embedder(self.cfg)
        return self._embedder

    @property
    def pipeline(self) -> Any:
        if self._pipeline is None:
            from ingest.pipeline import IngestPipeline

            self._pipeline = IngestPipeline(self.cfg, self.sm, self.llm, self.embedder)
        return self._pipeline

    # ------------------------------------------------------------------ handlers
    def _ingest(self, job: Job) -> dict[str, Any]:
        return self.pipeline.run(job.id, job.payload["document_id"])

    def _pack_rule_graph(self, workspace_id: str | None) -> bool:
        from core.models import Workspace
        from domains import get_pack

        if not self.cfg.graph.enabled or not workspace_id:
            return False
        with session_scope() as s:
            ws = s.get(Workspace, workspace_id)
            return bool(ws and get_pack(ws.domain_pack).rule_graph)

    def _graph_sync(self, job: Job) -> dict[str, Any]:
        from graph.store import get_graph_store
        from graph.sync import sync_document

        if not self._pack_rule_graph(job.workspace_id):
            return {"skipped": "graph rule-based tắt (config hoặc domain pack không bật)"}
        with session_scope() as s:
            return sync_document(s, get_graph_store(job.workspace_id), job.payload["document_id"],
                                 llm=bool(job.payload.get("llm", True)) and self.llm is not None)

    def _graph_rebuild(self, job: Job) -> dict[str, Any]:
        from graph.store import get_graph_store
        from graph.sync import rebuild_full

        if not self._pack_rule_graph(job.workspace_id):
            return {"skipped": "graph rule-based tắt (config hoặc domain pack không bật)"}
        with session_scope() as s:
            out = rebuild_full(s, get_graph_store(job.workspace_id), job.workspace_id,
                               llm=bool(job.payload.get("llm", True)) and self.llm is not None,
                               progress=lambda d, t: queue.update_progress(job.id, d, t))
        return {"documents": out["documents"], "graph": out["graph"]}

    def _knowledge_extract(self, job: Job) -> dict[str, Any]:
        from knowledge.pipeline import extract_document_knowledge

        return extract_document_knowledge(self.cfg, self.sm, self.llm, self.embedder, job.payload["document_id"],
                                          progress=lambda d, t, note=None: queue.update_progress(job.id, d, t, note=note))

    def _report_generate(self, job: Job) -> dict[str, Any]:
        from reports.generator import generate_report

        return generate_report(self.cfg, self.sm, self.llm, job.payload["report_id"],
                               progress=lambda d, t, note=None: queue.update_progress(job.id, d, t, note=note))

    def _model_analyze(self, job: Job) -> dict[str, Any]:
        from modeling.service import run_analyze

        return run_analyze(self.cfg, self.llm, job.payload["model_id"], main_override=job.payload.get("main_table"),
                           progress=lambda d, t, note=None: queue.update_progress(job.id, d, t, note=note))

    def _model_build(self, job: Job) -> dict[str, Any]:
        from modeling.service import run_build

        return run_build(self.cfg, job.payload["model_id"], progress=lambda d, t, note=None: queue.update_progress(job.id, d, t, note=note))

    def _reindex(self, job: Job) -> dict[str, Any]:
        from kb.reindex import run_reindex

        return run_reindex(self.cfg, self.sm, self.embedder, job.payload,
                           progress=lambda d, t, phase: queue.update_progress(job.id, d, t, note=phase))

    def _document_lifecycle(self, job: Job) -> dict[str, Any]:
        """Hậu xử lý sau xoá/khôi phục file từ API: đồng bộ graph cho các document bị ảnh hưởng."""
        from graph.store import get_graph_store
        from graph.sync import sync_document

        if not self._pack_rule_graph(job.workspace_id):
            return {"skipped": "graph rule-based tắt"}
        out = []
        with session_scope() as s:
            for did in job.payload.get("document_ids", []):
                out.append(sync_document(s, get_graph_store(job.workspace_id), did, llm=False)["status"])
        return {"synced": out}

    HANDLERS: dict[str, Callable[["Worker", Job], dict[str, Any]]] = {
        "ingest_file": _ingest, "graph_sync": _graph_sync, "graph_rebuild": _graph_rebuild,
        "reindex": _reindex, "document_lifecycle": _document_lifecycle,
        "knowledge_extract": _knowledge_extract, "report_generate": _report_generate,
        "model_analyze": _model_analyze, "model_build": _model_build,
    }

    # ------------------------------------------------------------------ loop
    def run_one(self) -> bool:
        with session_scope() as s:
            job = queue.claim_next(s, self.worker_id)
            if job is None:
                return False
            job_id, job_type = job.id, job.type
            s.expunge(job)
        handler = self.HANDLERS[job_type]
        start = time.perf_counter()
        with log_context(job_id=job_id):
            log_event(logger, "job_start", job_type=job_type, attempt=job.attempts)
            try:
                result = handler(self, job)
                with session_scope() as s:
                    queue.complete(s, s.get(Job, job_id), {**(s.get(Job, job_id).result or {}), **(result or {}),
                                                           "elapsed_seconds": round(time.perf_counter() - start, 2)})
            except queue.JobBlocked as e:
                with session_scope() as s:
                    j = s.get(Job, job_id)
                    queue.block(s, j, e.reason, e.result)
                    if j.document_id and job_type == "ingest_file":
                        doc = s.get(Document, j.document_id)
                        if doc is not None and doc.status in ("processing", "pending"):
                            doc.status = "blocked"
            except Exception as e:  # lỗi hệ thống -> retry có backoff, hết lượt -> error
                logger.exception("job lỗi")
                with session_scope() as s:
                    j = s.get(Job, job_id)
                    queue.fail(s, j, f"{type(e).__name__}: {e}"[:4000])
                    if j.status == "error" and j.document_id and job_type == "ingest_file":
                        doc = s.get(Document, j.document_id)
                        if doc is not None and doc.status == "processing":
                            doc.status = "error"
        return True

    def _periodic(self) -> None:
        now = time.monotonic()

        def due(name: str, every: float) -> bool:
            if now - self._last_periodic.get(name, 0) >= every:
                self._last_periodic[name] = now
                return True
            return False

        try:
            if due("recover", 60):
                with session_scope() as s:
                    queue.recover_stale(s, self.cfg.worker.stale_lock_minutes)
            if self.cfg.alerts.enabled and due("alerts", self.cfg.alerts.check_interval_seconds):
                from observability.alerts import run_alert_checks

                run_alert_checks(self.cfg)
            if due("retention", self.cfg.worker.retention_interval_hours * 3600):
                from observability.retention import run_retention

                run_retention(self.cfg)
        except Exception:
            logger.exception("tác vụ định kỳ lỗi (bỏ qua, chạy lại lần sau)")

    def stop(self, *_: Any) -> None:
        log_event(logger, "worker_stopping", worker_id=self.worker_id)
        self._stop = True

    def loop(self, once: bool = False) -> None:
        log_event(logger, "worker_started", worker_id=self.worker_id, config_version=self.cfg.config_version)
        heartbeat = os.environ.get("WORKER_HEARTBEAT_FILE", "/tmp/retailkb-worker.heartbeat")
        while not self._stop:
            try:  # healthcheck docker-compose: file heartbeat phải được cập nhật trong 2 phút gần nhất
                with open(heartbeat, "w") as f:
                    f.write(str(time.time()))
            except OSError:
                pass
            worked = self.run_one()
            if once and not worked:
                return
            if not once:
                self._periodic()
            if not worked:
                time.sleep(self.cfg.worker.poll_interval_seconds)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="RetailKB worker nền.")
    parser.add_argument("--once", action="store_true", help="Xử lý hết job đang chờ rồi thoát")
    args = parser.parse_args(argv)
    w = Worker()
    signal.signal(signal.SIGTERM, w.stop)
    signal.signal(signal.SIGINT, w.stop)
    w.loop(once=args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
