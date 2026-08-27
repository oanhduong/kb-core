"""Orchestrator hai tầng cho ingestion: agent chính điều phối, sub-agent xử lý từng file.

Kiến trúc:
- Agent chính (orchestrator) KHÔNG đọc nội dung file. Với mỗi file, nó spawn một sub-agent —
  một agent.loop.AgentLoop RIÊNG với context sạch (xem _run_subagent_for_file) — xử lý toàn bộ
  file đó từ đầu đến khi ghi xong KBRecord. Sub-agent chỉ trả về một manifest gọn
  (_build_manifest: file_id, source_type, doc_type, record_count, tóm tắt 2 dòng, output path,
  status). Nội dung đầy đủ (chunks, KBRecord.content, tool_calls) ở lại trên đĩa
  (data/output/*.json) và trong bộ nhớ của sub-agent đã kết thúc — KHÔNG BAO GIỜ vào context của
  agent chính. Đây là điểm khác với agent/batch.py cũ: batch.py cũng chạy song song nhiều
  AgentLoop, nhưng không có khái niệm "context agent chính" tách khỏi kết quả từng file.
- Trạng thái chạy (file nào done/blocked/pending, quy ước đã chốt) nằm ở agent/memory.py
  (data/ingest_state.json) — orchestrator đọc file này ở đầu run thay vì giữ trong lịch sử hội
  thoại/RAM. Chạy lại với --resume sẽ bỏ qua file đã done.
- Quy ước (decisions, ví dụ cách đặt store_code) được suy ra từ record của file xử lý xong đầu
  tiên có giá trị, lưu vào state, và truyền vào user_message của sub-agent CÁC FILE SAU (nộp theo
  kiểu rolling window — xem run_orchestrator) để giữ nhất quán dù mỗi sub-agent không có lịch sử
  chung.

Cách dùng: python -m agent.orchestrator [--root data/raw] [--workers 4] [--resume] [--only-failed]
"""
import argparse
import json
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

from agent import memory as agent_memory
from agent.loop import MODEL, SYSTEM_PROMPT, AgentLoop
from ingest import parsers
from security import gateway as security_gateway

logger = logging.getLogger(__name__)

DEFAULT_RAW_DIR = os.path.join("data", "raw")
DEFAULT_WORKERS = 4
DEFAULT_MANIFEST_PATH = os.path.join("data", "output", "manifest.json")


def _discover_files(root):
    files = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.startswith(".") or name.lower().endswith(".md"):
                continue
            files.append(os.path.join(dirpath, name))
    return sorted(files)


# =====================================================================================
# SUB-AGENT (context sạch mỗi file)
# =====================================================================================

def _run_subagent_for_file(file_path, model, max_turns, max_verify_retries, decisions):
    """Spawn một AgentLoop context sạch, xử lý trọn 1 file. decisions là quy ước đã chốt từ các
    file trước trong lần chạy này — đưa vào user_message vì sub-agent không có lịch sử chung với
    sub-agent của file khác."""
    client = anthropic.Anthropic()
    _gateway, gated_tools = security_gateway.build_ingest_tools(client)
    loop = AgentLoop(
        system_prompt=SYSTEM_PROMPT, tools=gated_tools, client=client, model=model,
        max_turns=max_turns, max_verify_retries=max_verify_retries,
    )
    source_type = parsers.detect_file_type(file_path)

    decisions_note = ""
    if decisions:
        decisions_note = "\n\nQuy ước đã chốt từ các file trước trong lần chạy này, áp dụng nhất quán:\n" + "\n".join(
            f"- {k}: {v}" for k, v in decisions.items()
        )

    user_message = (
        f"Hãy ingest file '{file_path}' vào knowledge base: xác định loại file, đọc toàn bộ nội dung có "
        "ý nghĩa nghiệp vụ theo từng phạm vi nhỏ, và ghi từng phần thành KBRecord qua write_kb_record."
        f"{decisions_note}"
    )
    result = loop.run(user_message, file_path)
    return source_type, result


# =====================================================================================
# MANIFEST (thứ duy nhất agent chính nhìn thấy về mỗi file)
# =====================================================================================

def _dominant_doc_type(records):
    if not records:
        return "unknown"
    counts = {}
    for r in records:
        dt = r.get("doc_type", "unknown")
        counts[dt] = counts.get(dt, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _summarize_two_lines(records, final_text):
    doc_types = {}
    for r in records:
        dt = r.get("doc_type", "unknown")
        doc_types[dt] = doc_types.get(dt, 0) + 1
    line1 = f"{len(records)} record — " + ", ".join(f"{dt}:{n}" for dt, n in sorted(doc_types.items()))
    line2 = (final_text or "").strip().splitlines()[0] if final_text else ""
    return f"{line1}\n{line2}".rstrip()


def _build_manifest(file_path, source_type, result):
    return {
        "file_id": file_path,
        "source_type": source_type,
        "doc_type": _dominant_doc_type(result.records),
        "record_count": len(result.records),
        "summary": _summarize_two_lines(result.records, result.final_text),
        "output_paths": sorted(
            os.path.join("data", "output", f"{r.get('record_id')}.json")
            for r in result.records
            if r.get("record_id")
        ),
        "status": result.status,
    }


# =====================================================================================
# DECISIONS (quy ước suy ra từ record, truyền cho sub-agent các file sau)
# =====================================================================================

def _describe_value_shape(sample):
    """Mô tả hình dạng một giá trị mẫu (chữ->A, số->9) để dùng làm mô tả quy ước ngắn gọn, không
    lộ nguyên văn dữ liệu nhạy cảm — vd 'HN-001' -> 'AA-999'."""
    shape = re.sub(r"[^\W\d_]", "A", sample, flags=re.UNICODE)
    shape = re.sub(r"\d", "9", shape)
    return shape


def _infer_store_code_convention(records):
    for r in records:
        code = (r.get("metadata") or {}).get("store_code")
        if isinstance(code, str) and code.strip():
            code = code.strip()
            return f"dạng '{_describe_value_shape(code)}' (ví dụ đã thấy: '{code}')"
    return None


def _update_decisions_from_result(state, decisions, records):
    if "store_code_format" not in decisions:
        convention = _infer_store_code_convention(records)
        if convention:
            decisions["store_code_format"] = convention
            agent_memory.record_decision(state, "store_code_format", convention)


# =====================================================================================
# SO SÁNH CONTEXT AGENT CHÍNH: trước/sau khi tách sub-agent
# =====================================================================================

def _approx_tokens(messages):
    """Ước lượng thô ~4 ký tự/token (không gọi API đếm token thật) — đủ để so sánh TƯƠNG ĐỐI giữa
    hai kiến trúc, không phải con số billing chính xác."""
    text = json.dumps(messages, ensure_ascii=False, default=str)
    return len(text), len(text) // 4


def _log_context_comparison(before, after):
    before_chars, before_tokens = _approx_tokens(before)
    after_chars, after_tokens = _approx_tokens(after)
    reduction_pct = 0.0 if before_tokens == 0 else round((1 - after_tokens / before_tokens) * 100, 1)
    stats = {
        "before_split_chars": before_chars,
        "before_split_tokens_approx": before_tokens,
        "after_split_chars": after_chars,
        "after_split_tokens_approx": after_tokens,
        "reduction_pct": reduction_pct,
    }
    logger.info(
        "context agent chính: trước tách sub-agent ~%d token (%d ký tự), sau tách ~%d token "
        "(%d ký tự) — giảm %.1f%%",
        before_tokens, before_chars, after_tokens, after_chars, reduction_pct,
    )
    return stats


# =====================================================================================
# ORCHESTRATOR
# =====================================================================================

def run_orchestrator(
    root=DEFAULT_RAW_DIR, workers=DEFAULT_WORKERS, model=MODEL, max_turns=15,
    max_verify_retries=3, resume=False, only_failed=False, state_path=agent_memory.STATE_PATH,
):
    state = agent_memory.load_state(state_path) if (resume or only_failed) else agent_memory.new_state()

    all_files = _discover_files(root)
    if only_failed:
        targets = [f for f in all_files if f in state["files_blocked"]]
    elif resume:
        targets = [f for f in all_files if not agent_memory.is_done(state, f)]
    else:
        targets = all_files

    agent_memory.set_pending(state, targets)
    agent_memory.save_state(state, state_path)

    decisions = dict(state["decisions"])
    # Context "sau khi tách": mỗi file chỉ đóng góp lời gọi + manifest gọn — đúng những gì agent
    # chính thực sự nhìn thấy trong kiến trúc hai tầng.
    main_context_after = []
    # Context "trước khi tách" (giả lập kiến trúc một tầng cũ): nếu agent chính không spawn
    # sub-agent riêng mà tự giữ toàn bộ record + tool_calls của từng file trong context của nó.
    main_context_before = []

    entries = []
    remaining = list(targets)
    in_flight = {}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        def submit_next():
            if remaining:
                f = remaining.pop(0)
                fut = executor.submit(
                    _run_subagent_for_file, f, model, max_turns, max_verify_retries, dict(decisions),
                )
                in_flight[fut] = f

        for _ in range(min(max(1, workers), len(remaining))):
            submit_next()

        while in_flight:
            fut = next(as_completed(list(in_flight.keys())))
            file_path = in_flight.pop(fut)
            source_type, result = fut.result()

            manifest = _build_manifest(file_path, source_type, result)

            main_context_after.append({"role": "user", "content": f"ingest_file('{file_path}')"})
            main_context_after.append({"role": "tool_result", "content": manifest})

            main_context_before.append({"role": "user", "content": f"ingest_file('{file_path}')"})
            main_context_before.append({
                "role": "tool_result",
                "content": {
                    "records": result.records,
                    "tool_calls": [
                        {
                            "tool_name": tc.tool_name, "arguments": tc.arguments,
                            "output": tc.output, "is_error": tc.is_error,
                        }
                        for tc in result.tool_calls
                    ],
                    "final_text": result.final_text,
                },
            })

            if result.status == "OK":
                agent_memory.mark_done(state, file_path, manifest)
                _update_decisions_from_result(state, decisions, result.records)
            else:
                # status=ERROR (ParserError, xem agent/loop.py) không set blocked_reason —
                # message thật nằm ở final_text, fallback về đó để lý do không bị rỗng trong state.
                reason = result.blocked_reason or result.final_text
                agent_memory.mark_blocked(state, file_path, reason, result.issues)

            agent_memory.save_state(state, state_path)
            entries.append({**manifest, "verify_retry_count": result.verify_retry_count})

            submit_next()

    order = {f: i for i, f in enumerate(targets)}
    entries.sort(key=lambda e: order[e["file_id"]])

    context_stats = _log_context_comparison(main_context_before, main_context_after)
    return entries, context_stats


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        description="Orchestrator hai tầng: agent chính điều phối, sub-agent xử lý từng file.",
    )
    parser.add_argument("--root", default=DEFAULT_RAW_DIR, help="Thư mục chứa file thô cần ingest.")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Số sub-agent chạy song song.")
    parser.add_argument("--model", default=MODEL, help="Model Claude dùng cho từng sub-agent.")
    parser.add_argument("--max-turns", type=int, default=15, help="Hard cap số turn cho mỗi sub-agent.")
    parser.add_argument("--max-verify-retries", type=int, default=3, help="Số lần retry theo verifier trước khi BLOCKED.")
    parser.add_argument("--out", default=DEFAULT_MANIFEST_PATH, help="Đường dẫn ghi manifest tổng hợp.")
    parser.add_argument(
        "--resume", action="store_true",
        help="Bỏ qua file đã có trong files_done của data/ingest_state.json (chạy tiếp phần dở dang).",
    )
    parser.add_argument(
        "--only-failed", action="store_true",
        help="Chỉ chạy lại các file đang ở files_blocked của lần chạy trước.",
    )
    args = parser.parse_args(argv)

    if args.resume and args.only_failed:
        print("Không dùng đồng thời --resume và --only-failed (only-failed là tập con của resume).", file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    entries, context_stats = run_orchestrator(
        root=args.root, workers=args.workers, model=args.model, max_turns=args.max_turns,
        max_verify_retries=args.max_verify_retries, resume=args.resume, only_failed=args.only_failed,
    )

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"model": args.model, "files": entries, "context_stats": context_stats}, f, ensure_ascii=False, indent=2)

    print(f"\nĐã xử lý {len(entries)} file, ghi manifest vào {args.out}")
    print(
        f"context agent chính: trước tách sub-agent ~{context_stats['before_split_tokens_approx']} token, "
        f"sau tách ~{context_stats['after_split_tokens_approx']} token "
        f"(giảm {context_stats['reduction_pct']}%)"
    )

    not_ok = [e for e in entries if e["status"] != "OK"]
    if not_ok:
        print(f"\nFile chưa xong (xem {agent_memory.STATE_PATH} để biết lý do, dùng --only-failed để chạy lại):")
        for e in not_ok:
            print(f"  - {e['file_id']}: status={e['status']}")

    return 1 if not_ok else 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
