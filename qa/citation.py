"""Citation bắt buộc (B2) — phần THUẦN (không gọi LLM/DB), unit test đầy đủ.

1. format_citation(): nhãn nguồn người đọc hiểu được, theo loại file:
       docx  -> "chính sách đổi trả, Điều 5"           (hoặc "..., mục <tiêu đề mục>")
       xlsx  -> "bảng giá tháng 8, Sheet HCM01 dòng 42"
       pdf   -> "catalogue đồ gia dụng, trang 3" (", bảng 1")
       audio/video -> "video hướng dẫn thu ngân, 02:15"
       image -> "poster khuyến mãi tháng 7"
2. Model được yêu cầu gắn mã [S1], [S2]... sau mỗi câu khẳng định. postcheck() bằng CODE:
       - bỏ mã không tồn tại trong danh sách nguồn đã cấp (model bịa mã);
       - không còn citation hợp lệ nào -> KHÔNG trả câu trả lời, thay bằng thông báo không đủ căn cứ;
       - tỉ lệ câu khẳng định có citation < citation.min_sentence_coverage -> cũng từ chối;
       - đánh số lại [S3] -> [1] theo thứ tự xuất hiện; trả danh sách citation để UI hiện link.
3. StreamingCitationGate: áp cùng quy tắc khi stream — giữ text lại tới khi thấy citation hợp lệ đầu
   tiên (không lộ câu trả lời chưa có căn cứ), đổi mã sang số thứ tự ngay trên luồng stream.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote
from typing import Any

_MARKER_RE = re.compile(r"\[(S\d+(?:\s*[,;]\s*S\d+)*)\]")
_MARKER_ID_RE = re.compile(r"S\d+")
_ARTICLE_RE = re.compile(r"Điều\s+(\d+)")
_XLSX_RE = re.compile(r"^(?P<sheet>.+?)!A(?P<row>\d+)(?::[A-Z]+\d+)?(?:\s*>\s*dòng\s*(?P<sub>\d+))?$")
_PDF_RE = re.compile(r"^Trang\s+(?P<page>\d+)(?:\s*>\s*Bảng\s+(?P<table>\d+))?")
_TIME_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=\S)|\n+")
_NON_CLAIM_RE = re.compile(
    r"^(xin chào|chào bạn|chào anh|chào chị|cảm ơn|dưới đây|sau đây|tóm lại|lưu ý:?$|bạn (có thể|vui lòng) liên hệ|"
    r"nếu (bạn|anh|chị) cần thêm)", re.IGNORECASE)


def display_title(title: str) -> str:
    """"CHÍNH SÁCH ĐỔI TRẢ HÀNG HÓA" -> "chính sách đổi trả hàng hóa"; giữ nguyên nếu đã viết thường/hoa
    hỗn hợp (tên riêng)."""
    t = re.sub(r"\s+", " ", title or "").strip()
    if t.isupper():
        return t.lower()
    return t[:1].lower() + t[1:] if t[:1].isupper() and not t[1:2].isupper() else t


def _mmss(hms: str) -> str:
    m = _TIME_RE.match(hms or "")
    if not m:
        return hms
    h, mi, s = (int(x) for x in m.groups())
    return f"{h:d}:{mi:02d}:{s:02d}" if h else f"{mi:02d}:{s:02d}"


def format_citation(title: str, source_type: str, location: str, meta: dict[str, Any] | None = None) -> str:
    meta = meta or {}
    name = display_title(title) or "tài liệu"
    loc = (location or "").strip()
    if source_type == "docx":
        arts = _ARTICLE_RE.findall(loc)
        if arts:
            return f"{name}, Điều {arts[-1]}"
        last = loc.split(" > ")[-1].strip() if loc else ""
        if last and display_title(last) != name and " > " in loc:
            return f"{name}, mục \"{last}\""
        return name
    if source_type == "xlsx":
        m = _XLSX_RE.match(loc)
        if m:
            row = m.group("row")
            return f"{name}, Sheet {m.group('sheet')} dòng {row}"
        return f"{name}, {loc}" if loc else name
    if source_type == "csv":
        m = re.match(r"^CSV!A(\d+)", loc)
        return f"{name}, dòng {m.group(1)}" if m else name
    if source_type == "pdf":
        m = _PDF_RE.match(loc)
        if m:
            out = f"{name}, trang {m.group('page')}"
            return out + (f", bảng {m.group('table')}" if m.group("table") else "")
        return name
    if source_type in ("audio", "video"):
        start = loc.split("-")[0].strip() if loc else ""
        return f"{name}, {_mmss(start)}" if _TIME_RE.match(start) else name
    if source_type == "image":
        return name
    return f"{name}, {loc}" if loc else name


# =====================================================================================
# POST-CHECK
# =====================================================================================

@dataclass
class Citation:
    n: int
    sid: str
    label: str
    chunk_id: str
    record_id: str
    document_id: str
    location: str
    snippet: str
    effective_date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"n": self.n, "sid": self.sid, "label": self.label, "chunk_id": self.chunk_id,
                "record_id": self.record_id, "document_id": self.document_id, "location": self.location,
                "snippet": self.snippet, "effective_date": self.effective_date,
                "url": f"/chunks/{quote(self.chunk_id, safe='')}"}


@dataclass
class PostCheckResult:
    ok: bool
    answer: str
    citations: list[Citation] = field(default_factory=list)
    used_sids: list[str] = field(default_factory=list)
    invalid_sids: list[str] = field(default_factory=list)
    claim_count: int = 0
    cited_claim_count: int = 0
    reason: str | None = None

    @property
    def coverage(self) -> float:
        return 1.0 if self.claim_count == 0 else self.cited_claim_count / self.claim_count


def marker_ids(text: str) -> list[str]:
    out: list[str] = []
    for m in _MARKER_RE.finditer(text):
        out.extend(_MARKER_ID_RE.findall(m.group(1)))
    return out


def _is_claim(sentence: str) -> bool:
    s = _MARKER_RE.sub("", sentence).strip(" -*•\t")
    if len(s) < 15 or not re.search(r"[A-Za-zÀ-ỹ]", s):
        return False
    if s.startswith("|") or s.startswith("#") or set(s) <= set("|-: "):
        return False  # dòng bảng Markdown / tiêu đề: citation đặt sau bảng, không đếm từng dòng
    return not _NON_CLAIM_RE.match(s)


def split_sentences(text: str) -> list[str]:
    """Tách câu theo dấu kết câu + khoảng trắng (không cắt "1.440.000"), gộp mảnh chỉ gồm mã nguồn
    ("... 7 ngày. [S2]") vào câu đứng trước."""
    out: list[str] = []
    for part in _SENTENCE_SPLIT_RE.split(text or ""):
        part = part.strip()
        if not part:
            continue
        if out and not _MARKER_RE.sub("", part).strip(" .,;:"):
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return out


def postcheck(answer: str, sources: dict[str, Any], *, min_coverage: float = 0.0,
              insufficient_message: str = "Không đủ căn cứ.") -> PostCheckResult:
    """sources: sid -> object có .label/.chunk_id/.record_id/.document_id/.location/.text/.effective_date."""
    valid = set(sources)
    invalid: list[str] = []
    numbering: dict[str, int] = {}

    def _renumber(m: re.Match[str]) -> str:
        nums = []
        for sid in _MARKER_ID_RE.findall(m.group(1)):
            if sid not in valid:
                invalid.append(sid)
                continue
            if sid not in numbering:
                numbering[sid] = len(numbering) + 1
            if numbering[sid] not in nums:
                nums.append(numbering[sid])
        return "".join(f"[{n}]" for n in nums)

    # đếm câu khẳng định có citation hợp lệ TRƯỚC khi đổi mã
    claims = cited = 0
    for sent in split_sentences(answer):
        if not _is_claim(sent):
            continue
        claims += 1
        if any(sid in valid for sid in marker_ids(sent)):
            cited += 1
    rendered = _MARKER_RE.sub(_renumber, answer)
    rendered = re.sub(r"[ \t]+([.,;:!?])", r"\1", re.sub(r"[ \t]{2,}", " ", rendered)).strip()
    citations = []
    for sid, n in sorted(numbering.items(), key=lambda kv: kv[1]):
        src = sources[sid]
        citations.append(Citation(n=n, sid=sid, label=src.label, chunk_id=src.chunk_id, record_id=src.record_id,
                                  document_id=src.document_id, location=src.location,
                                  snippet=(src.text or "")[:280], effective_date=src.effective_date))
    result = PostCheckResult(ok=True, answer=rendered, citations=citations, used_sids=list(numbering),
                             invalid_sids=sorted(set(invalid)), claim_count=claims, cited_claim_count=cited)
    if not citations:
        result.ok, result.reason, result.answer = False, "no_valid_citation", insufficient_message
        result.citations = []
    elif result.coverage < min_coverage:
        result.ok, result.reason, result.answer = False, f"coverage_{result.coverage:.2f}_below_{min_coverage:.2f}", insufficient_message
        result.citations = []
    return result


# =====================================================================================
# STREAMING GATE
# =====================================================================================

class StreamingCitationGate:
    """Nhận từng mảnh text model stream ra, trả mảnh được phép gửi cho user:
    - chưa thấy citation hợp lệ nào -> giữ lại toàn bộ (không gửi gì);
    - mã [S3] -> [n] theo thứ tự xuất hiện (khớp với postcheck), mã không hợp lệ bị bỏ;
    - mã bị cắt đôi giữa 2 mảnh ("[S" | "3]") được giữ lại tới mảnh sau."""

    def __init__(self, valid_sids: set[str]) -> None:
        self.valid = valid_sids
        self.numbering: dict[str, int] = {}
        self._pending = ""
        self._opened = False
        self.emitted = ""

    def _render(self, text: str) -> str:
        def repl(m: re.Match[str]) -> str:
            nums = []
            for sid in _MARKER_ID_RE.findall(m.group(1)):
                if sid in self.valid:
                    self.numbering.setdefault(sid, len(self.numbering) + 1)
                    nums.append(self.numbering[sid])
            return "".join(f"[{n}]" for n in dict.fromkeys(nums))
        return _MARKER_RE.sub(repl, text)

    def feed(self, piece: str) -> str:
        self._pending += piece
        cut = self._pending.rfind("[")
        if cut != -1 and "]" not in self._pending[cut:] and len(self._pending) - cut < 24:
            ready, self._pending = self._pending[:cut], self._pending[cut:]
        else:
            ready, self._pending = self._pending, ""
        if not self._opened:
            if any(sid in self.valid for sid in marker_ids(ready)):
                self._opened = True
            else:
                self._pending = ready + self._pending  # chưa có căn cứ -> giữ lại
                return ""
        out = self._render(ready)
        self.emitted += out
        return out

    def reset_pending(self) -> None:
        """Bỏ text đang giữ (chưa có căn cứ) — gọi đầu mỗi lượt model mới trong agent để lời dẫn trung gian
        ("Để tôi xem catalog…") không lọt ra khi gate mở ở lượt sau."""
        self._pending = ""

    def flush(self) -> str:
        if not self._opened:
            return ""
        out = self._render(self._pending)
        self._pending = ""
        self.emitted += out
        return out

    @property
    def opened(self) -> bool:
        return self._opened
