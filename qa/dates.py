"""Nhận diện mốc thời gian trong câu hỏi -> as_of (C2: trả lời "giá tại một thời điểm" từ lịch sử).

Chỉ kích hoạt khi câu hỏi nói rõ thời điểm QUÁ KHỨ/CỤ THỂ ("ngày 15/7/2026", "tháng 7/2026",
"tháng 7 năm nay", "hồi tháng trước"). Không có mốc -> None -> mặc định chỉ dữ liệu đang hiệu lực.
"""
from __future__ import annotations

import calendar
import re
from datetime import date

_FULL_DATE_RE = re.compile(r"\b(?:ngày\s+)?(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_YEAR_RE = re.compile(r"\btháng\s+(\d{1,2})\s*(?:/|năm|-)\s*(\d{4})\b", re.IGNORECASE)
_MONTH_ONLY_RE = re.compile(r"\b(?:hồi|vào|trong|của|giá)?\s*tháng\s+(\d{1,2})(?!\s*(?:/|năm|-)\s*\d)\b", re.IGNORECASE)
_LAST_MONTH_RE = re.compile(r"tháng trước", re.IGNORECASE)
_HIST_CUE_RE = re.compile(r"\b(hồi|lúc|vào|tại thời điểm|trước đây|cũ|tháng trước|ngày)\b", re.IGNORECASE)


def _valid(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def extract_as_of(question: str, today: date | None = None) -> date | None:
    today = today or date.today()
    m = _ISO_RE.search(question)
    if m:
        return _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _FULL_DATE_RE.search(question)
    if m:
        return _valid(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = _MONTH_YEAR_RE.search(question)
    if m:
        y, mo = int(m.group(2)), int(m.group(1))
        if 1 <= mo <= 12:
            return date(y, mo, calendar.monthrange(y, mo)[1]) if (y, mo) != (today.year, today.month) else today
    if _LAST_MONTH_RE.search(question):
        y, mo = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        return date(y, mo, calendar.monthrange(y, mo)[1])
    m = _MONTH_ONLY_RE.search(question)
    if m and _HIST_CUE_RE.search(question):
        mo = int(m.group(1))
        if 1 <= mo <= 12 and mo != today.month:
            y = today.year if mo < today.month else today.year - 1
            return date(y, mo, calendar.monthrange(y, mo)[1])
    return None
