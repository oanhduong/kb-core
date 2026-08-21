"""Vòng lặp agent dùng trực tiếp Anthropic Python SDK (không LangChain/framework nào khác).

Cách dùng: python -m agent.loop data/raw/<file>
"""
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field

import anthropic

from agent import tools as agent_tools
from ingest.invariants import ParserError

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096

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


@dataclass
class TurnResult:
    final_text: str
    turn_count: int
    total_input_tokens: int
    total_output_tokens: int
    tool_calls: list = field(default_factory=list)
    stop_reason: str = "end_turn"


class AgentLoop:
    """Vòng lặp agentic thủ công quanh client.messages.create — không dùng tool runner beta."""

    def __init__(self, system_prompt, tools, max_turns=15, model=MODEL, max_tokens=MAX_TOKENS, client=None):
        self.system_prompt = system_prompt
        self.tools = tools  # dict: tên tool -> {"function": callable, "schema": {"description","input_schema"}}
        self.max_turns = max_turns
        self.model = model
        self.max_tokens = max_tokens
        self.client = client or anthropic.Anthropic()
        self._api_tools = [{"name": name, **spec["schema"]} for name, spec in self.tools.items()]

    def run(self, user_message):
        messages = [{"role": "user", "content": user_message}]
        tool_calls = []
        total_in = 0
        total_out = 0
        final_text = ""
        turn = 0

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
                return TurnResult(
                    final_text=final_text,
                    turn_count=turn,
                    total_input_tokens=total_in,
                    total_output_tokens=total_out,
                    tool_calls=tool_calls,
                    stop_reason=response.stop_reason,
                )

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for block in tool_use_blocks:
                logger.info(
                    "turn=%d tool=%s args=%s input_tokens=%d output_tokens=%d latency=%.2fs",
                    turn, block.name, block.input, response.usage.input_tokens, response.usage.output_tokens, latency,
                )
                try:
                    output = self._call_tool(block.name, block.input)
                except ParserError as e:
                    tool_calls.append(ToolCallRecord(
                        turn=turn, tool_name=block.name, arguments=block.input, is_error=True,
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
                    )
                except Exception as e:
                    is_error = True
                    content = self._actionable_error_message(block.name, e)
                else:
                    is_error = False
                    content = output

                tool_calls.append(ToolCallRecord(
                    turn=turn, tool_name=block.name, arguments=block.input, is_error=is_error,
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
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

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
    result = loop.run(user_message)

    print(f"\n=== Kết quả ingest {file_path} ===")
    print(f"stop_reason: {result.stop_reason}")
    print(f"turns: {result.turn_count}")
    print(f"tokens: input={result.total_input_tokens} output={result.total_output_tokens}")
    print(f"tool_calls: {len(result.tool_calls)} (lỗi: {sum(1 for t in result.tool_calls if t.is_error)})")
    print(f"\n{result.final_text}")

    return 1 if result.stop_reason == "error" else 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
