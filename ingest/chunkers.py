"""Chunker cho pipeline retrieval: tách nội dung của KBRecord (đã ghi ra data/output/<record_id>.json
qua agent/tools.write_kb_record) thành các Chunk nhỏ hơn để embed/index.

Input: một hoặc nhiều schemas.KBRecord — content là văn bản gốc của MỘT phạm vi nghiệp vụ (một đoạn
văn, một bảng, một segment audio, ...) mà agent đã trích xuất.
Output: list[Chunk] — Chunk(chunk_id, parent_id, text, location, metadata). parent_id luôn là
record_id của KBRecord gốc để trace ngược khi cần.

Ba chiến lược, chọn qua ChunkerConfig.strategy (đăng ký trong STRATEGIES — cắm/rút bằng cách
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

Cách dùng CLI (so sánh 3 chiến lược trên cùng 1 tập KBRecord):
    python -m ingest.chunkers --input data/output --out data/chunks
    python -m ingest.chunkers --input data/output --out data/chunks --strategies fixed,recursive
"""
import argparse
import json
import os
import re
import sys
from dataclasses import dataclass

from pydantic import BaseModel, field_validator

from schemas import KBRecord

CHARS_PER_TOKEN = 4  # ước lượng thô, cùng quy ước với agent/orchestrator.py._approx_tokens


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


STRATEGIES = {
    "fixed": _chunk_fixed,
    "recursive": _chunk_recursive,
    "structure_aware": _chunk_structure_aware,
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


# =====================================================================================
# CLI — chạy cả 3 chiến lược ra 3 thư mục riêng để so sánh
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


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        description="Chunk toàn bộ KBRecord trong 1 thư mục theo 3 chiến lược để so sánh."
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

    for strategy in strategies:
        config = ChunkerConfig(
            strategy=strategy,
            fixed_chunk_tokens=args.fixed_chunk_tokens,
            fixed_overlap_tokens=args.fixed_overlap_tokens,
            recursive_chunk_chars=args.recursive_chunk_chars,
            recursive_overlap_chars=args.recursive_overlap_chars,
            small_table_max_rows=args.small_table_max_rows,
        )
        chunks = chunk_records(records, config)
        out_path = os.path.join(args.out, strategy, "chunks.jsonl")
        _write_chunks_jsonl(chunks, out_path)
        print(f"[{strategy}] {len(records)} record -> {len(chunks)} chunk -> {out_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
