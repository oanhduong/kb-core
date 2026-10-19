"""Alert đơn giản qua webhook (E3) — worker chạy mỗi alerts.check_interval_seconds.

Luật (ngưỡng trong config alerts.*):
    daily_cost        chi phí LLM hôm nay (bảng llm_usage) vượt daily_cost_usd
    error_rate_5m     tỉ lệ request QA lỗi trong 5 phút vượt error_rate_5m (khi có >= error_rate_min_requests)
    abstention_spike  tỉ lệ abstention 60 phút gần nhất >= abstention_spike_ratio x nền 7 ngày (và >= 30%,
                      đủ abstention_min_questions câu) — dấu hiệu KB thiếu dữ liệu hoặc index hỏng
    ingest_failed     có job ingest mới rơi vào error/blocked (mỗi job báo 1 lần)
    review_backlog    review queue tồn > review_queue_max mục
Chống spam: cùng (luật, fingerprint) không gửi lại trong cooldown_minutes (bảng alert_events).
Kênh: Slack incoming webhook ({"text"}), Telegram bot (sendMessage), hoặc generic JSON webhook.
evaluate_rules() THUẦN (nhận số liệu, trả danh sách alert) — unit test được không cần DB/mạng.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import func, select, text

from core.config import AppConfig, Settings, get_settings
from core.db import session_scope
from core.logging import log_event
from core.models import AlertEvent, Job, ReviewItem

logger = logging.getLogger(__name__)


@dataclass
class AlertMetrics:
    cost_today_usd: float = 0.0
    requests_5m: int = 0
    errors_5m: int = 0
    questions_1h: int = 0
    abstentions_1h: int = 0
    questions_baseline: int = 0
    abstentions_baseline: int = 0
    failed_ingest_jobs: list[dict[str, Any]] = field(default_factory=list)
    review_pending: int = 0
    today: str = ""


@dataclass
class Alert:
    rule: str
    fingerprint: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    job_ids: list[str] = field(default_factory=list)


def evaluate_rules(m: AlertMetrics, cfg: AppConfig) -> list[Alert]:
    a = cfg.alerts
    out: list[Alert] = []
    if m.cost_today_usd > a.daily_cost_usd:
        out.append(Alert("daily_cost", m.today, f"💸 Chi phí LLM hôm nay ${m.cost_today_usd:.2f} đã vượt ngưỡng "
                                                 f"${a.daily_cost_usd:.2f}.", {"cost_usd": round(m.cost_today_usd, 4)}))
    if m.requests_5m >= a.error_rate_min_requests and m.requests_5m:
        rate = m.errors_5m / m.requests_5m
        if rate > a.error_rate_5m:
            out.append(Alert("error_rate_5m", "5m", f"🔥 Tỉ lệ lỗi QA 5 phút qua {rate:.0%} ({m.errors_5m}/{m.requests_5m}) "
                                                   f"vượt ngưỡng {a.error_rate_5m:.0%}.", {"rate": round(rate, 3)}))
    if m.questions_1h >= a.abstention_min_questions:
        rate = m.abstentions_1h / m.questions_1h
        base = (m.abstentions_baseline / m.questions_baseline) if m.questions_baseline else None
        spike = (base is not None and base > 0 and rate >= a.abstention_spike_ratio * base) or (base in (None, 0) and rate >= 0.5)
        if spike and rate >= 0.3:
            base_txt = f"{base:.0%}" if base is not None else "chưa có"
            out.append(Alert("abstention_spike", "1h", f"📉 Tỉ lệ không trả lời được 60 phút qua {rate:.0%} "
                                                      f"({m.abstentions_1h}/{m.questions_1h}), nền 7 ngày {base_txt} — "
                                                      "KB có thể thiếu dữ liệu hoặc index hỏng.", {"rate": round(rate, 3), "baseline": base}))
    if m.failed_ingest_jobs:
        names = ", ".join(f"{j.get('filename') or j['document_id']} ({j['status']})" for j in m.failed_ingest_jobs[:10])
        out.append(Alert("ingest_failed", ",".join(sorted(j["job_id"] for j in m.failed_ingest_jobs)),
                         f"📄 {len(m.failed_ingest_jobs)} file ingest lỗi/bị chặn: {names}",
                         {"jobs": m.failed_ingest_jobs[:20]}, job_ids=[j["job_id"] for j in m.failed_ingest_jobs]))
    if m.review_pending > a.review_queue_max:
        out.append(Alert("review_backlog", "backlog", f"🧾 Review queue đang tồn {m.review_pending} câu hỏi chờ "
                                                     f"supervisor duyệt (ngưỡng {a.review_queue_max}).", {"pending": m.review_pending}))
    return out


def collect_metrics(session: Any, now: datetime | None = None, baseline_days: int = 7) -> AlertMetrics:
    from observability.costs import cost_today

    now = now or datetime.now(timezone.utc)
    q = lambda sql, **p: session.execute(text(sql), p).scalar_one()  # noqa: E731
    t5, t60, tb = now - timedelta(minutes=5), now - timedelta(hours=1), now - timedelta(days=baseline_days)
    failed = session.execute(text(
        "SELECT j.id, j.status, j.document_id, d.filename, j.error FROM jobs j LEFT JOIN documents d ON d.id = j.document_id "
        "WHERE j.type = 'ingest_file' AND j.status IN ('error','blocked') AND j.alerted = false ORDER BY j.finished_at")).all()
    return AlertMetrics(
        cost_today_usd=cost_today(session, now),
        requests_5m=q("SELECT count(*) FROM qa_traces WHERE created_at >= :t", t=t5),
        errors_5m=q("SELECT count(*) FROM qa_traces WHERE created_at >= :t AND status = 'error'", t=t5),
        questions_1h=q("SELECT count(*) FROM qa_traces WHERE created_at >= :t", t=t60),
        abstentions_1h=q("SELECT count(*) FROM abstentions WHERE created_at >= :t", t=t60),
        questions_baseline=q("SELECT count(*) FROM qa_traces WHERE created_at >= :a AND created_at < :b", a=tb, b=t60),
        abstentions_baseline=q("SELECT count(*) FROM abstentions WHERE created_at >= :a AND created_at < :b", a=tb, b=t60),
        failed_ingest_jobs=[{"job_id": r.id, "status": r.status, "document_id": r.document_id, "filename": r.filename,
                             "error": (r.error or "")[:200]} for r in failed],
        review_pending=session.execute(select(func.count()).select_from(ReviewItem).where(ReviewItem.status == "pending")).scalar_one(),
        today=now.strftime("%Y-%m-%d"),
    )


def send_webhook(alert: Alert, cfg: AppConfig, settings: Settings, client: httpx.Client | None = None) -> None:
    channel = cfg.alerts.channel
    text_msg = f"[Kho Dữ Liệu {settings.app_env}] {alert.message}"
    own = client is None
    client = client or httpx.Client(timeout=10.0)
    try:
        if channel == "telegram":
            if not (settings.telegram_bot_token and settings.telegram_chat_id):
                raise RuntimeError("thiếu TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID")
            r = client.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                            json={"chat_id": settings.telegram_chat_id, "text": text_msg})
        else:
            if not settings.alert_webhook_url:
                raise RuntimeError("thiếu ALERT_WEBHOOK_URL")
            payload = {"text": text_msg} if channel == "slack" else {"rule": alert.rule, "message": alert.message,
                                                                     "details": alert.details, "env": settings.app_env}
            r = client.post(settings.alert_webhook_url, json=payload)
        r.raise_for_status()
    finally:
        if own:
            client.close()


def run_alert_checks(cfg: AppConfig, *, settings: Settings | None = None, sender: Any = None) -> list[Alert]:
    settings = settings or get_settings()
    sender = sender or send_webhook
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        alerts = evaluate_rules(collect_metrics(s, now, cfg.alerts.abstention_baseline_days), cfg)
        sent = []
        for al in alerts:
            recent = s.execute(select(AlertEvent).where(
                AlertEvent.rule == al.rule, AlertEvent.fingerprint == al.fingerprint, AlertEvent.delivered.is_(True),
                AlertEvent.sent_at >= now - timedelta(minutes=cfg.alerts.cooldown_minutes))).first()
            if recent is not None:
                continue
            err = None
            try:
                sender(al, cfg, settings)
            except Exception as e:  # kênh alert hỏng: ghi lại + log, lần sau thử tiếp (không đánh dấu delivered)
                err = f"{type(e).__name__}: {e}"[:500]
                log_event(logger, "alert_delivery_failed", level=logging.ERROR, rule=al.rule, error=err)
            s.add(AlertEvent(rule=al.rule, fingerprint=al.fingerprint, message=al.message, delivered=err is None, error=err))
            if al.job_ids and err is None:
                for jid in al.job_ids:
                    job = s.get(Job, jid)
                    if job is not None:
                        job.alerted = True
            log_event(logger, "alert_fired", level=logging.WARNING, rule=al.rule, delivered=err is None, message=al.message)
            sent.append(al)
        return sent
