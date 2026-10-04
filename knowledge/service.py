"""Truy vấn knowledge graph mở cho UI/assistant: danh sách & tìm entity, chi tiết (nguồn, quan hệ),
lân cận 1–2 bước, tổng quan workspace, gộp/sửa entity (admin)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from core.errors import NotFoundError, ValidationFailed
from core.models import Document, Entity, EntityMention, KBRecordRow, Relation
from graph.resolve import fold_text, normalize_text


def entity_view(e: Entity) -> dict[str, Any]:
    return {"id": e.id, "type": e.type, "name": e.name, "aliases": e.aliases or [], "description": e.description,
            "mention_count": e.mention_count, "document_count": len(e.document_ids or []), "properties": e.properties or {}}


def list_entities(session: Session, workspace_id: str, *, q: str | None = None, type_: str | None = None,
                  document_id: str | None = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    query = select(Entity).where(Entity.workspace_id == workspace_id)
    if type_:
        query = query.where(Entity.type == type_)
    if q:
        fq = fold_text(q)
        query = query.where(or_(Entity.name_norm.ilike(f"%{fq}%"), Entity.name.ilike(f"%{q}%")))
    if document_id:
        query = query.where(Entity.document_ids.contains([document_id]))
    total = session.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = session.execute(query.order_by(Entity.mention_count.desc(), Entity.name).offset(offset).limit(limit)).scalars().all()
    return {"total": total, "items": [entity_view(e) for e in rows]}


def get_entity(session: Session, workspace_id: str, entity_id: str) -> Entity:
    e = session.get(Entity, entity_id)
    if e is None or e.workspace_id != workspace_id:
        raise NotFoundError("Không có thực thể")
    return e


def entity_detail(session: Session, workspace_id: str, entity_id: str, *, mention_limit: int = 30) -> dict[str, Any]:
    e = get_entity(session, workspace_id, entity_id)
    mentions = session.execute(
        select(EntityMention, Document.title, Document.source_type, KBRecordRow.location)
        .join(Document, Document.id == EntityMention.document_id)
        .join(KBRecordRow, KBRecordRow.record_id == EntityMention.record_id, isouter=True)
        .where(EntityMention.entity_id == entity_id).order_by(EntityMention.id).limit(mention_limit)).all()
    rels = session.execute(
        select(Relation, Entity).join(Entity, Entity.id == Relation.target_id).where(Relation.source_id == entity_id)).all()
    rels_in = session.execute(
        select(Relation, Entity).join(Entity, Entity.id == Relation.source_id).where(Relation.target_id == entity_id)).all()
    from qa.citation import format_citation

    return {
        **entity_view(e),
        "mentions": [{"id": m.id, "document_id": m.document_id, "document_title": t, "record_id": m.record_id, "location": loc,
                      "citation": format_citation(t, st, loc or ""), "snippet": m.snippet} for m, t, st, loc in mentions],
        "relations_out": [{"id": r.id, "type": r.type, "label": r.label, "evidence": r.evidence, "confidence": r.confidence,
                           "document_id": r.document_id, "record_id": r.record_id, "entity": entity_view(t)} for r, t in rels],
        "relations_in": [{"id": r.id, "type": r.type, "label": r.label, "evidence": r.evidence, "confidence": r.confidence,
                          "document_id": r.document_id, "record_id": r.record_id, "entity": entity_view(sr)} for r, sr in rels_in],
    }


def neighborhood(session: Session, workspace_id: str, entity_ids: list[str], *, depth: int = 1, limit_nodes: int = 150) -> dict[str, Any]:
    """Đồ thị con quanh 1 hoặc nhiều entity (depth 1–2) -> {nodes, edges} cho UI."""
    depth = max(1, min(depth, 2))
    seeds = [get_entity(session, workspace_id, i).id for i in entity_ids]
    nodes: dict[str, Entity] = {}
    frontier = set(seeds)
    edges: dict[str, Relation] = {}
    for _ in range(depth):
        if not frontier or len(nodes) >= limit_nodes:
            break
        rels = session.execute(select(Relation).where(Relation.workspace_id == workspace_id,
                                                      or_(Relation.source_id.in_(frontier), Relation.target_id.in_(frontier)))
                               .order_by(Relation.confidence.desc()).limit(limit_nodes * 4)).scalars().all()
        nxt: set[str] = set()
        for r in rels:
            edges[r.id] = r
            nxt.update({r.source_id, r.target_id})
        frontier = nxt - set(nodes) - set(frontier) if nodes else nxt
        ids = set(edges_endpoints(edges)) | set(seeds)
        for e in session.execute(select(Entity).where(Entity.id.in_(ids))).scalars():
            nodes[e.id] = e
        if len(nodes) >= limit_nodes:
            break
    keep = set(list(nodes)[:limit_nodes]) | set(seeds)
    return {
        "seeds": seeds,
        "nodes": [{**entity_view(e), "is_seed": e.id in seeds} for e in nodes.values() if e.id in keep],
        "edges": [{"id": r.id, "source": r.source_id, "target": r.target_id, "type": r.type, "label": r.label,
                   "confidence": r.confidence, "document_id": r.document_id, "record_id": r.record_id, "evidence": r.evidence}
                  for r in edges.values() if r.source_id in keep and r.target_id in keep],
    }


def edges_endpoints(edges: dict[str, Relation]) -> list[str]:
    out = []
    for r in edges.values():
        out += [r.source_id, r.target_id]
    return out


def overview(session: Session, workspace_id: str, *, top: int = 40) -> dict[str, Any]:
    """Tổng quan: số entity theo loại, số quan hệ theo loại, đồ thị con của top entity + cạnh giữa chúng."""
    type_counts = dict(session.execute(select(Entity.type, func.count()).where(Entity.workspace_id == workspace_id)
                                       .group_by(Entity.type).order_by(func.count().desc())).all())
    rel_counts = dict(session.execute(select(Relation.type, func.count()).where(Relation.workspace_id == workspace_id)
                                      .group_by(Relation.type).order_by(func.count().desc()).limit(30)).all())
    top_entities = session.execute(select(Entity).where(Entity.workspace_id == workspace_id)
                                   .order_by(Entity.mention_count.desc(), Entity.name).limit(top)).scalars().all()
    ids = {e.id for e in top_entities}
    rels = session.execute(select(Relation).where(Relation.workspace_id == workspace_id, Relation.source_id.in_(ids),
                                                  Relation.target_id.in_(ids))).scalars().all() if ids else []
    # nếu top entity ít nối nhau, bổ sung cạnh mạnh nhất ra ngoài để đồ thị không rời rạc
    if ids and len(rels) < len(ids) // 2:
        extra = session.execute(select(Relation).where(Relation.workspace_id == workspace_id, or_(Relation.source_id.in_(ids), Relation.target_id.in_(ids)))
                                .order_by(Relation.confidence.desc()).limit(top)).scalars().all()
        more_ids = {r.source_id for r in extra} | {r.target_id for r in extra}
        for e in session.execute(select(Entity).where(Entity.id.in_(more_ids - ids))).scalars():
            top_entities.append(e)
        ids |= more_ids
        rels = list({r.id: r for r in [*rels, *extra]}.values())
    return {
        "entity_count": int(sum(type_counts.values())), "relation_count": int(session.execute(select(func.count()).select_from(Relation).where(Relation.workspace_id == workspace_id)).scalar_one()),
        "types": [{"type": t, "count": int(n)} for t, n in type_counts.items()],
        "relation_types": [{"type": t, "count": int(n)} for t, n in rel_counts.items()],
        "nodes": [entity_view(e) for e in top_entities],
        "edges": [{"id": r.id, "source": r.source_id, "target": r.target_id, "type": r.type, "label": r.label, "confidence": r.confidence,
                   "document_id": r.document_id, "record_id": r.record_id} for r in rels],
    }


def merge_entities(session: Session, workspace_id: str, keep_id: str, merge_ids: list[str]) -> Entity:
    keep = get_entity(session, workspace_id, keep_id)
    for mid in merge_ids:
        if mid == keep_id:
            continue
        other = get_entity(session, workspace_id, mid)
        session.execute(update(EntityMention).where(EntityMention.entity_id == mid).values(entity_id=keep_id))
        session.execute(update(Relation).where(Relation.source_id == mid).values(source_id=keep_id))
        session.execute(update(Relation).where(Relation.target_id == mid).values(target_id=keep_id))
        aliases = [*(keep.aliases or []), other.name, *(other.aliases or [])]
        keep.aliases = [a for i, a in enumerate(aliases) if a and fold_text(a) != keep.name_norm and fold_text(a) not in {fold_text(x) for x in aliases[:i]}][:20]
        if other.description and not keep.description:
            keep.description = other.description
        session.delete(other)
    session.flush()
    # quan hệ tự trỏ sau gộp -> xoá
    for r in session.execute(select(Relation).where(Relation.source_id == keep_id, Relation.target_id == keep_id)).scalars():
        session.delete(r)
    from knowledge.extract import _recount

    _recount(session, workspace_id)
    return keep


def update_entity(session: Session, workspace_id: str, entity_id: str, *, name: str | None = None, type_: str | None = None,
                  description: str | None = None, aliases: list[str] | None = None) -> Entity:
    from knowledge.extract import ENTITY_TYPES

    e = get_entity(session, workspace_id, entity_id)
    if name is not None:
        name = normalize_text(name)
        if not name:
            raise ValidationFailed("Tên không được rỗng")
        e.name, e.name_norm = name, fold_text(name)
    if type_ is not None:
        if type_ not in ENTITY_TYPES:
            raise ValidationFailed(f"type phải là một trong {ENTITY_TYPES}")
        e.type = type_
    if description is not None:
        e.description = description
    if aliases is not None:
        e.aliases = [normalize_text(a) for a in aliases if normalize_text(a)][:20]
    session.flush()
    return e


def delete_entity(session: Session, workspace_id: str, entity_id: str) -> None:
    session.delete(get_entity(session, workspace_id, entity_id))
