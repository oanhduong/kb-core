"""Điều phối mô hình dữ liệu: tạo -> job phân tích (worker) -> người dùng duyệt/sửa (API, kiểm chứng tức thì trên
DuckDB) -> job build (warehouse Postgres: bảng + view, đối chiếu Excel, xuất sang Analytics) -> tài liệu quan hệ.

Dữ liệu bảng sau khi đọc file được lưu parquet (data/tables/<ws>/models/<id>/) -> mọi lần kiểm chứng/rebuild không phải
đọc lại Excel. Mỗi chỉnh sửa spec tăng revision; build ghi lại revision đã build để UI biết view có đang lỗi thời không.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics.tables import sql_identifier
from core.config import AppConfig
from core.db import session_scope
from core.errors import NotFoundError, ValidationFailed
from core.logging import log_event
from core.models import DataModel, DataTable, Document, Job
from modeling import explain as X
from modeling.evaluate import DuckEval, _jsonable
from modeling.spec import Candidate, Evidence, KeyPair, ModelParameters, ModelSpec, Relationship, Rule, Transform, now_iso

logger = logging.getLogger(__name__)
SUPPORTED = ("xlsx", "xlsm", "csv")


# ===================================================================================== lưu trữ dữ liệu bảng
def data_dir(cfg: AppConfig, ws_id: str, model_id: str) -> str:
    return os.path.join(cfg.analytics.storage_dir, ws_id, "models", model_id)


def save_frames(cfg: AppConfig, ws_id: str, model_id: str, frames: dict[str, pd.DataFrame]) -> None:
    d = data_dir(cfg, ws_id, model_id)
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d, exist_ok=True)
    for k, df in frames.items():
        df.to_parquet(os.path.join(d, f"{k}.parquet"), index=False)


def load_frames(cfg: AppConfig, ws_id: str, model_id: str, keys: list[str]) -> dict[str, pd.DataFrame]:
    d = data_dir(cfg, ws_id, model_id)
    out = {}
    for k in keys:
        p = os.path.join(d, f"{k}.parquet")
        if os.path.isfile(p):
            out[k] = pd.read_parquet(p)
    return out


_EVALS: "OrderedDict[tuple[str, str], DuckEval]" = OrderedDict()
_EVAL_LOCK = threading.Lock()


def evaluator(cfg: AppConfig, model: DataModel, spec: ModelSpec) -> DuckEval:
    key = (model.id, spec.analyzed_at or "")
    with _EVAL_LOCK:
        ev = _EVALS.get(key)
        if ev is not None:
            _EVALS.move_to_end(key)
            return ev
    frames = load_frames(cfg, model.workspace_id, model.id, [t.key for t in spec.tables])
    if spec.main_table not in frames:
        raise ValidationFailed("Dữ liệu của mô hình chưa sẵn sàng — hãy chạy phân tích lại.")
    ev = DuckEval(frames)
    with _EVAL_LOCK:
        _EVALS[key] = ev
        while len(_EVALS) > 4:
            _EVALS.popitem(last=False)
    return ev


# ===================================================================================== tạo & đọc
def get_model(s: Session, ws_id: str, model_id: str) -> DataModel:
    m = s.get(DataModel, model_id)
    if m is None or m.workspace_id != ws_id or m.deleted_at is not None:
        raise NotFoundError("Không có mô hình dữ liệu")
    return m


def spec_of(m: DataModel) -> ModelSpec:
    return ModelSpec.model_validate(m.spec) if m.spec else ModelSpec()


def save_spec(m: DataModel, spec: ModelSpec, *, bump: bool = True) -> None:
    m.spec = spec.model_dump(mode="json")
    if bump:
        m.revision = (m.revision or 0) + 1


def create_model(s: Session, cfg: AppConfig, ws_id: str, user: str, *, name: str | None, document_ids: list[str],
                 description: str | None = None) -> DataModel:
    from worker.queue import enqueue_job

    if not document_ids:
        raise ValidationFailed("Chọn ít nhất một file Excel/CSV")
    docs = s.execute(select(Document).where(Document.id.in_(document_ids), Document.workspace_id == ws_id, Document.deleted_at.is_(None))).scalars().all()
    if len(docs) != len(set(document_ids)):
        raise ValidationFailed("Có file không thuộc workspace hoặc đã bị xoá")
    bad = [d.filename for d in docs if d.source_type not in SUPPORTED]
    if bad:
        raise ValidationFailed(f"Chỉ hỗ trợ Excel/CSV: {', '.join(bad)}")
    order = {d: i for i, d in enumerate(document_ids)}
    docs = sorted(docs, key=lambda d: order[d.id])
    m = DataModel(workspace_id=ws_id, name=(name or docs[0].title or docs[0].filename)[:200], description=description,
                  status="analyzing", document_ids=[d.id for d in docs], spec={}, schema_name="", created_by=user)
    s.add(m)
    s.flush()
    m.schema_name = _schema(cfg, m.id)
    job = enqueue_job(s, "model_analyze", {"model_id": m.id}, workspace_id=ws_id, created_by=user, max_attempts=2)
    m.job_id = job.id
    return m


def _schema(cfg: AppConfig, model_id: str) -> str:
    from modeling.warehouse import schema_for

    return schema_for(cfg, model_id)


def list_models(s: Session, ws_id: str) -> list[DataModel]:
    return list(s.execute(select(DataModel).where(DataModel.workspace_id == ws_id, DataModel.deleted_at.is_(None))
                          .order_by(DataModel.updated_at.desc())).scalars())


def delete_model(s: Session, cfg: AppConfig, m: DataModel) -> None:
    m.deleted_at = datetime.now(timezone.utc)
    tid = (m.build_info or {}).get("data_table_id")
    if tid:
        t = s.get(DataTable, tid)
        if t is not None:
            t.is_active = False
    try:
        from modeling.warehouse import drop_schema, warehouse_engine

        drop_schema(warehouse_engine(), m.schema_name)
    except Exception:
        logger.exception("xoá schema %s lỗi", m.schema_name)


# ===================================================================================== job: phân tích
def run_analyze(cfg: AppConfig, llm: Any, model_id: str, *, main_override: str | None = None,
                progress: Callable[[int, int, str], None] | None = None) -> dict[str, Any]:
    from modeling.ai import make_proposer
    from modeling.infer import SourceFile, analyze
    from modeling.warehouse import load_tables, warehouse_engine

    with session_scope() as s:
        m = s.get(DataModel, model_id)
        if m is None or m.deleted_at is not None:
            return {"skipped": "model deleted"}
        docs = s.execute(select(Document).where(Document.id.in_(m.document_ids))).scalars().all()
        by_id = {d.id: d for d in docs}
        sources = [SourceFile(d.id, d.filename, d.title, d.storage_path, d.source_type) for i in m.document_ids if (d := by_id.get(i))]
        ws_id, schema = m.workspace_id, m.schema_name
        old = spec_of(m) if m.spec else None
        m.status, m.error = "analyzing", None
    proposer = make_proposer(cfg, llm) if (llm is not None and cfg.modeling.ai_suggestions) else None
    try:
        spec, frames = analyze(sources, llm_propose=proposer, max_rows=cfg.modeling.max_rows_per_table, progress=progress,
                               main_override=main_override or (old.main_table if old else None),
                               view_name=old.view_name if old and old.view_name and old.main_table == (main_override or old.main_table) else None)
        if old is not None:
            _carry_decisions(old, spec)
        save_frames(cfg, ws_id, model_id, frames)
        counts = load_tables(warehouse_engine(), schema, spec, frames)
    except Exception as e:
        with session_scope() as s:
            m = s.get(DataModel, model_id)
            if m is not None:
                m.status, m.error = "error", f"{type(e).__name__}: {e}"[:2000]
        raise
    with session_scope() as s:
        m = s.get(DataModel, model_id)
        save_spec(m, spec)
        m.status = "review"
        m.build_info = {**(m.build_info or {}), "loaded_at": now_iso(), "table_rows": counts}
    log_event(logger, "model_analyzed", model_id=model_id, tables=len(spec.tables), relationships=len(spec.relationships),
              columns=len(spec.columns))
    return {"tables": len(spec.tables), "relationships": len(spec.relationships), "columns": len(spec.columns)}


def _carry_decisions(old: ModelSpec, new: ModelSpec) -> None:
    """Phân tích lại (file mới/đổi sheet chính) giữ các quyết định người dùng đã xác nhận nếu cột/quan hệ còn tồn tại."""
    if old.main_table != new.main_table:
        return
    if old.parameters.as_of_source == "user":
        new.parameters = old.parameters
    for orel in old.relationships:
        if orel.status == "confirmed" or any(e.kind == "user" for e in orel.evidence):
            if new.table(orel.source_table) is None:
                continue
            existing = next((r for r in new.relationships if r.id == orel.id), None)
            if existing is not None:
                new.relationships.remove(existing)
            new.relationships.append(orel)
        elif orel.status == "rejected":
            r = new.relationship(orel.id)
            if r is not None:
                r.status = "rejected"
    for oc in old.columns:
        nc = new.column(oc.name)
        if nc is None:
            continue
        if oc.status in ("confirmed", "rejected") or any(e.kind == "user" for e in oc.evidence):
            nc.rule, nc.status, nc.evidence, nc.note = oc.rule, oc.status, oc.evidence, oc.note


# ===================================================================================== job: build
def run_build(cfg: AppConfig, model_id: str, *, progress: Callable[[int, int, str], None] | None = None) -> dict[str, Any]:
    from modeling.document import build_blocks  # noqa: F401  (đảm bảo import được trước khi build)
    from modeling.validate import validate
    from modeling.warehouse import create_view, load_tables, read_view, warehouse_engine

    def step(i: int, note: str) -> None:
        if progress:
            progress(i, 4, note)

    with session_scope() as s:
        m = s.get(DataModel, model_id)
        if m is None or m.deleted_at is not None:
            return {"skipped": "model deleted"}
        spec = spec_of(m)
        ws_id, schema, revision = m.workspace_id, m.schema_name, m.revision
        m.status, m.error = "building", None
    eng = warehouse_engine()
    try:
        step(0, "tables")
        frames = load_frames(cfg, ws_id, model_id, [t.key for t in spec.tables])
        if spec.main_table not in frames:
            raise ValidationFailed("Thiếu dữ liệu đã đọc của mô hình — hãy phân tích lại")
        from sqlalchemy import inspect

        have = set(inspect(eng).get_table_names(schema=schema)) if schema in inspect(eng).get_schema_names() else set()
        if not all(t.key in have for t in spec.tables):
            load_tables(eng, schema, spec, frames)
        step(1, "view")
        sql, problems = create_view(eng, cfg, schema, spec)
        step(2, "validate")
        view_df = read_view(eng, schema, spec.view_name)
        validation = validate(spec, frames[spec.main_table], view_df)
        step(3, "publish")
        table_id = None
        if cfg.modeling.publish_to_analytics:
            table_id = _publish(cfg, model_id, spec, view_df)
    except Exception as e:
        with session_scope() as s:
            m = s.get(DataModel, model_id)
            if m is not None:
                m.status, m.error = "error", f"{type(e).__name__}: {e}"[:2000]
        raise
    with session_scope() as s:
        m = s.get(DataModel, model_id)
        m.status = "ready"
        m.validation = validation
        m.built_at = datetime.now(timezone.utc)
        m.build_info = {**(m.build_info or {}), "revision": revision, "sql": sql, "problems": problems, "view_rows": int(len(view_df)),
                        "built_at": now_iso(), "data_table_id": table_id or (m.build_info or {}).get("data_table_id")}
    log_event(logger, "model_built", model_id=model_id, score=validation.get("score"), rows=len(view_df))
    return {"view": f"{schema}.{spec.view_name}", "rows": int(len(view_df)), "score": validation.get("score")}


def _publish(cfg: AppConfig, model_id: str, spec: ModelSpec, view_df: pd.DataFrame) -> str | None:
    """View -> bảng số liệu của workspace để hỏi đáp/khám phá trong app dùng ngay (DuckDB/parquet)."""
    with session_scope() as s:
        m = s.get(DataModel, model_id)
        tid = (m.build_info or {}).get("data_table_id")
        t = s.get(DataTable, tid) if tid else None
        if not m.document_ids:
            return None
        if t is None:
            used = set(s.execute(select(DataTable.name).where(DataTable.workspace_id == m.workspace_id)).scalars())
            t = DataTable(workspace_id=m.workspace_id, document_id=m.document_ids[0], name=sql_identifier(spec.view_name, used),
                          title=m.name, source_location=f"Data model · {spec.view_name}", columns=[], row_count=0, storage_path="")
            s.add(t)
            s.flush()
        folder = os.path.join(cfg.analytics.storage_dir, m.workspace_id)
        os.makedirs(folder, exist_ok=True)
        t.storage_path = os.path.join(folder, f"{t.id}.parquet")
        df = view_df.copy()
        df.to_parquet(t.storage_path, index=False)
        cols = [{"name": "source_row", "original": "Excel row", "type": "integer", "examples": [], "nullable": False, "role": "id"}]
        for c in spec.columns:
            if c.name not in df.columns:
                continue
            ex = [str(_jsonable(v))[:60] for v in df[c.name].dropna().unique()[:3]]
            role = "id" if c.is_key else ("time" if c.type in ("date", "timestamp") else "measure" if c.type in ("integer", "double") else "dimension")
            cols.append({"name": c.name, "original": c.label, "type": c.type, "examples": ex, "nullable": bool(df[c.name].isna().any()),
                         "role": role, "description": X.column_text(spec, c)["vi"]})
        t.columns = cols
        t.row_count = int(len(df))
        t.title = m.name
        t.description = m.description or f"Kết quả mô hình dữ liệu “{m.name}” (tính lại từ {len(spec.tables)} sheet)."
        sample = [{k: _jsonable(v) for k, v in row.items()} for row in df.head(3).to_dict(orient="records")]
        t.semantic = {"columns": {}, "suggested_questions": [], "sample": sample, "data_model_id": m.id}
        t.is_active = True
        return t.id


# ===================================================================================== chỉnh sửa (API)
def _recheck(cfg: AppConfig, m: DataModel, spec: ModelSpec, names: list[str] | None = None) -> None:
    ev = evaluator(cfg, m, spec)
    checks, problems = ev.check_all(spec)
    for c in spec.columns:
        if names is not None and c.name not in names and c.rule.kind != "formula":
            continue
        c.check = checks.get(c.name)
        c.problem = problems.get(c.name) if c.rule.kind != "unresolved" else None


def set_column(cfg: AppConfig, m: DataModel, name: str, *, rule: Rule | None = None, status: str | None = None,
               note: str | None = None, alternative: int | None = None, user: str = "") -> ModelSpec:
    spec = spec_of(m)
    col = spec.column(name)
    if col is None:
        raise NotFoundError("Không có cột")
    if alternative is not None:
        if not 0 <= alternative < len(col.alternatives):
            raise ValidationFailed("Gợi ý không tồn tại")
        alt = col.alternatives.pop(alternative)
        col.alternatives.insert(0, Candidate(rule=col.rule, check=col.check, evidence=col.evidence, confidence=col.confidence))
        col.rule, col.check, col.evidence, col.confidence = alt.rule, alt.check, alt.evidence, alt.confidence
        col.alternatives = [a for a in col.alternatives if a.rule.kind != "unresolved"][:4]
        col.status = "confirmed"
    if rule is not None:
        _validate_rule(spec, rule)
        if rule.relationship_id:
            rule.table = spec.relationship(rule.relationship_id).source_table
        if col.rule.kind != "unresolved" and col.rule != rule:
            col.alternatives = ([Candidate(rule=col.rule, check=col.check, evidence=col.evidence, confidence=col.confidence)] + col.alternatives)[:4]
        col.rule = rule
        col.evidence = [Evidence(kind="user", detail={"by": user})] + [e for e in col.evidence if e.kind in ("formula", "pivot")]
        col.confidence = 1.0
        col.status = "confirmed"
    if status is not None:
        col.status = status  # type: ignore[assignment]
    if note is not None:
        col.note = note.strip() or None
    if rule is not None or alternative is not None:
        _recheck(cfg, m, spec)
    save_spec(m, spec)
    return spec


def _validate_rule(spec: ModelSpec, rule: Rule) -> None:
    from modeling import expr as E

    if rule.kind in ("lookup", "aggregate"):
        rel = spec.relationship(rule.relationship_id)
        if rel is None or rel.status == "rejected":
            raise ValidationFailed("Chọn một liên kết (quan hệ) hợp lệ")
        src = spec.table(rel.source_table)
        names = {c.name for c in src.columns}
        if rule.column and rule.column not in names:
            raise ValidationFailed(f"Sheet “{src.title}” không có cột {rule.column}")
        if rule.kind == "lookup" and not rule.column:
            raise ValidationFailed("Chọn cột cần lấy")
        if rule.kind == "aggregate" and (rule.func or "count") not in ("count",) and not rule.column:
            raise ValidationFailed("Chọn cột cần tính")
        for f in rule.filters:
            if f.column not in names:
                raise ValidationFailed(f"Sheet “{src.title}” không có cột {f.column}")
    elif rule.kind == "formula":
        try:
            node = E.parse(rule.expression or "")
        except E.ExprError as e:
            raise ValidationFailed(f"Công thức chưa đúng: {e}") from e
        known = {c.label.strip().lower() for c in spec.columns} | {c.name for c in spec.columns}
        missing = [r for r in E.columns_used(node) if r.strip().lower() not in known and r not in known]
        if missing:
            raise ValidationFailed("Không có cột: " + ", ".join(f"[{x}]" for x in missing))
    elif rule.kind == "input":
        main = spec.main()
        if rule.column and main and rule.column not in {c.name for c in main.columns}:
            raise ValidationFailed("Cột đầu vào không có trong sheet chính")


def test_rule(cfg: AppConfig, m: DataModel, name: str, rule: Rule) -> dict[str, Any]:
    spec = spec_of(m)
    if spec.column(name) is None:
        raise NotFoundError("Không có cột")
    _validate_rule(spec, rule)
    if rule.relationship_id:
        rule.table = spec.relationship(rule.relationship_id).source_table
    ev = evaluator(cfg, m, spec)
    check, err = ev.check_rule(spec, name, rule)
    return {"check": check.model_dump() if check else None, "error": err, "text": X.rule_text(spec, rule), "formula": X.rule_formula(spec, rule)}


def describe_column(cfg: AppConfig, llm: Any, m: DataModel, name: str, text: str) -> dict[str, Any]:
    from modeling.ai import rule_from_words

    if llm is None:
        raise ValidationFailed("Chưa cấu hình model AI — hãy chọn quy tắc thủ công")
    spec = spec_of(m)
    ev = evaluator(cfg, m, spec)
    before = {r.id for r in spec.relationships}
    cand, err = rule_from_words(cfg, llm, spec, ev, name, text)
    if cand is None:
        raise ValidationFailed(f"Chưa dịch được mô tả thành quy tắc ({err}). Hãy mô tả cụ thể hơn: lấy từ sheet nào, đếm/tổng gì, điều kiện gì.")
    new_rels = [r for r in spec.relationships if r.id not in before]
    if new_rels:
        save_spec(m, spec)
    return {"rule": cand.rule.model_dump(mode="json"), "check": cand.check.model_dump() if cand.check else None,
            "text": X.rule_text(spec, cand.rule), "formula": X.rule_formula(spec, cand.rule),
            "why": next((e.note for e in cand.evidence if e.note), None), "new_relationships": [r.id for r in new_rels]}


def confirm(m: DataModel, *, columns: list[str] | None, min_confidence: float = 0.85, include_relationships: bool = True) -> int:
    spec = spec_of(m)
    n = 0
    for c in spec.columns:
        if c.status != "proposed" or c.rule.kind == "unresolved":
            continue
        if columns is not None:
            ok = c.name in columns
        else:
            rate = c.check.rate if c.check else None
            ok = c.confidence >= min_confidence and (rate is None or rate >= 0.98 or c.rule.kind == "input")
        if ok:
            c.status = "confirmed"
            n += 1
    if include_relationships:
        used = {c.rule.relationship_id for c in spec.columns if c.status == "confirmed" and c.rule.relationship_id}
        for r in spec.relationships:
            if r.status == "proposed" and r.id in used:
                r.status = "confirmed"
    save_spec(m, spec)
    return n


def set_relationship(cfg: AppConfig, m: DataModel, rid: str, *, status: str | None = None, keys: list[KeyPair] | None = None,
                     user: str = "") -> ModelSpec:
    from modeling.infer import measure_relationship

    spec = spec_of(m)
    rel = spec.relationship(rid)
    if rel is None:
        raise NotFoundError("Không có quan hệ")
    if keys is not None:
        _check_keys(spec, rel.source_table, keys)
        rel.keys = keys
        rel.evidence = [Evidence(kind="user", detail={"by": user})] + [e for e in rel.evidence if e.kind == "formula"]
        rel.status = "confirmed"
        ev = evaluator(cfg, m, spec)
        measure_relationship(rel, ev.frames)
    if status is not None:
        rel.status = status  # type: ignore[assignment]
    _recheck(cfg, m, spec)
    save_spec(m, spec)
    return spec


def add_relationship(cfg: AppConfig, m: DataModel, source_table: str, keys: list[KeyPair], user: str = "") -> Relationship:
    from modeling.infer import measure_relationship

    spec = spec_of(m)
    if spec.table(source_table) is None or source_table == spec.main_table:
        raise ValidationFailed("Sheet nguồn không hợp lệ")
    _check_keys(spec, source_table, keys)
    rid = f"r_{source_table}"
    n = 2
    while spec.relationship(rid):
        rid = f"r_{source_table}_{n}"
        n += 1
    rel = Relationship(id=rid, source_table=source_table, target_table=spec.main_table, keys=keys, status="confirmed", confidence=1.0,
                       evidence=[Evidence(kind="user", detail={"by": user})])
    ev = evaluator(cfg, m, spec)
    measure_relationship(rel, ev.frames)
    spec.relationships.append(rel)
    t = spec.table(source_table)
    if t is not None and t.role == "ignored":
        t.role = "source"
    save_spec(m, spec)
    return rel


def _check_keys(spec: ModelSpec, source_table: str, keys: list[KeyPair]) -> None:
    main, src = spec.main(), spec.table(source_table)
    if not keys or main is None or src is None:
        raise ValidationFailed("Cần ít nhất một cặp khoá")
    mn, sn = {c.name for c in main.columns}, {c.name for c in src.columns}
    for kp in keys:
        if kp.base_column not in mn or kp.source_column not in sn:
            raise ValidationFailed("Cột khoá không tồn tại")


def set_parameters(cfg: AppConfig, m: DataModel, as_of_date: str | None) -> ModelSpec:
    spec = spec_of(m)
    if as_of_date not in (None, "", "today"):
        try:
            datetime.strptime(as_of_date[:10], "%Y-%m-%d")
        except ValueError as e:
            raise ValidationFailed("Ngày báo cáo phải có dạng YYYY-MM-DD") from e
    spec.parameters = ModelParameters(as_of_date=(as_of_date or "today")[:10] if as_of_date != "today" else "today", as_of_source="user")
    _recheck(cfg, m, spec)
    save_spec(m, spec)
    return spec


def set_table_role(m: DataModel, key: str, role: str) -> ModelSpec:
    spec = spec_of(m)
    t = spec.table(key)
    if t is None or key == spec.main_table:
        raise ValidationFailed("Không đổi được vai trò sheet này")
    t.role = role  # type: ignore[assignment]
    if role == "ignored":
        for r in spec.relationships:
            if r.source_table == key:
                r.status = "rejected"
    save_spec(m, spec)
    return spec


# ===================================================================================== serialize
def _ev_list(spec: ModelSpec, evs: list[Evidence]) -> list[dict[str, Any]]:
    return [{"kind": e.kind, "text": X.evidence_text(spec, e), "detail": e.detail, "note": e.note} for e in evs]


def needs_attention(c: Any) -> bool:
    if c.status == "rejected":
        return True
    if c.rule.kind == "unresolved":
        return True
    if c.status == "confirmed":
        return False
    rate = c.check.rate if c.check else None
    return c.rule.kind != "input" and ((rate is not None and rate < 0.98) or c.confidence < 0.7)


def counts(spec: ModelSpec) -> dict[str, int]:
    return {"tables": len(spec.tables), "sources": sum(1 for t in spec.tables if t.role == "source"),
            "relationships": len(spec.active_relationships()), "columns": len(spec.columns),
            "calculated": sum(1 for c in spec.columns if c.rule.kind not in ("input", "constant")),
            "confirmed": sum(1 for c in spec.columns if c.status == "confirmed"),
            "attention": sum(1 for c in spec.columns if needs_attention(c)),
            "unresolved": sum(1 for c in spec.columns if c.rule.kind == "unresolved"),
            "matching": sum(1 for c in spec.columns if c.rule.kind not in ("input", "constant", "unresolved") and c.check and (c.check.rate or 0) >= 0.999)}


def serialize(s: Session, m: DataModel, *, detail: bool = False) -> dict[str, Any]:
    from worker.queue import job_view

    spec = spec_of(m)
    job = s.get(Job, m.job_id) if m.job_id else None
    v = m.validation or {}
    out: dict[str, Any] = {
        "id": m.id, "name": m.name, "description": m.description, "status": m.status, "document_ids": m.document_ids,
        "schema_name": m.schema_name, "view_name": spec.view_name, "error": m.error, "revision": m.revision,
        "built_revision": (m.build_info or {}).get("revision"), "stale": bool(m.built_at) and (m.build_info or {}).get("revision") != m.revision,
        "created_at": m.created_at, "updated_at": m.updated_at, "built_at": m.built_at, "created_by": m.created_by,
        "job": job_view(job) if job else None, "counts": counts(spec) if m.spec else None,
        "validation_summary": {"score": v.get("score"), **(v.get("summary") or {}), "rows_aligned": v.get("rows_aligned"),
                               "view_rows": v.get("view_rows"), "excel_rows": v.get("excel_rows")} if v else None,
        "main_title": spec.main().title if spec.main() else None,
    }
    if not detail:
        return out
    docs = s.execute(select(Document.id, Document.title, Document.filename).where(Document.id.in_(m.document_ids))).all()
    out["documents"] = [{"id": d.id, "title": d.title, "filename": d.filename} for d in docs]
    out["spec"] = spec.model_dump(mode="json")
    out["validation"] = v
    out["texts"] = {
        "columns": {c.name: {"rule": X.column_text(spec, c), "formula": X.rule_formula(spec, c.rule), "evidence": _ev_list(spec, c.evidence),
                             "attention": needs_attention(c),
                             "alternatives": [{"rule": X.rule_text(spec, a.rule), "formula": X.rule_formula(spec, a.rule),
                                               "evidence": _ev_list(spec, a.evidence)} for a in c.alternatives]} for c in spec.columns},
        "relationships": {r.id: {"text": X.relationship_text(spec, r), "keys": X.keys_text(spec, r), "evidence": _ev_list(spec, r.evidence),
                                 "used_by": [c.name for c in spec.columns if c.rule.relationship_id == r.id]} for r in spec.relationships},
        "notices": [{"code": n.code, "level": n.level, **X.notice_text(n.code, n.params)} for n in spec.notices],
    }
    out["build"] = {k: (m.build_info or {}).get(k) for k in ("sql", "problems", "view_rows", "built_at", "data_table_id", "revision", "table_rows")}
    return out


def transform_list(items: list[dict[str, Any]] | None) -> list[Transform]:
    return [Transform.model_validate(x) for x in (items or [])]
