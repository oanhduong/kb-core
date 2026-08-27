"""Chạy AgentLoop song song trên toàn bộ file trong data/raw (4 worker mặc định).

Cách dùng: python -m agent.batch [--root data/raw] [--workers 4] [--out data/output/run_summary.json]

Mỗi file chạy trong một AgentLoop độc lập (client Anthropic riêng — client không thread-safe để
share). Kết quả từng file ghi vào run_summary.json (status, turn_count, retry_count, tokens, cost,
wall time, issues), kèm bảng tổng kết in ra cuối run.
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

from agent.loop import MODEL, SYSTEM_PROMPT, AgentLoop
from security import gateway as security_gateway

DEFAULT_RAW_DIR = os.path.join("data", "raw")
DEFAULT_WORKERS = 4
DEFAULT_SUMMARY_PATH = os.path.join("data", "output", "run_summary.json")

# USD / 1 triệu token — khớp bảng giá claude-sonnet-4-6 dùng trong agent/loop.py.MODEL.
# Cập nhật PRICING nếu đổi model, batch.py không tự tra giá.
PRICING = {
    "claude-sonnet-4-6": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
}


def _discover_files(root):
    """Liệt kê mọi file thô cần ingest trong root — bỏ file ẩn (.DS_Store, ...) và tài liệu .md."""
    files = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.startswith(".") or name.lower().endswith(".md"):
                continue
            files.append(os.path.join(dirpath, name))
    return sorted(files)


def _cost_usd(model, input_tokens, output_tokens):
    price = PRICING.get(model)
    if price is None:
        return None
    return (
        input_tokens / 1_000_000 * price["input_per_mtok"]
        + output_tokens / 1_000_000 * price["output_per_mtok"]
    )


def _run_one_file(file_path, model, max_turns, max_verify_retries):
    """Chạy 1 AgentLoop độc lập cho 1 file — client riêng vì anthropic.Anthropic() không thread-safe
    để share giữa các worker chạy song song."""
    client = anthropic.Anthropic()
    _gateway, gated_tools = security_gateway.build_ingest_tools(client)
    loop = AgentLoop(
        system_prompt=SYSTEM_PROMPT, tools=gated_tools, client=client, model=model,
        max_turns=max_turns, max_verify_retries=max_verify_retries,
    )
    user_message = (
        f"Hãy ingest file '{file_path}' vào knowledge base: xác định loại file, đọc toàn bộ nội dung có "
        "ý nghĩa nghiệp vụ theo từng phạm vi nhỏ, và ghi từng phần thành KBRecord qua write_kb_record."
    )

    start = time.monotonic()
    try:
        result = loop.run(user_message, file_path)
    except Exception as e:
        wall_time = time.monotonic() - start
        return {
            "file": file_path,
            "status": "ERROR",
            "turn_count": 0,
            "retry_count": 0,
            "tokens": {"input": 0, "output": 0},
            "cost_usd": 0.0,
            "wall_time_seconds": round(wall_time, 2),
            "record_count": 0,
            "blocked_reason": f"Exception ngoài AgentLoop.run (lỗi hạ tầng, không phải verifier/parser): {e}",
            "issues": [],
        }
    wall_time = time.monotonic() - start

    return {
        "file": file_path,
        "status": result.status,
        "turn_count": result.turn_count,
        "retry_count": result.verify_retry_count,
        "tokens": {"input": result.total_input_tokens, "output": result.total_output_tokens},
        "cost_usd": _cost_usd(model, result.total_input_tokens, result.total_output_tokens),
        "wall_time_seconds": round(wall_time, 2),
        "record_count": len(result.records),
        "blocked_reason": result.blocked_reason,
        "issues": result.issues,
    }


def run_batch(root=DEFAULT_RAW_DIR, workers=DEFAULT_WORKERS, model=MODEL, max_turns=15, max_verify_retries=3):
    files = _discover_files(root)
    entries = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_run_one_file, f, model, max_turns, max_verify_retries): f
            for f in files
        }
        for future in as_completed(futures):
            entries.append(future.result())

    order = {f: i for i, f in enumerate(files)}
    entries.sort(key=lambda e: order[e["file"]])
    return entries


def _print_summary_table(entries):
    header = f"{'file':<55} {'status':<8} {'turns':>5} {'retries':>7} {'records':>7} {'tokens':>12} {'cost($)':>8} {'wall(s)':>8}"
    print(header)
    print("-" * len(header))
    for e in entries:
        tokens = e["tokens"]["input"] + e["tokens"]["output"]
        cost = e["cost_usd"] if e["cost_usd"] is not None else float("nan")
        print(
            f"{e['file']:<55} {e['status']:<8} {e['turn_count']:>5} {e['retry_count']:>7} "
            f"{e['record_count']:>7} {tokens:>12} {cost:>8.4f} {e['wall_time_seconds']:>8.2f}"
        )
    print("-" * len(header))

    total_files = len(entries)
    by_status = {}
    for e in entries:
        by_status[e["status"]] = by_status.get(e["status"], 0) + 1
    total_tokens = sum(e["tokens"]["input"] + e["tokens"]["output"] for e in entries)
    total_cost = sum(e["cost_usd"] or 0.0 for e in entries)
    total_records = sum(e["record_count"] for e in entries)
    total_wall = sum(e["wall_time_seconds"] for e in entries)

    status_str = ", ".join(f"{status}={count}" for status, count in sorted(by_status.items()))
    print(f"Tổng: {total_files} file ({status_str})")
    print(f"records={total_records} tokens={total_tokens} cost=${total_cost:.4f} tổng wall_time={total_wall:.2f}s")

    blocked = [e for e in entries if e["status"] == "BLOCKED"]
    if blocked:
        print("\nFile BLOCKED (model không tự xử lý xong trong số lần retry cho phép):")
        for e in blocked:
            print(f"  - {e['file']}: {e['blocked_reason']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Chạy AgentLoop song song trên toàn bộ data/raw.")
    parser.add_argument("--root", default=DEFAULT_RAW_DIR, help="Thư mục chứa file thô cần ingest.")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Số worker chạy song song.")
    parser.add_argument("--model", default=MODEL, help="Model Claude dùng cho AgentLoop.")
    parser.add_argument("--max-turns", type=int, default=15, help="Hard cap số turn cho mỗi file.")
    parser.add_argument("--max-verify-retries", type=int, default=3, help="Số lần retry theo verifier trước khi BLOCKED.")
    parser.add_argument("--out", default=DEFAULT_SUMMARY_PATH, help="Đường dẫn ghi run_summary.json.")
    args = parser.parse_args(argv)

    entries = run_batch(
        root=args.root, workers=args.workers, model=args.model,
        max_turns=args.max_turns, max_verify_retries=args.max_verify_retries,
    )

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"model": args.model, "runs": entries}, f, ensure_ascii=False, indent=2)

    _print_summary_table(entries)
    print(f"\nĐã ghi tóm tắt vào {args.out}")

    return 1 if any(e["status"] in ("ERROR", "BLOCKED") for e in entries) else 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
