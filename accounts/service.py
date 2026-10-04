"""Nghiệp vụ tài khoản & workspace. Mọi hàm nhận Session của caller (API commit).

- Mật khẩu: bcrypt. Email chuẩn hoá lowercase. Tài khoản đầu tiên của hệ thống là superadmin
  (hoặc email trùng BOOTSTRAP_ADMIN_EMAIL).
- Workspace: người tạo là owner; slug duy nhất sinh từ tên. Mỗi user có nhiều workspace.
- Thành viên: owner | editor | viewer; owner cuối cùng không được rời/hạ quyền. Lời mời theo email:
  user đã tồn tại -> thêm thẳng; chưa -> lưu invite, tự gia nhập khi đăng ký/đăng nhập.
- API key: gắn 1 workspace + role (service tích hợp), hoặc superadmin key toàn cục (BOOTSTRAP_ADMIN_API_KEY).
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import bcrypt
from slugify import slugify
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.errors import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError, ValidationFailed
from core.logging import log_event
from core.models import ApiKey, User, Workspace, WorkspaceInvite, WorkspaceMember, utcnow
from core.principal import ROLES, Principal, WorkspaceContext
import logging

from domains import get_pack, list_packs

logger = logging.getLogger(__name__)

API_KEY_PREFIX = "rk_"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8


# =====================================================================================
# USERS
# =====================================================================================

def normalize_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not _EMAIL_RE.match(e):
        raise ValidationFailed("Email không hợp lệ")
    return e


def hash_password(pw: str) -> str:
    if len(pw or "") < MIN_PASSWORD:
        raise ValidationFailed(f"Mật khẩu phải có ít nhất {MIN_PASSWORD} ký tự")
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw((pw or "").encode("utf-8"), hashed.encode())
    except ValueError:
        return False


def create_user(session: Session, *, email: str, password: str, name: str, locale: str = "vi") -> User:
    email = normalize_email(email)
    if session.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise ConflictError("Email đã được đăng ký")
    first_user = session.execute(select(func.count()).select_from(User)).scalar_one() == 0
    bootstrap_email = (os.environ.get("BOOTSTRAP_ADMIN_EMAIL") or "").lower()
    user = User(email=email, password_hash=hash_password(password), name=(name or email.split("@")[0]).strip()[:120],
                is_superadmin=first_user or email == bootstrap_email, locale=locale[:8] or "vi")
    session.add(user)
    session.flush()
    accept_pending_invites(session, user)
    log_event(logger, "user_created", user_id=user.id, superadmin=user.is_superadmin)
    return user


def authenticate_user(session: Session, email: str, password: str) -> User:
    email = (email or "").strip().lower()
    user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise UnauthorizedError("Email hoặc mật khẩu không đúng")
    if not user.is_active:
        raise ForbiddenError("Tài khoản đã bị khoá")
    user.last_login_at = utcnow()
    accept_pending_invites(session, user)
    return user


def change_password(session: Session, user: User, old: str, new: str) -> None:
    if not verify_password(old, user.password_hash):
        raise UnauthorizedError("Mật khẩu hiện tại không đúng")
    user.password_hash = hash_password(new)


def get_user_or_404(session: Session, user_id: str) -> User:
    u = session.get(User, user_id)
    if u is None:
        raise NotFoundError("Không có người dùng")
    return u


# =====================================================================================
# WORKSPACES
# =====================================================================================

def _unique_slug(session: Session, name: str) -> str:
    base = slugify(name)[:48] or "workspace"
    slug, n = base, 1
    while session.execute(select(Workspace.id).where(Workspace.slug == slug)).first():
        n += 1
        slug = f"{base}-{n}"
    return slug


def create_workspace(session: Session, owner: User, *, name: str, description: str | None = None,
                     domain_pack: str = "generic") -> Workspace:
    name = (name or "").strip()
    if not name:
        raise ValidationFailed("Tên workspace không được rỗng")
    if domain_pack not in {p.key for p in list_packs()}:
        raise ValidationFailed(f"domain_pack không hợp lệ: {domain_pack}")
    ws = Workspace(name=name[:120], slug=_unique_slug(session, name), owner_id=owner.id, description=description,
                   domain_pack=domain_pack)
    session.add(ws)
    session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=owner.id, role="owner"))
    session.flush()
    log_event(logger, "workspace_created", workspace_id=ws.id, owner=owner.id, pack=domain_pack)
    return ws


def list_user_workspaces(session: Session, user: User) -> list[tuple[Workspace, WorkspaceMember]]:
    q = (select(Workspace, WorkspaceMember).join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
         .where(WorkspaceMember.user_id == user.id, Workspace.archived_at.is_(None)).order_by(Workspace.created_at))
    return [(w, m) for w, m in session.execute(q).all()]


def get_workspace_or_404(session: Session, workspace_id: str) -> Workspace:
    ws = session.get(Workspace, workspace_id)
    if ws is None or ws.archived_at is not None:
        raise NotFoundError("Không có workspace")
    return ws


def resolve_context(session: Session, principal: Principal, workspace_id: str) -> WorkspaceContext:
    """Ngữ cảnh workspace cho principal: thành viên thật, API key gắn workspace, hoặc superadmin (owner ảo)."""
    ws = get_workspace_or_404(session, workspace_id)
    if principal.kind == "service":
        if principal.is_superadmin:
            return WorkspaceContext(ws.id, ws.name, ws.domain_pack, "owner", {}, ws.settings or {}, principal)
        if principal.key_workspace_id != ws.id:
            raise ForbiddenError("API key không thuộc workspace này")
        return WorkspaceContext(ws.id, ws.name, ws.domain_pack, principal.key_role or "viewer", {}, ws.settings or {}, principal)
    m = session.get(WorkspaceMember, (ws.id, principal.subject))
    if m is None:
        if principal.is_superadmin:
            return WorkspaceContext(ws.id, ws.name, ws.domain_pack, "owner", {}, ws.settings or {}, principal)
        raise ForbiddenError("Bạn không phải thành viên workspace này")
    return WorkspaceContext(ws.id, ws.name, ws.domain_pack, m.role, dict(m.attributes or {}), ws.settings or {}, principal)


def update_workspace(session: Session, ws: Workspace, ctx: WorkspaceContext, *, name: str | None = None,
                     description: str | None = None, domain_pack: str | None = None, settings: dict | None = None) -> Workspace:
    if not ctx.has_role("owner"):
        raise ForbiddenError("Chỉ owner mới sửa được workspace")
    if name is not None:
        if not name.strip():
            raise ValidationFailed("Tên workspace không được rỗng")
        ws.name = name.strip()[:120]
    if description is not None:
        ws.description = description
    if domain_pack is not None:
        if domain_pack not in {p.key for p in list_packs()}:
            raise ValidationFailed(f"domain_pack không hợp lệ: {domain_pack}")
        ws.domain_pack = domain_pack
    if settings is not None:
        ws.settings = {**(ws.settings or {}), **settings}
    return ws


def archive_workspace(session: Session, ws: Workspace, ctx: WorkspaceContext) -> None:
    if not ctx.has_role("owner"):
        raise ForbiddenError("Chỉ owner mới lưu trữ được workspace")
    ws.archived_at = utcnow()


# =====================================================================================
# MEMBERS & INVITES
# =====================================================================================

def _check_role(role: str) -> str:
    if role not in ROLES:
        raise ValidationFailed(f"role phải là một trong {ROLES}")
    return role


def _check_attributes(pack_key: str, attributes: dict | None) -> dict:
    pack = get_pack(pack_key)
    allowed = {a.key for a in pack.member_attributes}
    attrs = {k: v for k, v in (attributes or {}).items() if v not in (None, "")}
    unknown = set(attrs) - allowed
    if unknown:
        raise ValidationFailed(f"Thuộc tính không có trong domain pack {pack.name}: {sorted(unknown)}")
    return attrs


def list_members(session: Session, workspace_id: str) -> list[tuple[WorkspaceMember, User]]:
    q = (select(WorkspaceMember, User).join(User, User.id == WorkspaceMember.user_id)
         .where(WorkspaceMember.workspace_id == workspace_id).order_by(WorkspaceMember.created_at))
    return [(m, u) for m, u in session.execute(q).all()]


def _owner_count(session: Session, workspace_id: str) -> int:
    return session.execute(select(func.count()).select_from(WorkspaceMember)
                           .where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.role == "owner")).scalar_one()


def invite_member(session: Session, ctx: WorkspaceContext, *, email: str, role: str = "viewer",
                  attributes: dict | None = None) -> dict:
    if not ctx.has_role("owner"):
        raise ForbiddenError("Chỉ owner mới mời được thành viên")
    email = normalize_email(email)
    role = _check_role(role)
    attrs = _check_attributes(ctx.domain_pack, attributes)
    user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is not None:
        m = session.get(WorkspaceMember, (ctx.workspace_id, user.id))
        if m is None:
            m = WorkspaceMember(workspace_id=ctx.workspace_id, user_id=user.id, role=role, attributes=attrs, invited_by=ctx.subject)
            session.add(m)
        else:
            m.role, m.attributes = role, attrs
        session.flush()
        return {"status": "added", "user_id": user.id, "email": email, "role": role, "attributes": attrs}
    inv = session.execute(select(WorkspaceInvite).where(WorkspaceInvite.workspace_id == ctx.workspace_id,
                                                        WorkspaceInvite.email == email)).scalar_one_or_none()
    if inv is None:
        inv = WorkspaceInvite(workspace_id=ctx.workspace_id, email=email, role=role, attributes=attrs, invited_by=ctx.subject)
        session.add(inv)
    else:
        inv.role, inv.attributes, inv.accepted_at = role, attrs, None
    session.flush()
    return {"status": "invited", "invite_id": inv.id, "email": email, "role": role, "attributes": attrs,
            "note": "Người này chưa có tài khoản — sẽ tự vào workspace khi đăng ký bằng email trên."}


def accept_pending_invites(session: Session, user: User) -> int:
    invites = session.execute(select(WorkspaceInvite).where(WorkspaceInvite.email == user.email,
                                                            WorkspaceInvite.accepted_at.is_(None))).scalars().all()
    for inv in invites:
        if session.get(WorkspaceMember, (inv.workspace_id, user.id)) is None:
            session.add(WorkspaceMember(workspace_id=inv.workspace_id, user_id=user.id, role=inv.role,
                                        attributes=inv.attributes or {}, invited_by=inv.invited_by))
        inv.accepted_at = utcnow()
    session.flush()
    return len(invites)


def update_member(session: Session, ctx: WorkspaceContext, user_id: str, *, role: str | None = None,
                  attributes: dict | None = None) -> WorkspaceMember:
    if not ctx.has_role("owner"):
        raise ForbiddenError("Chỉ owner mới đổi vai trò thành viên")
    m = session.get(WorkspaceMember, (ctx.workspace_id, user_id))
    if m is None:
        raise NotFoundError("Không có thành viên")
    if role is not None:
        role = _check_role(role)
        if m.role == "owner" and role != "owner" and _owner_count(session, ctx.workspace_id) <= 1:
            raise ConflictError("Workspace phải còn ít nhất 1 owner")
        m.role = role
    if attributes is not None:
        m.attributes = _check_attributes(ctx.domain_pack, attributes)
    session.flush()
    return m


def remove_member(session: Session, ctx: WorkspaceContext, user_id: str) -> None:
    if user_id != ctx.subject and not ctx.has_role("owner"):
        raise ForbiddenError("Chỉ owner mới xoá được thành viên khác")
    m = session.get(WorkspaceMember, (ctx.workspace_id, user_id))
    if m is None:
        raise NotFoundError("Không có thành viên")
    if m.role == "owner" and _owner_count(session, ctx.workspace_id) <= 1:
        raise ConflictError("Owner cuối cùng không thể rời workspace — chuyển quyền owner trước")
    session.delete(m)


# =====================================================================================
# API KEYS
# =====================================================================================

def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_api_key(session: Session, ctx: WorkspaceContext, *, name: str, role: str = "viewer",
                   rate_limit: int | None = None) -> tuple[ApiKey, str]:
    if not ctx.has_role("owner"):
        raise ForbiddenError("Chỉ owner mới tạo được API key")
    role = _check_role(role)
    raw = API_KEY_PREFIX + secrets.token_urlsafe(32)
    row = ApiKey(workspace_id=ctx.workspace_id, name=(name or "key").strip()[:80], key_prefix=raw[:10], key_hash=hash_key(raw),
                 role=role, rate_limit_per_minute=rate_limit, created_by=ctx.subject)
    session.add(row)
    session.flush()
    return row, raw


def principal_from_api_key(session: Session, raw: str) -> tuple[Principal, int | None]:
    bootstrap = os.environ.get("BOOTSTRAP_ADMIN_API_KEY")
    if bootstrap and secrets.compare_digest(raw, bootstrap):
        return Principal(subject="apikey:bootstrap-admin", kind="service", is_superadmin=True, key_id="bootstrap"), None
    row = session.execute(select(ApiKey).where(ApiKey.key_hash == hash_key(raw), ApiKey.active.is_(True))).scalar_one_or_none()
    if row is None:
        raise UnauthorizedError("API key không hợp lệ hoặc đã bị thu hồi")
    row.last_used_at = utcnow()
    return Principal(subject=f"apikey:{row.name}", kind="service", key_id=row.id, key_workspace_id=row.workspace_id,
                     key_role=row.role), row.rate_limit_per_minute


def principal_from_user(user: User) -> Principal:
    return Principal(subject=user.id, email=user.email, name=user.name, kind="user", is_superadmin=user.is_superadmin)


