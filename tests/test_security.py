"""Pytest cho security/policy.py, security/gateway.py, security/sandbox.py.

Không gọi Docker/Anthropic thật: sandbox test tiêm docker client giả qua run_parser(client=...),
gateway test tiêm registry/approval_backend giả qua Gateway(...). Mọi test gateway monkeypatch
đường dẫn audit log / approval queue / KB output sang tmp_path — không đụng data/ thật của repo.

Test trọng tâm theo yêu cầu bài toán: agent bị compromise gọi thẳng delete_kb_record qua gateway
mà KHÔNG có approval phải bị chặn — xem test_compromised_agent_delete_without_approval_is_blocked.
"""
import json
import os
from types import SimpleNamespace

import pytest

from agent import tools as agent_tools
from agent.loop import AgentLoop
from security import gateway as security_gateway
from security import policy as security_policy
from security import sandbox as security_sandbox

VALID_RECORD = {
    "record_id": "price-abc-001",
    "source_file": "data/raw/pdf/fake.pdf",
    "source_type": "pdf",
    "doc_type": "price_list",
    "content": "Giá niêm yết SKU ABC: 100.000đ.",
    "location": "Trang 1",
    "metadata": {"sku": "ABC"},
    "confidence": 0.9,
}


# =====================================================================================
# POLICY
# =====================================================================================

@pytest.mark.parametrize("tool_name", ["detect_file_type", "parse_document", "extract_image_content", "transcribe_media"])
def test_policy_read_tools_auto_approve(tool_name):
    decision = security_policy.decide(tool_name)
    assert decision.category == security_policy.ToolCategory.READ
    assert decision.action == "auto_approve"
    assert decision.reason  # luôn kèm lý do


def test_policy_write_kb_record_is_dry_run_then_commit():
    decision = security_policy.decide("write_kb_record")
    assert decision.category == security_policy.ToolCategory.WRITE_REVERSIBLE
    assert decision.action == "dry_run_then_commit"


@pytest.mark.parametrize("tool_name", ["delete_kb_record", "publish_to_chatbot"])
def test_policy_irreversible_tools_require_approval(tool_name):
    decision = security_policy.decide(tool_name)
    assert decision.category == security_policy.ToolCategory.WRITE_IRREVERSIBLE
    assert decision.action == "require_approval"


def test_policy_unknown_tool_is_denied_fail_closed():
    decision = security_policy.decide("shell_exec")
    assert decision.category is None
    assert decision.action == "denied"
    assert "shell_exec" in decision.reason


# =====================================================================================
# GATEWAY — fixtures
# =====================================================================================

@pytest.fixture
def audit_path(tmp_path):
    return os.path.join(tmp_path, "audit.log")


def read_audit_entries(path):
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def make_gateway(tmp_path, audit_path, registry=None, approval_backend=None):
    default_registry = {
        "detect_file_type": lambda path: {"path": path, "kind": "pdf"},
        "write_kb_record": {
            "dry_run": lambda record: {"path": "preview.json", "content": record},
            "commit": lambda record: {"status": "written", "record_id": record["record_id"]},
        },
        "delete_kb_record": lambda record_id: (_ for _ in ()).throw(
            AssertionError(f"delete_kb_record KHÔNG được gọi thật khi chưa có approval (record_id={record_id})")
        ),
        "publish_to_chatbot": lambda record_id: {"status": "published", "record_id": record_id},
    }
    return security_gateway.Gateway(
        tool_registry=registry if registry is not None else default_registry,
        approval_backend=approval_backend,
        audit_log_path=audit_path,
    )


# =====================================================================================
# GATEWAY — READ auto-approve
# =====================================================================================

def test_gateway_read_tool_auto_approved_and_audited(tmp_path, audit_path):
    gw = make_gateway(tmp_path, audit_path)
    result = gw.call_tool("detect_file_type", {"path": "data/raw/f.pdf"})
    assert result == {"path": "data/raw/f.pdf", "kind": "pdf"}

    entries = read_audit_entries(audit_path)
    assert len(entries) == 1
    assert entries[0]["outcome"] == "auto_approved"
    assert entries[0]["category"] == "READ"
    assert entries[0]["reason"]


# =====================================================================================
# GATEWAY — WRITE_REVERSIBLE dry-run rồi mới commit
# =====================================================================================

def test_gateway_write_reversible_dry_runs_before_commit(tmp_path, audit_path):
    calls = []
    registry = {
        "write_kb_record": {
            "dry_run": lambda record: calls.append(("dry_run", record["record_id"])) or {"preview": True},
            "commit": lambda record: calls.append(("commit", record["record_id"])) or {"status": "written"},
        },
    }
    gw = make_gateway(tmp_path, audit_path, registry=registry)
    result = gw.call_tool("write_kb_record", {"record": VALID_RECORD})

    assert result == {"status": "written"}
    assert calls == [("dry_run", "price-abc-001"), ("commit", "price-abc-001")]  # đúng thứ tự

    entries = read_audit_entries(audit_path)
    outcomes = [e["outcome"] for e in entries]
    assert outcomes == ["dry_run", "committed"]
    assert "Preview" in entries[0]["reason"]  # preview phải xuất hiện trong log kèm lý do


def test_gateway_write_reversible_invalid_record_fails_at_dry_run_before_any_write(tmp_path, audit_path):
    written = []
    registry = {
        "write_kb_record": {
            "dry_run": security_gateway._write_kb_record_dry_run,
            "commit": lambda record: written.append(record) or {"status": "written"},
        },
    }
    gw = make_gateway(tmp_path, audit_path, registry=registry)
    bad_record = {**VALID_RECORD, "confidence": 5.0}  # ngoài khoảng [0,1]

    with pytest.raises(Exception):
        gw.call_tool("write_kb_record", {"record": bad_record})

    assert written == []  # dry_run raise trước -> commit không bao giờ được gọi


# =====================================================================================
# GATEWAY — WRITE_IRREVERSIBLE: TRỌNG TÂM bài toán
# =====================================================================================

def test_compromised_agent_delete_without_approval_is_blocked(tmp_path, audit_path):
    """Mô phỏng agent bị compromise (prompt injection hoặc model bị lừa) gọi thẳng
    delete_kb_record qua gateway mà KHÔNG có approval nào — phải bị chặn hoàn toàn, implementation
    xoá thật KHÔNG được thực thi (registry ném AssertionError nếu bị gọi), và audit log phải ghi
    lại lý do chặn."""
    gw = make_gateway(tmp_path, audit_path)  # không truyền approval_backend -> không có ai duyệt

    with pytest.raises(security_gateway.ApprovalRequired) as exc_info:
        gw.call_tool("delete_kb_record", {"record_id": "price-abc-001"}, requested_by="compromised-agent")

    assert exc_info.value.request_id  # có request_id để tra cứu/duyệt sau

    entries = read_audit_entries(audit_path)
    assert len(entries) == 1
    assert entries[0]["outcome"] == "pending_approval"
    assert entries[0]["category"] == "WRITE_IRREVERSIBLE"
    assert entries[0]["requested_by"] == "compromised-agent"
    assert "approval" in entries[0]["reason"].lower()


def test_publish_to_chatbot_without_approval_is_also_blocked(tmp_path, audit_path):
    gw = make_gateway(tmp_path, audit_path)
    with pytest.raises(security_gateway.ApprovalRequired):
        gw.call_tool("publish_to_chatbot", {"record_id": "price-abc-001"})


def test_gateway_irreversible_denied_by_human_via_queue_backend(tmp_path, audit_path):
    queue_path = os.path.join(tmp_path, "approval_queue.json")
    backend = security_gateway.QueueApprovalBackend(queue_path=queue_path)
    gw = make_gateway(tmp_path, audit_path, approval_backend=backend)

    # 1) lời gọi đầu: chưa có approval -> pending, không thực thi
    with pytest.raises(security_gateway.ApprovalRequired) as exc_info:
        gw.call_tool("delete_kb_record", {"record_id": "price-abc-001"})
    request_id = exc_info.value.request_id
    assert backend.check(request_id) == "pending"

    # 2) human từ chối qua queue (ngoài luồng agent)
    backend.deny(request_id, approver="ops-oanh")

    # 3) agent/job gọi lại đúng lời gọi cũ -> phải bị denied, KHÔNG được thực thi
    with pytest.raises(security_gateway.ApprovalDenied):
        gw.call_tool("delete_kb_record", {"record_id": "price-abc-001"})

    entries = read_audit_entries(audit_path)
    assert entries[-1]["outcome"] == "denied"
    assert "ops-oanh" not in entries[-1]["reason"]  # approver không cần lộ trong reason, chỉ cần request_id
    assert "request_id" in entries[-1]["reason"] or request_id in entries[-1]["reason"]


def test_gateway_irreversible_approved_via_queue_backend_then_commits(tmp_path, audit_path):
    queue_path = os.path.join(tmp_path, "approval_queue.json")
    backend = security_gateway.QueueApprovalBackend(queue_path=queue_path)
    executed = []
    registry = {
        "delete_kb_record": lambda record_id: executed.append(record_id) or {"status": "deleted", "record_id": record_id},
    }
    gw = make_gateway(tmp_path, audit_path, registry=registry, approval_backend=backend)

    with pytest.raises(security_gateway.ApprovalRequired) as exc_info:
        gw.call_tool("delete_kb_record", {"record_id": "price-abc-001"})
    request_id = exc_info.value.request_id

    backend.approve(request_id, approver="ops-oanh")
    result = gw.call_tool("delete_kb_record", {"record_id": "price-abc-001"})

    assert result == {"status": "deleted", "record_id": "price-abc-001"}
    assert executed == ["price-abc-001"]  # thực thi đúng 1 lần, sau khi đã approved

    entries = read_audit_entries(audit_path)
    assert entries[-1]["outcome"] == "committed"


def test_gateway_cli_approval_backend_approves_interactively(tmp_path, audit_path):
    backend = security_gateway.CLIApprovalBackend(input_fn=lambda _prompt: "y", print_fn=lambda *_a, **_k: None)
    executed = []
    registry = {
        "publish_to_chatbot": lambda record_id: executed.append(record_id) or {"status": "published"},
    }
    gw = make_gateway(tmp_path, audit_path, registry=registry, approval_backend=backend)

    # CLI backend duyệt ngay trong request() -> lời gọi đầu tiên đã đủ approved để commit.
    result = gw.call_tool("publish_to_chatbot", {"record_id": "price-abc-001"})
    assert result == {"status": "published"}
    assert executed == ["price-abc-001"]


def test_gateway_cli_approval_backend_denies_interactively(tmp_path, audit_path):
    backend = security_gateway.CLIApprovalBackend(input_fn=lambda _prompt: "n", print_fn=lambda *_a, **_k: None)
    gw = make_gateway(tmp_path, audit_path, approval_backend=backend)

    with pytest.raises(security_gateway.ApprovalDenied):
        gw.call_tool("delete_kb_record", {"record_id": "price-abc-001"})


# =====================================================================================
# GATEWAY — fail-closed: tool chưa whitelist / whitelist nhưng thiếu implementation
# =====================================================================================

def test_gateway_unknown_tool_is_denied(tmp_path, audit_path):
    gw = make_gateway(tmp_path, audit_path)
    with pytest.raises(security_gateway.PolicyDenied):
        gw.call_tool("shell_exec", {"cmd": "rm -rf /"})

    entries = read_audit_entries(audit_path)
    assert entries[0]["outcome"] == "denied"
    assert entries[0]["category"] == "UNKNOWN"


def test_gateway_whitelisted_tool_without_implementation_is_denied(tmp_path, audit_path):
    gw = make_gateway(tmp_path, audit_path, registry={})  # registry rỗng: không có implementation nào
    with pytest.raises(security_gateway.PolicyDenied):
        gw.call_tool("delete_kb_record", {"record_id": "price-abc-001"})


# =====================================================================================
# GATEWAY — implementation thật (KB store trên đĩa), monkeypatch đường dẫn sang tmp_path
# =====================================================================================

def test_delete_kb_record_real_impl_removes_file(tmp_path, monkeypatch):
    kb_dir = os.path.join(tmp_path, "output")
    monkeypatch.setattr(security_gateway, "KB_OUTPUT_DIR", kb_dir)
    os.makedirs(kb_dir, exist_ok=True)
    record_path = os.path.join(kb_dir, "price-abc-001.json")
    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(VALID_RECORD, f)

    result = security_gateway.delete_kb_record("price-abc-001")
    assert result["status"] == "deleted"
    assert not os.path.exists(record_path)


def test_delete_kb_record_real_impl_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(security_gateway, "KB_OUTPUT_DIR", os.path.join(tmp_path, "output"))
    with pytest.raises(security_gateway.GatewayError):
        security_gateway.delete_kb_record("does-not-exist")


def test_publish_to_chatbot_real_impl_copies_file(tmp_path, monkeypatch):
    kb_dir = os.path.join(tmp_path, "output")
    published_dir = os.path.join(tmp_path, "published")
    monkeypatch.setattr(security_gateway, "KB_OUTPUT_DIR", kb_dir)
    monkeypatch.setattr(security_gateway, "PUBLISHED_DIR", published_dir)
    os.makedirs(kb_dir, exist_ok=True)
    record_path = os.path.join(kb_dir, "price-abc-001.json")
    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(VALID_RECORD, f)

    result = security_gateway.publish_to_chatbot("price-abc-001")
    dest = os.path.join(published_dir, "price-abc-001.json")
    assert result["path"] == dest
    assert os.path.isfile(dest)
    assert os.path.isfile(record_path)  # publish là copy, không xoá bản gốc


def test_end_to_end_compromised_agent_cannot_bypass_gateway_for_delete(tmp_path, monkeypatch, audit_path):
    """Kịch bản đầy đủ hơn: dùng đúng registry thật (build_default_registry-style: delete_kb_record
    thật của gateway) thay vì stub AssertionError, để chứng minh ngay cả implementation thật cũng
    không chạy khi thiếu approval — không chỉ mock giả lập."""
    kb_dir = os.path.join(tmp_path, "output")
    monkeypatch.setattr(security_gateway, "KB_OUTPUT_DIR", kb_dir)
    os.makedirs(kb_dir, exist_ok=True)
    record_path = os.path.join(kb_dir, "price-abc-001.json")
    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(VALID_RECORD, f)

    registry = {"delete_kb_record": security_gateway.delete_kb_record}
    gw = security_gateway.Gateway(tool_registry=registry, approval_backend=None, audit_log_path=audit_path)

    with pytest.raises(security_gateway.ApprovalRequired):
        gw.call_tool("delete_kb_record", {"record_id": "price-abc-001"}, requested_by="compromised-agent")

    assert os.path.isfile(record_path)  # file KB vẫn còn nguyên — không có gì bị xoá


# =====================================================================================
# INTEGRATION — AgentLoop THẬT SỰ đi qua Gateway (không chỉ Gateway đứng riêng, tách khỏi runtime).
# Đây là bằng chứng cho việc security/gateway.build_ingest_tools() được các nơi khởi tạo AgentLoop
# (agent/loop.py::main, agent/orchestrator.py, agent/batch.py) dùng thay agent_tools.build_registry()
# trực tiếp — nếu AgentLoop._call_tool gọi thẳng function cũ (bỏ qua Gateway), 2 test dưới đây fail.
# =====================================================================================

def _fake_response(content, stop_reason="end_turn"):
    return SimpleNamespace(
        content=content, stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name, input_, id_="tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=input_, id=id_)


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)


def test_agentloop_write_kb_record_actually_routes_through_gateway(tmp_path, audit_path, monkeypatch):
    kb_dir = os.path.join(tmp_path, "output")
    monkeypatch.setattr(agent_tools, "OUTPUT_DIR", kb_dir)  # nơi write_kb_record commit thật ghi ra
    monkeypatch.setattr(security_gateway, "KB_OUTPUT_DIR", kb_dir)  # nơi gateway tính path cho dry-run

    gateway = security_gateway.Gateway(
        tool_registry=security_gateway.build_default_registry(client=None), audit_log_path=audit_path,
    )
    tools = security_gateway.build_gated_registry(
        gateway, tool_names=security_gateway.INGEST_TOOL_NAMES, requested_by="test-ingest-agent",
    )

    responses = [
        _fake_response([_tool_use_block("write_kb_record", {"record": VALID_RECORD})]),
        _fake_response([_text_block("Đã ghi xong 1 record.")]),
    ]
    client = _FakeClient(responses)
    loop = AgentLoop(system_prompt="SYS", tools=tools, client=client, enable_verifier=False)

    result = loop.run("ingest file test", "data/raw/fake.pdf")

    assert result.status == "OK"
    assert os.path.isfile(os.path.join(kb_dir, "price-abc-001.json"))  # commit thật đã chạy

    entries = read_audit_entries(audit_path)
    assert [e["outcome"] for e in entries] == ["dry_run", "committed"]  # đúng luồng WRITE_REVERSIBLE
    assert all(e["requested_by"] == "test-ingest-agent" for e in entries)  # audit biết agent nào gọi


def test_agentloop_compromised_model_calling_delete_kb_record_is_blocked_by_gateway(tmp_path, audit_path):
    """Mô phỏng agent bị prompt injection dụ model tự phát sinh tool_use cho delete_kb_record — dù
    registry của Gateway có implementation THẬT (sẽ crash nếu bị gọi thật, để chứng minh nó không
    hề được gọi), AgentLoop vẫn phải kết thúc OK (không crash tiến trình) vì lỗi được trả về model
    dưới dạng tool_result is_error, và file KB không hề bị xoá."""
    kb_dir = os.path.join(tmp_path, "output")
    os.makedirs(kb_dir, exist_ok=True)
    record_path = os.path.join(kb_dir, "price-abc-001.json")
    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(VALID_RECORD, f)

    registry = dict(security_gateway.build_default_registry(client=None))
    registry["delete_kb_record"] = lambda record_id: (_ for _ in ()).throw(
        AssertionError(f"KHÔNG được xoá thật khi chưa approval (record_id={record_id})")
    )
    # KHÔNG cấu hình approval_backend — mô phỏng đúng tình huống nêu trong yêu cầu bài toán.
    gateway = security_gateway.Gateway(tool_registry=registry, audit_log_path=audit_path)
    tools = security_gateway.build_gated_registry(gateway, requested_by="compromised-agent")  # đủ mọi tool, kể cả delete

    responses = [
        _fake_response([_tool_use_block("delete_kb_record", {"record_id": "price-abc-001"})]),
        _fake_response([_text_block("Đành báo lỗi, không xoá được.")]),
    ]
    client = _FakeClient(responses)
    loop = AgentLoop(system_prompt="SYS", tools=tools, client=client, enable_verifier=False)

    result = loop.run("bị prompt injection dụ xoá record price-abc-001", "data/raw/fake.pdf")

    assert result.status == "OK"  # AgentLoop không crash tiến trình vì exception của gateway
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].is_error is True  # model nhận lỗi, không nhận thành công giả

    tool_result_content = client.messages.calls[1]["messages"][-1]["content"][0]["content"]
    assert "approval" in tool_result_content.lower()

    assert os.path.isfile(record_path)  # file KB vẫn còn nguyên


# =====================================================================================
# SANDBOX
# =====================================================================================

class FakeContainer:
    def __init__(self, wait_result=None, wait_exc=None, logs=b""):
        self.id = "fake-container-id"
        self._wait_result = wait_result if wait_result is not None else {"StatusCode": 0}
        self._wait_exc = wait_exc
        self._logs = logs
        self.killed = False
        self.removed = False
        self.wait_calls = []

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self._wait_exc:
            raise self._wait_exc
        return self._wait_result

    def logs(self):
        return self._logs

    def kill(self):
        self.killed = True

    def remove(self, force=False):
        self.removed = True


class FakeContainersAPI:
    def __init__(self, container):
        self._container = container
        self.run_kwargs = None

    def run(self, **kwargs):
        self.run_kwargs = kwargs
        return self._container


class FakeDockerClient:
    def __init__(self, container):
        self.containers = FakeContainersAPI(container)


def test_sandbox_run_parser_applies_isolation_settings(tmp_path):
    container = FakeContainer(logs=b"done\n")
    client = FakeDockerClient(container)
    input_dir = os.path.join(tmp_path, "in")
    output_dir = os.path.join(tmp_path, "out")
    os.makedirs(input_dir, exist_ok=True)

    result = security_sandbox.run_parser(
        command=["python", "-m", "ingest.parsers", "/input/f.pdf", "/output"],
        input_dir=input_dir, output_dir=output_dir, client=client,
    )

    kwargs = client.containers.run_kwargs
    assert kwargs["network_disabled"] is True
    assert kwargs["network_mode"] == "none"
    assert kwargs["read_only"] is True
    assert kwargs["user"] == security_sandbox.NON_ROOT_USER
    assert kwargs["mem_limit"] == security_sandbox.DEFAULT_MEM_LIMIT
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["volumes"][os.path.abspath(input_dir)]["mode"] == "ro"
    assert kwargs["volumes"][os.path.abspath(output_dir)]["mode"] == "rw"

    assert result["exit_code"] == 0
    assert result["logs"] == "done\n"
    assert container.wait_calls == [security_sandbox.DEFAULT_TIMEOUT_SECONDS]
    assert container.removed is True
    assert container.killed is False


def test_sandbox_run_parser_kills_container_on_timeout(tmp_path):
    container = FakeContainer(wait_exc=TimeoutError("deadline exceeded"))
    client = FakeDockerClient(container)
    input_dir = os.path.join(tmp_path, "in")
    output_dir = os.path.join(tmp_path, "out")
    os.makedirs(input_dir, exist_ok=True)

    with pytest.raises(security_sandbox.SandboxTimeoutError):
        security_sandbox.run_parser(
            command=["sleep", "9999"], input_dir=input_dir, output_dir=output_dir,
            client=client, timeout=5,
        )

    assert container.killed is True  # container bị kill chủ động, không để treo nền
    assert container.removed is True  # vẫn dọn dẹp trong finally dù bị timeout


def test_sandbox_missing_docker_sdk_raises_actionable_error(monkeypatch, tmp_path):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "docker":
            raise ImportError("no module named docker")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(security_sandbox.SandboxError, match="docker"):
        security_sandbox._get_docker_client()
