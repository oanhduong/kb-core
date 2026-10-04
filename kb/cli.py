"""CLI KB (Makefile):  python -m kb.cli reindex [--doc-type policy] [--document ID] [--rechunk auto|true|false]
                                                [--model intfloat/...] [--wait]
                       python -m kb.cli health"""
from __future__ import annotations

import argparse
import json
import sys
import time

from core.config import get_config
from core.db import session_scope
from core.logging import configure_logging
from core.models import Job
from kb.health import kb_health
from worker.queue import enqueue_job, job_view


def main(argv: list[str] | None = None) -> int:
    configure_logging(fmt="text")
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reindex", help="Đẩy job reindex cho worker (chạy nền, có progress)")
    r.add_argument("--doc-type")
    r.add_argument("--document")
    r.add_argument("--rechunk", default="auto", choices=["auto", "true", "false"])
    r.add_argument("--model")
    r.add_argument("--dim", type=int)
    r.add_argument("--force", action="store_true")
    r.add_argument("--wait", action="store_true")
    sub.add_parser("health")
    args = p.parse_args(argv)
    if args.cmd == "health":
        with session_scope() as s:
            print(json.dumps(kb_health(s, get_config()), ensure_ascii=False, indent=2, default=str))
        return 0
    payload = {"doc_type": args.doc_type, "document_id": args.document, "model": args.model, "dim": args.dim,
               "rechunk": {"true": True, "false": False}.get(args.rechunk, "auto"), "force": args.force or None}
    payload = {k: v for k, v in payload.items() if v is not None}
    with session_scope() as s:
        job = enqueue_job(s, "reindex", payload, document_id=args.document, created_by="cli")
        job_id = job.id
    print(f"Job reindex {job_id}: {payload}")
    while args.wait:
        with session_scope() as s:
            j = s.get(Job, job_id)
            v = job_view(j)
            print(f"  {v['status']} {v['progress']} {(v['result'] or {}).get('note', '')}")
            if j.status in ("done", "blocked", "error"):
                print(json.dumps(v["result"], ensure_ascii=False, default=str))
                return 0 if j.status == "done" else 1
        time.sleep(3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
