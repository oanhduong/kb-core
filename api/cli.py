"""CLI xác thực (Makefile):  python -m api.cli token --user nv01 --role staff --store GM-HN01 [--ttl 720]
                            python -m api.cli api-key --name pos-gateway --role staff [--store GM-HN01] [--rate 120]
                            python -m api.cli revoke-key --id <key_id>"""
from __future__ import annotations

import argparse
import sys

from api.auth import create_api_key, issue_jwt
from core.db import session_scope
from core.models import ApiKey


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("token")
    t.add_argument("--user", required=True)
    t.add_argument("--role", default="staff", choices=["staff", "supervisor", "admin"])
    t.add_argument("--store")
    t.add_argument("--ttl", type=int)
    k = sub.add_parser("api-key")
    k.add_argument("--name", required=True)
    k.add_argument("--role", default="staff", choices=["staff", "supervisor", "admin"])
    k.add_argument("--store")
    k.add_argument("--rate", type=int)
    rv = sub.add_parser("revoke-key")
    rv.add_argument("--id", required=True)
    a = p.parse_args(argv)
    if a.cmd == "token":
        print(issue_jwt(user_id=a.user, role=a.role, store_code=a.store, ttl_minutes=a.ttl))
    elif a.cmd == "api-key":
        with session_scope() as s:
            row, raw = create_api_key(s, name=a.name, role=a.role, store_code=a.store, rate_limit=a.rate, created_by="cli")
            print(f"id={row.id} role={row.role} store={row.store_code}\nAPI key (chỉ hiện 1 lần): {raw}")
    else:
        with session_scope() as s:
            row = s.get(ApiKey, a.id)
            if row is None:
                print("Không có key", file=sys.stderr)
                return 1
            row.active = False
            print(f"Đã thu hồi {a.id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
