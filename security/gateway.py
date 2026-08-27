"""Gateway — chốt chặn duy nhất giữa agent runtime và DB/filesystem thật.

Kiến trúc: agent (agent/loop.py) KHÔNG được import trực tiếp bất kỳ hàm nào chạm filesystem thật
(agent/tools.write_kb_record, hay các thao tác xoá/publish bên dưới) — agent chỉ giữ TÊN tool và
tham số, gửi qua Gateway.call_tool(tool_name, arguments). Gateway là nơi duy nhất:
1. Có implementation thật (đọc/ghi/xoá file dưới data/output, publish sang data/published).
2. Tra security/policy.py để biết tool thuộc nhóm nào và phải làm gì trước khi thực thi.
3. Ghi audit log kèm LÝ DO cho mọi quyết định (auto-approve, dry-run, pending, denied, committed).

Vì agent runtime không giữ credential/đường dẫn thật nào (chỉ gọi qua tên tool), một agent bị
compromise (prompt injection, model bị lừa) nhiều nhất cũng chỉ gửi được lời gọi tool tới gateway —
và gateway áp policy y hệt cho MỌI lời gọi bất kể agent nào gửi, không có đường tắt. Xem
tests/test_security.py::test_compromised_agent_delete_without_approval_is_blocked cho bằng chứng.

Ba hành vi theo nhóm (xem security/policy.py):
- READ: gọi thẳng implementation, auto-approve.
- WRITE_REVERSIBLE (write_kb_record): gọi dry_run trước (validate + render JSON sẽ ghi, KHÔNG
  chạm đĩa), log preview, rồi mới gọi commit (ghi thật). Ghi đè theo record_id nên revert được
  bằng cách ghi lại bản cũ.
- WRITE_IRREVERSIBLE (delete_kb_record, publish_to_chatbot): KHÔNG BAO GIỜ tự thực thi. Tra
  approval_backend theo request_id xác định (hash tool+arguments) — approved mới commit, denied
  raise ApprovalDenied, còn lại (chưa có backend, hoặc còn pending) raise ApprovalRequired và tạo
  (nếu có backend) một pending request để human duyệt sau, KHÔNG thực thi.
"""
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass

from agent import tools as agent_tools
from schemas import KBRecord
from security import policy as security_policy

logger = logging.getLogger(__name__)

AUDIT_LOG_PATH = os.path.join("data", "security", "audit.log")
APPROVAL_QUEUE_PATH = os.path.join("data", "security", "approval_queue.json")
KB_OUTPUT_DIR = os.path.join("data", "output")
PUBLISHED_DIR = os.path.join("data", "published")

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]")


class GatewayError(Exception):
    """Lỗi chung khi gateway từ chối hoặc không thực hiện được một lời gọi tool."""


class PolicyDenied(GatewayError):
    """Tool chưa whitelist trong policy, hoặc policy cho phép nhưng gateway không có implementation
    đăng ký cho tool đó — cả hai trường hợp đều fail-closed, KHÔNG thực thi gì."""


class ApprovalRequired(GatewayError):
    """WRITE_IRREVERSIBLE chưa có approval hợp lệ — lời gọi bị chặn, KHÔNG thực thi implementation.
    request_id dùng để một quy trình duyệt riêng (CLI/queue) tra cứu và approve/deny sau."""

    def __init__(self, message, request_id):
        super().__init__(message)
        self.request_id = request_id


class ApprovalDenied(GatewayError):
    """Human đã từ chối approval cho request_id này — lời gọi bị chặn, KHÔNG thực thi."""

    def __init__(self, message, request_id):
        super().__init__(message)
        self.request_id = request_id


# =====================================================================================
# APPROVAL BACKEND — nơi WRITE_IRREVERSIBLE tra/tạo yêu cầu duyệt. Hai cách triển khai:
# CLI (duyệt ngay tại chỗ, đồng bộ) và queue file (duyệt bất đồng bộ bởi một quy trình khác).
# =====================================================================================

class ApprovalBackend:
    """Giao diện chung — request() tạo/ghi nhận yêu cầu (idempotent theo request_id), check() tra
    trạng thái hiện tại: "approved" | "denied" | "pending"."""

    def request(self, request_id, tool_name, arguments, reason):
        raise NotImplementedError

    def check(self, request_id):
        raise NotImplementedError


class CLIApprovalBackend(ApprovalBackend):
    """Duyệt ngay bằng CLI prompt (input()) — dùng khi chạy agent tương tác, có người trực chờ.
    request() prompt luôn và lưu kết quả, nên check() ngay sau đó trả về approved/denied thay vì
    pending — phù hợp vòng lặp đồng bộ của agent/loop.py (không cần "chạy lại sau")."""

    def __init__(self, input_fn=input, print_fn=print):
        self._input_fn = input_fn
        self._print_fn = print_fn
        self._resolved = {}

    def request(self, request_id, tool_name, arguments, reason):
        if request_id in self._resolved:
            return
        self._print_fn(
            f"\n[APPROVAL REQUIRED] tool={tool_name} arguments={arguments}\nLý do: {reason}\n"
            f"request_id={request_id}"
        )
        answer = self._input_fn("Duyệt hành động WRITE_IRREVERSIBLE này? [y/N]: ").strip().lower()
        self._resolved[request_id] = "approved" if answer == "y" else "denied"

    def check(self, request_id):
        return self._resolved.get(request_id, "pending")


class QueueApprovalBackend(ApprovalBackend):
    """Duyệt bất đồng bộ qua file hàng đợi trên đĩa — dùng khi không có người trực ngay lúc agent
    chạy: request() ghi một entry "pending" (idempotent, không ghi đè entry đã tồn tại), một quy
    trình/CLI riêng gọi approve()/deny() sau đó, và gateway chỉ tiếp tục khi agent (hoặc job retry)
    gọi lại call_tool và check() thấy đã resolved. Ghi qua file tạm + os.replace để không hỏng
    hàng đợi nếu bị ngắt giữa chừng — cùng kỹ thuật với agent/memory.py::save_state."""

    def __init__(self, queue_path=APPROVAL_QUEUE_PATH):
        self._queue_path = queue_path

    def _load(self):
        if not os.path.isfile(self._queue_path):
            return {}
        with open(self._queue_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data):
        os.makedirs(os.path.dirname(self._queue_path) or ".", exist_ok=True)
        tmp_path = f"{self._queue_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self._queue_path)

    def request(self, request_id, tool_name, arguments, reason):
        data = self._load()
        if request_id in data:
            return
        data[request_id] = {
            "tool_name": tool_name,
            "arguments": arguments,
            "reason": reason,
            "status": "pending",
            "created_at": time.time(),
            "approver": None,
        }
        self._save(data)

    def check(self, request_id):
        entry = self._load().get(request_id)
        return entry["status"] if entry else "pending"

    def approve(self, request_id, approver="human"):
        """Gọi từ quy trình duyệt riêng (CLI admin, dashboard), KHÔNG phải từ agent."""
        data = self._load()
        if request_id not in data:
            raise GatewayError(f"Không có yêu cầu approval nào với request_id={request_id}.")
        data[request_id]["status"] = "approved"
        data[request_id]["approver"] = approver
        self._save(data)

    def deny(self, request_id, approver="human"):
        data = self._load()
        if request_id not in data:
            raise GatewayError(f"Không có yêu cầu approval nào với request_id={request_id}.")
        data[request_id]["status"] = "denied"
        data[request_id]["approver"] = approver
        self._save(data)


def _request_id(tool_name, arguments):
    """Hash quyết định theo (tool_name, arguments) — cùng lời gọi (agent retry, hoặc job chạy lại
    sau khi human duyệt) luôn map về đúng 1 request_id, không tạo yêu cầu trùng lặp."""
    payload = json.dumps({"tool": tool_name, "arguments": arguments}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# =====================================================================================
# IMPLEMENTATION THẬT — nơi DUY NHẤT trong toàn bộ codebase được chạm data/output, data/published.
# agent/tools.write_kb_record được TÁI DÙNG cho phần validate + ghi (không nhân đôi logic), nhưng
# chỉ được gọi từ đây (qua registry của Gateway), không phải trực tiếp từ agent/loop.py.
# =====================================================================================

def _kb_record_path(record_id):
    safe_id = _SAFE_ID_RE.sub("_", record_id)
    return os.path.join(KB_OUTPUT_DIR, f"{safe_id}.json")


def _write_kb_record_dry_run(record):
    """Validate record qua schemas.KBRecord và trả về đúng nội dung/đường dẫn SẼ ghi —
    KHÔNG mở/ghi file. Lỗi validate raise ngay ở đây (trước khi có cơ hội ghi bất cứ gì)."""
    kb_record = KBRecord(**record)
    return {"path": _kb_record_path(kb_record.record_id), "content": kb_record.model_dump()}


def _write_kb_record_commit(record):
    return agent_tools.write_kb_record(record)


def delete_kb_record(record_id):
    """WRITE_IRREVERSIBLE — xoá hẳn 1 KBRecord khỏi data/output. Chỉ được gọi bởi Gateway.call_tool
    sau khi approval_backend xác nhận "approved" cho đúng request_id tương ứng."""
    path = _kb_record_path(record_id)
    if not os.path.isfile(path):
        raise GatewayError(f"Không tìm thấy KBRecord '{record_id}' tại {path} — không có gì để xoá.")
    os.remove(path)
    return {"status": "deleted", "record_id": record_id, "path": path}


def publish_to_chatbot(record_id):
    """WRITE_IRREVERSIBLE — copy 1 KBRecord đã ghi sang data/published (mô phỏng đẩy ra chatbot
    production). Chỉ được gọi bởi Gateway.call_tool sau khi có approval."""
    src = _kb_record_path(record_id)
    if not os.path.isfile(src):
        raise GatewayError(f"Không tìm thấy KBRecord '{record_id}' tại {src} — chưa thể publish.")
    os.makedirs(PUBLISHED_DIR, exist_ok=True)
    dest = os.path.join(PUBLISHED_DIR, os.path.basename(src))
    with open(src, "r", encoding="utf-8") as f:
        content = f.read()
    with open(dest, "w", encoding="utf-8") as f:
        f.write(content)
    return {"status": "published", "record_id": record_id, "path": dest}


def build_default_registry(client=None):
    """Registry mặc định cho Gateway — READ tái dùng thẳng agent/tools.py (đọc thuần, không tác
    dụng phụ nên an toàn dùng lại); WRITE_REVERSIBLE khai báo cặp dry_run/commit; WRITE_IRREVERSIBLE
    trỏ vào implementation thật ở trên."""
    agent_registry = agent_tools.build_registry(client)
    return {
        "detect_file_type": agent_registry["detect_file_type"]["function"],
        "parse_document": agent_registry["parse_document"]["function"],
        "extract_image_content": agent_registry["extract_image_content"]["function"],
        "transcribe_media": agent_registry["transcribe_media"]["function"],
        "write_kb_record": {"dry_run": _write_kb_record_dry_run, "commit": _write_kb_record_commit},
        "delete_kb_record": delete_kb_record,
        "publish_to_chatbot": publish_to_chatbot,
    }


# =====================================================================================
# FACADE CHO AgentLoop — đây là điểm nối THẬT giữa agent runtime và Gateway.
#
# AgentLoop (agent/loop.py) thực thi tool bằng cách gọi tools[name]["function"](**arguments) —
# xem AgentLoop._call_tool. Nếu nơi khởi tạo AgentLoop truyền thẳng agent_tools.build_registry(client)
# như "function" thì AgentLoop gọi thẳng write_kb_record/... mà KHÔNG qua Gateway — đó là lỗ hổng
# thực tế đã có trước khi module này tồn tại (agent/loop.py, agent/orchestrator.py, agent/batch.py
# gọi agent_tools.build_registry(client) trực tiếp).
#
# build_gated_registry() trả về ĐÚNG shape mà AgentLoop cần (name -> {"function","schema"}), nhưng
# "function" ở đây không làm gì ngoài forward (name, kwargs) sang gateway.call_tool() — implementation
# thật nằm trong `gateway` (Gateway(tool_registry=build_default_registry(client))), tách khỏi object
# AgentLoop cầm. Mọi nơi khởi tạo AgentLoop PHẢI dùng registry này thay vì agent_tools.build_registry()
# trực tiếp — xem agent/loop.py::main, agent/orchestrator.py::_run_subagent_for_file,
# agent/batch.py::_run_one_file.
# =====================================================================================

_SCHEMA_DELETE_KB_RECORD = {
    "description": (
        "Xoá vĩnh viễn một KBRecord đã ghi (data/output/<record_id>.json) khỏi knowledge base. "
        "KHÔNG hoàn tác được sau khi thực thi — luôn cần human approval qua Gateway trước khi lệnh "
        "xoá thật sự chạy. Chỉ gọi khi chắc chắn record đó sai/trùng lặp cần loại bỏ."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"record_id": {"type": "string", "description": "record_id của KBRecord cần xoá."}},
        "required": ["record_id"],
    },
}

_SCHEMA_PUBLISH_TO_CHATBOT = {
    "description": (
        "Đẩy một KBRecord đã ghi ra chatbot production (data/published/<record_id>.json), hiển thị "
        "cho người dùng cuối. KHÔNG dễ hoàn tác — luôn cần human approval qua Gateway trước khi "
        "lệnh publish thật sự chạy."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"record_id": {"type": "string", "description": "record_id của KBRecord cần publish."}},
        "required": ["record_id"],
    },
}


def _tool_schemas():
    """Mô tả tool cho Claude tool-use (chỉ "description"/"input_schema", KHÔNG có implementation) —
    tái dùng schema đã khai báo ở agent/tools.py cho 4 tool đọc + write_kb_record, bổ sung 2 schema
    cho delete_kb_record/publish_to_chatbot (chưa tồn tại ở agent/tools.py vì đó không phải thao
    tác "ingest"). client=None vì ở đây chỉ cần đọc schema, không gọi function nào."""
    agent_registry = agent_tools.build_registry(client=None)
    schemas = {name: spec["schema"] for name, spec in agent_registry.items()}
    schemas["delete_kb_record"] = _SCHEMA_DELETE_KB_RECORD
    schemas["publish_to_chatbot"] = _SCHEMA_PUBLISH_TO_CHATBOT
    return schemas


def build_gated_registry(gateway, tool_names=None, requested_by="agent"):
    """Registry để truyền thẳng vào AgentLoop(tools=...). Mỗi "function" chỉ forward sang
    gateway.call_tool(name, kwargs, requested_by=requested_by) — AgentLoop (và model đứng sau nó)
    không bao giờ cầm tham chiếu tới implementation thật, chỉ có thể xin Gateway thực thi hộ.

    tool_names giới hạn tập tool đưa vào tay agent (mặc định: tất cả tool đã khai báo policy).
    agent/loop.SYSTEM_PROMPT/EXTRACTION_GUIDELINE hiện chỉ dạy model dùng 5 tool ingest — TRUYỀN
    tool_names=agent_tools.build_registry(None).keys() ở nơi khởi tạo AgentLoop cho pipeline ingest
    để không đưa delete_kb_record/publish_to_chatbot vào tay một agent không có hướng dẫn khi nào
    nên gọi chúng (Gateway vẫn từ chối nếu lỡ gọi — đây chỉ là giảm bề mặt tấn công/nhầm lẫn thêm
    một lớp, không phải cơ chế chặn chính)."""
    schemas = _tool_schemas()
    names = tool_names if tool_names is not None else schemas.keys()
    return {
        name: {"function": _make_gated_call(gateway, name, requested_by), "schema": schemas[name]}
        for name in names
    }


def _make_gated_call(gateway, tool_name, requested_by):
    def _call(**kwargs):
        return gateway.call_tool(tool_name, kwargs, requested_by=requested_by)
    return _call


INGEST_TOOL_NAMES = (
    "detect_file_type", "parse_document", "extract_image_content", "transcribe_media", "write_kb_record",
)


def build_ingest_tools(client, requested_by="ingest-agent", approval_backend=None):
    """Điểm gọi DUY NHẤT nên dùng ở mọi nơi khởi tạo AgentLoop cho pipeline ingest (agent/loop.py::main,
    agent/orchestrator.py::_run_subagent_for_file, agent/batch.py::_run_one_file) — thay thế hoàn
    toàn agent_tools.build_registry(client) trực tiếp. Trả về tools dict cho AgentLoop(tools=...),
    chỉ chứa 5 tool ingest (không đưa delete_kb_record/publish_to_chatbot vào tay agent này — SYSTEM_
    PROMPT của nó không dạy khi nào nên gọi 2 tool đó). Gateway thật đứng sau (giữ implementation +
    audit log) được tạo mới mỗi lần gọi hàm này — khớp vòng đời 1 Gateway/1 client hiện tại của
    AgentLoop (mỗi sub-agent/file có anthropic.Anthropic() riêng, xem agent/orchestrator.py)."""
    gateway = Gateway(tool_registry=build_default_registry(client), approval_backend=approval_backend)
    return gateway, build_gated_registry(gateway, tool_names=INGEST_TOOL_NAMES, requested_by=requested_by)


# =====================================================================================
# GATEWAY
# =====================================================================================

@dataclass
class AuditEntry:
    ts: float
    tool_name: str
    category: str
    action: str
    outcome: str  # "auto_approved" | "dry_run" | "committed" | "pending_approval" | "denied" | "error"
    reason: str
    requested_by: str
    arguments: dict


class Gateway:
    """Chốt chặn duy nhất giữa agent và filesystem/DB thật.

    agent_registry chỉ nên chứa TÊN tool ở phía agent (schema cho Claude tool-use) — implementation
    thật nằm ở registry truyền vào đây (mặc định build_default_registry()), agent KHÔNG import
    hay giữ tham chiếu tới các hàm này."""

    def __init__(self, tool_registry=None, approval_backend=None, audit_log_path=AUDIT_LOG_PATH):
        self._registry = tool_registry if tool_registry is not None else build_default_registry()
        self._approval_backend = approval_backend
        self._audit_log_path = audit_log_path

    def call_tool(self, tool_name, arguments, requested_by="agent"):
        decision = security_policy.decide(tool_name)

        if decision.category is None:
            self._audit(tool_name, decision, "denied", requested_by, arguments)
            raise PolicyDenied(decision.reason)

        impl = self._registry.get(tool_name)
        if impl is None:
            reason = (
                f"Tool '{tool_name}' được policy cho phép ({decision.category.value}) nhưng KHÔNG có "
                "implementation đăng ký trong registry của gateway — fail-closed, không thực thi."
            )
            self._audit(tool_name, decision, "denied", requested_by, arguments, reason=reason)
            raise PolicyDenied(reason)

        if decision.category == security_policy.ToolCategory.READ:
            return self._call_read(tool_name, decision, impl, arguments, requested_by)

        if decision.category == security_policy.ToolCategory.WRITE_REVERSIBLE:
            return self._call_write_reversible(tool_name, decision, impl, arguments, requested_by)

        return self._call_write_irreversible(tool_name, decision, impl, arguments, requested_by)

    def _call_read(self, tool_name, decision, impl, arguments, requested_by):
        result = impl(**arguments)
        self._audit(tool_name, decision, "auto_approved", requested_by, arguments)
        return result

    def _call_write_reversible(self, tool_name, decision, impl, arguments, requested_by):
        dry_run_fn = impl["dry_run"] if isinstance(impl, dict) else None
        commit_fn = impl["commit"] if isinstance(impl, dict) else impl

        if dry_run_fn is not None:
            preview = dry_run_fn(**arguments)
            preview_text = json.dumps(preview, ensure_ascii=False)
            logger.info("gateway dry-run tool=%s preview=%s", tool_name, preview_text)
            self._audit(
                tool_name, decision, "dry_run", requested_by, arguments,
                reason=f"{decision.reason} Preview (chưa ghi): {preview_text}",
            )

        result = commit_fn(**arguments)
        self._audit(tool_name, decision, "committed", requested_by, arguments)
        return result

    def _call_write_irreversible(self, tool_name, decision, impl, arguments, requested_by):
        request_id = _request_id(tool_name, arguments)

        def resolved(status):
            """status đã là 'approved'/'denied' (không phải 'pending') -> thực thi hoặc raise
            ApprovalDenied, kèm audit. Trả về kết quả nếu approved, không return nếu denied (raise)."""
            if status == "approved":
                result = impl(**arguments)
                self._audit(
                    tool_name, decision, "committed", requested_by, arguments,
                    reason=f"{decision.reason} Đã approved cho request_id={request_id}.",
                )
                return result
            reason = f"{decision.reason} Human đã từ chối request_id={request_id}."
            self._audit(tool_name, decision, "denied", requested_by, arguments, reason=reason)
            raise ApprovalDenied(reason, request_id=request_id)

        status = self._approval_backend.check(request_id) if self._approval_backend else "pending"
        if status in ("approved", "denied"):
            return resolved(status)

        # status == "pending" (bao gồm cả trường hợp KHÔNG có approval_backend nào cấu hình — vẫn
        # phải fail-closed, KHÔNG coi "không có backend" là auto-approve). request() có thể resolve
        # NGAY (CLIApprovalBackend prompt đồng bộ) hoặc để pending (QueueApprovalBackend chờ human
        # duyệt sau) — check() lại sau request() để không bỏ sót trường hợp resolve đồng bộ.
        if self._approval_backend is not None:
            self._approval_backend.request(request_id, tool_name, arguments, decision.reason)
            status = self._approval_backend.check(request_id)
            if status in ("approved", "denied"):
                return resolved(status)

        reason = (
            f"{decision.reason} Tool WRITE_IRREVERSIBLE bắt buộc human approval qua CLI prompt hoặc "
            f"approval queue trước khi thực thi — chưa có approval cho request_id={request_id}"
            + ("." if self._approval_backend is not None else " (không có approval_backend nào được cấu hình).")
        )
        self._audit(tool_name, decision, "pending_approval", requested_by, arguments, reason=reason)
        raise ApprovalRequired(reason, request_id=request_id)

    def _audit(self, tool_name, decision, outcome, requested_by, arguments, reason=None):
        entry = AuditEntry(
            ts=time.time(),
            tool_name=tool_name,
            category=decision.category.value if decision.category else "UNKNOWN",
            action=decision.action,
            outcome=outcome,
            reason=reason or decision.reason,
            requested_by=requested_by,
            arguments=arguments,
        )
        os.makedirs(os.path.dirname(self._audit_log_path) or ".", exist_ok=True)
        with open(self._audit_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.__dict__, ensure_ascii=False, default=str) + "\n")
        logger.info(
            "gateway audit tool=%s category=%s outcome=%s requested_by=%s reason=%s",
            tool_name, entry.category, outcome, requested_by, entry.reason,
        )
