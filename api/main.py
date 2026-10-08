"""FastAPI app RetailKB Copilot (D1).

    uvicorn api.main:app --host 0.0.0.0 --port 8000

- Middleware: request_id (header X-Request-ID, trả lại trong response), structured access log, gắn
  request_id vào log context của mọi dòng log trong request.
- Exception handler: AppError -> JSON {error: {code, message, request_id}} đúng HTTP status; lỗi validate
  -> 422 tiếng Việt; MỌI lỗi khác -> JSON thân thiện + request_id (log đầy đủ stacktrace) — không bao
  giờ trả trang lỗi 500 trần (B9).
- /healthz (liveness, không chạm DB) — /readyz (DB + migration + graph + model sẵn sàng).
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api.routes import accounts, admin, analytics, ingest, insights, kb, knowledge, models, qa, reports, review
from core.config import get_config
from core.errors import AppError
from core.i18n import L, reset_lang, set_lang
from core.logging import bind_log_context, configure_logging, log_event, reset_log_context

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    if getattr(app.state, "services", None) is None:
        from api.state import build_services

        app.state.services = build_services(warmup=os.environ.get("API_WARMUP", "true").lower() == "true")
    yield
    from observability.tracing import flush

    flush()


def create_app(services=None) -> FastAPI:
    cfg = get_config()
    app = FastAPI(title="RetailKB API", version=cfg.config_version, lifespan=lifespan,
                  description="Khai phá tài liệu: upload file bất kỳ -> knowledge base, hỏi đáp có trích dẫn, phân tích bảng số liệu, "
                              "đồ thị quan hệ, báo cáo. Xác thực: Bearer JWT (đăng nhập) hoặc X-API-Key (tích hợp).")
    app.state.services = services
    origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"],
                       allow_headers=["*"], expose_headers=["X-Request-ID"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = rid
        token = bind_log_context(request_id=rid)
        lang_token = set_lang(request.headers.get("accept-language"))
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:  # lỗi lọt khỏi exception handler (hiếm) -> vẫn JSON thân thiện
            logger.exception("lỗi không xử lý được ở middleware")
            response = JSONResponse(status_code=500, content=_err("internal_error", _generic(), rid))
        finally:
            reset_log_context(token)
            reset_lang(lang_token)
        response.headers["X-Request-ID"] = rid
        if request.url.path not in ("/healthz",):
            principal = getattr(request.state, "principal", None)
            log_event(logger, "http_request", method=request.method, path=request.url.path, status=response.status_code,
                      latency_ms=int((time.perf_counter() - start) * 1000), request_id=rid,
                      principal=getattr(principal, "subject", None))
        return response

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        rid = getattr(request.state, "request_id", None)
        if exc.http_status >= 500:
            logger.error("AppError %s: %s context=%s", exc.code, exc.message, exc.context)
        headers = {"Retry-After": str(exc.context.get("retry_after"))} if exc.code == "rate_limited" else None
        return JSONResponse(status_code=exc.http_status, content=_err(exc.code, exc.message, rid), headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        body = _err("validation_failed", L("Dữ liệu gửi lên không hợp lệ.", "The submitted data is not valid."), getattr(request.state, "request_id", None))
        body["error"]["details"] = details
        return JSONResponse(status_code=422, content=body)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", None)
        logger.exception("lỗi không lường trước request_id=%s", rid)
        return JSONResponse(status_code=500, content=_err("internal_error", _generic(), rid))

    @app.get("/healthz", tags=["ops"], summary="Liveness")
    def healthz() -> dict:
        return {"status": "ok", "config_version": cfg.config_version}

    @app.get("/readyz", tags=["ops"], summary="Readiness: DB, migration, graph, embedding/reranker")
    def readyz(request: Request) -> JSONResponse:
        checks: dict[str, object] = {}
        ok = True
        services = request.app.state.services
        try:
            with services.sm() as s:
                checks["database"] = "ok"
                checks["migration"] = s.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except Exception as e:
            ok, checks["database"] = False, f"lỗi: {e}"[:200]
        try:
            with services.sm() as s:
                has_age = s.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'age'")).scalar()
            checks["graph_age"] = "ok" if has_age else "AGE chưa cài — graph rule-based (pack retail) tắt, QA vẫn chạy"
        except Exception as e:
            checks["graph_age"] = f"lỗi: {e}"[:200]
        checks["reranker"] = "ok" if services.retriever._rerank_ready.is_set() else (
            "tắt" if not services.cfg.retrieval.rerank.enabled else "đang tải (tạm dùng hybrid)")
        checks["anthropic_api_key"] = "ok" if services.settings.anthropic_api_key else "THIẾU — không trả lời được"
        checks["langfuse"] = "ok" if services.settings.langfuse_public_key else "tắt"
        return JSONResponse(status_code=200 if ok else 503, content={"ready": ok, "config_version": cfg.config_version,
                                                                      "config_hash": cfg.config_hash, "checks": checks})

    for r in (accounts.router, qa.router, ingest.router, kb.router, review.router, analytics.router, knowledge.router, reports.router,
              models.router, insights.router, admin.ws_router, admin.router):
        app.include_router(r)
    return app


def _generic() -> str:
    return L("Hệ thống gặp sự cố tạm thời. Vui lòng thử lại; nếu lỗi lặp lại hãy báo admin kèm mã request.",
             "Something went wrong on our side. Please try again; if it keeps happening, tell your admin and include the request ID.")


def _err(code: str, message: str, request_id: str | None) -> dict:
    return {"error": {"code": code, "message": message, "request_id": request_id}}


app = create_app()
