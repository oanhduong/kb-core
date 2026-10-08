"""Ngôn ngữ giao diện của người đang gọi (vi | en) cho mọi chữ cố định backend trả ra cho người dùng.

- API: middleware đọc header Accept-Language (web luôn gửi "vi" hoặc "en") -> contextvar; các thread SSE copy
  context nên thấy cùng ngôn ngữ. Worker (job nền) mặc định "vi"; job tự đặt lại bằng `use_lang(payload["lang"])`.
- `L(vi, en)` chọn chuỗi theo ngôn ngữ hiện tại — dùng cho message lỗi (AppError), thông báo từ chối/chờ duyệt,
  nhãn trích dẫn, giải thích độ tin cậy... Nội dung do AI sinh không đi qua đây (prompt tự chọn ngôn ngữ).
"""
from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator
from typing import Literal

Lang = Literal["vi", "en"]
DEFAULT_LANG: Lang = "vi"
_lang: contextvars.ContextVar[Lang] = contextvars.ContextVar("ui_lang", default=DEFAULT_LANG)


def normalize_lang(value: str | None) -> Lang:
    """'en-US,en;q=0.9' -> 'en'; thiếu/không nhận ra -> mặc định 'vi'."""
    first = (value or "").split(",")[0].strip().lower()
    return "en" if first.startswith("en") else "vi" if first.startswith("vi") else DEFAULT_LANG


def get_lang() -> Lang:
    return _lang.get()


def set_lang(lang: str | None) -> contextvars.Token:
    return _lang.set(normalize_lang(lang))


def reset_lang(token: contextvars.Token) -> None:
    _lang.reset(token)


@contextlib.contextmanager
def use_lang(lang: str | None) -> Iterator[Lang]:
    token = set_lang(lang)
    try:
        yield get_lang()
    finally:
        reset_lang(token)


def L(vi: str, en: str) -> str:
    return en if _lang.get() == "en" else vi


def language_name(lang: str | None = None) -> str:
    """Tên ngôn ngữ để chèn vào prompt ("Vietnamese"/"English")."""
    return "English" if (lang or get_lang()) == "en" else "Vietnamese"
