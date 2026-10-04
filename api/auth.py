"""Xác thực + phân quyền (D2).

- Service-to-service: header `X-API-Key: rk_<...>`. Key lưu dạng sha256 (bảng api_keys), có role,
  store_code (tuỳ chọn) và rate limit riêng. BOOTSTRAP_ADMIN_API_KEY (env) là key admin khởi tạo để
  tạo các key khác — nên thu hồi/đổi sau khi đã tạo key riêng.
- User: header `Authorization: Bearer <JWT>` (HS256, JWT_SECRET) với claim sub, role, store_code, exp,
  iss. JWT phát bởi hệ thống đăng nhập của khách (gọi POST /auth/token bằng API key admin/service),
  hoặc `make token` khi vận hành; môi trường dev có POST /auth/dev-login.
- Role: staff < supervisor < admin. Scope store: principal có store_code chỉ truy vấn được dữ liệu
  store đó + dữ liệu toàn hệ thống (store_code NULL); principal không gắn store (admin/service toàn hệ
  thống) truy vấn store bất kỳ theo tham số request.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import get_config, get_settings
from core.errors import ForbiddenError, UnauthorizedError, ValidationFailed
from core.models import ApiKey, utcnow
from core.principal import ROLES, Principal
from graph.resolve import normalize_store_code

API_KEY_PREFIX = "rk_"


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def create_api_key(session: Session, *, name: str, role: str, store_code: str | None, rate_limit: int | None,
                   created_by: str) -> tuple[ApiKey, str]:
    if role not in ROLES:
        raise ValidationFailed(f"role phải là một trong {ROLES}")
    raw = generate_api_key()
    row = ApiKey(name=name, key_prefix=raw[:10], key_hash=hash_key(raw), role=role,
                 store_code=normalize_store_code(store_code) if store_code else None,
                 rate_limit_per_minute=rate_limit, created_by=created_by)
    session.add(row)
    session.flush()
    return row, raw


def issue_jwt(*, user_id: str, role: str, store_code: str | None, ttl_minutes: int | None = None,
              extra: dict[str, Any] | None = None) -> str:
    settings, cfg = get_settings(), get_config()
    if not settings.jwt_secret:
        raise ValidationFailed("JWT_SECRET chưa cấu hình — không phát được token")
    if role not in ROLES:
        raise ValidationFailed(f"role phải là một trong {ROLES}")
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "role": role, "store_code": normalize_store_code(store_code) if store_code else None,
               "iss": cfg.auth.jwt_issuer, "iat": int(now.timestamp()),
               "exp": int((now + timedelta(minutes=ttl_minutes or cfg.auth.jwt_ttl_minutes)).timestamp()), **(extra or {})}
    return jwt.encode(payload, settings.jwt_secret, algorithm=cfg.auth.jwt_algorithm)


def decode_jwt(token: str) -> Principal:
    settings, cfg = get_settings(), get_config()
    if not settings.jwt_secret:
        raise UnauthorizedError("Server chưa cấu hình JWT_SECRET")
    try:
        data = jwt.decode(token, settings.jwt_secret, algorithms=[cfg.auth.jwt_algorithm], issuer=cfg.auth.jwt_issuer,
                          options={"require": ["sub", "exp", "role"]})
    except jwt.ExpiredSignatureError as e:
        raise UnauthorizedError("Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại.") from e
    except jwt.PyJWTError as e:
        raise UnauthorizedError(f"Token không hợp lệ: {e}") from e
    if data.get("role") not in ROLES:
        raise UnauthorizedError("Token có role không hợp lệ")
    return Principal(subject=str(data["sub"]), role=data["role"], store_code=data.get("store_code"), kind="user")


def _principal_from_api_key(raw: str, session: Session) -> tuple[Principal, int | None]:
    settings = get_settings()
    if settings.bootstrap_admin_api_key and secrets.compare_digest(raw, settings.bootstrap_admin_api_key):
        return Principal(subject="apikey:bootstrap-admin", role="admin", store_code=None, kind="service",
                         key_id="bootstrap"), None
    row = session.execute(select(ApiKey).where(ApiKey.key_hash == hash_key(raw), ApiKey.active.is_(True))).scalar_one_or_none()
    if row is None:
        raise UnauthorizedError("API key không hợp lệ hoặc đã bị thu hồi")
    row.last_used_at = utcnow()
    return Principal(subject=f"apikey:{row.name}", role=row.role, store_code=row.store_code, kind="service",
                     key_id=row.id), row.rate_limit_per_minute


def authenticate(request: Request, session: Session) -> Principal:
    from api.ratelimit import check_rate_limit

    cfg = get_config()
    api_key = request.headers.get("x-api-key")
    auth = request.headers.get("authorization", "")
    if api_key:
        principal, limit = _principal_from_api_key(api_key.strip(), session)
        limit = limit or cfg.auth.default_rate_limit_per_minute
    elif auth.lower().startswith("bearer "):
        principal = decode_jwt(auth[7:].strip())
        limit = cfg.auth.user_rate_limit_per_minute
    else:
        raise UnauthorizedError("Thiếu thông tin xác thực (X-API-Key hoặc Authorization: Bearer <JWT>)")
    check_rate_limit(session, principal.rate_key, limit)
    request.state.principal = principal
    return principal


def effective_store(principal: Principal, requested: str | None) -> str | None:
    """Store dùng để lọc dữ liệu cho request: principal gắn store -> luôn là store đó (request đòi store
    khác -> 403); principal không gắn store -> theo request (None = toàn hệ thống)."""
    req = normalize_store_code(requested) if requested else None
    if principal.store_code:
        if req and req != principal.store_code:
            raise ForbiddenError(f"Tài khoản chỉ được truy vấn dữ liệu cửa hàng {principal.store_code} "
                                 f"và dữ liệu toàn hệ thống, không được truy vấn {req}.")
        return principal.store_code
    return req


def can_see_store(principal: Principal, store_code: str | None) -> bool:
    return principal.store_code is None or store_code is None or store_code == principal.store_code


def get_principal_dependency():
    from api.deps import get_db

    def _dep(request: Request, session: Session = Depends(get_db)) -> Principal:
        return authenticate(request, session)
    return _dep


current_principal = get_principal_dependency()


def require_role(*roles: str):
    def _dep(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.has_role(*roles):
            raise ForbiddenError(f"Cần quyền {' hoặc '.join(roles)} (tài khoản hiện tại: {principal.role})")
        return principal
    return _dep
