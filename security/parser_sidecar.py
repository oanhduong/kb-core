"""Parser sandbox dạng SIDECAR cho docker-compose (F6: parser chạy trong sandbox KHÔNG có network egress).

Khác security/sandbox.py (worker tự `docker run` 1 container/ file — cần quyền Docker daemon, hợp với
triển khai có orchestrator riêng), sidecar là 1 container `parser` chạy thường trực với:
    network_mode: none (không có card mạng nào -> không exfiltrate được), rootfs read-only, user
    non-root, cap_drop ALL, no-new-privileges, giới hạn RAM/PID — xem docker-compose.yml.
Giao tiếp qua volume dùng chung (không qua mạng):
    worker ghi  <work>/inbox/<id>.json  = {"id", "path", "kind"}   (path nằm trong volume uploads, mount ro)
    sidecar ghi <work>/outbox/<id>.json = {"ok": true, "result": {...}} | {"ok": false, "error": "..."}
Ghi file luôn qua file tạm + os.replace (atomic) để bên đọc không bao giờ thấy file dở.
Model của docling/faster-whisper được tải SẴN trong image lúc build (container không có mạng).

    python -m security.parser_sidecar --work /work            # chạy trong container parser
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

POLL_SECONDS = 0.5


def _atomic_write(path: str, data: dict[str, Any]) -> None:
    tmp = f"{path}.tmp-{uuid.uuid4().hex[:6]}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    os.replace(tmp, path)


# ------------------------------------------------------------------ phía worker (client)
def request_parse(work_dir: str, path: str, kind: str, timeout: float) -> dict[str, Any]:
    from ingest.invariants import ParserError

    inbox, outbox = os.path.join(work_dir, "inbox"), os.path.join(work_dir, "outbox")
    os.makedirs(inbox, exist_ok=True)
    os.makedirs(outbox, exist_ok=True)
    rid = uuid.uuid4().hex
    _atomic_write(os.path.join(inbox, f"{rid}.json"), {"id": rid, "path": os.path.abspath(path), "kind": kind})
    out_path = os.path.join(outbox, f"{rid}.json")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.isfile(out_path):
            with open(out_path, encoding="utf-8") as f:
                resp = json.load(f)
            os.remove(out_path)
            if not resp.get("ok"):
                raise ParserError(f"Parser sidecar báo lỗi: {resp.get('error')}")
            return resp["result"]
        time.sleep(POLL_SECONDS)
    try:
        os.remove(os.path.join(inbox, f"{rid}.json"))
    except FileNotFoundError:
        pass
    raise TimeoutError(f"Parser sidecar không trả kết quả sau {timeout:.0f}s (container parser có đang chạy?)")


# ------------------------------------------------------------------ phía sidecar (server)
def _handle(req: dict[str, Any], whisper_size: str) -> dict[str, Any]:
    from ingest import parsers

    kind = req["kind"]
    if kind in ("audio", "video"):
        return parsers.parse_media(req["path"], model_size=whisper_size)
    return parsers._PARSERS[kind](req["path"])  # noqa: SLF001


def serve(work_dir: str, whisper_size: str = "small") -> None:
    inbox, outbox = os.path.join(work_dir, "inbox"), os.path.join(work_dir, "outbox")
    os.makedirs(inbox, exist_ok=True)
    os.makedirs(outbox, exist_ok=True)
    logger.info("parser sidecar sẵn sàng, inbox=%s", inbox)
    while True:
        names = sorted(n for n in os.listdir(inbox) if n.endswith(".json"))
        if not names:
            time.sleep(POLL_SECONDS)
            continue
        for name in names:
            src = os.path.join(inbox, name)
            try:
                with open(src, encoding="utf-8") as f:
                    req = json.load(f)
                os.remove(src)
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            start = time.perf_counter()
            try:
                resp = {"ok": True, "result": _handle(req, whisper_size)}
            except Exception as e:  # trả lỗi cho worker, sidecar tiếp tục phục vụ file khác
                resp = {"ok": False, "error": f"{type(e).__name__}: {e}"[:2000]}
            _atomic_write(os.path.join(outbox, f"{req['id']}.json"), resp)
            logger.info("parsed %s kind=%s ok=%s %.1fs", os.path.basename(req.get("path", "?")), req.get("kind"),
                        resp["ok"], time.perf_counter() - start)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Parser sandbox sidecar (không network).")
    p.add_argument("--work", default=os.environ.get("PARSER_WORK_DIR", "/work"))
    # WHISPER_MODEL_PATH: thư mục model faster-whisper tải sẵn trong image (không cần mạng/HF cache lúc chạy)
    p.add_argument("--whisper-size", default=os.environ.get("WHISPER_MODEL_PATH") or os.environ.get("WHISPER_MODEL_SIZE", "small"))
    args = p.parse_args(argv)
    serve(args.work, args.whisper_size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
