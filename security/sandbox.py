"""Chạy parser (ingest/parsers.py) trong container Docker cô lập, tách khỏi tiến trình gateway.

Dùng khi input file không tin cậy hoàn toàn (ví dụ upload trực tiếp từ người dùng cuối, chưa qua
kiểm duyệt) — nếu một lỗ hổng trong dependency parser (docling, faster-whisper, openpyxl, ...) bị
khai thác qua file độc hại, blast radius bị giới hạn trong container:
- network_disabled=True + network_mode="none": egress tắt hoàn toàn — parser vốn không cần
  internet, nên container bị compromise cũng không exfiltrate được dữ liệu ra ngoài.
- read_only=True trên rootfs, chỉ /output (bind mount output_dir) ghi được: container không ghi đè
  được binary/config của chính nó hay leo thang qua việc sửa file hệ thống.
- user=NON_ROOT_USER + cap_drop=["ALL"] + security_opt=["no-new-privileges"]: kể cả thoát được
  parser logic, tiến trình vẫn chạy quyền non-root, không có Linux capability nào để leo thang.
- timeout cứng DEFAULT_TIMEOUT_SECONDS: container.wait(timeout=...) — nếu bị treo (input cố ý gây
  vòng lặp vô hạn hoặc bomb giải nén), container bị kill chủ động thay vì chiếm tài nguyên host
  vô thời hạn.
- mem_limit/memswap_limit bằng nhau: chặn OOM lách qua swap; pids_limit chặn fork bomb.

Import docker (SDK) trễ (trong _get_docker_client), không import ở top-level module — máy chạy
test/dev không nhất thiết có docker-py hay Docker daemon; test_security.py mock qua tham số
`client=` của run_parser() nên không phụ thuộc docker thật.
"""
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MEM_LIMIT = "512m"
DEFAULT_IMAGE = "chatbot-retail-parser-sandbox:latest"
NON_ROOT_USER = "1000:1000"
PIDS_LIMIT = 128
TMPFS_SIZE = "64m"  # rootfs read-only nên container vẫn cần /tmp ghi được cho file tạm của parser


class SandboxError(Exception):
    """Lỗi khi cấu hình/khởi động sandbox — thiếu docker-py, image chưa build, daemon không kết nối được."""


class SandboxTimeoutError(SandboxError):
    """Container vượt quá timeout — ĐÃ bị kill trước khi exception này được raise. KHÔNG coi log/
    output thu được (nếu có) trước thời điểm kill là kết quả đáng tin cậy."""


def _get_docker_client():
    try:
        import docker
    except ImportError as e:
        raise SandboxError(
            "Thiếu thư viện docker (pip install docker) — cần để chạy security.sandbox.run_parser()."
        ) from e
    try:
        return docker.from_env()
    except Exception as e:
        raise SandboxError(f"Không kết nối được Docker daemon: {e}") from e


def _build_container_kwargs(command, input_dir, output_dir, image, mem_limit):
    input_dir = os.path.abspath(input_dir)
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    return {
        "image": image,
        "command": command,
        "detach": True,
        "network_disabled": True,
        "network_mode": "none",
        "read_only": True,
        "user": NON_ROOT_USER,
        "mem_limit": mem_limit,
        "memswap_limit": mem_limit,
        "pids_limit": PIDS_LIMIT,
        "security_opt": ["no-new-privileges"],
        "cap_drop": ["ALL"],
        "volumes": {
            input_dir: {"bind": "/input", "mode": "ro"},
            output_dir: {"bind": "/output", "mode": "rw"},
        },
        "tmpfs": {"/tmp": f"size={TMPFS_SIZE}"},
        # auto_remove=False cố ý: nhánh timeout phải kill() trước rồi mới remove() ở finally —
        # để docker tự auto-remove có thể race với việc đọc logs/exit code sau khi kill.
        "auto_remove": False,
    }


def run_parser(
    command, input_dir, output_dir, image=DEFAULT_IMAGE,
    timeout=DEFAULT_TIMEOUT_SECONDS, mem_limit=DEFAULT_MEM_LIMIT, client=None,
):
    """Chạy `command` (list argv, ví dụ ["python", "-m", "ingest.parsers", "/input/f.pdf", "/output"])
    trong container cô lập. input_dir mount read-only vào /input, output_dir mount read-write vào
    /output — parser trong container chỉ nên đọc /input và ghi /output.

    Trả về {"exit_code": int, "logs": str, "output_dir": str}. Raise SandboxTimeoutError nếu vượt
    `timeout` giây (container bị kill trước khi raise, không để chạy nền mồ côi). client cho phép
    tiêm docker client giả trong test — mặc định tự kết nối Docker daemon thật."""
    client = client or _get_docker_client()
    kwargs = _build_container_kwargs(command, input_dir, output_dir, image, mem_limit)

    logger.info(
        "sandbox: chạy command=%r image=%s timeout=%ds mem_limit=%s network=off read_only=True "
        "user=%s pids_limit=%d",
        command, image, timeout, mem_limit, NON_ROOT_USER, PIDS_LIMIT,
    )

    container = client.containers.run(**kwargs)
    try:
        try:
            result = container.wait(timeout=timeout)
        except Exception as e:
            logger.error(
                "sandbox: container %s vượt timeout=%ds hoặc không phản hồi, kill ngay: %s",
                getattr(container, "id", "?"), timeout, e,
            )
            container.kill()
            raise SandboxTimeoutError(f"Container vượt quá timeout {timeout}s — đã bị kill.") from e

        raw_logs = container.logs()
        logs = raw_logs.decode("utf-8", errors="replace") if isinstance(raw_logs, bytes) else str(raw_logs)
        exit_code = result.get("StatusCode", -1) if isinstance(result, dict) else -1
        logger.info("sandbox: container %s kết thúc exit_code=%d", getattr(container, "id", "?"), exit_code)
        return {"exit_code": exit_code, "logs": logs, "output_dir": output_dir}
    finally:
        try:
            container.remove(force=True)
        except Exception:
            logger.warning(
                "sandbox: không remove được container %s (có thể đã bị remove/kill trước đó)",
                getattr(container, "id", "?"),
            )
