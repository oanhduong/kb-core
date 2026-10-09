"""Analytics theo workspace: catalog bảng, xem trước/thống kê cột, chạy SQL read-only, hỏi bằng ngôn ngữ
tự nhiên (NL->SQL + chart), truy vấn đã lưu, xuất CSV/XLSX."""
from __future__ import annotations

import csv
import io
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics import engine
from analytics.charts import build_chart
from analytics.nl2sql import answer_with_sql
from analytics.relations import relationship_view, schedule_discovery
from api.auth import workspace
from api.deps import audit_action, get_db, get_services
from core.errors import NotFoundError
from core.i18n import L
from core.models import DataTable, SavedQuery, TableRelationship
from core.principal import WorkspaceContext
from knowledge.translate import localize
from worker.queue import job_view

router = APIRouter(prefix="/w/{workspace_id}", tags=["analytics"])


def _table(db: Session, ctx: WorkspaceContext, table_id: str) -> engine.TableInfo:
    for t in engine.catalog(db, ctx.workspace_id):
        if t.id == table_id or t.name == table_id:
            return t
    raise NotFoundError(L("Không có bảng dữ liệu", "Data table not found"))


@router.get("/tables", summary="Catalog bảng số liệu của workspace")
def list_tables(document_id: str | None = None, ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> list[dict]:
    return localize(db, ctx.workspace_id, [t.to_dict() for t in engine.catalog(db, ctx.workspace_id, document_ids=[document_id] if document_id else None)])


@router.get("/tables/{table_id}", summary="Schema + thống kê cột + xem trước")
def table_detail(table_id: str, ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db),
                 services: Any = Depends(get_services)) -> dict:
    t = _table(db, ctx, table_id)
    try:
        stats = engine.column_stats(services.cfg, t)
    except Exception as e:  # thống kê lỗi (cột lạ) không chặn xem bảng
        stats = {"error": str(e)[:200]}
    return {**localize(db, ctx.workspace_id, t.to_dict()), "stats": stats, "preview": engine.preview(services.cfg, t, limit=20).to_dict()}


@router.get("/tables/{table_id}/rows", summary="Dữ liệu bảng (phân trang)")
def table_rows(table_id: str, limit: int = Query(100, le=500), offset: int = Query(0, ge=0), ctx: WorkspaceContext = Depends(workspace("viewer")),
               db: Session = Depends(get_db), services: Any = Depends(get_services)) -> dict:
    return engine.preview(services.cfg, _table(db, ctx, table_id), limit=limit, offset=offset).to_dict()


class SQLBody(BaseModel):
    sql: str = Field(min_length=1, max_length=20000)
    limit: int | None = Field(default=None, ge=1, le=5000)
    chart: dict | None = None


# ------------------------------------------------------------------ quan hệ giữa các bảng (dò bằng dữ liệu)
@router.get("/analytics/relationships", summary="Quan hệ giữa các bảng của workspace (khác file/sheet) + job dò đang chạy")
def list_relationships(ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> dict:
    from core.models import Job

    rows = db.execute(select(TableRelationship).where(TableRelationship.workspace_id == ctx.workspace_id)
                      .order_by(TableRelationship.confidence.desc())).scalars().all()
    ids = {r.left_table_id for r in rows} | {r.right_table_id for r in rows}
    tables = {t.id: t for t in db.execute(select(DataTable).where(DataTable.id.in_(ids))).scalars()} if ids else {}
    job = db.execute(select(Job).where(Job.type == "relations_discover", Job.workspace_id == ctx.workspace_id)
                     .order_by(Job.created_at.desc()).limit(1)).scalars().first()
    items = [relationship_view(r, tables) for r in rows if tables.get(r.left_table_id) and tables[r.left_table_id].is_active
             and tables.get(r.right_table_id) and tables[r.right_table_id].is_active]
    # tên bảng/cột/sheet là tên người dùng đặt -> giữ nguyên (không localize)
    return {"items": items, "job": job_view(job) if job else None}


@router.post("/analytics/relationships/discover", summary="Dò lại quan hệ giữa mọi bảng + dựng thực thể từ cột mã (job nền)")
def discover_relationships(request: Request, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    job = schedule_discovery(db, ctx.workspace_id, created_by=ctx.subject, delay_seconds=0)
    audit_action(request, ctx.principal, "relationships_discover", job.id)
    return job_view(job)


class RelationshipReview(BaseModel):
    status: Literal["approved", "rejected", "proposed"]


@router.patch("/analytics/relationships/{rel_id}", summary="Xác nhận / bỏ 1 quan hệ (dò lại vẫn giữ quyết định)")
def review_relationship(rel_id: str, body: RelationshipReview, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")),
                        db: Session = Depends(get_db)) -> dict:
    r = db.get(TableRelationship, rel_id)
    if r is None or r.workspace_id != ctx.workspace_id:
        raise NotFoundError(L("Không có quan hệ này", "Relationship not found"))
    r.status, r.reviewed_by = body.status, ctx.subject
    audit_action(request, ctx.principal, "relationship_review", rel_id, status=body.status)
    # thực thể từ cột mã phụ thuộc quan hệ -> dựng lại
    schedule_discovery(db, ctx.workspace_id, created_by=ctx.subject, delay_seconds=5)
    tables = {t.id: t for t in db.execute(select(DataTable).where(DataTable.id.in_([r.left_table_id, r.right_table_id]))).scalars()}
    return relationship_view(r, tables)


@router.post("/analytics/query", summary="Chạy SQL DuckDB read-only trên các bảng của workspace")
def run_sql(body: SQLBody, ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db),
            services: Any = Depends(get_services)) -> dict:
    tables = engine.catalog(db, ctx.workspace_id)
    res = engine.run_query(services.cfg, tables, body.sql, limit=body.limit)
    chart = build_chart(res, body.chart, services.cfg.analytics.chart_max_points) if body.chart is not None else None
    return {"result": res.to_dict(), "chart": chart}


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    document_ids: list[str] | None = None


@router.post("/analytics/ask", summary="Hỏi số liệu bằng ngôn ngữ tự nhiên -> SQL (hiển thị để kiểm tra) + kết quả + chart")
def ask_data(body: AskBody, ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db),
             services: Any = Depends(get_services)) -> dict:
    tables = engine.catalog(db, ctx.workspace_id, document_ids=body.document_ids)
    return answer_with_sql(services.cfg, services.llm, tables, body.question).to_dict()


# ------------------------------------------------------------------ saved queries
class SavedQueryBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    sql: str = Field(min_length=1, max_length=20000)
    question: str | None = None
    chart_spec: dict | None = None


@router.get("/analytics/saved")
def list_saved(ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(SavedQuery).where(SavedQuery.workspace_id == ctx.workspace_id).order_by(SavedQuery.created_at.desc())).scalars()
    return [{"id": r.id, "title": r.title, "question": r.question, "sql": r.sql, "chart_spec": r.chart_spec, "created_by": r.created_by,
             "created_at": r.created_at} for r in rows]


@router.post("/analytics/saved", summary="Lưu truy vấn (+ chart) để dùng lại/đưa vào báo cáo")
def save_query(body: SavedQueryBody, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db),
               services: Any = Depends(get_services)) -> dict:
    engine.validate_sql(body.sql)
    row = SavedQuery(workspace_id=ctx.workspace_id, title=body.title, question=body.question, sql=body.sql, chart_spec=body.chart_spec,
                     created_by=ctx.subject)
    db.add(row)
    db.flush()
    audit_action(request, ctx.principal, "saved_query_create", row.id, title=body.title)
    return {"id": row.id, "title": row.title}


@router.post("/analytics/saved/{query_id}/run")
def run_saved(query_id: str, ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db),
              services: Any = Depends(get_services)) -> dict:
    row = db.get(SavedQuery, query_id)
    if row is None or row.workspace_id != ctx.workspace_id:
        raise NotFoundError(L("Không có truy vấn đã lưu", "Saved query not found"))
    res = engine.run_query(services.cfg, engine.catalog(db, ctx.workspace_id), row.sql)
    return {"result": res.to_dict(), "chart": build_chart(res, row.chart_spec, services.cfg.analytics.chart_max_points)}


@router.delete("/analytics/saved/{query_id}")
def delete_saved(query_id: str, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    row = db.get(SavedQuery, query_id)
    if row is None or row.workspace_id != ctx.workspace_id:
        raise NotFoundError(L("Không có truy vấn đã lưu", "Saved query not found"))
    db.delete(row)
    return {"deleted": query_id}


# ------------------------------------------------------------------ export
def _export(res: engine.QueryResult, fmt: str, filename: str) -> StreamingResponse:
    names = [c["name"] for c in res.columns]
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(names)
        w.writerows(res.rows)
        data = ("﻿" + buf.getvalue()).encode("utf-8")
        media = "text/csv; charset=utf-8"
    else:
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "data"
        ws.append(names)
        for r in res.rows:
            ws.append([v if isinstance(v, (int, float, str)) or v is None else str(v) for v in r])
        out = io.BytesIO()
        wb.save(out)
        data = out.getvalue()
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return StreamingResponse(iter([data]), media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}.{fmt}"'})


@router.get("/tables/{table_id}/export", summary="Xuất bảng (csv|xlsx)")
def export_table(table_id: str, fmt: Literal["csv", "xlsx"] = "xlsx", ctx: WorkspaceContext = Depends(workspace("viewer")),
                 db: Session = Depends(get_db), services: Any = Depends(get_services)) -> StreamingResponse:
    t = _table(db, ctx, table_id)
    res = engine.run_query(services.cfg, [t], f'SELECT * FROM "{t.name}"', limit=5000)
    return _export(res, fmt, t.name)


@router.post("/analytics/export", summary="Xuất kết quả 1 câu SQL (csv|xlsx)")
def export_query(body: SQLBody, fmt: Literal["csv", "xlsx"] = "xlsx", ctx: WorkspaceContext = Depends(workspace("viewer")),
                 db: Session = Depends(get_db), services: Any = Depends(get_services)) -> StreamingResponse:
    res = engine.run_query(services.cfg, engine.catalog(db, ctx.workspace_id), body.sql, limit=5000)
    return _export(res, fmt, "ket_qua")
