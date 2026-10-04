"""Graph store: Apache AGE trên CÙNG Postgres với pgvector (không thêm hệ thống mới) + bản in-memory
(networkx) cùng interface cho unit test và CLI dry-run.

Mô hình dữ liệu trên mỗi node/edge:
    source_chunk_ids: list record_id của "chunk gốc" (1 dòng xlsx, 1 Điều docx, 1 đoạn transcript —
                      xem kb/ids.py) đã sinh ra node/edge này.
    source_docs:      list document_id đóng góp — dùng cho cập nhật incremental (A6): document mới
                      ingest/thay thế/xoá -> remove_document(doc) rồi build lại riêng phần của doc đó.
    extracted_by:     "rule" | "llm" (edge do graph/extract.py sinh ra có thêm confidence, evidence).

Merge khi upsert: properties ghi đè (SET +=), source_chunk_ids/source_docs HỢP (không trùng lặp) —
1 SKU xuất hiện ở bảng giá + tồn kho 8 cửa hàng vẫn là 1 node, giữ đủ nguồn.

remove_document(doc): bỏ doc khỏi source_docs/source_chunk_ids; edge không còn nguồn -> xoá; node
không còn nguồn VÀ không còn cạnh -> xoá; node không còn nguồn nhưng còn cạnh của doc khác -> giữ,
đánh dấu stale=true (vd SKU còn STOCKS từ file tồn kho nhưng bảng giá gốc đã bị xoá).

An toàn Cypher: label/edge type luôn qua _validate_identifier (whitelist ký tự); giá trị luôn qua
_cypher_literal (escape \\, ', xuống dòng, và '$' -> \\u0024 để không phá dollar-quote $cy$).
"""
from __future__ import annotations

import json
import logging
import re
import threading
from abc import ABC, abstractmethod
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from graph.schema import NODE_LABELS

logger = logging.getLogger(__name__)

DEFAULT_GRAPH_NAME = "retail_kb"
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROP_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_AGTYPE_SUFFIX_RE = re.compile(r"::(?:vertex|edge|path|numeric|integer|float)\b")


class GraphUnavailable(Exception):
    """Không kết nối/truy vấn được graph (AGE thiếu, DB lỗi) — router fallback về vector (B9)."""


def _validate_identifier(name: str, what: str) -> str:
    if not _IDENT_RE.match(name or ""):
        raise ValueError(f"{what} không hợp lệ (chỉ chữ/số/gạch dưới, bắt đầu bằng chữ): {name!r}")
    return name


def _cypher_string(value: str) -> str:
    escaped = (value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
               .replace("\r", "\\r").replace("\t", "\\t").replace("$", "\\u0024"))
    return f"'{escaped}'"


def _cypher_literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value) if value == value and value not in (float("inf"), float("-inf")) else "null"
    if isinstance(value, str):
        return _cypher_string(value)
    if isinstance(value, (list, tuple, set)):
        return "[" + ", ".join(_cypher_literal(v) for v in value) + "]"
    if isinstance(value, dict):
        pairs = []
        for k, v in value.items():
            if not _PROP_KEY_RE.match(str(k)):
                raise ValueError(f"Tên thuộc tính không hợp lệ để làm key Cypher: {k!r}")
            pairs.append(f"{k}: {_cypher_literal(v)}")
        return "{" + ", ".join(pairs) + "}"
    return _cypher_string(str(value))  # date/Decimal/... -> chuỗi


def _parse_agtype(text: str | None) -> Any:
    if text is None:
        return None
    return json.loads(_AGTYPE_SUFFIX_RE.sub("", text))


# =====================================================================================
# KIỂU DỮ LIỆU
# =====================================================================================

@dataclass(frozen=True)
class NodeRef:
    label: str
    id: str


@dataclass
class GraphNode:
    label: str
    id: str
    props: dict[str, Any] = field(default_factory=dict)

    @property
    def ref(self) -> NodeRef:
        return NodeRef(self.label, self.id)

    @property
    def source_chunk_ids(self) -> list[str]:
        return list(self.props.get("source_chunk_ids") or [])


@dataclass
class GraphEdge:
    type: str
    src: NodeRef
    dst: NodeRef
    props: dict[str, Any] = field(default_factory=dict)

    @property
    def source_chunk_ids(self) -> list[str]:
        return list(self.props.get("source_chunk_ids") or [])


@dataclass
class GraphPath:
    nodes: list[GraphNode]
    edges: list[GraphEdge]

    @property
    def length(self) -> int:
        return len(self.edges)

    def key(self) -> tuple:
        return tuple((n.label, n.id) for n in self.nodes) + tuple(e.type for e in self.edges)


def _node_payload(n: dict[str, Any]) -> tuple[str, str, dict[str, Any], list[str], list[str]]:
    label = _validate_identifier(n["label"], "Label")
    props = {k: v for k, v in (n.get("properties") or {}).items() if v is not None}
    return label, str(n["id"]), props, list(n.get("source_chunk_ids") or []), list(n.get("source_docs") or [])


# =====================================================================================
# INTERFACE
# =====================================================================================

class GraphStore(ABC):
    name: str = "abstract"

    @abstractmethod
    def upsert_nodes(self, nodes: list[dict[str, Any]]) -> int: ...

    @abstractmethod
    def upsert_edges(self, edges: list[dict[str, Any]]) -> int: ...

    @abstractmethod
    def remove_document(self, document_id: str) -> dict[str, int]: ...

    @abstractmethod
    def clear(self) -> None: ...

    @abstractmethod
    def get_node(self, label: str, node_id: str) -> GraphNode | None: ...

    @abstractmethod
    def list_nodes(self, label: str, limit: int | None = None) -> list[GraphNode]: ...

    @abstractmethod
    def paths_from(self, starts: list[NodeRef], max_hops: int, limit: int) -> list[GraphPath]:
        """Mọi đường đi đơn (không lặp node) dài 1..max_hops từ các node khởi điểm, vô hướng.
        Đường đi 2 hop kết thúc ở node CÙNG loại với node khởi điểm (vd SKU -> ngành hàng -> SKU khác)
        bị bỏ, trừ khi node cuối cũng là 1 node khởi điểm — các "hub" (ngành hàng, NCC, cửa hàng) nối
        tới hàng trăm node cùng loại, giữ lại sẽ làm nổ số đường đi mà không mang thông tin."""

    @abstractmethod
    def edges_of_type(self, edge_type: str, limit: int | None = None) -> list[GraphEdge]: ...

    @abstractmethod
    def stats(self) -> dict[str, Any]: ...

    def healthcheck(self) -> bool:
        self.stats()
        return True

    def close(self) -> None:
        """No-op mặc định."""

    def write_accumulator(self, acc: Any) -> dict[str, int]:
        """Ghi cả GraphAccumulator (graph/model.py): node trước, edge sau."""
        n = self.upsert_nodes(acc.node_list())
        e = self.upsert_edges(acc.edge_list())
        return {"nodes": n, "edges": e}


# =====================================================================================
# IN-MEMORY (networkx) — test + CLI dry-run
# =====================================================================================

class MemoryGraphStore(GraphStore):
    name = "memory"

    def __init__(self) -> None:
        import networkx as nx

        self.g = nx.MultiDiGraph()
        self._lock = threading.Lock()

    @staticmethod
    def _k(label: str, node_id: str) -> str:
        return f"{label}:{node_id}"

    @staticmethod
    def _merge_lists(existing: list[str] | None, new: list[str]) -> list[str]:
        out = list(existing or [])
        out.extend(v for v in new if v not in out)
        return out

    def upsert_nodes(self, nodes: list[dict[str, Any]]) -> int:
        with self._lock:
            for n in nodes:
                label, node_id, props, src, docs = _node_payload(n)
                key = self._k(label, node_id)
                if key not in self.g:
                    self.g.add_node(key, label=label, props={"id": node_id})
                data = self.g.nodes[key]["props"]
                data.update(props)
                data["id"] = node_id
                data["source_chunk_ids"] = self._merge_lists(data.get("source_chunk_ids"), src)
                data["source_docs"] = self._merge_lists(data.get("source_docs"), docs)
                data.pop("stale", None) if docs else None
        return len(nodes)

    def upsert_edges(self, edges: list[dict[str, Any]]) -> int:
        written = 0
        with self._lock:
            for e in edges:
                etype = _validate_identifier(e["type"], "Edge type")
                a = self._k(e["from_label"], str(e["from_id"]))
                b = self._k(e["to_label"], str(e["to_id"]))
                if a not in self.g or b not in self.g:
                    logger.debug("bỏ qua edge %s: thiếu node %s hoặc %s", etype, a, b)
                    continue
                props = {k: v for k, v in (e.get("properties") or {}).items() if v is not None}
                if self.g.has_edge(a, b, key=etype):
                    data = self.g.edges[a, b, etype]["props"]
                else:
                    self.g.add_edge(a, b, key=etype, props={})
                    data = self.g.edges[a, b, etype]["props"]
                data.update(props)
                data["source_chunk_ids"] = self._merge_lists(data.get("source_chunk_ids"), list(e.get("source_chunk_ids") or []))
                data["source_docs"] = self._merge_lists(data.get("source_docs"), list(e.get("source_docs") or []))
                written += 1
        return written

    def remove_document(self, document_id: str) -> dict[str, int]:
        prefix = f"{document_id}:"
        removed_edges = removed_nodes = stale = 0
        with self._lock:
            for a, b, k, data in list(self.g.edges(keys=True, data=True)):
                props = data["props"]
                if document_id not in (props.get("source_docs") or []):
                    continue
                props["source_docs"] = [d for d in props["source_docs"] if d != document_id]
                props["source_chunk_ids"] = [c for c in props.get("source_chunk_ids") or [] if not c.startswith(prefix)]
                if not props["source_docs"]:
                    self.g.remove_edge(a, b, key=k)
                    removed_edges += 1
            for key, data in list(self.g.nodes(data=True)):
                props = data["props"]
                if document_id not in (props.get("source_docs") or []):
                    continue
                props["source_docs"] = [d for d in props["source_docs"] if d != document_id]
                props["source_chunk_ids"] = [c for c in props.get("source_chunk_ids") or [] if not c.startswith(prefix)]
                if not props["source_docs"]:
                    if self.g.degree(key) == 0:
                        self.g.remove_node(key)
                        removed_nodes += 1
                    else:
                        props["stale"] = True
                        stale += 1
        return {"removed_edges": removed_edges, "removed_nodes": removed_nodes, "stale_nodes": stale}

    def clear(self) -> None:
        with self._lock:
            self.g.clear()

    def _node(self, key: str) -> GraphNode:
        d = self.g.nodes[key]
        return GraphNode(label=d["label"], id=d["props"]["id"], props=dict(d["props"]))

    def get_node(self, label: str, node_id: str) -> GraphNode | None:
        key = self._k(label, node_id)
        return self._node(key) if key in self.g else None

    def list_nodes(self, label: str, limit: int | None = None) -> list[GraphNode]:
        out = [self._node(k) for k, d in self.g.nodes(data=True) if d["label"] == label]
        return out[:limit] if limit else out

    def paths_from(self, starts: list[NodeRef], max_hops: int, limit: int) -> list[GraphPath]:
        paths: list[GraphPath] = []
        start_keys = {self._k(s.label, s.id) for s in starts}
        for start in starts:
            skey = self._k(start.label, start.id)
            if skey not in self.g:
                continue
            frontier: list[tuple[list[str], list[GraphEdge]]] = [([skey], [])]
            for _hop in range(max_hops):
                nxt: list[tuple[list[str], list[GraphEdge]]] = []
                for node_keys, edges in frontier:
                    cur = node_keys[-1]
                    for a, b, k, data in list(self.g.out_edges(cur, keys=True, data=True)) + \
                            list(self.g.in_edges(cur, keys=True, data=True)):
                        other = b if a == cur else a
                        if other in node_keys:
                            continue
                        e = GraphEdge(type=k, src=self._node(a).ref, dst=self._node(b).ref, props=dict(data["props"]))
                        new = (node_keys + [other], edges + [e])
                        nxt.append(new)
                        end_label = self.g.nodes[other]["label"]
                        if len(new[1]) >= 2 and end_label == start.label and other not in start_keys:
                            continue
                        paths.append(GraphPath(nodes=[self._node(x) for x in new[0]], edges=new[1]))
                        if len(paths) >= limit:
                            return paths
                frontier = nxt
        return paths

    def edges_of_type(self, edge_type: str, limit: int | None = None) -> list[GraphEdge]:
        out = [GraphEdge(type=k, src=self._node(a).ref, dst=self._node(b).ref, props=dict(d["props"]))
               for a, b, k, d in self.g.edges(keys=True, data=True) if k == edge_type]
        return out[:limit] if limit else out

    def stats(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "node_count": self.g.number_of_nodes(),
            "edge_count": self.g.number_of_edges(),
            "node_count_by_label": dict(Counter(d["label"] for _, d in self.g.nodes(data=True))),
            "edge_count_by_type": dict(Counter(k for _, _, k in self.g.edges(keys=True))),
        }

    def save_json(self, path: str) -> None:
        data = {
            "nodes": [{"label": d["label"], **d["props"]} for _, d in self.g.nodes(data=True)],
            "edges": [{"type": k, "from": a, "to": b, **d["props"]} for a, b, k, d in self.g.edges(keys=True, data=True)],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)


# =====================================================================================
# APACHE AGE
# =====================================================================================

_MERGE_LISTS = (
    "{v}.source_chunk_ids = coalesce({v}.source_chunk_ids, []) + "
    "[x IN {src} WHERE NOT x IN coalesce({v}.source_chunk_ids, [])], "
    "{v}.source_docs = coalesce({v}.source_docs, []) + [x IN {docs} WHERE NOT x IN coalesce({v}.source_docs, [])]"
)


class AgeGraphStore(GraphStore):
    """Mỗi thao tác mở 1 connection psycopg riêng (LOAD 'age' + search_path) — graph query ít, đơn
    giản và không đụng pool SQLAlchemy dùng cho bảng thường. Ghi theo lô trong 1 transaction."""

    name = "age"

    def __init__(self, dsn: str, graph_name: str = DEFAULT_GRAPH_NAME, connect_timeout: int = 5) -> None:
        self.dsn = dsn
        self.graph_name = _validate_identifier(graph_name, "Tên graph")
        self.connect_timeout = connect_timeout

    @contextmanager
    def _conn(self, write: bool = False) -> Iterator[Any]:
        import psycopg

        try:
            conn = psycopg.connect(self.dsn, connect_timeout=self.connect_timeout, autocommit=not write)
        except Exception as e:
            raise GraphUnavailable(f"Không kết nối được Postgres cho graph: {e}") from e
        try:
            with conn.cursor() as cur:
                cur.execute("LOAD 'age'")
                cur.execute('SET search_path = ag_catalog, "$user", public')
            yield conn
            if write:
                conn.commit()
        except GraphUnavailable:
            raise
        except Exception as e:
            if write:
                conn.rollback()
            raise GraphUnavailable(f"Lỗi truy vấn graph AGE: {type(e).__name__}: {e}") from e
        finally:
            conn.close()

    def _cypher(self, cur: Any, query: str, columns: str = "v agtype") -> list[tuple]:
        # AGE bắt buộc tên graph là hằng chuỗi (không nhận tham số bind) — graph_name đã qua
        # _validate_identifier nên nội suy an toàn.
        cur.execute(f"SELECT * FROM cypher('{self.graph_name}', $cy${query}$cy$) AS ({columns})")
        return cur.fetchall() if cur.description else []

    def ensure(self) -> None:
        with self._conn(write=True) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM ag_catalog.ag_graph WHERE name = %s", [self.graph_name])
            if cur.fetchone() is None:
                cur.execute("SELECT ag_catalog.create_graph(%s)", [self.graph_name])

    def upsert_nodes(self, nodes: list[dict[str, Any]]) -> int:
        if not nodes:
            return 0
        with self._conn(write=True) as conn, conn.cursor() as cur:
            for n in nodes:
                label, node_id, props, src, docs = _node_payload(n)
                props.pop("id", None)
                set_parts = [_MERGE_LISTS.format(v="n", src=_cypher_literal(src), docs=_cypher_literal(docs))]
                if props:
                    set_parts.insert(0, f"n += {_cypher_literal(props)}")
                q = (f"MERGE (n:{label} {{id: {_cypher_literal(node_id)}}}) SET {', '.join(set_parts)}"
                     + (" REMOVE n.stale" if docs else "") + " RETURN n.id")
                self._cypher(cur, q)
        return len(nodes)

    def upsert_edges(self, edges: list[dict[str, Any]]) -> int:
        written = 0
        if not edges:
            return 0
        with self._conn(write=True) as conn, conn.cursor() as cur:
            for e in edges:
                etype = _validate_identifier(e["type"], "Edge type")
                fl = _validate_identifier(e["from_label"], "Label")
                tl = _validate_identifier(e["to_label"], "Label")
                props = {k: v for k, v in (e.get("properties") or {}).items() if v is not None}
                set_parts = [_MERGE_LISTS.format(v="r", src=_cypher_literal(list(e.get("source_chunk_ids") or [])),
                                                 docs=_cypher_literal(list(e.get("source_docs") or [])))]
                if props:
                    set_parts.insert(0, f"r += {_cypher_literal(props)}")
                q = (f"MATCH (a:{fl} {{id: {_cypher_literal(str(e['from_id']))}}}), "
                     f"(b:{tl} {{id: {_cypher_literal(str(e['to_id']))}}}) "
                     f"MERGE (a)-[r:{etype}]->(b) SET {', '.join(set_parts)} RETURN type(r)")
                if self._cypher(cur, q):
                    written += 1
        return written

    def remove_document(self, document_id: str) -> dict[str, int]:
        d = _cypher_literal(document_id)
        prefix = _cypher_literal(f"{document_id}:")
        strip = (f"SET {{v}}.source_docs = [x IN {{v}}.source_docs WHERE x <> {d}], "
                 f"{{v}}.source_chunk_ids = [x IN coalesce({{v}}.source_chunk_ids, []) WHERE NOT x STARTS WITH {prefix}]")
        with self._conn(write=True) as conn, conn.cursor() as cur:
            self._cypher(cur, f"MATCH ()-[r]->() WHERE {d} IN r.source_docs {strip.format(v='r')} RETURN 1")
            removed_edges = len(self._cypher(cur, "MATCH ()-[r]->() WHERE size(r.source_docs) = 0 DELETE r RETURN 1"))
            self._cypher(cur, f"MATCH (n) WHERE {d} IN n.source_docs {strip.format(v='n')} RETURN 1")
            removed_nodes = len(self._cypher(
                cur, "MATCH (n) WHERE size(n.source_docs) = 0 OPTIONAL MATCH (n)-[r]-() "
                     "WITH n, count(r) AS deg WHERE deg = 0 DELETE n RETURN 1"))
            stale = len(self._cypher(cur, "MATCH (n) WHERE size(n.source_docs) = 0 SET n.stale = true RETURN 1"))
        return {"removed_edges": removed_edges, "removed_nodes": removed_nodes, "stale_nodes": stale}

    def clear(self) -> None:
        with self._conn(write=True) as conn, conn.cursor() as cur:
            self._cypher(cur, "MATCH (n) DETACH DELETE n RETURN 1")

    @staticmethod
    def _row_node(raw: Any) -> GraphNode:
        label, props = raw[0], raw[1]
        return GraphNode(label=label, id=str(props.get("id")), props=props)

    def get_node(self, label: str, node_id: str) -> GraphNode | None:
        label = _validate_identifier(label, "Label")
        with self._conn() as conn, conn.cursor() as cur:
            rows = self._cypher(cur, f"MATCH (n:{label} {{id: {_cypher_literal(node_id)}}}) "
                                     "RETURN [label(n), properties(n)]")
        return self._row_node(_parse_agtype(rows[0][0])) if rows else None

    def list_nodes(self, label: str, limit: int | None = None) -> list[GraphNode]:
        label = _validate_identifier(label, "Label")
        q = f"MATCH (n:{label}) RETURN [label(n), properties(n)]" + (f" LIMIT {int(limit)}" if limit else "")
        try:
            with self._conn() as conn, conn.cursor() as cur:
                rows = self._cypher(cur, q)
        except GraphUnavailable as e:
            if "does not exist" in str(e):  # label chưa từng được tạo trong graph
                return []
            raise
        return [self._row_node(_parse_agtype(r[0])) for r in rows]

    def paths_from(self, starts: list[NodeRef], max_hops: int, limit: int) -> list[GraphPath]:
        if not starts:
            return []
        max_hops = max(1, min(int(max_hops), 2))
        results: list[GraphPath] = []
        start_ids = _cypher_literal([s.id for s in starts])
        with self._conn() as conn, conn.cursor() as cur:
            for hops in range(1, max_hops + 1):
                for start in starts:
                    label = _validate_identifier(start.label, "Label")
                    hub_filter = (f" WHERE label(m) <> '{label}' OR m.id IN {start_ids}" if hops >= 2 else "")
                    q = (f"MATCH p = (s:{label} {{id: {_cypher_literal(start.id)}}})-[*{hops}]-(m){hub_filter} "
                         "RETURN [n IN nodes(p) | [id(n), label(n), properties(n)]], "
                         "[r IN relationships(p) | [type(r), properties(r), start_id(r), end_id(r)]] "
                         f"LIMIT {int(limit)}")
                    for ns_raw, es_raw in self._cypher(cur, q, "ns agtype, es agtype"):
                        results.append(self._build_path(_parse_agtype(ns_raw), _parse_agtype(es_raw)))
                if len(results) >= limit:
                    break
        # bỏ path đi vòng (quay lại node đã thăm)
        uniq = []
        seen = set()
        for p in results:
            ids = [(n.label, n.id) for n in p.nodes]
            if len(set(ids)) != len(ids) or p.key() in seen:
                continue
            seen.add(p.key())
            uniq.append(p)
        return uniq[:limit]

    @staticmethod
    def _build_path(ns: list, es: list) -> GraphPath:
        by_internal: dict[int, GraphNode] = {}
        nodes = []
        for internal_id, label, props in ns:
            node = GraphNode(label=label, id=str(props.get("id")), props=props)
            by_internal[internal_id] = node
            nodes.append(node)
        edges = []
        for etype, props, start_id, end_id in es:
            src, dst = by_internal.get(start_id), by_internal.get(end_id)
            if src is None or dst is None:
                continue
            edges.append(GraphEdge(type=etype, src=src.ref, dst=dst.ref, props=props))
        return GraphPath(nodes=nodes, edges=edges)

    def edges_of_type(self, edge_type: str, limit: int | None = None) -> list[GraphEdge]:
        etype = _validate_identifier(edge_type, "Edge type")
        q = (f"MATCH (a)-[r:{etype}]->(b) RETURN [label(a), a.id], [label(b), b.id], properties(r)"
             + (f" LIMIT {int(limit)}" if limit else ""))
        try:
            with self._conn() as conn, conn.cursor() as cur:
                rows = self._cypher(cur, q, "a agtype, b agtype, p agtype")
        except GraphUnavailable as e:
            if "does not exist" in str(e):
                return []
            raise
        out = []
        for a, b, p in rows:
            al, aid = _parse_agtype(a)
            bl, bid = _parse_agtype(b)
            out.append(GraphEdge(type=etype, src=NodeRef(al, str(aid)), dst=NodeRef(bl, str(bid)), props=_parse_agtype(p)))
        return out

    def stats(self) -> dict[str, Any]:
        with self._conn() as conn, conn.cursor() as cur:
            nodes = self._cypher(cur, "MATCH (n) RETURN label(n), count(n)", "l agtype, c agtype")
            edges = self._cypher(cur, "MATCH ()-[r]->() RETURN type(r), count(r)", "t agtype, c agtype")
        by_label = {_parse_agtype(k): _parse_agtype(v) for k, v in nodes}
        by_type = {_parse_agtype(k): _parse_agtype(v) for k, v in edges}
        return {"backend": self.name, "graph": self.graph_name,
                "node_count": sum(by_label.values()), "edge_count": sum(by_type.values()),
                "node_count_by_label": by_label, "edge_count_by_type": by_type}


# =====================================================================================
# FACTORY
# =====================================================================================

_store_cache: dict[str, GraphStore] = {}
_store_factory: Any = None


def graph_name_for(workspace_id: str) -> str:
    return _validate_identifier(f"ws_{workspace_id}", "Tên graph")


def get_graph_store(workspace_id: str) -> GraphStore:
    """Mỗi workspace 1 graph AGE riêng (`ws_<id>`) trong cùng Postgres; tạo graph nếu chưa có."""
    if workspace_id not in _store_cache:
        if _store_factory is not None:
            _store_cache[workspace_id] = _store_factory(workspace_id)
        else:
            from core.config import get_settings

            store = AgeGraphStore(get_settings().psycopg_dsn, graph_name_for(workspace_id))
            store.ensure()
            _store_cache[workspace_id] = store
    return _store_cache[workspace_id]


def set_graph_store_factory(factory: Any) -> None:
    """Test: thay cách tạo store theo workspace (vd MemoryGraphStore)."""
    global _store_factory
    _store_factory = factory
    _store_cache.clear()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI vận hành
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Tiện ích graph store (AGE).")
    parser.add_argument("cmd", choices=["ensure", "stats"])
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args(argv)
    store = get_graph_store(args.workspace)
    try:
        if args.cmd == "ensure" and isinstance(store, AgeGraphStore):
            store.ensure()
        print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
        return 0
    except GraphUnavailable as e:
        print(f"Graph không dùng được: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["NODE_LABELS", "GraphStore", "MemoryGraphStore", "AgeGraphStore", "GraphUnavailable", "NodeRef",
           "GraphNode", "GraphEdge", "GraphPath", "get_graph_store", "set_graph_store_factory", "graph_name_for", "_cypher_literal",
           "_validate_identifier"]
