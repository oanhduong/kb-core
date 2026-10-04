"""KB health (C6) — tín hiệu vận hành cho admin:
- tổng record theo doc_type (theo trạng thái active/superseded/duplicate/quarantined/deleted)
- số file blocked đang chờ xử lý (+ danh sách + lý do)
- số conflict chưa giải quyết
- câu hỏi bị abstention trong 7 ngày gần nhất KÈM NỘI DUNG (gộp câu giống nhau) — tín hiệu KB thiếu gì
- ngày cập nhật gần nhất theo từng doc_type
- phụ: độ phủ embedding của model hiện tại, hàng đợi job, graph.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from core.config import AppConfig
from core.models import Abstention, Conflict, Document, Job
from graph.resolve import fold_text
from kb.repository import current_kb_version, record_counts_by_doc_type


def kb_health(session: Session, cfg: AppConfig, *, days: int = 7, graph_stats: dict[str, Any] | None = None,
              embedding_model: str | None = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    blocked_docs = session.execute(select(Document).where(Document.status == "blocked", Document.deleted_at.is_(None))
                                   .order_by(Document.updated_at.desc())).scalars().all()
    blocked_jobs = session.execute(select(Job).where(Job.status.in_(("blocked", "error")), Job.type == "ingest_file")
                                   .order_by(Job.finished_at.desc().nullslast()).limit(50)).scalars().all()
    open_conflicts = session.execute(select(func.count()).select_from(Conflict).where(Conflict.status == "open")).scalar_one()

    abst = session.execute(select(Abstention).where(Abstention.created_at >= since).order_by(Abstention.created_at.desc())).scalars().all()
    grouped: dict[str, dict[str, Any]] = {}
    for a in abst:
        key = fold_text(a.question)
        g = grouped.setdefault(key, {"question": a.question, "count": 0, "reasons": {}, "stores": set(), "last_at": a.created_at})
        g["count"] += 1
        g["reasons"][a.reason] = g["reasons"].get(a.reason, 0) + 1
        if a.store_code:
            g["stores"].add(a.store_code)
    abst_list = sorted(({**g, "stores": sorted(g["stores"])} for g in grouped.values()),
                       key=lambda g: (-g["count"], -g["last_at"].timestamp()))

    last_update = {dt: ts for dt, ts in session.execute(
        select(Document.doc_type, func.max(Document.updated_at)).where(Document.deleted_at.is_(None), Document.status.in_(("active", "superseded")))
        .group_by(Document.doc_type)).all()}
    active_docs = {dt: n for dt, n in session.execute(
        select(Document.doc_type, func.count()).where(Document.is_active.is_(True), Document.deleted_at.is_(None))
        .group_by(Document.doc_type)).all()}

    from index.embeddings import effective_embedding_model

    model = embedding_model or effective_embedding_model(cfg)
    cov = session.execute(text(
        "SELECT count(*) AS total, count(e.chunk_id) AS embedded FROM kb_chunks c "
        "LEFT JOIN chunk_embeddings e ON e.chunk_id = c.chunk_id AND e.model = :m "
        "WHERE c.deleted = false AND c.role <> 'parent'"), {"m": model}).one()
    jobs = {s: int(n) for s, n in session.execute(text("SELECT status, count(*) FROM jobs GROUP BY status")).all()}
    total_q = session.execute(text("SELECT count(*) FROM qa_traces WHERE created_at >= :s"), {"s": since}).scalar_one()

    return {
        "generated_at": now.isoformat(),
        "config_version": cfg.config_version,
        "kb_version": current_kb_version(session),
        "records_by_doc_type": record_counts_by_doc_type(session),
        "active_documents_by_doc_type": active_docs,
        "last_updated_by_doc_type": {k: v.isoformat() if v else None for k, v in last_update.items()},
        "blocked_files": {
            "count": len(blocked_docs),
            "items": [{"document_id": d.id, "filename": d.filename, "updated_at": d.updated_at.isoformat(),
                       "reason": next((j.error for j in blocked_jobs if j.document_id == d.id), None)} for d in blocked_docs[:50]],
        },
        "failed_ingest_jobs": [{"job_id": j.id, "document_id": j.document_id, "status": j.status, "error": (j.error or "")[:300]}
                               for j in blocked_jobs if j.status == "error"][:20],
        "open_conflicts": int(open_conflicts),
        "abstentions_last_days": {
            "days": days, "count": len(abst), "questions_total": int(total_q),
            "rate": round(len(abst) / total_q, 3) if total_q else None,
            "questions": [{**g, "last_at": g["last_at"].isoformat()} for g in abst_list[:100]],
        },
        "embedding_coverage": {"model": model, "retrievable_chunks": int(cov.total), "embedded": int(cov.embedded),
                               "missing": int(cov.total) - int(cov.embedded)},
        "jobs": jobs,
        "graph": graph_stats,
    }
