"""Câu hỏi tự nhiên -> SQL DuckDB (LLM) -> validate -> chạy -> chart. Sai SQL -> gửi lỗi cho model sửa
(tối đa analytics.sql_max_retries). Trả cả SQL + kết quả + chart để UI hiển thị và người dùng kiểm tra."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from analytics.charts import build_chart
from analytics.engine import QueryResult, TableInfo, catalog_prompt, run_query
from core.config import AppConfig, read_prompt
from core.errors import ValidationFailed
from core.llm import LLMClient, LLMOutputInvalid, LLMUnavailable
from core.logging import log_event
from observability import tracing

logger = logging.getLogger(__name__)


class _ChartHint(BaseModel):
    type: str | None = None
    x: str | None = None
    y: str | list[str] | None = None
    series: str | None = None
    title: str | None = None


class _NL2SQLOut(BaseModel):
    sql: str | None = None
    chart: _ChartHint | None = None
    reason: str = ""
    assumptions: list[str] = Field(default_factory=list)


@dataclass
class AnalyticsAnswer:
    sql: str | None
    result: QueryResult | None
    chart: dict[str, Any] | None
    reason: str
    assumptions: list[str] = field(default_factory=list)
    attempts: int = 0
    error: str | None = None
    tables_used: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"sql": self.sql, "result": self.result.to_dict() if self.result else None, "chart": self.chart, "reason": self.reason,
                "assumptions": self.assumptions, "attempts": self.attempts, "error": self.error, "tables_used": self.tables_used}


def _tables_in_sql(sql: str, tables: list[TableInfo]) -> list[str]:
    low = (sql or "").lower()
    return [t.name for t in tables if t.name in low]


def answer_with_sql(cfg: AppConfig, llm: LLMClient, tables: list[TableInfo], question: str, *, context: str | None = None) -> AnalyticsAnswer:
    if not tables:
        return AnalyticsAnswer(None, None, None, "Workspace chưa có bảng số liệu nào để truy vấn.", error="no_tables")
    system = read_prompt("nl2sql", cfg) + "\n\n" + catalog_prompt(tables)
    user = (f"Ngữ cảnh: {context}\n" if context else "") + f"Câu hỏi: {question}"
    messages = [{"role": "user", "content": user}]
    last_err = ""
    attempts = 0
    with tracing.span("analytics.nl2sql", input={"question": question, "tables": len(tables)}) as sp:
        for attempt in range(cfg.analytics.sql_max_retries + 1):
            attempts = attempt + 1
            try:
                res = llm.complete("nl2sql", system=system, messages=messages, max_tokens=900)
                from core.llm import extract_json_object

                out = _NL2SQLOut.model_validate(extract_json_object(res.text))
            except (LLMUnavailable, LLMOutputInvalid) as e:
                sp.update(level="ERROR", error=str(e)[:200])
                return AnalyticsAnswer(None, None, None, "Không sinh được truy vấn.", attempts=attempts, error=str(e)[:300])
            except Exception as e:  # JSON lỗi -> cho model sửa
                last_err = f"Output không phải JSON hợp lệ: {e}"
                messages += [{"role": "assistant", "content": res.text}, {"role": "user", "content": last_err + ". Trả lại đúng 1 JSON."}]
                continue
            if not out.sql:
                sp.update(output={"sql": None, "reason": out.reason})
                return AnalyticsAnswer(None, None, None, out.reason or "Câu hỏi không trả lời được bằng các bảng hiện có.",
                                       out.assumptions, attempts, error="not_answerable")
            try:
                result = run_query(cfg, tables, out.sql)
            except ValidationFailed as e:
                last_err = e.message
                log_event(logger, "nl2sql_retry", level=logging.WARNING, attempt=attempts, error=last_err[:200])
                messages += [{"role": "assistant", "content": res.text},
                             {"role": "user", "content": f"SQL trên lỗi khi chạy: {last_err}\nHãy sửa SQL (vẫn trả về đúng 1 JSON)."}]
                continue
            chart = build_chart(result, out.chart.model_dump() if out.chart else None, cfg.analytics.chart_max_points)
            sp.update(output={"sql": out.sql, "rows": result.row_count, "chart": chart["type"] if chart else None, "attempts": attempts})
            return AnalyticsAnswer(out.sql, result, chart, out.reason, out.assumptions, attempts, tables_used=_tables_in_sql(out.sql, tables))
        sp.update(level="WARNING", error=last_err[:200])
    return AnalyticsAnswer(None, None, None, "Không tạo được truy vấn chạy được sau nhiều lần thử.", attempts=attempts, error=last_err[:300])
