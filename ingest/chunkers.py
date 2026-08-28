"""Chunker cho pipeline retrieval: tách nội dung của KBRecord (đã ghi ra data/output/<record_id>.json
qua agent/tools.write_kb_record) thành các Chunk nhỏ hơn để embed/index.

Input: một hoặc nhiều schemas.KBRecord — content là văn bản gốc của MỘT phạm vi nghiệp vụ (một đoạn
văn, một bảng, một segment audio, ...) mà agent đã trích xuất.
Output: list[Chunk] — Chunk(chunk_id, parent_id, text, location, metadata). parent_id là record_id
của KBRecord gốc để trace ngược khi cần — NGOẠI TRỪ chiến lược parent_child (xem bên dưới), nơi
parent_id của child chunk trỏ tới chunk_id của parent thay vào đó (record_id gốc vẫn giữ ở
metadata["source_record_id"]).

Bốn chiến lược, chọn qua ChunkerConfig.strategy (đăng ký trong STRATEGIES — cắm/rút bằng cách
thêm/bớt entry trong dict đó, không cần sửa chunk_record()):
    fixed            — cửa sổ trượt theo token xấp xỉ (mặc định 512 token, overlap 50). "Token" ở
                        đây là ước lượng thô ~4 ký tự/token, cùng quy ước với
                        agent/orchestrator.py._approx_tokens (không tách token thật).
    recursive        — LangChain RecursiveCharacterTextSplitter, separator ưu tiên đoạn ("\\n\\n")
                        > câu (". ", "! ", "? ") > từ (" ").
    structure_aware  — tách theo ranh giới cấu trúc: Điều/Khoản (policy), Chương/Phần/Mục hoặc
                        section đánh số (docx/pdf). Gộp các đơn vị nhỏ liền kề tới ngưỡng
                        recursive_chunk_chars nhưng KHÔNG BAO GIỜ cắt giữa một đơn vị (kể cả khi
                        một Điều vượt ngưỡng). Không tìm được ranh giới cấu trúc nào -> rơi xuống
                        tách theo đoạn (blank line), rồi cuối cùng xuống recursive nếu vẫn là một
                        khối liền không có đoạn.

Dữ liệu dạng bảng (lưới "header | cell | cell", đúng định dạng ingest/parsers.py dùng cho
kind="table"/xlsx) được xử lý RIÊNG, ÁP DỤNG CHO CẢ 3 CHIẾN LƯỢC — kiểm tra trước, và nếu khớp thì
bỏ qua bước switch theo strategy ở trên:
    - Bảng có ít hơn ChunkerConfig.small_table_max_rows dòng dữ liệu -> giữ nguyên cả bảng làm 1 chunk.
    - Bảng lớn hơn -> mỗi dòng dữ liệu thành 1 chunk riêng, serialize thành câu tự nhiên có tên cột
      (vd "SKU ABC123, tên Tai nghe X, giá 590000, category Điện tử, store HCM01") — tên cột lặp lại
      ở MỌI row-chunk để chunk đứng một mình vẫn hiểu được, không phụ thuộc context xung quanh.

Media (source_type audio/video) cần gom nhiều KBRecord segment lại, nên KHÔNG đi qua chunk_record()
đơn lẻ — dùng chunk_records() (số nhiều): các segment cùng source_file được cụm hoá theo khoảng cách
thời gian (xem _chunk_media_cluster), giữ location "HH:MM:SS-HH:MM:SS" (start cụm - end cụm). Giả
định: metadata của KBRecord segment audio/video có thể có key "end" (HH:MM:SS); nếu không có, coi
segment đó tức thời (end = start).

Chiến lược thứ 4, parent_child (cũng đăng ký trong STRATEGIES, chọn qua ChunkerConfig.strategy như 3
chiến lược kia): chia 2 tầng — parent ~ChunkerConfig.parent_chunk_tokens (mặc định 1024 token) để trả
cho MODEL đọc khi trả lời, chứa nhiều child ~ChunkerConfig.child_chunk_tokens (mặc định 256 token) để
EMBED/SEARCH (đoạn ngắn cho semantic search chính xác hơn). child.parent_id trỏ tới chunk_id của
PARENT (khác 3 strategy kia, nơi parent_id luôn là record_id) — record_id gốc vẫn giữ ở
metadata["source_record_id"] của cả parent lẫn child để truy ngược. Downstream index chỉ nên embed
chunk có metadata["chunk_role"]=="child"; chunk "parent" lưu riêng để tra cứu khi trả context.

Hai bước xử lý HẬU KỲ (post-processing), tuỳ chọn, chạy SAU khi đã có chunk thô từ chunk_records():
    normalize_chunk_metadata(records, chunks, ...) — chuẩn hoá 8 field metadata dùng chung cho MỌI
        chunk (kể cả bảng/media/parent_child): source_file, doc_type, store_code, sku (list),
        category, effective_date, version, is_active. Rule (regex/heuristic trên content + field
        model đã tự điền qua write_kb_record) chạy trước; LLM (Claude Haiku) chỉ gọi cho field rule
        không ra được VÀ record.metadata cũng chưa có sẵn — tối đa 1 lần GỌI/record (cache theo
        record_id), không phải 1 lần/chunk, để giới hạn chi phí.
    add_contextual_retrieval(chunks, records, ...) — kỹ thuật "contextual retrieval": sinh 1-2 câu
        ngữ cảnh bằng Claude Haiku, gắn vào đầu mỗi chunk (nói chunk này thuộc phần nào của tài liệu
        gốc). BẮT BUỘC dùng prompt caching: document gốc (KBRecord.content) đặt ở khối có
        cache_control ephemeral, CHỈ chunk text thay đổi giữa các lần gọi cùng 1 record — cache được
        tái sử dụng cho mọi chunk của cùng record. BỎ QUA (không tốn thêm lệnh gọi LLM): chunk
        table_row/table_whole (header đã là ngữ cảnh, xem _chunk_table) và chunk vai trò "parent" của
        parent_child (đã đủ dài 1024 token để tự chứa ngữ cảnh). Cost thật (usage.* của từng lệnh
        gọi, không phải ước lượng) được log và có thể tích luỹ qua tham số cost_log — CLI --contextual-
        retrieval ghi báo cáo tổng hợp ra bench/contextual_retrieval.md.

Cách dùng CLI (so sánh 4 chiến lược trên cùng 1 tập KBRecord, tuỳ chọn bật 2 bước hậu kỳ):
    python -m ingest.chunkers --input data/output --out data/chunks
    python -m ingest.chunkers --input data/output --out data/chunks --strategies fixed,recursive
    python -m ingest.chunkers --input data/output --out data/chunks --normalize-metadata
    python -m ingest.chunkers --input data/output --out data/chunks --contextual-retrieval
"""
import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass

import anthropic
from pydantic import BaseModel, field_validator

from schemas import KBRecord

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4  # ước lượng thô, cùng quy ước với agent/orchestrator.py._approx_tokens

# Model rẻ/nhanh dùng cho 2 bước hậu kỳ tốn LLM (contextual retrieval + metadata LLM fallback) — cả
# hai đều là tác vụ đơn giản (tóm tắt ngắn / trích field), không cần model mạnh như agent/loop.py.MODEL
# dùng cho ingest chính. Giá USD/1M token khớp bảng giá hiện hành — cập nhật nếu đổi model, code
# không tự tra giá (keyed theo model, cùng quy ước với agent/batch.py.PRICING).
HAIKU_MODEL = "claude-haiku-4-5"
HAIKU_PRICING = {
    "claude-haiku-4-5": {"input_per_mtok": 1.00, "output_per_mtok": 5.00},
}
_CACHE_WRITE_MULTIPLIER = 1.25  # ghi cache đắt hơn input thường ~1.25x
_CACHE_READ_MULTIPLIER = 0.10  # đọc cache rẻ hơn input thường ~10x


class Chunk(BaseModel):
    chunk_id: str
    parent_id: str
    text: str
    location: str
    metadata: dict

    @field_validator("chunk_id", "parent_id", "text", "location")
    @classmethod
    def _not_blank(cls, v):
        if not v.strip():
            raise ValueError("giá trị không được để trống")
        return v


@dataclass
class ChunkerConfig:
    strategy: str = "fixed"
    fixed_chunk_tokens: int = 512
    fixed_overlap_tokens: int = 50
    recursive_chunk_chars: int = 1800
    recursive_overlap_chars: int = 200
    small_table_max_rows: int = 10
    media_max_gap_seconds: float = 5.0
    media_max_chunk_chars: int = 2000
    # parent_child (xem _chunk_parent_child): parent lớn để model đọc, child nhỏ để embed/search.
    parent_chunk_tokens: int = 1024
    child_chunk_tokens: int = 256
    child_overlap_tokens: int = 30


def _make_chunk(record, index, text, location=None, extra_metadata=None):
    metadata = dict(record.metadata)
    if extra_metadata:
        metadata.update(extra_metadata)
    return Chunk(
        chunk_id=f"{record.record_id}#{index:04d}",
        parent_id=record.record_id,
        text=text,
        location=location if location is not None else record.location,
        metadata=metadata,
    )


# =====================================================================================
# TABULAR DATA — áp dụng cho mọi chiến lược, kiểm tra trước khi switch theo strategy
# =====================================================================================

_TABLE_ROW_SEP = " | "


def _parse_table_grid(content):
    """Nhận diện lưới bảng dạng 'header | cell | cell' (định dạng ingest/parsers.py dùng cho
    kind="table"/xlsx). Trả về (headers, rows) nếu content đúng là lưới bảng (>=2 dòng, >=2 cột,
    mọi dòng cùng số cột); ngược lại trả về None (không phải bảng)."""
    lines = [ln for ln in content.split("\n") if ln.strip()]
    if len(lines) < 2:
        return None
    grid = [[cell.strip() for cell in ln.split(_TABLE_ROW_SEP)] for ln in lines]
    n_cols = len(grid[0])
    if n_cols < 2 or any(len(row) != n_cols for row in grid):
        return None
    return grid[0], grid[1:]


def _serialize_row(headers, row):
    return ", ".join(f"{h} {v}" for h, v in zip(headers, row))


def _chunk_table(record, headers, rows, config):
    if len(rows) < config.small_table_max_rows:
        return [_make_chunk(
            record, 0, record.content,
            extra_metadata={"strategy": "table_whole", "columns": headers, "row_count": len(rows)},
        )]
    return [
        _make_chunk(
            record, i, _serialize_row(headers, row),
            location=f"{record.location} > dòng {i + 1}",
            extra_metadata={"strategy": "table_row", "columns": headers, "row_index": i},
        )
        for i, row in enumerate(rows)
    ]


# =====================================================================================
# fixed — cửa sổ trượt theo token xấp xỉ (CHARS_PER_TOKEN ký tự/token)
# =====================================================================================

def _snap_to_word_start(text, start, end, next_start):
    """Chỉnh next_start (điểm bắt đầu cửa sổ kế tiếp) về ngay sau 1 khoảng trắng, để không bắt đầu
    giữa từ. Ưu tiên khoảng trắng đầu tiên trong [next_start, end) (giữ next_start tiến gần step
    dự kiến); nếu không có, lùi về khoảng trắng gần nhất trước đó trong [start, next_start). Không
    tìm được khoảng trắng nào (1 từ khổng lồ) thì giữ nguyên next_start (chấp nhận cắt giữa từ)."""
    snap_after = text.find(" ", next_start, end)
    if snap_after != -1:
        return snap_after + 1
    snap_before = text.rfind(" ", start, next_start)
    if snap_before > start:
        return snap_before + 1
    return next_start


def _char_window_spans(text, window_chars, overlap_chars):
    """list[(start, end)] cửa sổ trượt theo ký tự, lùi end về khoảng trắng gần nhất và chỉnh start
    của cửa sổ kế tiếp về đầu từ (_snap_to_word_start) để không cắt giữa từ. step = window_chars -
    overlap_chars, luôn >= 1 để đảm bảo tiến hết text."""
    n = len(text)
    if n <= window_chars:
        return [(0, n)]
    step = max(window_chars - overlap_chars, 1)
    spans = []
    start = 0
    while start < n:
        end = min(start + window_chars, n)
        if end < n:
            snap = text.rfind(" ", start, end)
            if snap > start:
                end = snap
        spans.append((start, end))
        if end >= n:
            break
        next_start = start + step
        if next_start < n:
            next_start = _snap_to_word_start(text, start, end, next_start)
        start = next_start
    return spans


def _chunk_fixed(record, config):
    text = record.content
    window_chars = max(config.fixed_chunk_tokens * CHARS_PER_TOKEN, 1)
    overlap_chars = min(config.fixed_overlap_tokens * CHARS_PER_TOKEN, window_chars - 1) if window_chars > 1 else 0
    spans = _char_window_spans(text, window_chars, overlap_chars)
    return [
        _make_chunk(
            record, i, text[start:end].strip(),
            extra_metadata={"strategy": "fixed", "chunk_index": i, "chunk_count": len(spans)},
        )
        for i, (start, end) in enumerate(spans)
    ]


# =====================================================================================
# recursive — LangChain RecursiveCharacterTextSplitter, đoạn > câu > từ
# =====================================================================================

def _chunk_recursive(record, config):
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except ImportError as e:
        raise ImportError(
            "Thiếu thư viện langchain-text-splitters. Cài đặt: pip install langchain-text-splitters"
        ) from e

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.recursive_chunk_chars,
        chunk_overlap=config.recursive_overlap_chars,
        separators=["\n\n", "\n", ". ", "! ", "? ", " ", ""],
        length_function=len,
    )
    pieces = [p.strip() for p in splitter.split_text(record.content) if p.strip()]
    if not pieces:
        pieces = [record.content.strip()]
    return [
        _make_chunk(
            record, i, piece,
            extra_metadata={"strategy": "recursive", "chunk_index": i, "chunk_count": len(pieces)},
        )
        for i, piece in enumerate(pieces)
    ]


# =====================================================================================
# structure_aware — Điều/Khoản (policy), heading (docx/pdf) — không cắt giữa một đơn vị
# =====================================================================================

_STRUCTURE_BOUNDARY_PATTERNS = [
    re.compile(r"(?m)^\s*Điều\s+\d+\b"),
    re.compile(r"(?m)^\s*Khoản\s+\d+\b"),
    re.compile(r"(?m)^\s*(?:Chương|Phần|Mục)\s+\d+\b"),
    re.compile(r"(?m)^\s*\d+(?:\.\d+)*[.)]\s+\S"),  # section đánh số kiểu "1.2 Quy định chung"
]


def _split_by_structure_boundaries(text):
    """Trả về list[(start, end)] — mỗi span là 1 đơn vị cấu trúc (Điều/Khoản/Chương/section), các
    span tile liền mạch toàn bộ text (không dùng string đã strip, để _group_spans cắt lại đúng
    nguyên văn kể cả khoảng trắng gốc giữa các đơn vị)."""
    for pattern in _STRUCTURE_BOUNDARY_PATTERNS:
        starts = [m.start() for m in pattern.finditer(text)]
        if not starts:
            continue
        if starts[0] != 0:
            starts = [0] + starts
        ends = starts[1:] + [len(text)]
        spans = [(s, e) for s, e in zip(starts, ends) if text[s:e].strip()]
        return spans or None
    return None


def _split_by_paragraphs(text):
    """list[(start, end)] theo đoạn (ranh giới là dòng trắng), cùng quy ước span như
    _split_by_structure_boundaries."""
    seps = [m.span() for m in re.finditer(r"\n\s*\n", text)]
    if not seps:
        return None
    spans, pos = [], 0
    for sep_start, sep_end in seps:
        if text[pos:sep_start].strip():
            spans.append((pos, sep_start))
        pos = sep_end
    if text[pos:].strip():
        spans.append((pos, len(text)))
    return spans if len(spans) >= 2 else None


def _group_spans(text, spans, max_chars):
    """Gộp các span liên tiếp tới ngưỡng max_chars, KHÔNG BAO GIỜ cắt giữa 1 span — kể cả khi bản
    thân 1 span đã vượt max_chars (vẫn giữ nguyên thành 1 group riêng). Mỗi group được CẮT TRỰC
    TIẾP từ text gốc theo (group_start, group_end) — không tái tạo dấu phân cách như join(), nên
    giữ đúng nguyên văn (kể cả khoảng trắng) giữa các span được gộp chung."""
    grouped = []
    group_start, group_end, group_len = None, None, 0
    for start, end in spans:
        unit_len = end - start
        if group_start is not None and group_len + unit_len > max_chars:
            grouped.append(text[group_start:group_end].strip())
            group_start = None
        if group_start is None:
            group_start = start
            group_len = 0
        group_end = end
        group_len += unit_len
    if group_start is not None:
        grouped.append(text[group_start:group_end].strip())
    return grouped


def _chunk_structure_aware(record, config):
    content = record.content
    spans = _split_by_structure_boundaries(content) or _split_by_paragraphs(content)
    if spans is None:
        return _chunk_recursive(record, config)  # không có ranh giới cấu trúc nào -> fallback cuối

    grouped = _group_spans(content, spans, config.recursive_chunk_chars)
    return [
        _make_chunk(
            record, i, piece,
            extra_metadata={"strategy": "structure_aware", "chunk_index": i, "chunk_count": len(grouped)},
        )
        for i, piece in enumerate(grouped)
    ]


# =====================================================================================
# parent_child — parent lớn (model đọc) chứa nhiều child nhỏ (embed/search); child.parent_id trỏ
# tới chunk_id của PARENT, khác quy ước record_id của 3 strategy trên (xem docstring đầu file).
# =====================================================================================

def _chunk_parent_child(record, config):
    text = record.content
    parent_window_chars = max(config.parent_chunk_tokens * CHARS_PER_TOKEN, 1)
    # Không overlap giữa các parent: mỗi parent đã đủ lớn (mặc định 1024 token) để tự đứng làm ngữ
    # cảnh trả cho model, và child bên trong đã có overlap riêng của chính nó (child_overlap_tokens)
    # để không mất thông tin ở ranh giới câu — overlap 2 tầng chồng nhau chỉ tốn thêm token lưu trữ
    # mà không có lợi ích rõ ràng cho chất lượng search.
    parent_spans = _char_window_spans(text, parent_window_chars, 0)

    child_window_chars = max(config.child_chunk_tokens * CHARS_PER_TOKEN, 1)
    child_overlap_chars = (
        min(config.child_overlap_tokens * CHARS_PER_TOKEN, child_window_chars - 1)
        if child_window_chars > 1 else 0
    )

    chunks = []
    for p_idx, (p_start, p_end) in enumerate(parent_spans):
        parent_text = text[p_start:p_end].strip()
        parent_id = f"{record.record_id}#p{p_idx:04d}"
        chunks.append(Chunk(
            chunk_id=parent_id,
            parent_id=record.record_id,
            text=parent_text,
            location=record.location,
            metadata={
                **record.metadata,
                "strategy": "parent_child", "chunk_role": "parent",
                "chunk_index": p_idx, "chunk_count": len(parent_spans),
                "source_record_id": record.record_id,
            },
        ))

        child_spans = _char_window_spans(parent_text, child_window_chars, child_overlap_chars)
        for c_idx, (c_start, c_end) in enumerate(child_spans):
            child_text = parent_text[c_start:c_end].strip()
            chunks.append(Chunk(
                chunk_id=f"{parent_id}#c{c_idx:04d}",
                parent_id=parent_id,  # trỏ tới CHUNK cha, không phải record_id — xem docstring đầu file
                text=child_text,
                location=record.location,
                metadata={
                    **record.metadata,
                    "strategy": "parent_child", "chunk_role": "child",
                    "chunk_index": c_idx, "chunk_count": len(child_spans),
                    "source_record_id": record.record_id,
                },
            ))
    return chunks


STRATEGIES = {
    "fixed": _chunk_fixed,
    "recursive": _chunk_recursive,
    "structure_aware": _chunk_structure_aware,
    "parent_child": _chunk_parent_child,
}


def chunk_record(record, config=None):
    """Chunk MỘT KBRecord theo config.strategy. Bảng (lưới 'header | cell') luôn được xử lý riêng
    qua _chunk_table, bất kể strategy nào đang chọn."""
    config = config or ChunkerConfig()
    grid = _parse_table_grid(record.content)
    if grid is not None:
        headers, rows = grid
        return _chunk_table(record, headers, rows, config)

    strategy_fn = STRATEGIES.get(config.strategy)
    if strategy_fn is None:
        raise ValueError(f"Chiến lược chunking không hợp lệ: {config.strategy!r} (hỗ trợ: {', '.join(STRATEGIES)})")
    return strategy_fn(record, config)


# =====================================================================================
# MEDIA — gom cụm segment audio/video liền kề, giữ timestamp bắt đầu/kết thúc
# =====================================================================================

_TIME_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})$")


def _hms_to_seconds(hms):
    m = _TIME_RE.match(hms or "")
    if not m:
        return None
    h, mnt, s = (int(x) for x in m.groups())
    return h * 3600 + mnt * 60 + s


def _seconds_to_hms(total_seconds):
    total = int(round(total_seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _segment_bounds(record):
    """(start_sec, end_sec) của 1 KBRecord segment audio/video. start lấy từ location (HH:MM:SS);
    end lấy từ metadata['end'] nếu có (cùng định dạng), ngược lại coi segment tức thời (end=start)."""
    start = _hms_to_seconds(record.location)
    if start is None:
        return None, None
    end_raw = record.metadata.get("end") if isinstance(record.metadata, dict) else None
    end = _hms_to_seconds(end_raw) if isinstance(end_raw, str) else None
    return start, end if end is not None else start


def _chunk_media_cluster(records, config):
    bounded = [(r, *_segment_bounds(r)) for r in records]
    bad = next((r for r, s, _ in bounded if s is None), None)
    if bad is not None:
        raise ValueError(f"KBRecord segment media có location không đúng định dạng HH:MM:SS: {bad.record_id!r}")
    bounded.sort(key=lambda b: b[1])

    clusters, current, current_chars, prev_end = [], [], 0, None
    for record, start, end in bounded:
        gap = start - prev_end if prev_end is not None else 0
        exceeds_gap = gap > config.media_max_gap_seconds
        exceeds_size = current_chars + len(record.content) > config.media_max_chunk_chars
        if current and (exceeds_gap or exceeds_size):
            clusters.append(current)
            current, current_chars = [], 0
        current.append((record, start, end))
        current_chars += len(record.content)
        prev_end = end
    if current:
        clusters.append(current)

    chunks = []
    for i, cluster in enumerate(clusters):
        anchor = cluster[0][0]
        text = " ".join(r.content for r, _, _ in cluster)
        location = f"{_seconds_to_hms(cluster[0][1])}-{_seconds_to_hms(max(e for _, _, e in cluster))}"
        chunks.append(_make_chunk(
            anchor, i, text, location=location,
            extra_metadata={
                "strategy": "media_cluster",
                "segment_count": len(cluster),
                "segment_record_ids": [r.record_id for r, _, _ in cluster],
            },
        ))
    return chunks


def chunk_records(records, config=None):
    """Entry point cho nhiều KBRecord (CLI/batch): audio/video được gom theo source_file rồi cụm
    hoá (_chunk_media_cluster); các loại còn lại chunk độc lập qua chunk_record()."""
    config = config or ChunkerConfig()
    chunks = []
    media_groups = {}
    for record in records:
        if record.source_type in ("audio", "video"):
            media_groups.setdefault((record.source_type, record.source_file), []).append(record)
        else:
            chunks.extend(chunk_record(record, config))
    for group_records in media_groups.values():
        chunks.extend(_chunk_media_cluster(group_records, config))
    return chunks


def _owning_record_id(chunk):
    """record_id gốc mà chunk thuộc về — parent_id ở 3 strategy gốc + media_cluster CHÍNH LÀ
    record_id, nhưng ở parent_child, parent_id trỏ tới chunk cha nên phải đọc metadata
    'source_record_id' (do _chunk_parent_child gắn cho cả parent lẫn child) thay vào đó."""
    return chunk.metadata.get("source_record_id") or chunk.parent_id


# =====================================================================================
# STAGE C — chuẩn hoá metadata cho MỌI chunk: rule (regex/heuristic) trước, LLM (Haiku) chỉ cho
# field rule không ra được — tối đa 1 lệnh gọi LLM/record (không phải /chunk), xem docstring đầu file.
# =====================================================================================

NORMALIZED_METADATA_FIELDS = ("store_code", "sku", "category", "effective_date", "version", "is_active")

# Heuristic thô, KHÔNG chính xác tuyệt đối (chấp nhận được vì đây là rule "cố gắng trước", LLM sẽ
# vá phần rule bỏ sót) — SKU: 2-5 chữ hoa (kể cả Đ — prefix theo category tiếng Việt viết tắt rất
# phổ biến trong dữ liệu thật, vd "ĐT-018" = Điện Tử, "ĐM-032" = Điện Máy, "ĐCT-006" = Đồ Chơi Trẻ
# em, xem data/raw/xlsx/01_bang_gia_sku.xlsx) + 2-6 số, có thể có dấu gạch ngang (vd "MB-2201").
_SKU_RE = re.compile(r"\b[A-ZĐ]{2,5}-?\d{2,6}(?:-[A-Z0-9]+)?\b")
# store_code: 2-4 chữ hoa (kể cả Đ) + ÍT NHẤT 1 số (vd "HCM01", "HN-001") — bắt buộc có số để tránh
# khớp nhầm các từ viết tắt toàn chữ hoa xuất hiện thường xuyên trong content (vd "SKU", "POS",
# "CSKH" — đã từng khớp nhầm "SKU" thành store_code khi test trên bảng giá thật). VẪN CÓ THỂ khớp
# nhầm với SKU (cùng hình dạng chữ+số) khi content không có nhãn rõ ràng — xem _rule_based_record_metadata,
# nơi rule CHỦ ĐỘNG bỏ qua match không nhãn nếu content đã có SKU-shaped token khác (điển hình bảng
# giá: nhiều SKU nhưng không có store_code nào) thay vì đoán bừa, để LLM fallback xử lý đúng hơn.
_STORE_CODE_RE = re.compile(r"\b[A-ZĐ]{2,4}-?\d{1,4}\b")
# IGNORECASE chỉ nên áp cho phần NHÃN ("cửa hàng"/"chi nhánh"/"store"/"tại"), không áp cho phần mã —
# mã store_code luôn viết hoa, viết thường ở đây nhiều khả năng là từ thường trong câu, không phải mã.
_STORE_CODE_LABELED_RE = re.compile(r"(?:[Cc]ửa hàng|[Cc]hi nhánh|[Ss]tore|tại)\s+([A-ZĐ]{2,4}\d*)\b")
_EFFECTIVE_DATE_ISO_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_EFFECTIVE_DATE_VN_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_VERSION_RE = re.compile(r"\b[vV](?:ersion)?\.?\s?\d+(?:\.\d+)*\b")
_IS_ACTIVE_NEGATIVE_RE = re.compile(
    r"hết hiệu lực|ngừng áp dụng|đã hủy|hủy bỏ|không còn áp dụng|hết hạn", re.IGNORECASE,
)

METADATA_EXTRACTION_SYSTEM_PROMPT = (
    "Bạn trích xuất metadata chuẩn hoá cho một đoạn tri thức bán lẻ. Chỉ trả về đúng 1 dòng JSON "
    "hợp lệ, đúng các field được yêu cầu — giá trị null cho field không xác định được TỪ NỘI DUNG "
    "đưa vào, không suy đoán hay bịa thông tin không có trong văn bản. Không thêm giải thích, không "
    "thêm markdown code fence."
)


def _is_missing(value):
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return len(value) == 0
    return False


def _find_all_skus(text):
    return sorted(set(_SKU_RE.findall(text)))


def _normalize_date(text):
    m = _EFFECTIVE_DATE_ISO_RE.search(text)
    if m:
        return m.group(0)
    m = _EFFECTIVE_DATE_VN_RE.search(text)
    if not m:
        return None
    d, mo, y = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def _rule_based_record_metadata(record):
    """Thử suy ra 6 field từ record.metadata (agent đã tự điền lúc ingest, xem agent/tools.py) trước,
    regex trên record.content nếu record.metadata chưa có — KHÔNG động tới sku (normalize_chunk_metadata
    tính lại sku theo TỪNG chunk, xem ở dưới)."""
    meta = record.metadata if isinstance(record.metadata, dict) else {}
    content = record.content

    store_code = meta.get("store_code")
    if _is_missing(store_code) or not isinstance(store_code, str):
        m_labeled = _STORE_CODE_LABELED_RE.search(content)
        if m_labeled:
            store_code = m_labeled.group(1)  # có nhãn rõ ràng -> tin cậy nhất, không cần phân biệt với SKU
        elif _SKU_RE.search(content):
            # content đã có SKU-shaped token (cùng hình dạng chữ+số với store_code) mà KHÔNG có nhãn
            # "cửa hàng"/"chi nhánh"/"tại" nào — rất có thể match của _STORE_CODE_RE bên dưới chính
            # là SKU bị nhận nhầm (từng xảy ra thật: record chỉ có SKU, không có store_code, nhưng
            # rule cũ gán luôn SKU dòng đầu làm store_code cho MỌI row-chunk). Không đoán mò, để None
            # cho LLM fallback quyết định (LLM đọc được ngữ cảnh đầy đủ hơn regex).
            store_code = None
        else:
            m = _STORE_CODE_RE.search(content)
            store_code = m.group(0) if m else None

    category = meta.get("category")
    if _is_missing(category) or not isinstance(category, str):
        category = None  # không có heuristic đáng tin cho category -> để trống, LLM fallback xử lý

    effective_date = meta.get("effective_date")
    if _is_missing(effective_date) or not (isinstance(effective_date, str) and _EFFECTIVE_DATE_ISO_RE.fullmatch(effective_date)):
        effective_date = _normalize_date(content)

    version = meta.get("version")
    if _is_missing(version) or not isinstance(version, str):
        m = _VERSION_RE.search(content)
        version = m.group(0) if m else None

    is_active_meta = meta.get("is_active")
    is_active = is_active_meta if isinstance(is_active_meta, bool) else not bool(_IS_ACTIVE_NEGATIVE_RE.search(content))

    return {
        "store_code": store_code, "category": category, "effective_date": effective_date,
        "version": version, "is_active": is_active,
    }


def _extract_metadata_by_llm(record, missing_fields, client, model):
    """Gọi Haiku CHỈ cho field trong missing_fields, dùng toàn bộ record.content làm ngữ cảnh — 1
    lệnh gọi duy nhất cho mọi field còn thiếu của record này (không phải 1 lệnh gọi/field)."""
    prompt = (
        f"doc_type: {record.doc_type}\nNội dung:\n{record.content}\n\n"
        f"Trích xuất các field sau từ nội dung trên (chỉ các field này, JSON 1 dòng dạng "
        f'{{"field": giá_trị, ...}}, null nếu không xác định được): {", ".join(missing_fields)}.\n'
        "- category: loại sản phẩm/dịch vụ liên quan (vd 'Điện tử', 'Gia dụng'), lấy nguyên văn nếu có.\n"
        "- version: số phiên bản chính sách/tài liệu nếu có ghi rõ.\n"
        "- store_code: mã cửa hàng áp dụng nếu có ghi rõ.\n"
        "- effective_date: ngày hiệu lực, định dạng YYYY-MM-DD.\n"
        "- is_active: true/false, nội dung có nói còn hiệu lực hay đã hết hiệu lực/hủy bỏ không."
    )
    response = client.messages.create(
        model=model, max_tokens=200,
        system=METADATA_EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "\n".join(b.text for b in response.content if b.type == "text").strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning(
            "normalize_chunk_metadata: LLM fallback trả về JSON không hợp lệ (record=%s), bỏ qua: %r",
            record.record_id, text,
        )
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in missing_fields}


def _record_level_metadata(record, client, model, use_llm_fallback):
    result = _rule_based_record_metadata(record)
    missing = [f for f in NORMALIZED_METADATA_FIELDS if f != "sku" and _is_missing(result.get(f))]
    if missing and use_llm_fallback:
        client = client or anthropic.Anthropic()
        llm_result = _extract_metadata_by_llm(record, missing, client, model)
        for field_name in missing:
            value = llm_result.get(field_name)
            if not _is_missing(value):
                result[field_name] = value
    return result


def normalize_chunk_metadata(records, chunks, client=None, model=HAIKU_MODEL, use_llm_fallback=True):
    """Chuẩn hoá 8 field metadata (source_file, doc_type, store_code, sku, category, effective_date,
    version, is_active) cho MỌI chunk trong `chunks` — kể cả chunk bảng/media/parent_child. record_id
    sở hữu mỗi chunk được suy ra qua _owning_record_id() để hoạt động đúng với cả parent_child (nơi
    chunk.parent_id trỏ tới chunk cha, không phải record_id). LLM fallback (nếu bật) chạy tối đa 1
    lần/record (cache theo record_id trong `cache`), KHÔNG phải 1 lần/chunk. sku là ngoại lệ — tính
    lại theo TỪNG chunk bằng regex (rẻ, không gọi LLM) vì một chunk cụ thể (đặc biệt row-chunk của
    bảng giá) thường chỉ nhắc một phần SKU của cả record, không phải toàn bộ."""
    record_by_id = {r.record_id: r for r in records}
    cache = {}
    normalized = []
    for chunk in chunks:
        record_id = _owning_record_id(chunk)
        record = record_by_id.get(record_id)
        if record is None:
            normalized.append(chunk)  # không tìm được record gốc (không nên xảy ra) -> giữ nguyên
            continue
        if record_id not in cache:
            cache[record_id] = _record_level_metadata(record, client, model, use_llm_fallback)
        base = cache[record_id]
        chunk_sku = _find_all_skus(chunk.text)
        new_metadata = {
            **chunk.metadata,
            "source_file": record.source_file,
            "doc_type": record.doc_type,
            "store_code": base["store_code"],
            "sku": chunk_sku,
            "category": base["category"],
            "effective_date": base["effective_date"],
            "version": base["version"],
            "is_active": base["is_active"],
        }
        normalized.append(chunk.model_copy(update={"metadata": new_metadata}))
    return normalized


# =====================================================================================
# STAGE A — contextual retrieval: sinh 1-2 câu ngữ cảnh bằng Haiku, gắn vào đầu mỗi chunk. BẮT BUỘC
# dùng prompt caching (document gốc cached, chỉ chunk text thay đổi) — xem docstring đầu file.
# =====================================================================================

CONTEXTUAL_RETRIEVAL_SYSTEM_PROMPT = (
    "Bạn là trợ lý chuẩn bị dữ liệu cho hệ thống retrieval của RetailKB Copilot. Cho một tài liệu "
    "đầy đủ và một đoạn trích (chunk) nằm trong tài liệu đó, hãy viết MỘT câu ngữ cảnh ngắn (tiếng "
    "Việt) nói rõ chunk thuộc phần nào của tài liệu (điều khoản/mục/đối tượng áp dụng/hạng mục...). "
    "Câu ngữ cảnh PHẢI kết thúc bằng dấu hai chấm (:) để khi ghép trực tiếp với chunk phía sau (chỉ "
    "cách nhau 1 dấu cách, không thêm ký tự nào khác) đọc thành một câu tự nhiên — ví dụ: 'Thuộc Điều "
    "5 chính sách đổi trả, áp dụng cho hàng điện tử:'. CHỈ trả về đúng câu ngữ cảnh đó — không lặp "
    "lại nguyên văn chunk, không thêm giải thích hay tiền tố nào khác."
)

CONTEXTUAL_RETRIEVAL_USER_TEMPLATE = (
    "Đây là đoạn trích (chunk) cần thêm ngữ cảnh:\n<chunk>\n{chunk}\n</chunk>\n\n"
    "Viết câu ngữ cảnh theo đúng hướng dẫn ở system prompt cho chunk này, dựa trên toàn văn tài liệu "
    "ở trên."
)

# Chunk KHÔNG cần contextual retrieval: row/whole-table (header đã là ngữ cảnh, xem _chunk_table) và
# chunk vai trò "parent" của parent_child (đã đủ dài — mặc định 1024 token — để tự chứa ngữ cảnh).
_CONTEXTUAL_RETRIEVAL_SKIP_STRATEGIES = {"table_row", "table_whole"}


def _needs_contextual_retrieval(chunk):
    if chunk.metadata.get("strategy") in _CONTEXTUAL_RETRIEVAL_SKIP_STRATEGIES:
        return False
    if chunk.metadata.get("chunk_role") == "parent":
        return False
    return True


def _contextual_cost_usd(usage, model):
    price = HAIKU_PRICING.get(model)
    if price is None:
        return None
    cache_created = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    return (
        usage.input_tokens / 1_000_000 * price["input_per_mtok"]
        + cache_created / 1_000_000 * price["input_per_mtok"] * _CACHE_WRITE_MULTIPLIER
        + cache_read / 1_000_000 * price["input_per_mtok"] * _CACHE_READ_MULTIPLIER
        + usage.output_tokens / 1_000_000 * price["output_per_mtok"]
    )


def _generate_chunk_context(document, chunk_text, client, model):
    """Document đặt ở khối RIÊNG có cache_control ephemeral — khối theo sau (chunk_text) không có
    cache_control nên luôn được coi là phần "động", đúng yêu cầu "document gốc cached, chỉ chunk
    thay đổi". Cache được tái sử dụng cho MỌI chunk gọi tiếp theo với CÙNG document (cùng record)."""
    response = client.messages.create(
        model=model,
        max_tokens=150,
        system=CONTEXTUAL_RETRIEVAL_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"<document>\n{document}\n</document>",
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "text", "text": CONTEXTUAL_RETRIEVAL_USER_TEMPLATE.format(chunk=chunk_text)},
            ],
        }],
    )
    context_text = "\n".join(b.text for b in response.content if b.type == "text").strip()
    return context_text, response.usage


def add_contextual_retrieval(chunks, records, client=None, model=HAIKU_MODEL, cost_log=None):
    """Gắn ngữ cảnh (Haiku) vào đầu mỗi chunk đủ điều kiện (_needs_contextual_retrieval); chunk bị
    bỏ qua giữ NGUYÊN VẸN (cùng object, không copy) trong kết quả trả về. cost_log, nếu truyền vào,
    được append 1 entry/lệnh gọi LLM thực tế — dùng để tổng hợp cost thật (không phải ước lượng) cho
    bench, xem CLI --contextual-retrieval bên dưới."""
    client = client or anthropic.Anthropic()
    record_by_id = {r.record_id: r for r in records}
    result = []
    for chunk in chunks:
        if not _needs_contextual_retrieval(chunk):
            result.append(chunk)
            continue
        record = record_by_id.get(_owning_record_id(chunk))
        if record is None:
            result.append(chunk)  # không tìm được document gốc -> giữ nguyên, không đoán mò
            continue

        context_text, usage = _generate_chunk_context(record.content, chunk.text, client, model)
        cost_usd = _contextual_cost_usd(usage, model)
        cache_created = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        logger.info(
            "contextual_retrieval: record=%s chunk=%s cache_creation=%d cache_read=%d input=%d "
            "output=%d cost_usd=%s",
            record.record_id, chunk.chunk_id, cache_created, cache_read,
            usage.input_tokens, usage.output_tokens,
            f"{cost_usd:.6f}" if cost_usd is not None else "n/a",
        )
        if cost_log is not None:
            cost_log.append({
                "record_id": record.record_id, "chunk_id": chunk.chunk_id, "model": model,
                "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                "cache_creation_input_tokens": cache_created, "cache_read_input_tokens": cache_read,
                "cost_usd": cost_usd or 0.0,
            })

        new_text = f"{context_text} {chunk.text}" if context_text else chunk.text
        result.append(chunk.model_copy(update={
            "text": new_text,
            "metadata": {**chunk.metadata, "contextual_retrieval": bool(context_text)},
        }))
    return result


# =====================================================================================
# CLI — chạy 4 chiến lược ra 4 thư mục riêng để so sánh, tuỳ chọn bật 2 bước hậu kỳ (metadata/context)
# =====================================================================================

def _load_records(input_dir):
    records = []
    for name in sorted(os.listdir(input_dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(input_dir, name), "r", encoding="utf-8") as f:
            data = json.load(f)
        records.append(KBRecord(**data))
    return records


def _write_chunks_jsonl(chunks, out_path):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk.model_dump(), ensure_ascii=False) + "\n")


DEFAULT_CONTEXTUAL_RETRIEVAL_JSONL = os.path.join("bench", "contextual_retrieval_runs.jsonl")
DEFAULT_CONTEXTUAL_RETRIEVAL_MD = os.path.join("bench", "contextual_retrieval.md")


def _aggregate_contextual_cost(cost_log):
    if not cost_log:
        return {
            "chunk_count": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            "cost_usd": 0.0, "cost_usd_per_chunk": 0.0,
        }
    total_cost = sum(e["cost_usd"] for e in cost_log)
    return {
        "chunk_count": len(cost_log),
        "input_tokens": sum(e["input_tokens"] for e in cost_log),
        "output_tokens": sum(e["output_tokens"] for e in cost_log),
        "cache_creation_input_tokens": sum(e["cache_creation_input_tokens"] for e in cost_log),
        "cache_read_input_tokens": sum(e["cache_read_input_tokens"] for e in cost_log),
        "cost_usd": round(total_cost, 6),
        "cost_usd_per_chunk": round(total_cost / len(cost_log), 6),
    }


def _write_contextual_retrieval_report(cost_by_strategy, cost_log, model, out_md, out_jsonl):
    """Ghi cost THẬT (từ usage.* của từng lệnh gọi API, không phải ước lượng) ra bench/ để sau này
    cân nhắc contextual retrieval có đáng chi phí hay không."""
    os.makedirs(os.path.dirname(out_jsonl) or ".", exist_ok=True)
    with open(out_jsonl, "w", encoding="utf-8") as f:
        for entry in cost_log:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    lines = [
        "# Contextual retrieval — cost thật trên corpus",
        "",
        f"model=`{model}` · tổng {len(cost_log)} lệnh gọi (mỗi lệnh = 1 chunk được thêm ngữ cảnh)",
        "",
        "| Strategy | Chunk | Input token | Output token | Cache write | Cache read | Cost (USD) | Cost/chunk (USD) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    total_cost = 0.0
    for strategy, agg in cost_by_strategy.items():
        total_cost += agg["cost_usd"]
        lines.append(
            f"| {strategy} | {agg['chunk_count']} | {agg['input_tokens']:,} | {agg['output_tokens']:,} "
            f"| {agg['cache_creation_input_tokens']:,} | {agg['cache_read_input_tokens']:,} "
            f"| ${agg['cost_usd']:.4f} | ${agg['cost_usd_per_chunk']:.6f} |"
        )
    lines.append("")
    lines.append(f"**Tổng cost thật (mọi strategy đã chạy): ${total_cost:.4f}**")
    lines.append("")
    lines.append(
        "Cost tính từ `response.usage.*` thật của từng lệnh gọi (input/output/cache_creation/"
        "cache_read), theo bảng giá `HAIKU_PRICING` — cache write ×1.25, cache read ×0.1 so với giá "
        "input thường (xem ingest/chunkers.py._contextual_cost_usd). Chi tiết từng lệnh gọi ở "
        f"`{out_jsonl}`."
    )

    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        description="Chunk toàn bộ KBRecord trong 1 thư mục theo 4 chiến lược để so sánh."
    )
    parser.add_argument("--input", default=os.path.join("data", "output"), help="Thư mục chứa KBRecord (*.json).")
    parser.add_argument("--out", default=os.path.join("data", "chunks"),
                         help="Thư mục gốc ghi kết quả, mỗi strategy ra 1 thư mục con.")
    parser.add_argument("--strategies", default=",".join(STRATEGIES), help="Danh sách strategy, phân cách bởi dấu phẩy.")
    parser.add_argument("--fixed-chunk-tokens", type=int, default=ChunkerConfig.fixed_chunk_tokens)
    parser.add_argument("--fixed-overlap-tokens", type=int, default=ChunkerConfig.fixed_overlap_tokens)
    parser.add_argument("--recursive-chunk-chars", type=int, default=ChunkerConfig.recursive_chunk_chars)
    parser.add_argument("--recursive-overlap-chars", type=int, default=ChunkerConfig.recursive_overlap_chars)
    parser.add_argument("--small-table-max-rows", type=int, default=ChunkerConfig.small_table_max_rows)
    parser.add_argument("--parent-chunk-tokens", type=int, default=ChunkerConfig.parent_chunk_tokens)
    parser.add_argument("--child-chunk-tokens", type=int, default=ChunkerConfig.child_chunk_tokens)
    parser.add_argument("--child-overlap-tokens", type=int, default=ChunkerConfig.child_overlap_tokens)
    parser.add_argument(
        "--normalize-metadata", action="store_true",
        help="Bật chuẩn hoá metadata (Stage C): rule trước, LLM (Haiku) chỉ cho field rule không ra.",
    )
    parser.add_argument(
        "--contextual-retrieval", action="store_true",
        help=(
            "Bật contextual retrieval (Stage A): sinh ngữ cảnh bằng Haiku cho mỗi chunk (trừ "
            "table_row/table_whole và chunk vai trò 'parent'). Tốn thêm chi phí API — cost thật "
            f"được ghi ra {DEFAULT_CONTEXTUAL_RETRIEVAL_MD} sau khi chạy."
        ),
    )
    parser.add_argument("--haiku-model", default=HAIKU_MODEL, help="Model dùng cho 2 bước hậu kỳ tốn LLM.")
    args = parser.parse_args(argv)

    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    for s in strategies:
        if s not in STRATEGIES:
            print(f"Lỗi: strategy không hợp lệ '{s}' (hỗ trợ: {', '.join(STRATEGIES)})", file=sys.stderr)
            return 2

    try:
        records = _load_records(args.input)
    except Exception as e:
        print(f"Lỗi khi đọc KBRecord từ {args.input}: {e}", file=sys.stderr)
        return 1

    if not records:
        print(f"Không tìm thấy KBRecord nào trong {args.input}", file=sys.stderr)
        return 1

    client = anthropic.Anthropic() if (args.normalize_metadata or args.contextual_retrieval) else None
    cost_log = []
    cost_by_strategy = {}

    for strategy in strategies:
        config = ChunkerConfig(
            strategy=strategy,
            fixed_chunk_tokens=args.fixed_chunk_tokens,
            fixed_overlap_tokens=args.fixed_overlap_tokens,
            recursive_chunk_chars=args.recursive_chunk_chars,
            recursive_overlap_chars=args.recursive_overlap_chars,
            small_table_max_rows=args.small_table_max_rows,
            parent_chunk_tokens=args.parent_chunk_tokens,
            child_chunk_tokens=args.child_chunk_tokens,
            child_overlap_tokens=args.child_overlap_tokens,
        )
        chunks = chunk_records(records, config)

        if args.normalize_metadata:
            chunks = normalize_chunk_metadata(records, chunks, client=client, model=args.haiku_model)

        if args.contextual_retrieval:
            strategy_cost_log = []
            chunks = add_contextual_retrieval(
                chunks, records, client=client, model=args.haiku_model, cost_log=strategy_cost_log,
            )
            cost_log.extend(strategy_cost_log)
            cost_by_strategy[strategy] = _aggregate_contextual_cost(strategy_cost_log)

        out_path = os.path.join(args.out, strategy, "chunks.jsonl")
        _write_chunks_jsonl(chunks, out_path)
        print(f"[{strategy}] {len(records)} record -> {len(chunks)} chunk -> {out_path}", file=sys.stderr)

    if args.contextual_retrieval:
        _write_contextual_retrieval_report(
            cost_by_strategy, cost_log, args.haiku_model,
            DEFAULT_CONTEXTUAL_RETRIEVAL_MD, DEFAULT_CONTEXTUAL_RETRIEVAL_JSONL,
        )
        total_cost = sum(a["cost_usd"] for a in cost_by_strategy.values())
        print(
            f"\ncontextual retrieval: {len(cost_log)} chunk, cost thật ${total_cost:.4f} — "
            f"báo cáo ở {DEFAULT_CONTEXTUAL_RETRIEVAL_MD}",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
