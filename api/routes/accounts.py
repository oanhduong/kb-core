"""Tài khoản & workspace: /auth/*, /workspaces, /w/{id}/members, /w/{id}/api-keys, /admin/users."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from accounts import service as accounts
from api.auth import current_principal, current_user, issue_jwt, require_superadmin, workspace
from api.deps import audit_action, get_db
from core.errors import ForbiddenError, NotFoundError
from core.i18n import L
from core.models import ApiKey, User, Workspace, WorkspaceInvite, WorkspaceMember
from core.principal import Principal, WorkspaceContext
from domains import get_pack, list_packs

router = APIRouter(tags=["accounts"])


def _user_view(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "is_superadmin": u.is_superadmin, "locale": u.locale,
            "created_at": u.created_at, "last_login_at": u.last_login_at, "is_active": u.is_active}


def _ws_view(w: Workspace, m: WorkspaceMember | None = None) -> dict:
    pack = get_pack(w.domain_pack)
    return {"id": w.id, "name": w.name, "slug": w.slug, "description": w.description, "domain_pack": w.domain_pack,
            "domain_pack_name": pack.name, "settings": w.settings, "owner_id": w.owner_id, "created_at": w.created_at,
            "role": m.role if m else None, "attributes": (m.attributes if m else {}) or {},
            "member_attributes": [{"key": a.key, "label": a.label, "description": a.description} for a in pack.member_attributes],
            "suggested_questions": list(pack.suggested_questions)}


# ------------------------------------------------------------------ auth
class SignupBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=120)
    workspace_name: str | None = Field(default=None, max_length=120, description="Tạo luôn workspace đầu tiên")


class LoginBody(BaseModel):
    email: EmailStr
    password: str


@router.post("/auth/signup", summary="Đăng ký tài khoản (tài khoản đầu tiên là superadmin)")
def signup(body: SignupBody, db: Session = Depends(get_db)) -> dict:
    user = accounts.create_user(db, email=body.email, password=body.password, name=body.name)
    ws = None
    if body.workspace_name:
        ws = accounts.create_workspace(db, user, name=body.workspace_name)
    db.commit()
    return {"access_token": issue_jwt(user), "token_type": "bearer", "user": _user_view(user),
            "workspace": _ws_view(ws, db.get(WorkspaceMember, (ws.id, user.id))) if ws else None}


@router.post("/auth/login", summary="Đăng nhập -> JWT")
def login(body: LoginBody, db: Session = Depends(get_db)) -> dict:
    user = accounts.authenticate_user(db, body.email, body.password)
    db.commit()
    return {"access_token": issue_jwt(user), "token_type": "bearer", "user": _user_view(user)}


@router.get("/auth/me", summary="Tài khoản hiện tại + workspace của tôi")
def me(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    if principal.kind == "service":
        return {"principal": {"subject": principal.subject, "kind": "service", "is_superadmin": principal.is_superadmin,
                              "workspace_id": principal.key_workspace_id, "role": principal.key_role}, "workspaces": []}
    user = db.get(User, principal.subject)
    return {"user": _user_view(user), "workspaces": [_ws_view(w, m) for w, m in accounts.list_user_workspaces(db, user)],
            "domain_packs": [{"key": p.key, "name": p.name, "description": p.description} for p in list_packs()]}


class MeUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    locale: str | None = Field(default=None, max_length=8)
    old_password: str | None = None
    new_password: str | None = Field(default=None, min_length=8, max_length=200)


@router.patch("/auth/me")
def update_me(body: MeUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if body.name is not None:
        user.name = body.name.strip()[:120] or user.name
    if body.locale:
        user.locale = body.locale
    if body.new_password:
        accounts.change_password(db, user, body.old_password or "", body.new_password)
    return _user_view(user)


# ------------------------------------------------------------------ workspaces
class WorkspaceBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    domain_pack: str = "generic"


@router.get("/workspaces", summary="Workspace của tôi")
def list_workspaces(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    return [_ws_view(w, m) for w, m in accounts.list_user_workspaces(db, user)]


@router.post("/workspaces", summary="Tạo workspace (người tạo là owner)")
def create_workspace(body: WorkspaceBody, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    ws = accounts.create_workspace(db, user, name=body.name, description=body.description, domain_pack=body.domain_pack)
    audit_action(request, accounts.principal_from_user(user), "workspace_create", ws.id, name=ws.name, pack=ws.domain_pack)
    return _ws_view(ws, db.get(WorkspaceMember, (ws.id, user.id)))


@router.get("/w/{workspace_id}", summary="Thông tin workspace + vai trò của tôi")
def get_workspace(ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> dict:
    ws = accounts.get_workspace_or_404(db, ctx.workspace_id)
    m = db.get(WorkspaceMember, (ws.id, ctx.subject)) if ctx.principal and ctx.principal.kind == "user" else None
    view = _ws_view(ws, m)
    view["role"], view["attributes"] = ctx.role, ctx.attributes
    view["member_count"] = len(accounts.list_members(db, ws.id))
    return view


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    domain_pack: str | None = None
    settings: dict | None = None


@router.patch("/w/{workspace_id}", summary="Sửa workspace (owner)")
def update_workspace(body: WorkspaceUpdate, request: Request, ctx: WorkspaceContext = Depends(workspace("owner")),
                     db: Session = Depends(get_db)) -> dict:
    ws = accounts.update_workspace(db, accounts.get_workspace_or_404(db, ctx.workspace_id), ctx, **body.model_dump(exclude_unset=True))
    audit_action(request, ctx.principal, "workspace_update", ws.id, fields=sorted(body.model_dump(exclude_unset=True)))
    return _ws_view(ws)


@router.delete("/w/{workspace_id}", summary="Lưu trữ workspace (owner) — dữ liệu giữ lại, ẩn khỏi danh sách")
def archive_workspace(request: Request, ctx: WorkspaceContext = Depends(workspace("owner")), db: Session = Depends(get_db)) -> dict:
    accounts.archive_workspace(db, accounts.get_workspace_or_404(db, ctx.workspace_id), ctx)
    audit_action(request, ctx.principal, "workspace_archive", ctx.workspace_id)
    return {"archived": ctx.workspace_id}


# ------------------------------------------------------------------ members
@router.get("/w/{workspace_id}/members")
def list_members(ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> dict:
    members = [{"user_id": u.id, "email": u.email, "name": u.name, "role": m.role, "attributes": m.attributes,
                "joined_at": m.created_at} for m, u in accounts.list_members(db, ctx.workspace_id)]
    invites = [{"invite_id": i.id, "email": i.email, "role": i.role, "attributes": i.attributes, "created_at": i.created_at}
               for i in db.execute(select(WorkspaceInvite).where(WorkspaceInvite.workspace_id == ctx.workspace_id,
                                                                 WorkspaceInvite.accepted_at.is_(None))).scalars()]
    return {"members": members, "pending_invites": invites}


class InviteBody(BaseModel):
    email: EmailStr
    role: Literal["viewer", "editor", "owner"] = "viewer"
    attributes: dict[str, Any] | None = None


@router.post("/w/{workspace_id}/members", summary="Mời thành viên theo email (owner)")
def invite(body: InviteBody, request: Request, ctx: WorkspaceContext = Depends(workspace("owner")), db: Session = Depends(get_db)) -> dict:
    out = accounts.invite_member(db, ctx, email=body.email, role=body.role, attributes=body.attributes)
    audit_action(request, ctx.principal, "member_invite", ctx.workspace_id, email=body.email, role=body.role)
    return out


class MemberUpdate(BaseModel):
    role: Literal["viewer", "editor", "owner"] | None = None
    attributes: dict[str, Any] | None = None


@router.patch("/w/{workspace_id}/members/{user_id}")
def update_member(user_id: str, body: MemberUpdate, request: Request, ctx: WorkspaceContext = Depends(workspace("owner")),
                  db: Session = Depends(get_db)) -> dict:
    m = accounts.update_member(db, ctx, user_id, role=body.role, attributes=body.attributes)
    audit_action(request, ctx.principal, "member_update", ctx.workspace_id, user_id=user_id, role=m.role)
    return {"user_id": user_id, "role": m.role, "attributes": m.attributes}


@router.delete("/w/{workspace_id}/members/{user_id}", summary="Xoá thành viên / rời workspace")
def remove_member(user_id: str, request: Request, ctx: WorkspaceContext = Depends(workspace("viewer")), db: Session = Depends(get_db)) -> dict:
    accounts.remove_member(db, ctx, user_id)
    audit_action(request, ctx.principal, "member_remove", ctx.workspace_id, user_id=user_id)
    return {"removed": user_id}


@router.delete("/w/{workspace_id}/invites/{invite_id}")
def cancel_invite(invite_id: str, ctx: WorkspaceContext = Depends(workspace("owner")), db: Session = Depends(get_db)) -> dict:
    inv = db.get(WorkspaceInvite, invite_id)
    if inv is None or inv.workspace_id != ctx.workspace_id:
        raise NotFoundError(L("Không có lời mời", "Invitation not found"))
    db.delete(inv)
    return {"cancelled": invite_id}


# ------------------------------------------------------------------ api keys (per workspace)
class ApiKeyBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: Literal["viewer", "editor", "owner"] = "viewer"
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=100000)


@router.get("/w/{workspace_id}/api-keys")
def list_keys(ctx: WorkspaceContext = Depends(workspace("owner")), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(ApiKey).where(ApiKey.workspace_id == ctx.workspace_id).order_by(ApiKey.created_at.desc())).scalars()
    return [{"id": k.id, "name": k.name, "prefix": k.key_prefix, "role": k.role, "rate_limit_per_minute": k.rate_limit_per_minute,
             "active": k.active, "created_at": k.created_at, "last_used_at": k.last_used_at} for k in rows]


@router.post("/w/{workspace_id}/api-keys", summary="Tạo API key tích hợp (key thô chỉ hiện 1 lần)")
def create_key(body: ApiKeyBody, request: Request, ctx: WorkspaceContext = Depends(workspace("owner")), db: Session = Depends(get_db)) -> dict:
    row, raw = accounts.create_api_key(db, ctx, name=body.name, role=body.role, rate_limit=body.rate_limit_per_minute)
    audit_action(request, ctx.principal, "api_key_create", row.id, name=body.name, role=body.role)
    return {"id": row.id, "name": row.name, "api_key": raw, "role": row.role, "rate_limit_per_minute": row.rate_limit_per_minute}


@router.delete("/w/{workspace_id}/api-keys/{key_id}")
def revoke_key(key_id: str, request: Request, ctx: WorkspaceContext = Depends(workspace("owner")), db: Session = Depends(get_db)) -> dict:
    k = db.get(ApiKey, key_id)
    if k is None or k.workspace_id != ctx.workspace_id:
        raise NotFoundError(L("Không có API key", "API key not found"))
    k.active = False
    audit_action(request, ctx.principal, "api_key_revoke", key_id)
    return {"id": key_id, "active": False}


# ------------------------------------------------------------------ superadmin
@router.get("/admin/users", summary="Superadmin: danh sách người dùng")
def admin_users(principal: Principal = Depends(require_superadmin), db: Session = Depends(get_db)) -> list[dict]:
    return [_user_view(u) for u in db.execute(select(User).order_by(User.created_at)).scalars()]


class AdminUserUpdate(BaseModel):
    is_active: bool | None = None
    is_superadmin: bool | None = None


@router.patch("/admin/users/{user_id}")
def admin_update_user(user_id: str, body: AdminUserUpdate, request: Request, principal: Principal = Depends(require_superadmin),
                      db: Session = Depends(get_db)) -> dict:
    u = accounts.get_user_or_404(db, user_id)
    if u.id == principal.subject and body.is_superadmin is False:
        raise ForbiddenError(L("Không tự bỏ quyền superadmin của chính mình", "You cannot remove your own superadmin permission"))
    if body.is_active is not None:
        u.is_active = body.is_active
    if body.is_superadmin is not None:
        u.is_superadmin = body.is_superadmin
    audit_action(request, principal, "admin_user_update", user_id, **body.model_dump(exclude_unset=True))
    return _user_view(u)


@router.get("/admin/workspaces", summary="Superadmin: mọi workspace")
def admin_workspaces(principal: Principal = Depends(require_superadmin), db: Session = Depends(get_db)) -> list[dict]:
    return [_ws_view(w) | {"archived_at": w.archived_at} for w in db.execute(select(Workspace).order_by(Workspace.created_at)).scalars()]
