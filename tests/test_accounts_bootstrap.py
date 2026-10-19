"""Ai được superadmin khi đăng ký: SaaS public không được để người đăng ký đầu tiên ở production chiếm quyền."""
from unittest.mock import MagicMock

import pytest

from accounts import service


def _signup(monkeypatch, *, env: str, bootstrap: str | None, email: str = "khach@example.com", existing_users: int = 0):
    monkeypatch.setenv("APP_ENV", env)
    if bootstrap is None:
        monkeypatch.delenv("BOOTSTRAP_ADMIN_EMAIL", raising=False)
    else:
        monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", bootstrap)
    monkeypatch.setattr(service, "accept_pending_invites", lambda s, u: None)
    monkeypatch.setattr(service, "hash_password", lambda pw: "hashed")
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None  # email chưa đăng ký
    session.execute.return_value.scalar_one.return_value = existing_users
    return service.create_user(session, email=email, password="matkhau123", name="Khách")


@pytest.mark.parametrize("env", ["prod", "production"])
def test_first_signup_in_production_is_not_superadmin(monkeypatch, env):
    assert _signup(monkeypatch, env=env, bootstrap=None).is_superadmin is False
    assert _signup(monkeypatch, env=env, bootstrap="admin@khodulieu.vn").is_superadmin is False


def test_bootstrap_email_is_superadmin_in_production(monkeypatch):
    assert _signup(monkeypatch, env="prod", bootstrap="Admin@KhoDuLieu.vn", email="admin@khodulieu.vn",
                   existing_users=5).is_superadmin is True


def test_dev_without_bootstrap_email_first_user_is_superadmin(monkeypatch):
    assert _signup(monkeypatch, env="dev", bootstrap=None).is_superadmin is True
    assert _signup(monkeypatch, env="dev", bootstrap=None, existing_users=1).is_superadmin is False
