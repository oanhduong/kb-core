"""Vòng lặp agent dùng trực tiếp Anthropic Python SDK (không LangChain/framework nào khác).

Cách dùng: python -m agent.loop data/raw/<file>

Trước khi chấp nhận model dừng (không còn tool_use trong response), vòng lặp chạy
agent.verifier.verify() trên toàn bộ record đã ghi + bằng chứng tool_calls trong lượt này ("force
loop" — xem agent/verifier.py). Nếu verifier fail, issues được append vào messages như một lượt
user mới và vòng lặp tiếp tục, tối đa max_verify_retries lần. Việc này bị chặn (bounded) theo 3
cơ chế độc lập, xem TurnResult.status:
- max_verify_retries vượt quá -> status BLOCKED (model không tự sửa xong trong số lần cho phép).
- stagnation: issues lượt N giống hệt lượt N-1 -> dừng sớm, cũng BLOCKED (model bị kẹt).
- max_turns (hard cap toàn cục cho 1 file, không phân biệt lý do) -> status MAX_TURNS.
BLOCKED/MAX_TURNS khác ERROR (ParserError — luôn là bug ở ingest/parsers.py, không retry) ở chỗ
BLOCKED/MAX_TURNS là giới hạn của MODEL trong lượt chạy này, không phải bug code.

Prompt caching: request gửi lên được xếp theo thứ tự tĩnh -> động để tối đa cache hit (xem
_build_system_blocks): tool definitions (tham số `tools`, do SDK luôn render trước `system`) ->
SYSTEM_PROMPT (quy tắc route tool, gần như không đổi) -> EXTRACTION_GUIDELINE (hướng dẫn trích
xuất/ghi record) -> FEW_SHOT_EXAMPLES (ví dụ minh hoạ, cũng tĩnh) — cache_control ephemeral đặt ở
khối cuối cùng của phần tĩnh này (FEW_SHOT_EXAMPLES) nên toàn bộ tools+system phía trước cũng được
cache theo. Ngoài ra mỗi lượt còn đánh dấu thêm 1 breakpoint "di động" trên message cuối cùng của
`messages` (_set_cache_boundary) để cache incremental cả phần lịch sử hội thoại đang tăng dần —
marker cũ bị gỡ trước khi gắn marker mới để không vượt giới hạn 4 breakpoint/request của API.
usage.cache_creation_input_tokens/usage.cache_read_input_tokens được log mỗi lượt (xem log
"cache:") để đối chiếu hit rate. Chế độ debug --break-cache (xem main()) chèn timestamp vào đầu
SYSTEM_PROMPT mỗi lượt để CỐ Ý phá cache — dùng làm bằng chứng đối chiếu số liệu.

Compaction: khi lịch sử hội thoại (messages) vượt ngưỡng số lượt hoặc token ước lượng, các lượt cũ
(trừ COMPACT_KEEP_RECENT_TURNS lượt gần nhất) được nén thành một message tóm tắt duy nhất (xem
_compact_messages). Giữ NGUYÊN VĂN: text agent tự viết (quyết định/giải thích), tin nhắn issue của
verifier, và tool_result lỗi (is_error=True). Bỏ: nội dung tool_result THÀNH CÔNG (chunk gốc từ
parse_document/transcribe_media, ...) — an toàn để bỏ vì verifier không đọc `messages`, nó đọc
tool_calls (list riêng, tích luỹ độc lập trong run(), không bị compaction đụng tới) và KBRecord đã
ghi vẫn còn nguyên trên đĩa qua write_kb_record. Mỗi lần compact được log rõ (turn, số lượt bị gộp,
token trước/sau) kèm cảnh báo: compaction viết lại `messages` nên phá breakpoint cache "di động" ở
trên (marker cũ bị gỡ, lượt kế tiếp sẽ không hit cache cho phần messages — chỉ phần system/tools
tĩnh, không phụ thuộc messages, vẫn tiếp tục hit bình thường).
"""
import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field

import anthropic

from agent import verifier as agent_verifier
from ingest.invariants import ParserError
from security import gateway as security_gateway

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096
MAX_VERIFY_RETRIES = 3
REDUNDANT_CALL_THRESHOLD = 3  # cùng tool + cùng argument lần thứ N này trở đi thì cảnh báo model

# Compaction: NGƯỠNG NÀO ĐẠT TRƯỚC thì trigger (số lượt hoặc token ước lượng của messages),
# và số lượt gần nhất luôn giữ nguyên văn (không đưa vào phần bị nén) để model còn đủ ngữ cảnh gần
# để tiếp tục mạch việc đang làm dở.
COMPACT_TURN_THRESHOLD = 8
COMPACT_TOKEN_THRESHOLD = 40_000
COMPACT_KEEP_RECENT_TURNS = 3

SYSTEM_PROMPT = """Bạn là agent ingest tài liệu cho RetailKB Copilot — chuyển file thô (docx/xlsx/pdf/image/audio/video)
thành các KBRecord ghi vào knowledge base.

Quy tắc route tool:
- Luôn gọi detect_file_type trước để biết loại file thật (không suy đoán qua đuôi file).
- Với xlsx nhiều sheet hoặc pdf nhiều trang, gọi parse_document theo từng phạm vi nhỏ (1 sheet /
  vài trang một lần) thay vì lấy toàn bộ cùng lúc, để tránh tràn ngữ cảnh.
- Với ảnh, luôn dùng extract_image_content — không dùng parse_document.
- Nếu parse_document báo một trang pdf nằm trong stats.needs_ocr_pages, dùng
  extract_image_content(path_or_page="<file>.pdf#trang=<N>") cho đúng trang đó.
- Với audio/video, dùng transcribe_media; nếu file dài, chia theo start_sec/end_sec từng đoạn."""

EXTRACTION_GUIDELINE = """Hướng dẫn trích xuất và ghi KBRecord:
- Với mỗi đoạn nội dung có ý nghĩa nghiệp vụ (chính sách, giá, khuyến mãi, hợp đồng, ...), gọi
  write_kb_record để ghi một KBRecord — content là nội dung gốc của đoạn (không tự diễn giải/rút gọn
  làm mất thông tin), location lấy đúng từ chunk tương ứng, confidence phản ánh mức độ chắc chắn
  (thấp hơn với nội dung từ OCR/ASR so với text layer/bảng tính đọc trực tiếp).
- Nếu write_kb_record báo lỗi định dạng field, sửa lại đúng field đó rồi gọi lại — không lặp lại
  y hệt input cũ.
- Trước khi dừng, hệ thống sẽ tự kiểm tra (verifier) xem đã đọc đủ phạm vi file, route đúng tool,
  phân loại/metadata hợp lý chưa. Nếu verifier báo vấn đề, xử lý đúng theo hướng dẫn rồi mới dừng —
  không lặp lại y hệt các lần gọi tool trước đó.
- Khi đã ghi xong toàn bộ nội dung có ý nghĩa của file, trả lời tóm tắt ngắn gọn số record đã ghi
  rồi dừng (không gọi thêm tool)."""

FEW_SHOT_EXAMPLES = """Ví dụ minh hoạ cách trích xuất (few-shot, dữ liệu giả định, không phải file thật):

Input (chunk từ parse_document, location="Trang 3"):
  "Giá niêm yết SKU MB-2201 (Máy xay sinh tố Mini) áp dụng từ 01/03/2026: 590.000đ tại các cửa
  hàng khu vực HN. Khuyến mãi giảm 10% cho khách hàng thành viên."

Output đúng — tách 2 record theo loại quyết định nghiệp vụ (giá và khuyến mãi có thể hết hạn khác
nhau, gộp chung sẽ khó truy vấn/verify riêng từng loại sau này):
  write_kb_record({
    "record_id": "price-mb2201-hn-20260301",
    "source_file": "<đường dẫn file gốc>", "source_type": "pdf", "doc_type": "price_list",
    "content": "Giá niêm yết SKU MB-2201 (Máy xay sinh tố Mini) áp dụng từ 01/03/2026: 590.000đ tại các cửa hàng khu vực HN.",
    "location": "Trang 3",
    "metadata": {"sku": "MB-2201", "store_code": "HN", "effective_date": "2026-03-01"},
    "confidence": 0.95
  })
  write_kb_record({
    "record_id": "promo-mb2201-member-20260301",
    "source_file": "<đường dẫn file gốc>", "source_type": "pdf", "doc_type": "promotion",
    "content": "Khuyến mãi giảm 10% cho khách hàng thành viên, áp dụng cùng đợt giá SKU MB-2201 từ 01/03/2026.",
    "location": "Trang 3",
    "metadata": {"sku": "MB-2201", "effective_date": "2026-03-01"},
    "confidence": 0.9
  })"""


@dataclass
class ToolCallRecord:
    turn: int
    tool_name: str
    arguments: dict
    is_error: bool
    input_tokens: int
    output_tokens: int
    latency_seconds: float
    output: object = None  # kết quả gốc (dict) của tool khi thành công — bằng chứng cho verifier


@dataclass
class TurnResult:
    final_text: str
    turn_count: int
    total_input_tokens: int
    total_output_tokens: int
    tool_calls: list = field(default_factory=list)
    stop_reason: str = "end_turn"
    status: str = "OK"  # OK | BLOCKED | ERROR | MAX_TURNS
    blocked_reason: str = ""
    records: list = field(default_factory=list)
    verify_retry_count: int = 0
    issues: list = field(default_factory=list)  # [{"check","message"}] — chỉ khi status=BLOCKED
    compaction_events: list = field(default_factory=list)  # [{"turn","merged_turns","tokens_before","tokens_after"}]


# =====================================================================================
# COMPACTION — hàm thuần, không phụ thuộc AgentLoop, test trực tiếp được (xem tests/test_loop.py)
# =====================================================================================

def _approx_tokens(messages):
    """Ước lượng thô ~4 ký tự/token (không gọi API đếm token thật) — cùng công thức với
    agent/orchestrator.py._approx_tokens, chỉ dùng để SO SÁNH tương đối trước/sau compact."""
    return len(json.dumps(messages, ensure_ascii=False, default=str)) // 4


def _split_turn_groups(messages):
    """messages[0] luôn là user message gốc (instruction ban đầu của run(), không đưa vào compact).
    Phần còn lại được nhóm thành từng cặp (assistant, user) đúng theo thứ tự AgentLoop.run tạo ra —
    tại thời điểm gọi hàm này (đầu mỗi vòng lặp, trước khi gọi API) messages luôn có độ dài lẻ nên
    mọi group đều là cặp đầy đủ, không có group dở dang."""
    return [messages[i:i + 2] for i in range(1, len(messages), 2)]


def _block_type(block):
    if isinstance(block, dict):
        return block.get("type")
    return getattr(block, "type", None)


def _block_text(block):
    if isinstance(block, dict):
        return block.get("text", "")
    return getattr(block, "text", "") or ""


def _extract_verbatim(group):
    """Trích phần PHẢI giữ nguyên văn của một lượt cũ trước khi nén: text agent tự viết (quyết
    định/giải thích), tin nhắn verifier/hệ thống (issue thô dạng string), và tool_result LỖI. Tool
    trace (tên tool + tham số, không kèm kết quả) được giữ riêng để agent còn biết đã thử gì. Chủ
    động BỎ tool_result thành công — an toàn vì verifier đọc tool_calls (tích luỹ riêng trong
    run(), không đụng messages) chứ không đọc messages, và KBRecord đã ghi vẫn còn trên đĩa."""
    verbatim, tool_trace = [], []
    for msg in group:
        role, content = msg["role"], msg["content"]
        if role == "assistant":
            for block in content:
                btype = _block_type(block)
                if btype == "text":
                    text = _block_text(block).strip()
                    if text:
                        verbatim.append(f"[quyết định/giải thích của agent] {text}")
                elif btype == "tool_use":
                    name = block.get("name") if isinstance(block, dict) else getattr(block, "name", "?")
                    args = block.get("input") if isinstance(block, dict) else getattr(block, "input", {})
                    tool_trace.append(f"- đã gọi {name}({json.dumps(args, ensure_ascii=False)})")
        elif role == "user":
            if isinstance(content, str):
                stripped = content.strip()
                if stripped:
                    verbatim.append(f"[verifier/hệ thống] {stripped}")
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                        verbatim.append(f"[lỗi tool] {block.get('content')}")
    return verbatim, tool_trace


def _compact_messages(messages, keep_recent=COMPACT_KEEP_RECENT_TURNS):
    """Nén các lượt cũ (trừ `keep_recent` lượt gần nhất) thành một message tóm tắt duy nhất, chèn
    ngay sau messages[0] (instruction gốc). Trả về (messages_mới, số_lượt_đã_gộp) — số_lượt_đã_gộp
    == 0 nghĩa là không đủ lượt cũ để nén có ích, messages trả về nguyên vẹn (không copy)."""
    groups = _split_turn_groups(messages)
    if len(groups) <= keep_recent:
        return messages, 0

    old_groups, recent_groups = groups[:-keep_recent], groups[-keep_recent:]

    verbatim_lines, tool_trace_lines = [], []
    for group in old_groups:
        v, t = _extract_verbatim(group)
        verbatim_lines.extend(v)
        tool_trace_lines.extend(t)

    summary_parts = [
        f"### Tóm tắt {len(old_groups)} lượt hội thoại cũ đã được nén (compaction) ###",
        "Tool output thành công và các nhánh thăm dò đã lược bỏ để tiết kiệm ngữ cảnh — KBRecord đã "
        "ghi vẫn còn nguyên trên đĩa, không phụ thuộc lịch sử hội thoại này.",
    ]
    if verbatim_lines:
        summary_parts.append("Giữ nguyên văn (quyết định, issue verifier, lỗi tool):")
        summary_parts.extend(verbatim_lines)
    if tool_trace_lines:
        summary_parts.append("Trace tool đã gọi (chỉ tên + tham số, không kèm kết quả):")
        summary_parts.extend(tool_trace_lines)

    compact_message = {"role": "user", "content": "\n".join(summary_parts)}
    new_messages = [messages[0], compact_message]
    for group in recent_groups:
        new_messages.extend(group)
    return new_messages, len(old_groups)


class AgentLoop:
    """Vòng lặp agentic thủ công quanh client.messages.create — không dùng tool runner beta."""

    def __init__(
        self, system_prompt, tools, max_turns=15, max_verify_retries=MAX_VERIFY_RETRIES,
        model=MODEL, max_tokens=MAX_TOKENS, client=None, enable_verifier=True,
        extraction_guideline=EXTRACTION_GUIDELINE, few_shot_examples=FEW_SHOT_EXAMPLES, break_cache=False,
        compact_turn_threshold=COMPACT_TURN_THRESHOLD, compact_token_threshold=COMPACT_TOKEN_THRESHOLD,
        compact_keep_recent_turns=COMPACT_KEEP_RECENT_TURNS,
    ):
        self.system_prompt = system_prompt
        self.extraction_guideline = extraction_guideline
        self.few_shot_examples = few_shot_examples
        self.break_cache = break_cache  # True = chèn timestamp vào system prompt mỗi lượt, CỐ Ý phá cache (debug)
        self.tools = tools  # dict: tên tool -> {"function": callable, "schema": {"description","input_schema"}}
        self.max_turns = max_turns
        self.max_verify_retries = max_verify_retries
        self.model = model
        self.max_tokens = max_tokens
        self.client = client or anthropic.Anthropic()
        self.enable_verifier = enable_verifier  # False = bỏ force-loop verifier (dùng cho bench ablation)
        self.compact_turn_threshold = compact_turn_threshold
        self.compact_token_threshold = compact_token_threshold
        self.compact_keep_recent_turns = compact_keep_recent_turns
        self._api_tools = [{"name": name, **spec["schema"]} for name, spec in self.tools.items()]

    def _build_system_blocks(self):
        """Layout tĩnh -> động: SYSTEM_PROMPT (quy tắc route) -> EXTRACTION_GUIDELINE -> FEW_SHOT_EXAMPLES,
        cache_control ephemeral ở khối cuối cùng (few-shot) — cache luôn cả tools+2 khối trước đó
        (SDK render tools trước system). break_cache chèn timestamp vào SYSTEM_PROMPT mỗi lần gọi
        (không phải chỉ 1 lần lúc khởi tạo) để phá prefix ngay từ khối đầu tiên, dùng đối chiếu số
        liệu cache_read_input_tokens tụt về 0."""
        system_prompt_text = self.system_prompt
        if self.break_cache:
            system_prompt_text = f"[debug --break-cache ts={time.time_ns()}]\n{system_prompt_text}"
        return [
            {"type": "text", "text": system_prompt_text},
            {"type": "text", "text": self.extraction_guideline},
            {"type": "text", "text": self.few_shot_examples, "cache_control": {"type": "ephemeral"}},
        ]

    @staticmethod
    def _ensure_block_list(content):
        if isinstance(content, str):
            return [{"type": "text", "text": content}]
        return content

    def _set_cache_boundary(self, messages):
        """Đánh dấu message user CUỐI CÙNG làm breakpoint cache incremental cho phần `messages` —
        tách biệt với breakpoint tĩnh ở system/tools. Gỡ marker cũ trước khi gắn marker mới (nếu
        không, số breakpoint sẽ tăng dần theo số lượt và có thể vượt giới hạn 4/request của API)."""
        if self._cache_marked_block is not None:
            self._cache_marked_block.pop("cache_control", None)
        last = messages[-1]
        last["content"] = self._ensure_block_list(last["content"])
        last["content"][-1]["cache_control"] = {"type": "ephemeral"}
        self._cache_marked_block = last["content"][-1]

    def _maybe_compact(self, messages, turn):
        """Kiểm tra ngưỡng (số lượt hoặc token ước lượng) và nén nếu vượt. Trả về (messages có thể
        đã được thay thế, event dict hoặc None nếu không compact lượt này)."""
        groups = _split_turn_groups(messages)
        tokens_before = _approx_tokens(messages)
        if len(groups) <= self.compact_turn_threshold and tokens_before <= self.compact_token_threshold:
            return messages, None

        new_messages, merged_turns = _compact_messages(messages, keep_recent=self.compact_keep_recent_turns)
        if merged_turns == 0:
            return messages, None

        tokens_after = _approx_tokens(new_messages)
        reduction_pct = 0.0 if tokens_before == 0 else round((1 - tokens_after / tokens_before) * 100, 1)
        logger.warning(
            "turn=%d compaction: gộp %d lượt cũ thành 1 block tóm tắt, giữ nguyên %d lượt gần nhất "
            "— token ước lượng %d -> %d (giảm %.1f%%). LƯU Ý: compaction viết lại messages nên phá "
            "breakpoint cache incremental của phần hội thoại (lượt kế tiếp cache_read cho phần "
            "messages sẽ về 0/thấp — đây là lượt đắt); phần system/tools tĩnh không đụng tới nên vẫn "
            "hit cache bình thường.",
            turn, merged_turns, self.compact_keep_recent_turns, tokens_before, tokens_after, reduction_pct,
        )
        self._cache_marked_block = None  # marker cũ trỏ vào message đã bị compact bỏ đi, không còn hợp lệ
        event = {
            "turn": turn, "merged_turns": merged_turns,
            "tokens_before": tokens_before, "tokens_after": tokens_after,
        }
        return new_messages, event

    def run(self, user_message, file_path):
        messages = [{"role": "user", "content": user_message}]
        tool_calls = []
        total_in = 0
        total_out = 0
        final_text = ""
        turn = 0
        verify_retries = 0
        previous_issue_key = None
        call_signature_counts = {}
        compaction_events = []
        self._cache_marked_block = None

        while True:
            turn += 1
            if turn > self.max_turns:
                logger.info("turn=%d dừng: đã đạt max_turns=%d", turn, self.max_turns)
                return TurnResult(
                    final_text=final_text,
                    turn_count=turn - 1,
                    total_input_tokens=total_in,
                    total_output_tokens=total_out,
                    tool_calls=tool_calls,
                    stop_reason="max_turns",
                    status="MAX_TURNS",
                    blocked_reason=f"Đạt hard cap max_turns={self.max_turns} cho file {file_path}.",
                    records=self._gather_records(tool_calls),
                    verify_retry_count=verify_retries,
                    compaction_events=compaction_events,
                )

            messages, compaction_event = self._maybe_compact(messages, turn)
            if compaction_event is not None:
                compaction_events.append(compaction_event)
            self._set_cache_boundary(messages)

            start = time.monotonic()
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self._build_system_blocks(),
                tools=self._api_tools,
                messages=messages,
            )
            latency = time.monotonic() - start
            total_in += response.usage.input_tokens
            total_out += response.usage.output_tokens
            cache_created = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
            cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
            logger.info(
                "turn=%d cache: creation=%d read=%d (uncached input=%d output=%d) latency=%.2fs",
                turn, cache_created, cache_read, response.usage.input_tokens, response.usage.output_tokens, latency,
            )

            text = "\n".join(b.text for b in response.content if b.type == "text")
            if text:
                final_text = text

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

            if not tool_use_blocks:
                logger.info(
                    "turn=%d tool=- input_tokens=%d output_tokens=%d latency=%.2fs stop_reason=%s",
                    turn, response.usage.input_tokens, response.usage.output_tokens, latency, response.stop_reason,
                )

                records = self._gather_records(tool_calls)

                if not self.enable_verifier:
                    return TurnResult(
                        final_text=final_text,
                        turn_count=turn,
                        total_input_tokens=total_in,
                        total_output_tokens=total_out,
                        tool_calls=tool_calls,
                        stop_reason=response.stop_reason,
                        status="OK",
                        records=records,
                        verify_retry_count=verify_retries,
                        compaction_events=compaction_events,
                    )

                verify_result = agent_verifier.verify(file_path, records, tool_calls)

                if verify_result.passed:
                    return TurnResult(
                        final_text=final_text,
                        turn_count=turn,
                        total_input_tokens=total_in,
                        total_output_tokens=total_out,
                        tool_calls=tool_calls,
                        stop_reason=response.stop_reason,
                        status="OK",
                        records=records,
                        verify_retry_count=verify_retries,
                        compaction_events=compaction_events,
                    )

                verify_retries += 1
                issue_key = tuple(sorted((i.check, i.message) for i in verify_result.issues))
                stagnant = issue_key == previous_issue_key
                previous_issue_key = issue_key

                if stagnant:
                    logger.warning(
                        "turn=%d verifier: issues y hệt lượt trước, dừng sớm (stagnation)", turn,
                    )
                    return TurnResult(
                        final_text=final_text,
                        turn_count=turn,
                        total_input_tokens=total_in,
                        total_output_tokens=total_out,
                        tool_calls=tool_calls,
                        stop_reason="blocked",
                        status="BLOCKED",
                        blocked_reason=(
                            f"Verifier phát hiện đúng {len(verify_result.issues)} vấn đề y hệt lượt "
                            f"kiểm tra trước (turn {turn}) — model không tự sửa được, dừng sớm "
                            "(stagnation) thay vì lặp vô ích."
                        ),
                        records=records,
                        verify_retry_count=verify_retries,
                        issues=[{"check": i.check, "message": i.message} for i in verify_result.issues],
                        compaction_events=compaction_events,
                    )

                if verify_retries > self.max_verify_retries:
                    logger.warning(
                        "turn=%d verifier: vượt max_verify_retries=%d, dừng BLOCKED", turn, self.max_verify_retries,
                    )
                    return TurnResult(
                        final_text=final_text,
                        turn_count=turn,
                        total_input_tokens=total_in,
                        total_output_tokens=total_out,
                        tool_calls=tool_calls,
                        stop_reason="blocked",
                        status="BLOCKED",
                        blocked_reason=(
                            f"Đã thử sửa theo verifier {self.max_verify_retries} lần nhưng vẫn còn "
                            f"{len(verify_result.issues)} vấn đề chưa xử lý xong."
                        ),
                        records=records,
                        verify_retry_count=verify_retries,
                        issues=[{"check": i.check, "message": i.message} for i in verify_result.issues],
                        compaction_events=compaction_events,
                    )

                logger.info(
                    "turn=%d verifier fail (%d vấn đề), retry %d/%d",
                    turn, len(verify_result.issues), verify_retries, self.max_verify_retries,
                )
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": agent_verifier.format_issues(verify_result.issues)})
                continue

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for block in tool_use_blocks:
                logger.info(
                    "turn=%d tool=%s args=%s input_tokens=%d output_tokens=%d latency=%.2fs",
                    turn, block.name, block.input, response.usage.input_tokens, response.usage.output_tokens, latency,
                )
                signature = (block.name, json.dumps(block.input, sort_keys=True, ensure_ascii=False))
                call_signature_counts[signature] = call_signature_counts.get(signature, 0) + 1
                redundant = call_signature_counts[signature] >= REDUNDANT_CALL_THRESHOLD

                try:
                    output = self._call_tool(block.name, block.input)
                except ParserError as e:
                    tool_calls.append(ToolCallRecord(
                        turn=turn, tool_name=block.name, arguments=block.input, is_error=True, output=None,
                        input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens,
                        latency_seconds=latency,
                    ))
                    logger.error("turn=%d tool=%s LỖI INVARIANT, dừng hẳn (không retry): %s", turn, block.name, e)
                    return TurnResult(
                        final_text=f"Dừng xử lý do lỗi invariant tầng code (bug ở parser, không retry): {e}",
                        turn_count=turn,
                        total_input_tokens=total_in,
                        total_output_tokens=total_out,
                        tool_calls=tool_calls,
                        stop_reason="error",
                        status="ERROR",
                        records=self._gather_records(tool_calls),
                        verify_retry_count=verify_retries,
                        compaction_events=compaction_events,
                    )
                except Exception as e:
                    is_error = True
                    output = None
                    content = self._actionable_error_message(block.name, e)
                else:
                    is_error = False
                    content = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)

                if redundant:
                    content = (
                        f"{content}\n\n[CẢNH BÁO: đây là lần thứ {call_signature_counts[signature]} bạn gọi "
                        f"tool '{block.name}' với đúng tham số {block.input!r} — có vẻ đang lặp lại vô ích, "
                        "hãy thử phương án khác thay vì gọi lại y hệt.]"
                    )

                tool_calls.append(ToolCallRecord(
                    turn=turn, tool_name=block.name, arguments=block.input, is_error=is_error, output=output,
                    input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens,
                    latency_seconds=latency,
                ))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_error,
                })

            messages.append({"role": "user", "content": tool_results})

    def _call_tool(self, name, arguments):
        spec = self.tools.get(name)
        if spec is None:
            raise KeyError(f"Tool '{name}' không có trong registry — model gọi nhầm tên tool.")
        try:
            result = spec["function"](**arguments)
        except TypeError as e:
            raise TypeError(f"Tham số gọi tool '{name}' không khớp input_schema: {e}") from e
        return result

    @staticmethod
    def _gather_records(tool_calls):
        """Gộp record theo record_id, giữ lần ghi THÀNH CÔNG gần nhất — write_kb_record ghi đè
        data/output/<record_id>.json mỗi lần gọi, nên khi model sửa lỗi bằng cách gọi lại cùng
        record_id, bản cũ không còn tồn tại trên đĩa và không nên tính vào bằng chứng cho verifier."""
        records_by_id = {}
        for tc in tool_calls:
            if tc.tool_name != "write_kb_record" or tc.is_error:
                continue
            if not isinstance(tc.arguments, dict) or "record" not in tc.arguments:
                continue
            record = tc.arguments["record"]
            key = record.get("record_id") if isinstance(record, dict) else None
            records_by_id[key if key is not None else id(record)] = record
        return list(records_by_id.values())

    @staticmethod
    def _actionable_error_message(tool_name, exc):
        message = str(exc).strip()
        if message:
            return message
        return f"Tool '{tool_name}' lỗi {type(exc).__name__} nhưng không có message chi tiết."


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description="Chạy 1 AgentLoop trên 1 file.")
    parser.add_argument("file_path", help="Đường dẫn file thô cần ingest, ví dụ data/raw/<file>.")
    parser.add_argument(
        "--break-cache", action="store_true",
        help=(
            "Debug: chèn timestamp vào SYSTEM_PROMPT mỗi lượt để CỐ Ý phá prompt cache. Dùng để "
            "chạy đối chiếu với lần chạy không có cờ này — so sánh cache_read_input_tokens trong log "
            "'cache:' giữa hai lần chạy làm bằng chứng caching có tác dụng."
        ),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    file_path = args.file_path
    if not os.path.isfile(file_path):
        print(f"Không tìm thấy file: {file_path}", file=sys.stderr)
        return 1

    if args.break_cache:
        logger.warning(
            "--break-cache bật: mỗi lượt sẽ chèn timestamp mới vào system prompt để CỐ Ý phá cache "
            "prefix — dự kiến cache_read_input_tokens về 0 mọi lượt, khác với chạy không có cờ này."
        )

    client = anthropic.Anthropic()
    _gateway, gated_tools = security_gateway.build_ingest_tools(client)
    loop = AgentLoop(system_prompt=SYSTEM_PROMPT, tools=gated_tools, client=client, break_cache=args.break_cache)

    user_message = (
        f"Hãy ingest file '{file_path}' vào knowledge base: xác định loại file, đọc toàn bộ nội dung có "
        "ý nghĩa nghiệp vụ theo từng phạm vi nhỏ, và ghi từng phần thành KBRecord qua write_kb_record."
    )
    result = loop.run(user_message, file_path)

    print(f"\n=== Kết quả ingest {file_path} ===")
    print(f"status: {result.status}")
    print(f"stop_reason: {result.stop_reason}")
    print(f"turns: {result.turn_count} (verify_retries: {result.verify_retry_count})")
    print(f"tokens: input={result.total_input_tokens} output={result.total_output_tokens}")
    print(f"tool_calls: {len(result.tool_calls)} (lỗi: {sum(1 for t in result.tool_calls if t.is_error)})")
    print(f"records: {len(result.records)}")
    if result.compaction_events:
        print(f"compaction: {len(result.compaction_events)} lần — {result.compaction_events}")
    if result.blocked_reason:
        print(f"blocked_reason: {result.blocked_reason}")
    print(f"\n{result.final_text}")

    return 1 if result.status in ("ERROR", "BLOCKED") else 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
