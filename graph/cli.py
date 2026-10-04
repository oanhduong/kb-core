"""CLI vận hành graph (chạy trong container api/worker, xem Makefile):

    python -m graph.cli stats
    python -m graph.cli sync --document <document_id> [--no-llm]     # cập nhật incremental 1 document
    python -m graph.cli rebuild [--no-llm] [--enqueue]               # rebuild TOÀN BỘ (lệnh riêng, A6)
    python -m graph.cli aliases list [--kind store]
    python -m graph.cli aliases add --kind store --alias "Cầu Giấy" --canonical GM-HN01
    python -m graph.cli aliases seed                                 # nạp alias mặc định dữ liệu mẫu

--enqueue: đẩy job graph_rebuild cho worker (khuyến nghị trên production — không block terminal,
có trạng thái theo dõi qua GET /ingest/{job_id}).
"""
from __future__ import annotations

import argparse
import json
import sys

from core.db import session_scope
from core.logging import configure_logging
from graph.resolve import default_seed_aliases, load_aliases, upsert_alias
from graph.store import GraphUnavailable, get_graph_store
from graph.sync import rebuild_full, sync_document


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Vận hành graph layer RetailKB.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    p_sync = sub.add_parser("sync")
    p_sync.add_argument("--document", required=True)
    p_sync.add_argument("--no-llm", action="store_true")
    p_rb = sub.add_parser("rebuild")
    p_rb.add_argument("--no-llm", action="store_true")
    p_rb.add_argument("--enqueue", action="store_true")
    p_al = sub.add_parser("aliases")
    p_al.add_argument("action", choices=["list", "add", "seed"])
    p_al.add_argument("--kind")
    p_al.add_argument("--alias")
    p_al.add_argument("--canonical")
    p_al.add_argument("--note")
    args = parser.parse_args(argv)

    store = get_graph_store()
    try:
        if args.cmd == "stats":
            print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
        elif args.cmd == "sync":
            with session_scope() as s:
                print(json.dumps(sync_document(s, store, args.document, llm=not args.no_llm), ensure_ascii=False, indent=2, default=str))
        elif args.cmd == "rebuild":
            if args.enqueue:
                from worker.queue import enqueue_job

                with session_scope() as s:
                    job = enqueue_job(s, "graph_rebuild", {"llm": not args.no_llm}, created_by="cli")
                    print(f"Đã đưa job graph_rebuild vào hàng đợi: {job.id}")
            else:
                with session_scope() as s:
                    out = rebuild_full(s, store, llm=not args.no_llm,
                                       progress=lambda i, n: print(f"  {i}/{n} document", file=sys.stderr))
                print(json.dumps(out["graph"], ensure_ascii=False, indent=2))
        elif args.cmd == "aliases":
            with session_scope() as s:
                if args.action == "list":
                    for a in load_aliases(s):
                        if not args.kind or a.kind == args.kind:
                            print(f"[{a.id}] {a.kind}: {a.alias!r} -> {a.canonical!r}" + (f"  ({a.note})" if a.note else ""))
                elif args.action == "add":
                    if not (args.kind and args.alias and args.canonical):
                        print("Cần --kind --alias --canonical", file=sys.stderr)
                        return 2
                    row = upsert_alias(s, args.kind, args.alias, args.canonical, note=args.note, actor="cli")
                    print(f"OK [{row.id}] {row.kind}: {row.alias!r} -> {row.canonical!r}")
                else:
                    for a in default_seed_aliases():
                        upsert_alias(s, a.kind, a.alias, a.canonical, note="seed", actor="seed")
                    print(f"Đã nạp {len(default_seed_aliases())} alias mặc định.")
    except GraphUnavailable as e:
        print(f"Graph không dùng được (AGE/DB): {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
