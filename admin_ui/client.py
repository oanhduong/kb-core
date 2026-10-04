"""HTTP client gọi API RetailKB cho Admin UI (Streamlit chỉ là giao diện — mọi nghiệp vụ, phân quyền,
audit đều ở API; UI dùng API key admin trong biến môi trường ADMIN_API_KEY)."""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY") or os.environ.get("BOOTSTRAP_ADMIN_API_KEY", "")
LANGFUSE_PUBLIC_URL = os.environ.get("LANGFUSE_PUBLIC_URL", "")


class ApiError(Exception):
    def __init__(self, status: int, message: str, request_id: str | None = None) -> None:
        super().__init__(f"[{status}] {message}" + (f" (request_id={request_id})" if request_id else ""))
        self.status = status


def _client() -> httpx.Client:
    return httpx.Client(base_url=API_BASE_URL, headers={"X-API-Key": ADMIN_API_KEY}, timeout=60.0)


def _check(r: httpx.Response) -> Any:
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
            raise ApiError(r.status_code, err.get("message", r.text), err.get("request_id"))
        except ValueError:
            raise ApiError(r.status_code, r.text[:300]) from None
    return r.json()


def get(path: str, **params: Any) -> Any:
    with _client() as c:
        return _check(c.get(path, params={k: v for k, v in params.items() if v not in (None, "", [])}))


def post(path: str, json: Any = None, files: Any = None, data: Any = None) -> Any:
    with _client() as c:
        return _check(c.post(path, json=json, files=files, data=data, timeout=300.0))


def patch(path: str, json: Any) -> Any:
    with _client() as c:
        return _check(c.patch(path, json=json))


def delete(path: str) -> Any:
    with _client() as c:
        return _check(c.delete(path))


def enc(record_id: str) -> str:
    return quote(record_id, safe="")
