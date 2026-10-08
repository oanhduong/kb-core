"""Mô hình dữ liệu nghiệp vụ: tạo từ file Excel/CSV đã tải lên -> phân tích (nền) -> duyệt từng quan hệ/cột bằng ngôn ngữ
nghiệp vụ (kiểm chứng tức thì trên dữ liệu) -> build view trong warehouse -> đối chiếu Excel -> tài liệu quan hệ / kết nối BI."""
from __future__ import annotations

import io
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.auth import workspace
from api.deps import audit_action, get_db, get_services
from core.config import get_settings
from core.errors import ValidationFailed
from core.principal import WorkspaceContext
from modeling import service as svc
from modeling.spec import KeyPair, Rule

router = APIRouter(prefix="/w/{workspace_id}/models", tags=["data-models"])


class CreateBody(BaseModel):
    document_ids: list[str] = Field(min_length=1, max_length=20)
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


@router.get("", summary="Danh sách mô hình dữ liệu")
def list_models(ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> list[dict]:
    return [svc.serialize(db, m) for m in svc.list_models(db, ctx.workspace_id)]


@router.post("", summary="Tạo mô hình từ file Excel/CSV đã tải lên (phân tích chạy nền)")
def create_model(body: CreateBody, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db),
                 services: Any = Depends(get_services)) -> dict:
    m = svc.create_model(db, services.cfg, ctx.workspace_id, ctx.subject, name=body.name, document_ids=body.document_ids,
                         description=body.description)
    db.flush()
    audit_action(request, ctx.principal, "data_model_create", m.id, documents=body.document_ids)
    return svc.serialize(db, m)


@router.get("/{model_id}", summary="Chi tiết mô hình: spec + diễn giải vi/en + đối chiếu")
def get_model(model_id: str, ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> dict:
    return svc.serialize(db, svc.get_model(db, ctx.workspace_id, model_id), detail=True)


class PatchBody(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


@router.patch("/{model_id}")
def patch_model(model_id: str, body: PatchBody, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    m = svc.get_model(db, ctx.workspace_id, model_id)
    if body.name is not None and body.name.strip():
        m.name = body.name.strip()
    if body.description is not None:
        m.description = body.description.strip() or None
    return svc.serialize(db, m)


@router.delete("/{model_id}")
def delete_model(model_id: str, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db),
                 services: Any = Depends(get_services)) -> dict:
    m = svc.get_model(db, ctx.workspace_id, model_id)
    svc.delete_model(db, services.cfg, m)
    audit_action(request, ctx.principal, "data_model_delete", model_id)
    return {"deleted": model_id}


def _busy(m: Any) -> None:
    if m.status in ("analyzing", "building"):
        raise ValidationFailed("Mô hình đang được xử lý — vui lòng đợi xong rồi thao tác tiếp")


class ReanalyzeBody(BaseModel):
    main_table: str | None = None


@router.post("/{model_id}/reanalyze", summary="Phân tích lại (vd đổi sheet chính) — giữ các quyết định đã duyệt")
def reanalyze(model_id: str, body: ReanalyzeBody, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    from worker.queue import enqueue_job

    m = svc.get_model(db, ctx.workspace_id, model_id)
    _busy(m)
    if body.main_table:
        spec = svc.spec_of(m)
        if spec.table(body.main_table) is None:
            raise ValidationFailed("Không có sheet này")
    job = enqueue_job(db, "model_analyze", {"model_id": m.id, "main_table": body.main_table}, workspace_id=ctx.workspace_id,
                      created_by=ctx.subject, max_attempts=2)
    m.job_id, m.status = job.id, "analyzing"
    return svc.serialize(db, m)


class ParamsBody(BaseModel):
    as_of_date: str | None = Field(default=None, description="YYYY-MM-DD | today")


@router.put("/{model_id}/parameters", summary="Ngày báo cáo (cho các cột 'số ngày kể từ…')")
def set_params(model_id: str, body: ParamsBody, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db),
               services: Any = Depends(get_services)) -> dict:
    m = svc.get_model(db, ctx.workspace_id, model_id)
    _busy(m)
    svc.set_parameters(services.cfg, m, body.as_of_date)
    return svc.serialize(db, m, detail=True)


class ColumnBody(BaseModel):
    rule: Rule | None = None
    status: Literal["proposed", "confirmed", "rejected"] | None = None
    note: str | None = Field(default=None, max_length=2000)
    alternative: int | None = Field(default=None, ge=0, le=10)


@router.patch("/{model_id}/columns/{name}", summary="Duyệt / đổi quy tắc / chọn gợi ý khác cho 1 cột")
def patch_column(model_id: str, name: str, body: ColumnBody, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")),
                 db: Session = Depends(get_db), services: Any = Depends(get_services)) -> dict:
    m = svc.get_model(db, ctx.workspace_id, model_id)
    _busy(m)
    svc.set_column(services.cfg, m, name, rule=body.rule, status=body.status, note=body.note, alternative=body.alternative, user=ctx.subject)
    audit_action(request, ctx.principal, "data_model_column", model_id, column=name, status=body.status,
                 rule=body.rule.model_dump(mode="json") if body.rule else None, alternative=body.alternative)
    return svc.serialize(db, m, detail=True)


class TestBody(BaseModel):
    rule: Rule


@router.post("/{model_id}/columns/{name}/test", summary="Thử 1 quy tắc trên dữ liệu (không lưu)")
def test_column(model_id: str, name: str, body: TestBody, ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db),
                services: Any = Depends(get_services)) -> dict:
    m = svc.get_model(db, ctx.workspace_id, model_id)
    return svc.test_rule(services.cfg, m, name, body.rule)


class DescribeBody(BaseModel):
    text: str = Field(min_length=3, max_length=2000)


@router.post("/{model_id}/columns/{name}/describe", summary="Mô tả logic bằng lời -> AI dịch thành quy tắc (đã kiểm chứng, chưa lưu)")
def describe_column(model_id: str, name: str, body: DescribeBody, ctx: WorkspaceContext = Depends(workspace("editor")),
                    db: Session = Depends(get_db), services: Any = Depends(get_services)) -> dict:
    m = svc.get_model(db, ctx.workspace_id, model_id)
    _busy(m)
    return svc.describe_column(services.cfg, services.llm, m, name, body.text)


class ConfirmBody(BaseModel):
    columns: list[str] | None = None   # None = mọi cột đề xuất có độ tin cậy cao


@router.post("/{model_id}/confirm", summary="Duyệt hàng loạt")
def confirm(model_id: str, body: ConfirmBody, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")),
            db: Session = Depends(get_db)) -> dict:
    m = svc.get_model(db, ctx.workspace_id, model_id)
    _busy(m)
    n = svc.confirm(m, columns=body.columns)
    audit_action(request, ctx.principal, "data_model_confirm", model_id, count=n)
    return {"confirmed": n, "model": svc.serialize(db, m, detail=True)}


class RelBody(BaseModel):
    status: Literal["proposed", "confirmed", "rejected"] | None = None
    keys: list[KeyPair] | None = None


@router.patch("/{model_id}/relationships/{rid}", summary="Duyệt / sửa khoá nối của 1 quan hệ")
def patch_relationship(model_id: str, rid: str, body: RelBody, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")),
                       db: Session = Depends(get_db), services: Any = Depends(get_services)) -> dict:
    m = svc.get_model(db, ctx.workspace_id, model_id)
    _busy(m)
    svc.set_relationship(services.cfg, m, rid, status=body.status, keys=body.keys, user=ctx.subject)
    audit_action(request, ctx.principal, "data_model_relationship", model_id, relationship=rid, status=body.status)
    return svc.serialize(db, m, detail=True)


class NewRelBody(BaseModel):
    source_table: str
    keys: list[KeyPair] = Field(min_length=1, max_length=4)


@router.post("/{model_id}/relationships", summary="Thêm quan hệ")
def add_relationship(model_id: str, body: NewRelBody, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db),
                     services: Any = Depends(get_services)) -> dict:
    m = svc.get_model(db, ctx.workspace_id, model_id)
    _busy(m)
    svc.add_relationship(services.cfg, m, body.source_table, body.keys, user=ctx.subject)
    return svc.serialize(db, m, detail=True)


class TableRoleBody(BaseModel):
    role: Literal["source", "ignored"]


@router.patch("/{model_id}/tables/{key}", summary="Dùng / bỏ qua 1 sheet phụ")
def patch_table(model_id: str, key: str, body: TableRoleBody, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    m = svc.get_model(db, ctx.workspace_id, model_id)
    _busy(m)
    svc.set_table_role(m, key, body.role)
    return svc.serialize(db, m, detail=True)


@router.post("/{model_id}/build", summary="Tạo/cập nhật view trong warehouse + đối chiếu Excel (chạy nền)")
def build(model_id: str, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    from worker.queue import enqueue_job

    m = svc.get_model(db, ctx.workspace_id, model_id)
    _busy(m)
    if not m.spec:
        raise ValidationFailed("Mô hình chưa phân tích xong")
    job = enqueue_job(db, "model_build", {"model_id": m.id}, workspace_id=ctx.workspace_id, created_by=ctx.subject, max_attempts=2)
    m.job_id, m.status = job.id, "building"
    audit_action(request, ctx.principal, "data_model_build", model_id, revision=m.revision)
    return svc.serialize(db, m)


def _records(df: Any) -> dict[str, Any]:
    import math

    import pandas as pd

    def cell(v: Any) -> Any:
        if v is None or v is pd.NA or v is pd.NaT:
            return None
        if isinstance(v, float) and math.isnan(v):
            return None
        if hasattr(v, "isoformat"):
            return v.isoformat()[:10] if getattr(v, "hour", 0) == 0 and getattr(v, "minute", 0) == 0 else v.isoformat()
        if hasattr(v, "item"):
            return v.item()
        return v

    return {"columns": [str(c) for c in df.columns], "rows": [[cell(v) for v in r] for r in df.itertuples(index=False, name=None)]}


@router.get("/{model_id}/preview", summary="Xem trước kết quả (view đã build, hoặc tính tạm từ spec hiện tại)")
def preview(model_id: str, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), live: bool = False,
            ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db), services: Any = Depends(get_services)) -> dict:
    m = svc.get_model(db, ctx.workspace_id, model_id)
    spec = svc.spec_of(m)
    if m.built_at and not live:
        from modeling.warehouse import read_view, warehouse_engine

        df = read_view(warehouse_engine(), m.schema_name, spec.view_name, limit=limit, offset=offset)
        source = "warehouse"
    else:
        ev = svc.evaluator(services.cfg, m, spec)
        df, _ = ev.run(spec)
        df = df.iloc[offset: offset + limit]
        source = "draft"
    return {**_records(df), "source": source, "total": spec.main().row_count if spec.main() else 0}


@router.get("/{model_id}/tables/{key}/rows", summary="Dữ liệu 1 sheet đã nạp")
def table_rows(model_id: str, key: str, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
               ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db), services: Any = Depends(get_services)) -> dict:
    m = svc.get_model(db, ctx.workspace_id, model_id)
    spec = svc.spec_of(m)
    t = spec.table(key)
    if t is None:
        raise ValidationFailed("Không có sheet")
    frames = svc.load_frames(services.cfg, m.workspace_id, m.id, [key])
    df = frames.get(key)
    if df is None:
        return {"columns": [], "rows": [], "total": 0}
    return {**_records(df.iloc[offset: offset + limit]), "total": int(len(df))}


@router.get("/{model_id}/document", summary="Tài liệu quan hệ (md|html|docx|pdf, vi|en)")
def document(model_id: str, fmt: Literal["md", "html", "docx", "pdf"] = "md", lang: Literal["vi", "en"] = "vi",
             ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> Response:
    from datetime import datetime

    from modeling.document import build_blocks, to_markdown
    from reports.export import export_docx, export_pdf, report_html

    m = svc.get_model(db, ctx.workspace_id, model_id)
    spec = svc.spec_of(m)
    title, blocks = build_blocks(spec, name=m.name, schema=m.schema_name, validation=m.validation or None,
                                 view_sql=(m.build_info or {}).get("sql"), lang=lang)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    fname = f"{spec.view_name}_relationships_{lang}"
    if fmt == "md":
        return Response(to_markdown(title, blocks), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{fname}.md"'})
    if fmt == "html":
        return Response(report_html(title, blocks, workspace_name=m.name, generated_at=stamp), media_type="text/html; charset=utf-8")
    if fmt == "docx":
        data = export_docx(title, blocks, workspace_name=m.name, generated_at=stamp)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        data = export_pdf(title, blocks, workspace_name=m.name, generated_at=stamp)
        media = "application/pdf"
    return StreamingResponse(io.BytesIO(data), media_type=media, headers={"Content-Disposition": f'attachment; filename="{fname}.{fmt}"'})


@router.get("/{model_id}/connection", summary="Thông tin kết nối BI (Superset…) tới view")
def connection(model_id: str, ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db),
               services: Any = Depends(get_services)) -> dict:
    from sqlalchemy.engine import make_url

    m = svc.get_model(db, ctx.workspace_id, model_id)
    spec = svc.spec_of(m)
    st = get_settings()
    dsn = st.warehouse_public_dsn
    if not dsn:
        try:
            u = make_url(st.warehouse_url or st.database_url)
            role = services.cfg.modeling.reader_role or (u.username or "")
            sock = u.query.get("host") if u.query else None
            if u.host or not sock:
                dsn = f"postgresql://{role}@{u.host or 'localhost'}{':' + str(u.port) if u.port else ''}/{u.database}"
            else:  # kết nối qua unix socket (dev)
                dsn = f"postgresql://{role}@/{u.database}?host={sock}"
        except Exception:
            dsn = None
    cols = ", ".join(f'"{c.name}"' for c in spec.columns[:6])
    return {"dsn": dsn, "schema": m.schema_name, "view": spec.view_name, "qualified": f"{m.schema_name}.{spec.view_name}",
            "reader_role": services.cfg.modeling.reader_role, "built": bool(m.built_at),
            "tables": [{"key": t.key, "title": t.title, "role": t.role} for t in spec.tables],
            "sample_sql": f'SELECT source_row, {cols}\nFROM "{m.schema_name}"."{spec.view_name}"\nLIMIT 100;'}
