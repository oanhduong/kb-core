"""Message lỗi người dùng đọc theo ngôn ngữ request: mặc định tiếng Việt, `use_lang("en")` -> tiếng Anh.
Không cần DB: dùng session giả và các hàm thuần (validate SQL, parse công thức, chuẩn hoá email)."""
import pytest

from accounts.service import authenticate_user, normalize_email
from analytics.engine import SQLNotAllowed, validate_sql
from core.errors import UnauthorizedError, ValidationFailed
from core.i18n import use_lang
from modeling import expr as E


class _NoUserResult:
    def scalar_one_or_none(self):
        return None


class _FakeSession:
    def execute(self, *_a, **_kw):
        return _NoUserResult()


def _message(fn, exc_type):
    with pytest.raises(exc_type) as ei:
        fn()
    return ei.value.message if hasattr(ei.value, "message") else str(ei.value)


def test_wrong_password_message_follows_language():
    login = lambda: authenticate_user(_FakeSession(), "nobody@example.com", "secret123")  # noqa: E731
    assert _message(login, UnauthorizedError) == "Email hoặc mật khẩu không đúng"
    with use_lang("en"):
        assert _message(login, UnauthorizedError) == "Incorrect email or password"
    # ra khỏi context -> về lại mặc định tiếng Việt
    assert _message(login, UnauthorizedError) == "Email hoặc mật khẩu không đúng"


def test_invalid_email_message_follows_language():
    assert _message(lambda: normalize_email("not-an-email"), ValidationFailed) == "Email không hợp lệ"
    with use_lang("en-US,en;q=0.9"):
        assert _message(lambda: normalize_email("not-an-email"), ValidationFailed) == "Invalid email"


def test_sql_validation_message_follows_language():
    bad = lambda: validate_sql("DELETE FROM t")  # noqa: E731
    assert _message(bad, SQLNotAllowed) == "Chỉ chấp nhận câu SELECT (hoặc WITH ... SELECT)"
    with use_lang("en"):
        assert _message(bad, SQLNotAllowed) == "Only SELECT statements (or WITH ... SELECT) are allowed"


def test_formula_error_message_follows_language():
    bad = lambda: E.parse("FOO([a])")  # noqa: E731
    assert _message(bad, E.ExprError) == "Hàm FOO chưa được hỗ trợ"
    with use_lang("en"):
        assert _message(bad, E.ExprError) == "Function FOO is not supported yet"
