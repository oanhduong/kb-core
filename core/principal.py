"""Danh tính người/dịch vụ gọi API — dùng chung api (auth) và qa (scope theo store, review theo role)."""
from __future__ import annotations

from dataclasses import dataclass

ROLES = ("staff", "supervisor", "admin")


@dataclass(frozen=True)
class Principal:
    subject: str  # user_id (JWT sub) hoặc "apikey:<name>"
    role: str  # staff | supervisor | admin
    store_code: str | None  # None = không giới hạn cửa hàng (admin, service toàn hệ thống)
    kind: str = "user"  # user | service
    key_id: str | None = None

    def has_role(self, *roles: str) -> bool:
        order = {r: i for i, r in enumerate(ROLES)}
        mine = order.get(self.role, -1)
        return any(mine >= order.get(r, 99) for r in roles)

    @property
    def rate_key(self) -> str:
        return f"key:{self.key_id}" if self.key_id else f"user:{self.subject}"
