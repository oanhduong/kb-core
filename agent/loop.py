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
"""
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field

import anthropic

from agent import tools as agent_tools
from agent import verifier as agent_verifier
from ingest.invariants import ParserError

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096
MAX_VERIFY_RETRIES = 3
REDUNDANT_CALL_THRESHOLD = 3  # cùng tool + cùng argument lần thứ N này trở đi thì cảnh báo model

SYSTEM_PROMPT = """Bạn là agent ingest tài liệu cho RetailKB Copilot — chuyển file thô (docx/xlsx/pdf/image/audio/video)
thành các KBRecord ghi vào knowledge base.

Quy tắc:
- Luôn gọi detect_file_type trước để biết loại file thật (không suy đoán qua đuôi file).
- Với xlsx nhiều sheet hoặc pdf nhiều trang, gọi parse_document theo từng phạm vi nhỏ (1 sheet /
  vài trang một lần) thay vì lấy toàn bộ cùng lúc, để tránh tràn ngữ cảnh.
- Với ảnh, luôn dùng extract_image_content — không dùng parse_document.
- Nếu parse_document báo một trang pdf nằm trong stats.needs_ocr_pages, dùng
  extract_image_content(path_or_page="<file>.pdf#trang=<N>") cho đúng trang đó.
- Với audio/video, dùng transcribe_media; nếu file dài, chia theo start_sec/end_sec từng đoạn.
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


class AgentLoop:
    """Vòng lặp agentic thủ công quanh client.messages.create — không dùng tool runner beta."""

    def __init__(
        self, system_prompt, tools, max_turns=15, max_verify_retries=MAX_VERIFY_RETRIES,
        model=MODEL, max_tokens=MAX_TOKENS, client=None, enable_verifier=True,
    ):
        self.system_prompt = system_prompt
        self.tools = tools  # dict: tên tool -> {"function": callable, "schema": {"description","input_schema"}}
        self.max_turns = max_turns
        self.max_verify_retries = max_verify_retries
        self.model = model
        self.max_tokens = max_tokens
        self.client = client or anthropic.Anthropic()
        self.enable_verifier = enable_verifier  # False = bỏ force-loop verifier (dùng cho bench ablation)
        self._api_tools = [{"name": name, **spec["schema"]} for name, spec in self.tools.items()]

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
                )

            start = time.monotonic()
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self.system_prompt,
                tools=self._api_tools,
                messages=messages,
            )
            latency = time.monotonic() - start
            total_in += response.usage.input_tokens
            total_out += response.usage.output_tokens

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
    if len(argv) != 1:
        print("Cách dùng: python -m agent.loop data/raw/<file>", file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    file_path = argv[0]
    if not os.path.isfile(file_path):
        print(f"Không tìm thấy file: {file_path}", file=sys.stderr)
        return 1

    client = anthropic.Anthropic()
    registry = agent_tools.build_registry(client)
    loop = AgentLoop(system_prompt=SYSTEM_PROMPT, tools=registry, client=client)

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
    if result.blocked_reason:
        print(f"blocked_reason: {result.blocked_reason}")
    print(f"\n{result.final_text}")

    return 1 if result.status in ("ERROR", "BLOCKED") else 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
