"""Phase 1 benchmark: so sánh 4 cấu hình agent trên cùng corpus data/raw.

Bốn cấu hình (xem CONFIGS bên dưới):
    A. raw + prompt tối giản, không verifier — baseline thấp nhất.
    B. raw + prompt chi tiết (giống agent/loop.py.SYSTEM_PROMPT), không verifier — tách ảnh hưởng
       của PROMPT ra khỏi ảnh hưởng của ENFORCEMENT. Nếu B gần bằng C thì cải thiện của harness đến
       từ prompt, không phải từ verifier/force-loop.
    C. harness đầy đủ (agent.loop.AgentLoop mặc định) — verifier + force loop + bounded retry.
    D. harness đầy đủ nhưng verifier bị tắt (agent.loop.AgentLoop(enable_verifier=False)) — vẫn giữ
       nguyên mọi cơ chế khác của harness (cảnh báo gọi tool lặp lại, max_turns) để tách riêng đóng
       góp của CHÍNH verifier, so với D.

"raw" (A, B) dùng RawLoop bên dưới — một vòng lặp tool-calling tối giản: không có verifier, không
có cảnh báo gọi tool lặp lại, chỉ dừng khi model tự dừng (hoặc chạm max_turns). Đây là baseline
"không có kỹ thuật enforcement nào" để đối chiếu với harness thật.

Chấm điểm: DÙ cấu hình nào đang chạy, sau khi vòng lặp kết thúc, kết quả cuối (records + toàn bộ
tool_calls đã thực hiện trong lượt) được chấm lại bằng CHÍNH agent.verifier.verify() — cùng một hàm,
cùng ngưỡng, cho cả 4 cấu hình — để "pass" có nghĩa giống nhau bất kể agent có tự verify trong lúc
chạy hay không. Với C, việc chấm lại này trùng với quyết định nội bộ của AgentLoop (cùng input).

Lưu ý về cost: extract_image_content (agent/tools.py) tự gọi client.messages.create() cho vision
nhưng KHÔNG cộng dồn response.usage vào tổng token của AgentLoop — đây là khoảng trống có sẵn trong
pipeline, không phải bug của script này. cost_usd/tokens report ở đây vì vậy là CẬN DƯỚI (undercount)
cho các file có gọi extract_image_content (image, pdf scan trang, ...). Xem ghi chú trong phase1.md.

Cách dùng:
    python -m bench.run_phase1                              # full: 25 file x 4 config x 3 rep
    python -m bench.run_phase1 --reps 1 --configs A,C        # chạy nhanh, ít cấu hình hơn
    python -m bench.run_phase1 --files data/raw/xlsx/01_bang_gia_sku.xlsx --reps 1
    python -m bench.run_phase1 --resume                      # tiếp tục từ bench/phase1_runs.jsonl cũ
    python -m bench.run_phase1 --from-jsonl bench/phase1_runs.jsonl --report-only
"""
import argparse
import json
import os
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import anthropic

from agent import loop as agent_loop
from agent import tools as agent_tools
from agent import verifier as agent_verifier
from agent.batch import PRICING, _cost_usd, _discover_files
from agent.loop import MAX_TOKENS, MODEL, AgentLoop
from ingest.invariants import ParserError

DEFAULT_ROOT = os.path.join("data", "raw")
DEFAULT_OUT_JSONL = os.path.join("bench", "phase1_runs.jsonl")
DEFAULT_OUT_MD = os.path.join("bench", "phase1.md")
DEFAULT_REPS = 3
DEFAULT_MAX_TURNS = 15
DEFAULT_MAX_VERIFY_RETRIES = 3

USER_MESSAGE_TEMPLATE = (
    "Hãy ingest file '{file_path}' vào knowledge base: xác định loại file, đọc toàn bộ nội dung có "
    "ý nghĩa nghiệp vụ theo từng phạm vi nhỏ, và ghi từng phần thành KBRecord qua write_kb_record."
)

# Prompt "tối giản" cho cấu hình A: chỉ nói model phải làm gì ở mức cao nhất, KHÔNG có bất kỳ hướng
# dẫn nào trùng với điều verifier sẽ kiểm tra (scope theo sheet/trang, routing OCR, chuẩn hoá
# metadata theo doc_type, coverage audio/video, xung đột giá...). Đây chính là phần bị "cắt" so với
# SYSTEM_PROMPT của harness — để đo ảnh hưởng thuần của prompt trong cấu hình B.
MINIMAL_SYSTEM_PROMPT = """Bạn là agent ingest tài liệu cho RetailKB Copilot.

Nhiệm vụ: đọc file được giao, trích xuất nội dung, và gọi write_kb_record để ghi lại các đoạn nội
dung quan trọng. Dùng detect_file_type để biết loại file, rồi chọn tool đọc phù hợp (parse_document,
extract_image_content, hoặc transcribe_media). Khi đã ghi xong, trả lời tóm tắt ngắn gọn rồi dừng."""


# =====================================================================================
# RawLoop — vòng lặp tool-calling tối giản, không verifier, không cảnh báo gọi tool lặp lại
# =====================================================================================

class RawLoop(AgentLoop):
    """Baseline "raw": kế thừa AgentLoop chỉ để dùng lại _call_tool/_gather_records (cơ chế gọi tool
    cơ bản, không phải enforcement), nhưng override hẳn run() để bỏ verifier VÀ bỏ luôn cảnh báo gọi
    tool lặp lại (REDUNDANT_CALL_THRESHOLD) — hai thứ đó là phần "harness", không phải "raw".
    Chỉ còn lại: gọi model -> gọi tool -> trả kết quả -> lặp tới khi model tự dừng hoặc chạm max_turns.
    """

    def run(self, user_message, file_path):
        messages = [{"role": "user", "content": user_message}]
        tool_calls = []
        total_in = 0
        total_out = 0
        final_text = ""
        turn = 0

        while True:
            turn += 1
            if turn > self.max_turns:
                return agent_loop.TurnResult(
                    final_text=final_text,
                    turn_count=turn - 1,
                    total_input_tokens=total_in,
                    total_output_tokens=total_out,
                    tool_calls=tool_calls,
                    stop_reason="max_turns",
                    status="MAX_TURNS",
                    blocked_reason=f"Đạt hard cap max_turns={self.max_turns} cho file {file_path}.",
                    records=self._gather_records(tool_calls),
                )

            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self.system_prompt,
                tools=self._api_tools,
                messages=messages,
            )
            total_in += response.usage.input_tokens
            total_out += response.usage.output_tokens

            text = "\n".join(b.text for b in response.content if b.type == "text")
            if text:
                final_text = text

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

            if not tool_use_blocks:
                return agent_loop.TurnResult(
                    final_text=final_text,
                    turn_count=turn,
                    total_input_tokens=total_in,
                    total_output_tokens=total_out,
                    tool_calls=tool_calls,
                    stop_reason=response.stop_reason,
                    status="OK",
                    records=self._gather_records(tool_calls),
                )

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for block in tool_use_blocks:
                try:
                    output = self._call_tool(block.name, block.input)
                except ParserError as e:
                    tool_calls.append(agent_loop.ToolCallRecord(
                        turn=turn, tool_name=block.name, arguments=block.input, is_error=True, output=None,
                        input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens,
                        latency_seconds=0.0,
                    ))
                    return agent_loop.TurnResult(
                        final_text=f"Dừng xử lý do lỗi invariant tầng code (bug ở parser, không retry): {e}",
                        turn_count=turn,
                        total_input_tokens=total_in,
                        total_output_tokens=total_out,
                        tool_calls=tool_calls,
                        stop_reason="error",
                        status="ERROR",
                        records=self._gather_records(tool_calls),
                    )
                except Exception as e:
                    is_error = True
                    output = None
                    content = self._actionable_error_message(block.name, e)
                else:
                    is_error = False
                    content = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)

                tool_calls.append(agent_loop.ToolCallRecord(
                    turn=turn, tool_name=block.name, arguments=block.input, is_error=is_error, output=output,
                    input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens,
                    latency_seconds=0.0,
                ))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_error,
                })

            messages.append({"role": "user", "content": tool_results})


# =====================================================================================
# CẤU HÌNH
# =====================================================================================

CONFIGS = {
    "A": {"label": "A: raw + prompt tối giản (không verifier)", "loop": "raw", "prompt": "minimal"},
    "B": {"label": "B: raw + prompt chi tiết (không verifier)", "loop": "raw", "prompt": "detailed"},
    "C": {"label": "C: harness đầy đủ (verifier + force loop + bounded retry)", "loop": "harness",
          "prompt": "detailed", "enable_verifier": True},
    "D": {"label": "D: harness bỏ verifier (chỉ giữ bounded retry)", "loop": "harness",
          "prompt": "detailed", "enable_verifier": False},
}
CONFIG_ORDER = ["A", "B", "C", "D"]


def _build_loop(config_key, client, model, max_turns, max_verify_retries):
    cfg = CONFIGS[config_key]
    registry = agent_tools.build_registry(client)
    system_prompt = MINIMAL_SYSTEM_PROMPT if cfg["prompt"] == "minimal" else agent_loop.SYSTEM_PROMPT

    if cfg["loop"] == "raw":
        return RawLoop(system_prompt=system_prompt, tools=registry, client=client, model=model, max_turns=max_turns)

    return AgentLoop(
        system_prompt=system_prompt, tools=registry, client=client, model=model,
        max_turns=max_turns, max_verify_retries=max_verify_retries, enable_verifier=cfg["enable_verifier"],
    )


def _source_type(file_path):
    return os.path.basename(os.path.dirname(file_path))


def _score_run(file_path, result):
    """Chấm lại bằng chính agent.verifier.verify() — giống nhau cho cả 4 cấu hình.

    ERROR (ParserError) luôn tính là fail thẳng, không chấm — đó là bug tầng parser, không phải kết
    quả agent nên đánh giá qua verifier (scope/routing/...) vốn được thiết kế cho quyết định của model.
    """
    if result.status == "ERROR":
        return False, [{"check": "parser_error", "message": result.final_text}]
    verify_result = agent_verifier.verify(file_path, result.records, result.tool_calls)
    return verify_result.passed, [{"check": i.check, "message": i.message} for i in verify_result.issues]


# =====================================================================================
# THỰC THI 1 RUN
# =====================================================================================

def _run_single(file_path, config_key, rep, model, max_turns, max_verify_retries):
    client = anthropic.Anthropic()
    loop = _build_loop(config_key, client, model, max_turns, max_verify_retries)
    user_message = USER_MESSAGE_TEMPLATE.format(file_path=file_path)

    start = time.monotonic()
    try:
        result = loop.run(user_message, file_path)
    except Exception as e:
        wall_time = time.monotonic() - start
        return {
            "file": file_path, "source_type": _source_type(file_path), "config": config_key, "rep": rep,
            "status": "ERROR", "pass": False,
            "issues": [{"check": "infra_exception", "message": str(e)}],
            "turn_count": 0, "tokens_input": 0, "tokens_output": 0, "tokens_total": 0,
            "cost_usd": 0.0, "wall_time_seconds": round(wall_time, 2), "record_count": 0,
        }
    wall_time = time.monotonic() - start

    passed, issues = _score_run(file_path, result)
    tokens_total = result.total_input_tokens + result.total_output_tokens
    return {
        "file": file_path,
        "source_type": _source_type(file_path),
        "config": config_key,
        "rep": rep,
        "status": result.status,
        "pass": passed,
        "issues": issues,
        "turn_count": result.turn_count,
        "tokens_input": result.total_input_tokens,
        "tokens_output": result.total_output_tokens,
        "tokens_total": tokens_total,
        "cost_usd": _cost_usd(model, result.total_input_tokens, result.total_output_tokens) or 0.0,
        "wall_time_seconds": round(wall_time, 2),
        "record_count": len(result.records),
    }


# =====================================================================================
# CHẠY BATCH + JOURNAL (jsonl) ĐỂ RESUME ĐƯỢC KHI BỊ NGẮT GIỮA CHỪNG
# =====================================================================================

def _load_jsonl(path):
    if not os.path.isfile(path):
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def run_all(files, configs, reps, model, max_turns, max_verify_retries, workers, out_jsonl, resume):
    done_keys = set()
    existing = []
    if resume:
        existing = _load_jsonl(out_jsonl)
        done_keys = {(e["file"], e["config"], e["rep"]) for e in existing}
        print(f"[resume] {len(done_keys)} run đã có trong {out_jsonl}, bỏ qua.")
    else:
        os.makedirs(os.path.dirname(out_jsonl) or ".", exist_ok=True)
        open(out_jsonl, "w", encoding="utf-8").close()  # bắt đầu mới, ghi đè

    tasks = [
        (f, c, r)
        for c in configs
        for f in files
        for r in range(reps)
        if (f, c, r) not in done_keys
    ]
    total = len(tasks) + len(done_keys)
    print(f"[plan] {len(files)} file x {len(configs)} config x {reps} rep = {total} run "
          f"({len(tasks)} cần chạy, {len(done_keys)} đã có).")

    write_lock = threading.Lock()
    results = list(existing)

    def _append(entry):
        with write_lock:
            with open(out_jsonl, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_run_single, f, c, r, model, max_turns, max_verify_retries): (f, c, r)
            for f, c, r in tasks
        }
        for future in as_completed(futures):
            f, c, r = futures[future]
            entry = future.result()
            _append(entry)
            results.append(entry)
            completed += 1
            print(
                f"[{completed}/{len(tasks)}] {c} rep={r} {f} -> status={entry['status']} "
                f"pass={entry['pass']} tokens={entry['tokens_total']} wall={entry['wall_time_seconds']}s"
            )

    return results


# =====================================================================================
# THỐNG KÊ
# =====================================================================================

def _mean(xs):
    return statistics.mean(xs) if xs else float("nan")


def _std(xs):
    return statistics.stdev(xs) if len(xs) > 1 else 0.0


def _aggregate(runs):
    passes = [1.0 if r["pass"] else 0.0 for r in runs]
    tokens = [r["tokens_total"] for r in runs]
    costs = [r["cost_usd"] for r in runs]
    walls = [r["wall_time_seconds"] for r in runs]
    return {
        "n": len(runs),
        "pass_rate_mean": _mean(passes), "pass_rate_std": _std(passes),
        "tokens_mean": _mean(tokens), "tokens_std": _std(tokens),
        "cost_mean": _mean(costs), "cost_std": _std(costs),
        "wall_mean": _mean(walls), "wall_std": _std(walls),
        "status_counts": dict(Counter(r["status"] for r in runs)),
    }


def _aggregate_by(runs, keyfunc):
    groups = defaultdict(list)
    for r in runs:
        groups[keyfunc(r)].append(r)
    return {k: _aggregate(v) for k, v in groups.items()}


def _top_issue_checks(runs, top_n=5):
    counter = Counter()
    for r in runs:
        for issue in r["issues"]:
            counter[issue["check"]] += 1
    return counter.most_common(top_n)


# =====================================================================================
# BÁO CÁO bench/phase1.md
# =====================================================================================

def _fmt_pct(x):
    return "n/a" if x != x else f"{x * 100:.1f}%"


def _fmt_num(x, nd=0):
    return "n/a" if x != x else f"{x:,.{nd}f}"


def _fmt_money(x):
    return "n/a" if x != x else f"${x:.4f}"


def generate_report(runs, out_path, model, max_turns, max_verify_retries, reps, generated_at):
    configs_present = [c for c in CONFIG_ORDER if any(r["config"] == c for r in runs)]
    overall = _aggregate_by(runs, lambda r: r["config"])
    by_source_config = _aggregate_by(runs, lambda r: (r["source_type"], r["config"]))
    by_file_config = _aggregate_by(runs, lambda r: (r["file"], r["config"]))
    source_types = sorted({r["source_type"] for r in runs})
    files_sorted = sorted({r["file"] for r in runs})

    lines = []
    lines.append("# Phase 1 benchmark — raw vs. harness")
    lines.append("")
    lines.append(f"Sinh lúc {generated_at} · model=`{model}` · max_turns={max_turns} · "
                  f"max_verify_retries={max_verify_retries} · reps/file={reps} · tổng run={len(runs)}")
    lines.append("")
    lines.append("## Cấu hình")
    lines.append("")
    for c in configs_present:
        lines.append(f"- **{CONFIGS[c]['label']}**")
    lines.append("")
    lines.append(
        "Chấm điểm: mọi cấu hình được chấm lại bằng CÙNG một `agent.verifier.verify()` trên "
        "records + tool_calls cuối cùng của lượt chạy, bất kể cấu hình đó có tự verify trong lúc "
        "chạy hay không (\"pass\" nghĩa giống nhau ở cả 4 cột)."
    )
    lines.append("")
    lines.append(
        "**Giới hạn cần biết:** `extract_image_content` (agent/tools.py) tự gọi Claude vision "
        "nhưng không cộng dồn token usage của lần gọi đó vào tổng token/cost của AgentLoop — "
        "tokens/cost ở bảng dưới vì vậy là CẬN DƯỚI cho các file cần OCR/vision (image, pdf scan)."
    )
    lines.append("")

    # ----- Bảng 1: tổng quan theo config -----
    lines.append("## 1. Tổng quan theo cấu hình")
    lines.append("")
    lines.append("| Config | n | Pass rate (mean ± std) | Token TB (mean ± std) | Cost TB (mean ± std) | Wall time TB (mean ± std, s) | Status |")
    lines.append("|---|---:|---|---|---|---|---|")
    for c in configs_present:
        a = overall[c]
        status_str = ", ".join(f"{k}={v}" for k, v in sorted(a["status_counts"].items()))
        lines.append(
            f"| {c} | {a['n']} | {_fmt_pct(a['pass_rate_mean'])} ± {_fmt_pct(a['pass_rate_std'])} "
            f"| {_fmt_num(a['tokens_mean'])} ± {_fmt_num(a['tokens_std'])} "
            f"| {_fmt_money(a['cost_mean'])} ± {_fmt_money(a['cost_std'])} "
            f"| {_fmt_num(a['wall_mean'], 1)} ± {_fmt_num(a['wall_std'], 1)} "
            f"| {status_str} |"
        )
    lines.append("")

    # ----- Bảng 2: chi tiết theo source_type -----
    lines.append("## 2. Chi tiết theo source_type")
    lines.append("")
    lines.append("| source_type | Config | n | Pass rate (mean ± std) | Token TB | Cost TB | Wall time TB (s) |")
    lines.append("|---|---|---:|---|---|---|---|")
    for st in source_types:
        for c in configs_present:
            key = (st, c)
            if key not in by_source_config:
                continue
            a = by_source_config[key]
            lines.append(
                f"| {st} | {c} | {a['n']} | {_fmt_pct(a['pass_rate_mean'])} ± {_fmt_pct(a['pass_rate_std'])} "
                f"| {_fmt_num(a['tokens_mean'])} | {_fmt_money(a['cost_mean'])} | {_fmt_num(a['wall_mean'], 1)} |"
            )
    lines.append("")

    # ----- Nguyên nhân fail phổ biến theo config -----
    lines.append("## 3. Nguyên nhân fail phổ biến theo cấu hình")
    lines.append("")
    for c in configs_present:
        c_runs = [r for r in runs if r["config"] == c]
        fails = [r for r in c_runs if not r["pass"]]
        lines.append(f"**{c}** — {len(fails)}/{len(c_runs)} run fail.")
        top = _top_issue_checks(fails)
        if top:
            for check, count in top:
                lines.append(f"  - `{check}`: {count} lần")
        lines.append("")

    # ----- Ablation C vs D -----
    lines.append("## 4. Ablation: C (verifier bật) vs. D (verifier tắt)")
    lines.append("")
    if "C" in overall and "D" in overall:
        c, d = overall["C"], overall["D"]
        pass_delta = c["pass_rate_mean"] - d["pass_rate_mean"]
        tok_delta = c["tokens_mean"] - d["tokens_mean"]
        cost_delta = c["cost_mean"] - d["cost_mean"]
        wall_delta = c["wall_mean"] - d["wall_mean"]
        lines.append(
            f"- Pass rate: C={_fmt_pct(c['pass_rate_mean'])} vs D={_fmt_pct(d['pass_rate_mean'])} "
            f"(chênh lệch {pass_delta * 100:+.1f} điểm phần trăm)."
        )
        lines.append(
            f"- Token TB: C={_fmt_num(c['tokens_mean'])} vs D={_fmt_num(d['tokens_mean'])} "
            f"(chênh lệch {tok_delta:+,.0f})."
        )
        lines.append(
            f"- Cost TB: C={_fmt_money(c['cost_mean'])} vs D={_fmt_money(d['cost_mean'])} "
            f"(chênh lệch {cost_delta:+.4f})."
        )
        lines.append(
            f"- Wall time TB: C={_fmt_num(c['wall_mean'], 1)}s vs D={_fmt_num(d['wall_mean'], 1)}s "
            f"(chênh lệch {wall_delta:+.1f}s)."
        )
        lines.append("")
        if pass_delta >= 0.05:
            lines.append(
                f"→ Verifier (force loop) đóng góp thật: +{pass_delta * 100:.1f} điểm phần trăm pass "
                f"rate so với chỉ giữ bounded retry, đổi lấy thêm ~{tok_delta:,.0f} token/run và "
                f"~{wall_delta:.1f}s/run."
            )
        elif pass_delta <= -0.02:
            lines.append(
                "→ KHÔNG TÔ HỒNG: D pass rate cao hơn hoặc bằng C — verifier hiện tại không cải "
                "thiện (thậm chí có thể đang gây stagnation/BLOCKED oan). Cần xem lại ngưỡng hoặc "
                "logic verifier trước khi coi force-loop là có giá trị."
            )
        else:
            lines.append(
                "→ KHÔNG TÔ HỒNG: chênh lệch pass rate giữa C và D nằm trong biên nhiễu (< 5 điểm "
                "phần trăm) trên cỡ mẫu này — chưa đủ bằng chứng để khẳng định verifier tạo khác biệt "
                "thật (cần tăng số rep hoặc số file để giảm nhiễu trước khi kết luận)."
            )
    else:
        lines.append("Thiếu dữ liệu cấu hình C hoặc D — không tính được ablation.")
    lines.append("")

    # ----- Tách ảnh hưởng prompt (B) khỏi enforcement (C) -----
    lines.append("## 5. Tách ảnh hưởng prompt (B) khỏi enforcement (C)")
    lines.append("")
    if "B" in overall and "C" in overall:
        b, c = overall["B"], overall["C"]
        pass_delta_bc = c["pass_rate_mean"] - b["pass_rate_mean"]
        lines.append(
            f"- Pass rate: A={_fmt_pct(overall.get('A', {}).get('pass_rate_mean', float('nan')))}, "
            f"B={_fmt_pct(b['pass_rate_mean'])}, C={_fmt_pct(c['pass_rate_mean'])}."
        )
        if abs(pass_delta_bc) < 0.05:
            lines.append(
                "→ B gần bằng C (chênh < 5 điểm phần trăm): cải thiện của harness so với raw đến "
                "chủ yếu từ PROMPT chi tiết, không phải từ verifier/force-loop. Bảng mục 4 (C vs D) "
                "xác nhận thêm điều này nếu delta ở đó cũng nhỏ."
            )
        else:
            lines.append(
                f"→ B kém hơn C {pass_delta_bc * 100:.1f} điểm phần trăm: riêng prompt chi tiết KHÔNG "
                "đủ để đạt pass rate của harness — phần chênh lệch này đến từ enforcement "
                "(verifier + force loop), không phải prompt."
            )
    else:
        lines.append("Thiếu dữ liệu cấu hình B hoặc C — không tách được ảnh hưởng prompt/enforcement.")
    lines.append("")

    # ----- Phụ lục: chi tiết theo file -----
    lines.append("## Phụ lục: chi tiết theo file")
    lines.append("")
    lines.append("| file | Config | n | Pass rate | Token TB | Cost TB | Wall time TB (s) |")
    lines.append("|---|---|---:|---|---|---|---|")
    for f in files_sorted:
        for c in configs_present:
            key = (f, c)
            if key not in by_file_config:
                continue
            a = by_file_config[key]
            lines.append(
                f"| {f} | {c} | {a['n']} | {_fmt_pct(a['pass_rate_mean'])} "
                f"| {_fmt_num(a['tokens_mean'])} | {_fmt_money(a['cost_mean'])} | {_fmt_num(a['wall_mean'], 1)} |"
            )
    lines.append("")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# =====================================================================================
# CLI
# =====================================================================================

def main(argv=None):
    parser = argparse.ArgumentParser(description="Phase 1 benchmark: raw vs. harness trên data/raw.")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="Thư mục corpus (mặc định data/raw).")
    parser.add_argument("--files", default=None, help="Danh sách file cụ thể, phân cách bởi dấu phẩy (bỏ qua --root).")
    parser.add_argument("--configs", default=",".join(CONFIG_ORDER), help="Danh sách config, phân cách bởi dấu phẩy (vd A,C).")
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS, help="Số lần chạy lặp lại mỗi (file, config).")
    parser.add_argument("--model", default=MODEL, help="Model Claude dùng cho mọi cấu hình.")
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS, help="Hard cap số turn mỗi run.")
    parser.add_argument("--max-verify-retries", type=int, default=DEFAULT_MAX_VERIFY_RETRIES, help="Chỉ áp dụng cho config C.")
    parser.add_argument("--workers", type=int, default=4, help="Số worker chạy song song.")
    parser.add_argument("--out-jsonl", default=DEFAULT_OUT_JSONL, help="File journal (mỗi dòng 1 run) để resume được.")
    parser.add_argument("--out-md", default=DEFAULT_OUT_MD, help="File báo cáo markdown.")
    parser.add_argument("--resume", action="store_true", help="Tiếp tục từ --out-jsonl đã có, bỏ qua run đã xong.")
    parser.add_argument("--from-jsonl", default=None, help="Bỏ qua chạy thật, chỉ sinh báo cáo từ 1 file jsonl có sẵn.")
    args = parser.parse_args(argv)

    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    for c in configs:
        if c not in CONFIGS:
            print(f"Config không hợp lệ: {c!r} (hợp lệ: {sorted(CONFIGS)})", file=sys.stderr)
            return 2

    if args.from_jsonl:
        runs = _load_jsonl(args.from_jsonl)
        if not runs:
            print(f"Không đọc được run nào từ {args.from_jsonl}", file=sys.stderr)
            return 1
    else:
        if args.files:
            files = [f.strip() for f in args.files.split(",") if f.strip()]
            missing = [f for f in files if not os.path.isfile(f)]
            if missing:
                print(f"Không tìm thấy file: {missing}", file=sys.stderr)
                return 1
        else:
            files = _discover_files(args.root)
            if not files:
                print(f"Không có file nào trong {args.root}", file=sys.stderr)
                return 1

        runs = run_all(
            files=files, configs=configs, reps=args.reps, model=args.model,
            max_turns=args.max_turns, max_verify_retries=args.max_verify_retries,
            workers=args.workers, out_jsonl=args.out_jsonl, resume=args.resume,
        )

    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    generate_report(
        runs, args.out_md, model=args.model, max_turns=args.max_turns,
        max_verify_retries=args.max_verify_retries, reps=args.reps, generated_at=generated_at,
    )
    print(f"\nĐã ghi báo cáo vào {args.out_md} ({len(runs)} run).")
    return 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
