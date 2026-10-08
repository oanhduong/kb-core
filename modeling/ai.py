"""AI đề xuất quy tắc cho cột chưa giải thích được (và dịch mô tả bằng lời của người dùng thành quy tắc).

AI chỉ ĐỀ XUẤT trong ngôn ngữ quy tắc khai báo (modeling/spec.Rule) — không viết SQL. Mọi đề xuất được chạy thật trên
dữ liệu (DuckEval) và gắn tỉ lệ khớp với Excel trước khi tới tay người duyệt; đề xuất sai cú pháp/tham chiếu sai bị loại.
Dữ liệu khách hàng (tên sheet, header, giá trị mẫu) luôn được bọc <untrusted_document> chống prompt injection.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field, ValidationError

from core.config import AppConfig, read_prompt
from core.llm import LLMOutputInvalid, LLMUnavailable
from core.logging import log_event
from modeling.evaluate import DuckEval
from modeling.explain import keys_text
from modeling.infer import measure_relationship
from modeling.spec import Candidate, Evidence, KeyPair, ModelSpec, Relationship, Rule
from security.provenance import wrap_untrusted

logger = logging.getLogger(__name__)
BATCH = 10


class _NewRel(BaseModel):
    source_table: str
    base_column: str
    source_column: str


class _Item(BaseModel):
    name: str
    rule: dict[str, Any]
    new_relationship: _NewRel | None = None
    why: str = ""
    why_vi: str = ""


class _Out(BaseModel):
    columns: list[_Item] = Field(default_factory=list)


def _values_line(df: pd.DataFrame, col: str, ctype: str, distinct: int) -> str:
    s = df[col].dropna()
    if s.empty:
        return "all empty"
    if ctype in ("text", "boolean") and distinct <= 15:
        vc = s.astype(str).value_counts().head(15)
        return "values: " + ", ".join(f"{k} ({v})" for k, v in vc.items())
    if ctype in ("integer", "double"):
        return f"min {s.min()}, max {s.max()}, e.g. " + ", ".join(str(v) for v in s.head(4).tolist())
    if ctype in ("date", "timestamp"):
        d = pd.to_datetime(s, errors="coerce").dropna()
        return f"from {d.min().date()} to {d.max().date()}" if not d.empty else "dates"
    return "e.g. " + ", ".join(str(v)[:30] for v in s.head(4).tolist())


def catalog(spec: ModelSpec, frames: dict[str, pd.DataFrame], targets: list[str]) -> str:
    main = spec.main()
    mdf = frames[main.key]
    lines = [f"MAIN SHEET “{main.title}” = table {main.key} ({main.row_count} rows). Report date AS_OF() = {spec.parameters.as_of_date or 'today'}.",
             "", "Columns to explain (SQL name | label | group | type | data):"]
    tset = set(targets)
    for c in spec.columns:
        if c.name in tset:
            mc = next((x for x in main.columns if x.name == c.name), None)
            lines.append(f"- {c.name} | {c.label} | {c.group or '-'} | {c.type} | {_values_line(mdf, c.name, c.type, mc.distinct if mc else 99)}")
    lines += ["", "Other main-sheet columns usable in formulas (label | type | current rule):"]
    for c in spec.columns:
        if c.name not in tset:
            lines.append(f"- [{c.label}] | {c.type} | {c.rule.kind}")
    lines += ["", "SUPPORTING TABLES and how they link to the main sheet:"]
    rel_by_src: dict[str, list[Relationship]] = {}
    for r in spec.active_relationships():
        rel_by_src.setdefault(r.source_table, []).append(r)
    for t in spec.tables:
        if t.key == main.key or t.role == "ignored" and t.key not in rel_by_src:
            continue
        df = frames.get(t.key)
        if df is None:
            continue
        rels = rel_by_src.get(t.key, [])
        link = "; ".join(f"{r.id}: {keys_text(spec, r)['en']} ({'one row' if r.cardinality == 'one_to_one' else 'many rows'} per main row)" for r in rels) or "no link found"
        lines.append(f"## table {t.key} — sheet “{t.title}”, {t.row_count} rows — {link}")
        for c in t.columns:
            lines.append(f"- {c.name} | {c.label} | {c.type} | {_values_line(df, c.name, c.type, c.distinct)}")
        for p in t.pivots:
            vals = ", ".join(f"{v.func} of {v.field}" for v in p.values)
            filt = "; ".join(f"{f.field}: {'only ' + ', '.join(f.selected) if f.selected else 'excluding ' + ', '.join(f.excluded) if f.excluded else 'all'}" for f in p.filters)
            lines.append(f"  pivot “{p.name}”: {vals} by {', '.join(p.rows)}{' — filters ' + filt if filt else ''}")
    return "\n".join(lines)


def _candidate(spec: ModelSpec, frames: dict[str, pd.DataFrame], ev: DuckEval, item: _Item, *, user_text: str | None = None) -> Candidate | None:
    col = spec.column(item.name)
    if col is None:
        return None
    raw = dict(item.rule)
    if raw.get("kind") == "formula" and not raw.get("expression"):  # model đôi khi đặt công thức ở key khác
        raw["expression"] = raw.get("formula") or raw.get("expr") or raw.get("value")
    try:
        rule = Rule.model_validate(raw)
    except ValidationError as e:
        log_event(logger, "model_ai_rule_invalid", level=logging.WARNING, column=item.name, error=str(e)[:300])
        return None
    if rule.kind in ("lookup", "aggregate"):
        if item.new_relationship is not None and not rule.relationship_id:
            nr = item.new_relationship
            if spec.table(nr.source_table) is None:
                return None
            rid = f"r_{nr.source_table}_ai"
            n = 2
            while spec.relationship(rid):
                rid = f"r_{nr.source_table}_ai{n}"
                n += 1
            rel = Relationship(id=rid, source_table=nr.source_table, target_table=spec.main_table,
                               keys=[KeyPair(base_column=nr.base_column, source_column=nr.source_column)], confidence=0.5,
                               evidence=[Evidence(kind="ai", note=item.why or None)])
            measure_relationship(rel, frames)
            spec.relationships.append(rel)
            rule.relationship_id = rid
        rel = spec.relationship(rule.relationship_id)
        if rel is None:
            return None
        rule.table = rel.source_table
    check, err = ev.check_rule(spec, item.name, rule)
    if err:
        log_event(logger, "model_ai_rule_failed", level=logging.WARNING, column=item.name, error=err[:300])
        return None
    rate = check.rate if check else None
    conf = 0.3 if rate is None else round(min(0.9, 0.25 + 0.65 * rate), 3)
    evidence = [Evidence(kind="ai" if user_text is None else "user", note=item.why or None,
                         detail={"why_vi": item.why_vi} if item.why_vi else ({"text": user_text} if user_text else {}))]
    if check and check.compared:
        evidence.append(Evidence(kind="data_match", detail={"matched": check.matched, "compared": check.compared}))
    return Candidate(rule=rule, check=check, evidence=evidence, confidence=conf)


def make_proposer(cfg: AppConfig, llm: Any):
    """-> hàm (spec, targets, ev) -> {cột: [Candidate]} dùng trong infer.analyze."""

    def propose(spec: ModelSpec, targets: list[str], ev: DuckEval) -> dict[str, list[Candidate]]:
        out: dict[str, list[Candidate]] = {}
        frames = ev.frames
        for i in range(0, len(targets), BATCH):
            batch = targets[i:i + BATCH]
            user = ("Propose a rule for each of these columns: " + ", ".join(batch) + "\n\n"
                    + wrap_untrusted(catalog(spec, frames, batch), source=f"workbook {spec.main().title if spec.main() else ''}", doc_type="table"))
            try:
                res = llm.complete_json("data_model", system=read_prompt("data_model_rules", cfg), user=user, schema=_Out,
                                        max_tokens=cfg.modeling.ai_max_tokens, max_retries=1)
            except (LLMOutputInvalid, LLMUnavailable) as e:
                log_event(logger, "model_ai_failed", level=logging.WARNING, error=str(e)[:300])
                continue
            for item in res.columns:
                if item.name not in batch:
                    continue
                cand = _candidate(spec, frames, ev, item)
                if cand is not None:
                    out.setdefault(item.name, []).append(cand)
        return out

    return propose


def rule_from_words(cfg: AppConfig, llm: Any, spec: ModelSpec, ev: DuckEval, column: str, text: str) -> tuple[Candidate | None, str | None]:
    """Người dùng mô tả logic bằng lời -> 1 quy tắc (đã kiểm chứng trên dữ liệu)."""
    col = spec.column(column)
    if col is None:
        return None, "unknown_column"
    user = (f"The reviewer describes how column {column} (“{col.label}”) is calculated, in their own words:\n"
            + wrap_untrusted(text[:2000], source="reviewer description", doc_type="user_text")
            + f"\n\nTranslate exactly this description into a rule for {column}. If the description is ambiguous, choose the reading that"
              " reproduces the data best.\n\n"
            + wrap_untrusted(catalog(spec, ev.frames, [column]), source="workbook catalog", doc_type="table"))
    try:
        res = llm.complete_json("data_model", system=read_prompt("data_model_rules", cfg), user=user, schema=_Out,
                                max_tokens=cfg.modeling.ai_max_tokens, max_retries=2)
    except (LLMOutputInvalid, LLMUnavailable) as e:
        return None, str(e)[:300]
    item = next((x for x in res.columns if x.name == column), res.columns[0] if res.columns else None)
    if item is None:
        return None, "empty"
    item.name = column
    cand = _candidate(spec, ev.frames, ev, item, user_text=text)
    if cand is None:
        return None, "invalid_rule"
    return cand, None

