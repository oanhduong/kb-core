"""Knowledge graph mở theo workspace: /w/{ws}/entities, chi tiết, lân cận, tổng quan, gộp/sửa (editor)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.auth import workspace
from api.deps import audit_action, get_db
from core.principal import WorkspaceContext
from knowledge import service as ks
from knowledge.translate import localize

router = APIRouter(prefix="/w/{workspace_id}", tags=["knowledge"])


@router.get("/entities", summary="Thực thể trong workspace (tìm theo tên, lọc loại/tài liệu, xếp theo số lần nhắc)")
def list_entities(q: str | None = None, type: str | None = None, document_id: str | None = None, limit: int = Query(100, le=500),
                  offset: int = Query(0, ge=0), ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> dict:
    return localize(db, ctx.workspace_id, ks.list_entities(db, ctx.workspace_id, q=q, type_=type, document_id=document_id, limit=limit, offset=offset))


@router.get("/entities/{entity_id}", summary="Chi tiết thực thể: nguồn nhắc tới (bấm ra đoạn gốc), quan hệ vào/ra")
def entity_detail(entity_id: str, ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> dict:
    return localize(db, ctx.workspace_id, ks.entity_detail(db, ctx.workspace_id, entity_id))


@router.get("/entities/{entity_id}/neighborhood", summary="Đồ thị con quanh thực thể (depth 1–2)")
def entity_neighborhood(entity_id: str, depth: int = Query(1, ge=1, le=2), limit: int = Query(120, le=400),
                        ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> dict:
    return localize(db, ctx.workspace_id, ks.neighborhood(db, ctx.workspace_id, [entity_id], depth=depth, limit_nodes=limit))


class NeighborhoodBody(BaseModel):
    entity_ids: list[str] = Field(min_length=1, max_length=20)
    depth: int = Field(default=1, ge=1, le=2)
    limit: int = Field(default=150, le=400)


@router.post("/graph/neighborhood", summary="Đồ thị con quanh nhiều thực thể")
def multi_neighborhood(body: NeighborhoodBody, ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> dict:
    return localize(db, ctx.workspace_id, ks.neighborhood(db, ctx.workspace_id, body.entity_ids, depth=body.depth, limit_nodes=body.limit))


@router.get("/graph/overview", summary="Tổng quan knowledge graph: loại thực thể, loại quan hệ, đồ thị top entity")
def graph_overview(top: int = Query(40, le=150), ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> dict:
    return localize(db, ctx.workspace_id, ks.overview(db, ctx.workspace_id, top=top))


class EntityPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    type: Literal["person", "organization", "product", "location", "project", "event", "document", "policy_clause", "metric", "date", "money", "other"] | None = None
    description: str | None = Field(default=None, max_length=1000)
    aliases: list[str] | None = None


@router.patch("/entities/{entity_id}", summary="Sửa tên/loại/mô tả/alias thực thể")
def patch_entity(entity_id: str, body: EntityPatch, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")),
                 db: Session = Depends(get_db)) -> dict:
    e = ks.update_entity(db, ctx.workspace_id, entity_id, name=body.name, type_=body.type, description=body.description, aliases=body.aliases)
    audit_action(request, ctx.principal, "entity_update", entity_id, fields=sorted(body.model_dump(exclude_unset=True)))
    return ks.entity_view(e)


class MergeBody(BaseModel):
    merge_ids: list[str] = Field(min_length=1, max_length=50)


@router.post("/entities/{entity_id}/merge", summary="Gộp các thực thể trùng vào thực thể này (giữ mọi nguồn/quan hệ)")
def merge(entity_id: str, body: MergeBody, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")),
          db: Session = Depends(get_db)) -> dict:
    e = ks.merge_entities(db, ctx.workspace_id, entity_id, body.merge_ids)
    audit_action(request, ctx.principal, "entity_merge", entity_id, merged=body.merge_ids)
    return ks.entity_view(e)


@router.delete("/entities/{entity_id}", summary="Xoá thực thể (kèm mention/quan hệ)")
def delete_entity(entity_id: str, request: Request, ctx: WorkspaceContext = Depends(workspace("editor")), db: Session = Depends(get_db)) -> dict:
    ks.delete_entity(db, ctx.workspace_id, entity_id)
    audit_action(request, ctx.principal, "entity_delete", entity_id)
    return {"deleted": entity_id}
