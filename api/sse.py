"""Server-Sent Events (B8).

Generator QAService.stream() dùng contextvars (trace Langfuse, log context) — nếu để Starlette gọi
next() lần lượt trong threadpool, mỗi lần có thể chạy ở luồng/ngữ cảnh khác và mất trace. Vì vậy cả
generator chạy trong MỘT thread riêng (copy context của request), đẩy event qua queue; response async
đọc queue, gửi heartbeat mỗi 15s để proxy không cắt kết nối. Client ngắt giữa chừng: thread vẫn chạy
nốt (câu trả lời + trace vẫn được lưu), chỉ là không gửi tiếp.
"""
from __future__ import annotations

import contextvars
import json
import logging
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Any

import anyio
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)
_SENTINEL = object()
HEARTBEAT_SECONDS = 15.0


def format_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def sse_response(gen_factory: Callable[[], Iterator[tuple[str, dict[str, Any]]]]) -> StreamingResponse:
    q: queue.Queue = queue.Queue(maxsize=256)
    ctx = contextvars.copy_context()

    def produce() -> None:
        try:
            for ev, data in gen_factory():
                q.put((ev, data))
        except Exception:
            logger.exception("SSE producer lỗi")
            q.put(("error", {"message": "Hệ thống gặp sự cố khi tạo câu trả lời, vui lòng thử lại."}))
            q.put(("done", {"status": "error"}))
        finally:
            q.put(_SENTINEL)

    threading.Thread(target=ctx.run, args=(produce,), daemon=True, name="sse-producer").start()

    def _get() -> Any:
        try:
            return q.get(timeout=HEARTBEAT_SECONDS)
        except queue.Empty:
            return None

    async def body():
        while True:
            item = await anyio.to_thread.run_sync(_get)
            if item is None:
                yield ": ping\n\n"
                continue
            if item is _SENTINEL:
                break
            ev, data = item
            yield format_event(ev, data)

    return StreamingResponse(body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})
