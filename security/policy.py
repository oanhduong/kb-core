"""Chính sách phân loại tool — theo TOOL, KHÔNG theo agent gọi nó.

security/gateway.py là nơi duy nhất thực thi tool; module này chỉ trả lời "tool này thuộc nhóm gì
và gateway nên làm gì với nó", không tự thực thi hay chạm filesystem/DB. Ba nhóm:
- READ: chỉ đọc, không có tác dụng phụ -> auto-approve.
- WRITE_REVERSIBLE: có tác dụng phụ nhưng sửa/xoá lại được (ghi đè 1 file JSON theo record_id) ->
  gateway dry-run (in ra cái sẽ ghi) trước, rồi mới commit.
- WRITE_IRREVERSIBLE: tác dụng phụ khó/không hoàn tác (xoá record, publish ra chatbot production)
  -> bắt buộc human approval, gateway KHÔNG tự thực thi dù request hợp lệ.

Tool không có trong POLICY_MAP bị coi là chưa whitelist -> decide() trả action="denied" (fail-closed:
lỗi cấu hình thiếu policy cho tool mới không bao giờ tự động mở quyền, phải khai báo tường minh).

Chọn dict Python thay vì OPA/rego: chính sách ở đây có 3 nhóm cố định, không cần ngôn ngữ policy
riêng — dict tra cứu O(1), dễ review trong code review, dễ test (test_security.py gọi decide()
trực tiếp, không cần dựng OPA server).
"""
import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ToolCategory(str, Enum):
    READ = "READ"
    WRITE_REVERSIBLE = "WRITE_REVERSIBLE"
    WRITE_IRREVERSIBLE = "WRITE_IRREVERSIBLE"


# Nguồn sự thật duy nhất cho phân loại tool. Thêm tool mới ở đây trước khi đăng ký implementation
# trong security/gateway.py — gateway sẽ denied bất kỳ tool nào không có mặt ở đây.
POLICY_MAP = {
    "detect_file_type": ToolCategory.READ,
    "parse_document": ToolCategory.READ,
    "extract_image_content": ToolCategory.READ,
    "transcribe_media": ToolCategory.READ,
    "write_kb_record": ToolCategory.WRITE_REVERSIBLE,
    "delete_kb_record": ToolCategory.WRITE_IRREVERSIBLE,
    "publish_to_chatbot": ToolCategory.WRITE_IRREVERSIBLE,
}

_ACTION_BY_CATEGORY = {
    ToolCategory.READ: "auto_approve",
    ToolCategory.WRITE_REVERSIBLE: "dry_run_then_commit",
    ToolCategory.WRITE_IRREVERSIBLE: "require_approval",
}


@dataclass(frozen=True)
class PolicyDecision:
    tool_name: str
    category: ToolCategory | None  # None nghĩa là tool chưa whitelist
    action: str  # "auto_approve" | "dry_run_then_commit" | "require_approval" | "denied"
    reason: str  # LUÔN có — gateway ghi kèm reason này vào audit log cho mọi quyết định


def classify(tool_name):
    """Trả về ToolCategory của tool, hoặc None nếu chưa được khai báo trong POLICY_MAP."""
    return POLICY_MAP.get(tool_name)


def decide(tool_name):
    """Quyết định chính sách cho một tool — thuần, không có side effect, luôn kèm lý do."""
    category = classify(tool_name)

    if category is None:
        decision = PolicyDecision(
            tool_name=tool_name,
            category=None,
            action="denied",
            reason=(
                f"Tool '{tool_name}' không có trong POLICY_MAP — mặc định từ chối (fail-closed). "
                "Tool mới phải được khai báo tường minh vào một trong ba nhóm READ/WRITE_REVERSIBLE/"
                "WRITE_IRREVERSIBLE trước khi có thể gọi qua gateway."
            ),
        )
    else:
        action = _ACTION_BY_CATEGORY[category]
        decision = PolicyDecision(
            tool_name=tool_name,
            category=category,
            action=action,
            reason=f"Tool '{tool_name}' thuộc nhóm {category.value} -> action={action}.",
        )

    logger.info(
        "policy tool=%s category=%s action=%s reason=%s",
        tool_name, category.value if category else None, decision.action, decision.reason,
    )
    return decision
