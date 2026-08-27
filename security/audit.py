"""Audit log append-only cho mọi lời gọi tool đi qua security/gateway.py.

Khác với log JSONL đơn giản đã có sẵn trong Gateway._audit (xem security/gateway.py) — vốn chỉ ghi
outcome/reason cho từng quyết định policy — module này định nghĩa MỘT schema đầy đủ hơn cho một
entry audit (run_id, file_id, tool_name, arguments, result_hash, policy_decision, approved_by,
latency) và hai cơ chế giám sát vận hành trên trên tập entry đó: rate limit theo tool và anomaly
detection đơn giản. Thiết kế để Gateway (hoặc một quy trình vận hành khác có quyền) là nơi DUY NHẤT
gọi AppendOnlyAuditLog.append() — agent/model không bao giờ cầm tham chiếu tới object này (không có
tool nào trong agent/tools.py hay security/gateway.build_default_registry trỏ tới nó), cùng triết lý
"an toàn theo cấu trúc code, không phải theo ACL hệ điều hành" đã dùng ở security/policy.py.

Append-only: class chỉ có append() (ghi) và read_entries() (đọc) — KHÔNG có update/delete entry.
File log luôn mở ở chế độ "a" (append), không bao giờ "w" (ghi đè) — một entry đã ghi ra đĩa không
thể bị sửa hay xoá bởi chính module này.

arguments được hash nếu chuỗi JSON của nó dài hơn ARGUMENTS_HASH_THRESHOLD_CHARS (giữ nguyên văn nếu
ngắn, để đọc log còn có ngữ cảnh actionable); result LUÔN được hash (không giữ nguyên văn) — kết quả
tool (nội dung KBRecord, chunk trích xuất, ...) có thể chứa dữ liệu khách hàng, hash để log dùng đối
chiếu/audit trace mà không nhân bản dữ liệu nhạy cảm ra một nơi lưu trữ thứ hai.

CLI: `python -m security.audit --run <run_id>` đọc lại toàn bộ entry của 1 run; bỏ --run để xem báo
cáo anomaly tổng hợp trên toàn bộ log.
"""
import argparse
import hashlib
import json
import logging
import os
import sys
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from statistics import mean

logger = logging.getLogger(__name__)

DEFAULT_AUDIT_LOG_PATH = os.path.join("data", "security", "audit_trail.log")
ARGUMENTS_HASH_THRESHOLD_CHARS = 200


class AuditError(Exception):
    """Lỗi chung của module audit — vd đọc phải dòng log hỏng."""


class RateLimitExceeded(AuditError):
    """Một (subject, tool_name) vượt quá số lời gọi cho phép trong cửa sổ thời gian cấu hình."""

    def __init__(self, message, subject, tool_name):
        super().__init__(message)
        self.subject = subject
        self.tool_name = tool_name


# =====================================================================================
# ENTRY SCHEMA + HASHING
# =====================================================================================

def _stable_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _stable_hash(value):
    return "sha256:" + hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _encode_arguments(arguments, threshold_chars=ARGUMENTS_HASH_THRESHOLD_CHARS):
    """Giữ nguyên arguments nếu chuỗi JSON ngắn (đọc log còn actionable); hash nếu dài (vd content
    của một KBRecord dài hoặc chunk lớn truyền qua write_kb_record) — tránh log phình to vô ích."""
    encoded = _stable_json(arguments)
    if len(encoded) <= threshold_chars:
        return arguments
    return {"sha256": _stable_hash(arguments), "truncated": True, "length_chars": len(encoded)}


@dataclass
class AuditEntry:
    ts: float
    run_id: str
    file_id: str
    tool_name: str
    arguments: object  # dict nguyên văn nếu ngắn, hoặc {"sha256","truncated","length_chars"} nếu dài
    result_hash: str  # luôn hash, không bao giờ giữ nguyên văn kết quả
    policy_decision: str
    approved_by: object  # str hoặc None
    latency_seconds: float


# =====================================================================================
# APPEND-ONLY LOG
# =====================================================================================

class AppendOnlyAuditLog:
    """Sink duy nhất cho AuditEntry. Chỉ expose append()/read_entries() — không có API sửa/xoá entry.

    KHÔNG import/gọi class này từ agent/tools.py hay bất kỳ registry tool nào lộ ra cho model — chỉ
    code có đặc quyền (security/gateway.Gateway, quy trình vận hành/CLI) mới nên cầm instance này."""

    def __init__(self, path=None):
        # Không bind DEFAULT_AUDIT_LOG_PATH làm default param trực tiếp (default arg chỉ eval MỘT
        # LẦN lúc định nghĩa hàm) — tra module attribute bên trong thân hàm để monkeypatch
        # security.audit.DEFAULT_AUDIT_LOG_PATH (test, hoặc nơi khác đổi đường dẫn mặc định lúc
        # runtime) có tác dụng với mọi AppendOnlyAuditLog() tạo sau đó, kể cả không truyền path.
        self._path = path if path is not None else DEFAULT_AUDIT_LOG_PATH

    def append(
        self, *, run_id, file_id, tool_name, arguments, result, policy_decision,
        latency_seconds, approved_by=None, ts=None,
    ):
        entry = AuditEntry(
            ts=ts if ts is not None else time.time(),
            run_id=run_id,
            file_id=file_id,
            tool_name=tool_name,
            arguments=_encode_arguments(arguments),
            result_hash=_stable_hash(result),
            policy_decision=policy_decision,
            approved_by=approved_by,
            latency_seconds=latency_seconds,
        )
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(_stable_json(asdict(entry)) + "\n")
        logger.info(
            "audit run_id=%s file_id=%s tool=%s decision=%s latency=%.3fs",
            run_id, file_id, tool_name, policy_decision, latency_seconds,
        )
        return entry

    def read_entries(self, run_id=None):
        """Đọc lại toàn bộ entry (hoặc chỉ của `run_id` nếu truyền), theo đúng thứ tự đã ghi. Dòng
        log hỏng (không parse được JSON) bị bỏ qua kèm cảnh báo — không làm hỏng việc đọc các dòng
        còn lại (log là append-only, một dòng lỗi không nên chặn toàn bộ audit trail)."""
        if not os.path.isfile(self._path):
            return []
        entries = []
        with open(self._path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("audit: bỏ qua dòng %d hỏng (không parse được JSON) tại %s", lineno, self._path)
                    continue
                if run_id is not None and data.get("run_id") != run_id:
                    continue
                entries.append(data)
        return entries


# =====================================================================================
# RATE LIMIT THEO TOOL
# =====================================================================================

class RateLimiter:
    """Sliding window đơn giản: tối đa `max_calls` lần gọi cho mỗi (subject, tool_name) trong
    `window_seconds` giây gần nhất. subject thường là run_id hoặc file_id — gọi theo tool để một
    tool bị lạm dụng (model bị kẹt vòng lặp, hoặc bị injection dụ gọi tràn lan) không ảnh hưởng tới
    hạn mức của các tool khác trên cùng subject.

    time_fn cho phép tiêm đồng hồ giả trong test (cùng kiểu dependency injection với
    security.gateway.CLIApprovalBackend.input_fn) — mặc định dùng time.time thật."""

    def __init__(self, max_calls, window_seconds, time_fn=time.time):
        if max_calls < 1:
            raise ValueError("max_calls phải >= 1")
        self._max_calls = max_calls
        self._window_seconds = window_seconds
        self._time_fn = time_fn
        self._calls = {}  # (subject, tool_name) -> deque[timestamp]

    def _evict_old(self, key, now):
        dq = self._calls.setdefault(key, deque())
        cutoff = now - self._window_seconds
        while dq and dq[0] < cutoff:
            dq.popleft()
        return dq

    def check(self, subject, tool_name):
        """Ghi nhận 1 lời gọi (subject, tool_name) tại thời điểm hiện tại. Raise RateLimitExceeded
        NGAY (không ghi nhận lời gọi bị từ chối vào cửa sổ) nếu đã đạt max_calls trong window."""
        now = self._time_fn()
        key = (subject, tool_name)
        dq = self._evict_old(key, now)
        if len(dq) >= self._max_calls:
            raise RateLimitExceeded(
                f"subject={subject!r} tool={tool_name!r} vượt rate limit "
                f"{self._max_calls} lần/{self._window_seconds}s.",
                subject=subject, tool_name=tool_name,
            )
        dq.append(now)


# =====================================================================================
# ANOMALY DETECTION ĐƠN GIẢN
# =====================================================================================

@dataclass
class AnomalyReport:
    file_id: str
    call_count: int
    average_call_count: float
    threshold: float


def detect_anomalies(entries, threshold_multiplier=3.0, min_calls=5):
    """So sánh số tool call của mỗi file_id với TRUNG BÌNH số tool call trên toàn bộ các file_id có
    trong `entries` — file nào gọi vượt quá `average * threshold_multiplier` (và có ít nhất
    `min_calls` lần gọi, để tránh cảnh báo nhiễu khi số lượng còn quá nhỏ so ý nghĩa thống kê) được
    coi là bất thường (vd bị prompt injection dụ gọi tool lặp vô ích, hoặc file input cố ý gây vòng
    lặp). Cần ít nhất 2 file_id khác nhau trong `entries` mới đủ cơ sở so sánh — trả về [] nếu không."""
    counts = {}
    for e in entries:
        file_id = e.get("file_id") if isinstance(e, dict) else e.file_id
        if file_id is None:
            continue
        counts[file_id] = counts.get(file_id, 0) + 1

    if len(counts) < 2:
        return []

    average = mean(counts.values())
    threshold = average * threshold_multiplier

    anomalies = [
        AnomalyReport(file_id=fid, call_count=c, average_call_count=average, threshold=threshold)
        for fid, c in counts.items()
        if c >= min_calls and c > threshold
    ]
    anomalies.sort(key=lambda a: a.call_count, reverse=True)
    return anomalies


# =====================================================================================
# CLI — python -m security.audit --run <id>
# =====================================================================================

def _format_entry(entry):
    ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.get("ts", 0)))
    return (
        f"[{ts_str}] run={entry.get('run_id')} file={entry.get('file_id')} "
        f"tool={entry.get('tool_name')} decision={entry.get('policy_decision')} "
        f"approved_by={entry.get('approved_by')} latency={entry.get('latency_seconds'):.3f}s "
        f"args={entry.get('arguments')} result_hash={entry.get('result_hash')}"
    )


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description="Đọc lại security/audit.py audit trail.")
    parser.add_argument("--run", default=None, help="Chỉ hiển thị entry của run_id này.")
    parser.add_argument("--log-path", default=DEFAULT_AUDIT_LOG_PATH, help="Đường dẫn file audit trail.")
    parser.add_argument(
        "--anomaly-threshold", type=float, default=3.0,
        help="Hệ số nhân trung bình để coi 1 file là bất thường (mặc định 3.0).",
    )
    args = parser.parse_args(argv)

    log = AppendOnlyAuditLog(path=args.log_path)
    entries = log.read_entries(run_id=args.run)

    if not entries:
        scope = f"run_id={args.run}" if args.run else f"log {args.log_path}"
        print(f"Không có entry nào cho {scope}.")
        return 0

    print(f"=== {len(entries)} entry ===")
    for e in entries:
        print(_format_entry(e))

    if args.run is None:
        all_entries = log.read_entries()
        anomalies = detect_anomalies(all_entries, threshold_multiplier=args.anomaly_threshold)
        if anomalies:
            print(f"\n=== Cảnh báo anomaly (ngưỡng x{args.anomaly_threshold}) ===")
            for a in anomalies:
                print(
                    f"  file={a.file_id}: {a.call_count} tool call "
                    f"(trung bình {a.average_call_count:.1f}, ngưỡng {a.threshold:.1f})"
                )

    return 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
