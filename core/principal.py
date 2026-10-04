"""Danh tính người/dịch vụ gọi API + ngữ cảnh workspace — dùng chung api, qa, kb.

Principal: ai đang gọi (user đăng nhập bằng JWT, hoặc service bằng API key).
WorkspaceContext: workspace đang thao tác + vai trò của principal trong đó (owner > editor > viewer)
+ thuộc tính thành viên do domain pack định nghĩa (vd store_code của pack retail).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ROLES = ("viewer", "editor", "owner")
_ROLE_RANK = {r: i for i, r in enumerate(ROLES)}


def role_at_least(role: str, minimum: str) -> bool:
    return _ROLE_RANK.get(role, -1) >= _ROLE_RANK.get(minimum, 99)


@dataclass(frozen=True)
class Principal:
    subject: str  # user_id, hoặc "apikey:<name>"
    email: str | None = None
    name: str | None = None
    kind: str = "user"  # user | service
    is_superadmin: bool = False
    key_id: str | None = None
    key_workspace_id: str | None = None  # API key gắn 1 workspace (None = key toàn cục/superadmin)
    key_role: str | None = None

    @property
    def rate_key(self) -> str:
        return f"key:{self.key_id}" if self.key_id else f"user:{self.subject}"


@dataclass(frozen=True)
class WorkspaceContext:
    workspace_id: str
    workspace_name: str
    domain_pack: str
    role: str
    attributes: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    principal: Principal | None = None

    def has_role(self, minimum: str) -> bool:
        return role_at_least(self.role, minimum)

    @property
    def scope_value(self) -> str | None:
        """Giá trị thuộc tính giới hạn dữ liệu (pack retail: store_code) — None = thấy toàn workspace."""
        from domains import get_pack

        attr = get_pack(self.domain_pack).scope_attribute
        if attr is None:
            return None
        v = self.attributes.get(attr.key)
        return str(v) if v else None

    @property
    def subject(self) -> str:
        return self.principal.subject if self.principal else "unknown"
