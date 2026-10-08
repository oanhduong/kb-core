"""Ngôn ngữ giao diện của người đang gọi (vi | en) cho mọi chữ cố định backend trả ra cho người dùng.

- API: middleware đọc header Accept-Language (web luôn gửi "vi" hoặc "en") -> contextvar; các thread SSE copy
  context nên thấy cùng ngôn ngữ. Worker (job nền) mặc định "vi"; job tự đặt lại bằng `use_lang(payload["lang"])`.
- `L(vi, en)` chọn chuỗi theo ngôn ngữ hiện tại — dùng cho message lỗi (AppError), thông báo từ chối/chờ duyệt,
  nhãn trích dẫn, giải thích độ tin cậy... Nội dung do AI sinh không đi qua đây (prompt tự chọn ngôn ngữ).
"""
from __future__ import annotations

import contextlib
import contextvars
import re
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


_VI_CHARS = set("àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ")
# chữ của khuôn hệ thống (không phải của tài liệu): dòng bảng "Cột là giá trị", vị trí "Sheet X dòng 3"
_TEMPLATE_WORDS = re.compile(r"\b(là|dòng|trang|bảng)\b", re.IGNORECASE)


def detect_lang(text: str | None, default: Lang = DEFAULT_LANG) -> Lang:
    """Ngôn ngữ của một đoạn nội dung: đủ chữ cái mà gần như không có dấu tiếng Việt -> 'en'."""
    letters = [c for c in _TEMPLATE_WORDS.sub(" ", text or "").lower() if c.isalpha()]
    if len(letters) < 20:  # chuỗi ngắn (nhãn, tiêu đề): có dấu tiếng Việt là đủ để biết
        return "vi" if any(c in _VI_CHARS for c in letters) else default
    vi_ratio = sum(c in _VI_CHARS for c in letters) / len(letters)
    return "vi" if vi_ratio >= 0.01 else "en"


def output_language_rule(lang: str, subject: str = "the source content") -> str:
    """Đoạn nối vào CUỐI system prompt: ghi đè quy tắc 'viết tiếng Việt' của prompt đã ghim sha256."""
    return (f"\n\nOUTPUT LANGUAGE (overrides any language rule above): {subject} is in {language_name(lang)}, so write every "
            f"free-text value in {language_name(lang)}. Keep names, codes, numbers and JSON keys exactly as they are.")
