"""Xác thực + phân quyền.

- User: đăng ký/đăng nhập email + mật khẩu -> JWT (HS256, JWT_SECRET; claim sub=user_id, email, exp, iss).
  Header `Authorization: Bearer <JWT>`.
- Service: header `X-API-Key: rk_...` — key gắn 1 workspace + role, hoặc BOOTSTRAP_ADMIN_API_KEY (superadmin).
- Workspace: route dạng /w/{workspace_id}/... dùng dependency `workspace(min_role)` -> WorkspaceContext
  (vai trò owner/editor/viewer trong workspace đó; superadmin coi như owner mọi workspace).
- Rate limit theo key/user (api/ratelimit.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Path, Request
from sqlalchemy.orm import Session

from accounts import service as accounts
from api.deps import get_db
from core.config import get_config, get_settings
from core.errors import ForbiddenError, UnauthorizedError, ValidationFailed
from core.models import User
from core.principal import Principal, WorkspaceContext


def issue_jwt(user: User, ttl_minutes: int | None = None) -> str:
    settings, cfg = get_settings(), get_config()
    if not settings.jwt_secret:
        raise ValidationFailed("JWT_SECRET chưa cấu hình — không phát được token")
    now = datetime.now(timezone.utc)
    payload = {"sub": user.id, "email": user.email, "name": user.name, "iss": cfg.auth.jwt_issuer,
               "iat": int(now.timestamp()), "exp": int((now + timedelta(minutes=ttl_minutes or cfg.auth.jwt_ttl_minutes)).timestamp())}
    return jwt.encode(payload, settings.jwt_secret, algorithm=cfg.auth.jwt_algorithm)


def decode_jwt(token: str) -> dict:
    settings, cfg = get_settings(), get_config()
    if not settings.jwt_secret:
        raise UnauthorizedError("Server chưa cấu hình JWT_SECRET")
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[cfg.auth.jwt_algorithm], issuer=cfg.auth.jwt_issuer,
                          options={"require": ["sub", "exp"]})
    except jwt.ExpiredSignatureError as e:
        raise UnauthorizedError("Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại.") from e
    except jwt.PyJWTError as e:
        raise UnauthorizedError(f"Token không hợp lệ: {e}") from e


def authenticate(request: Request, session: Session) -> Principal:
    from api.ratelimit import check_rate_limit

    cfg = get_config()
    api_key = request.headers.get("x-api-key")
    auth = request.headers.get("authorization", "")
    if api_key:
        principal, limit = accounts.principal_from_api_key(session, api_key.strip())
        limit = limit or cfg.auth.default_rate_limit_per_minute
    elif auth.lower().startswith("bearer "):
        data = decode_jwt(auth[7:].strip())
        user = session.get(User, str(data["sub"]))
        if user is None or not user.is_active:
            raise UnauthorizedError("Tài khoản không tồn tại hoặc đã bị khoá")
        principal = accounts.principal_from_user(user)
        limit = cfg.auth.user_rate_limit_per_minute
    else:
        raise UnauthorizedError("Thiếu thông tin xác thực (Authorization: Bearer <JWT> hoặc X-API-Key)")
    check_rate_limit(session, principal.rate_key, limit)
    request.state.principal = principal
    return principal


def current_principal(request: Request, session: Session = Depends(get_db)) -> Principal:
    return authenticate(request, session)


def current_user(request: Request, session: Session = Depends(get_db)) -> User:
    p = authenticate(request, session)
    if p.kind != "user":
        raise ForbiddenError("Thao tác này cần tài khoản người dùng (không dùng API key)")
    return session.get(User, p.subject)


def require_superadmin(principal: Principal = Depends(current_principal)) -> Principal:
    if not principal.is_superadmin:
        raise ForbiddenError("Cần quyền quản trị hệ thống (superadmin)")
    return principal


def workspace(min_role: str = "viewer"):
    """Dependency: /w/{workspace_id}/... -> WorkspaceContext, yêu cầu vai trò tối thiểu."""

    def _dep(request: Request, workspace_id: str = Path(...), session: Session = Depends(get_db)) -> WorkspaceContext:
        principal = authenticate(request, session)
        ctx = accounts.resolve_context(session, principal, workspace_id)
        if not ctx.has_role(min_role):
            raise ForbiddenError(f"Cần vai trò {min_role} trở lên trong workspace (bạn là {ctx.role})")
        request.state.workspace = ctx
        return ctx

    return _dep


def effective_scope(ctx: WorkspaceContext, requested: str | None) -> str | None:
    """Giá trị scope (pack retail: store_code) áp cho request: thành viên có scope -> luôn là scope đó
    (xin scope khác -> 403); không có scope -> theo request (None = toàn workspace)."""
    mine = ctx.scope_value
    req = (requested or "").strip().upper() or None
    if mine:
        if req and req != mine:
            raise ForbiddenError(f"Tài khoản chỉ được truy vấn dữ liệu {mine} và dữ liệu toàn hệ thống, không được truy vấn {req}.")
        return mine
    return req
