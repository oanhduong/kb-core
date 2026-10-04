"""VectorStore — interface chung cho tầng lưu trữ + truy vấn vector của RetailKB Copilot.

Hai implementation, cùng interface (VectorStore ABC): upsert(records), search(query_vec, top_k,
filters), delete(ids), count(). Cả hai chạy bằng docker compose (xem ops/docker-compose.yml):
    PgVectorStore  — Postgres + pgvector. Pre-filter bằng WHERE SQL THẬT chạy trước ANN search
                     (store_code/doc_type có index B-tree, sku có index GIN, effective_date có
                     index B-tree cho range query) — không phải quét toàn bộ rồi lọc hậu kỳ. ANN
                     dùng HNSW index (vector_cosine_ops).
    QdrantStore    — Qdrant. Payload filter (FieldCondition/MatchAny/DatetimeRange) áp trong lúc
                     duyệt HNSW graph — Qdrant gọi đây là "filterable HNSW", về bản chất cũng là
                     pre-filter (không search rồi lọc lại).

Filter hỗ trợ đúng nhu cầu thật của retail (câu hỏi luôn gắn với 1 store + 1 thời điểm, xem Filters
bên dưới): doc_type, store_code, sku, is_active, effective_date range (effective_date_from/to).

Cả 2 store CÒN hỗ trợ tham số `prefilter` (mặc định True) ngoài interface ABC — prefilter=False chạy
ANN KHÔNG filter (lấy candidate pool lớn hơn top_k theo `oversample_factor`) rồi lọc lại bằng Python
sau (_matches_filters). Đây KHÔNG phải cách dùng khuyến nghị cho production — chỉ tồn tại để
index/bench_prefilter.py đo được chênh lệch recall/latency giữa 2 cách trên CÙNG 1 engine.

record.metadata (dict) của mỗi VectorRecord nên đã qua ingest.chunkers.normalize_chunk_metadata —
store đọc thẳng metadata["store_code"]/["sku"]/["doc_type"]/["is_active"]/["effective_date"] để lập
cột lọc, và lưu nguyên metadata gốc để trả lại nguyên vẹn trong SearchResult.
"""
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from index.embeddings import DEFAULT_DIM as _DEFAULT_EMBED_DIM

DEFAULT_PG_DSN = os.environ.get("RETAILKB_PG_DSN", "postgresql://retail:retail@localhost:5432/retailkb")
DEFAULT_QDRANT_URL = os.environ.get("RETAILKB_QDRANT_URL", "http://localhost:6333")
DEFAULT_TABLE = "chunks"
DEFAULT_COLLECTION = "chunks"
DEFAULT_OVERSAMPLE_FACTOR = 8

_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(name, what):
    if not _SAFE_IDENT_RE.match(name):
        raise ValueError(
            f"{what} không hợp lệ (chỉ chữ/số/gạch dưới, bắt đầu bằng chữ hoặc gạch dưới): {name!r}"
        )
    return name


# =====================================================================================
# KIỂU DỮ LIỆU DÙNG CHUNG
# =====================================================================================

@dataclass
class VectorRecord:
    id: str
    vector: list
    text: str
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_chunk(cls, chunk, vector):
        """Tiện ích ghép 1 ingest.chunkers.Chunk (đã chunk, CHƯA embed) với vector đã tính sẵn."""
        return cls(id=chunk.chunk_id, vector=vector, text=chunk.text, metadata=dict(chunk.metadata))


@dataclass
class SearchResult:
    id: str
    score: float
    text: str
    metadata: dict = field(default_factory=dict)


_FILTER_FIELDS = ("doc_type", "store_code", "sku", "is_active", "effective_date_from", "effective_date_to", "as_of", "workspace_id", "document_ids")


@dataclass
class Filters:
    """Bộ lọc metadata dùng chung cho cả 2 backend. doc_type/store_code/sku nhận 1 giá trị hoặc list
    (OR trong cùng field, AND giữa các field khác nhau) — sku khớp kiểu "overlap" (chunk.metadata["sku"]
    là list, chỉ cần giao nhau với filter là khớp, không cần khớp hết). effective_date_from/to là
    chuỗi ISO 'YYYY-MM-DD', 2 đầu đều inclusive; để None nếu không giới hạn đầu đó."""

    doc_type: object = None
    store_code: object = None
    sku: object = None
    is_active: object = None
    effective_date_from: object = None
    effective_date_to: object = None
    # as_of ('YYYY-MM-DD'): hỏi "tại thời điểm" (C2) — bỏ qua is_active, lấy record có hiệu lực vào ngày
    # đó: effective_date <= as_of < valid_to (valid_to NULL = còn hiệu lực). store_code có thể chứa None
    # trong list = "kể cả dữ liệu toàn hệ thống (không gắn cửa hàng)".
    as_of: object = None
    workspace_id: object = None  # multi-tenant: chỉ chunk của workspace này
    document_ids: object = None  # giới hạn trong 1 số tài liệu (hỏi "trong file X")

    def is_empty(self):
        return all(getattr(self, f) is None for f in _FILTER_FIELDS)


def _as_list(value):
    return value if isinstance(value, list) else [value]


def _matches_filters(metadata, filters):
    """Kiểm tra 1 metadata dict có khớp Filters hay không — thuần Python, không đụng DB/engine nào.
    Dùng làm bước lọc HẬU KỲ khi prefilter=False (xem docstring đầu file), và test được độc lập."""
    metadata = metadata or {}
    if filters.workspace_id is not None and metadata.get("workspace_id") != filters.workspace_id:
        return False
    if filters.document_ids is not None and metadata.get("document_id") not in _as_list(filters.document_ids):
        return False
    if filters.doc_type is not None and metadata.get("doc_type") not in _as_list(filters.doc_type):
        return False
    if filters.store_code is not None and metadata.get("store_code") not in _as_list(filters.store_code):
        return False
    if filters.sku is not None:
        wanted = set(_as_list(filters.sku))
        have = metadata.get("sku") or []
        have = have if isinstance(have, list) else [have]
        if not wanted.intersection(have):
            return False
    if filters.as_of is not None:
        eff, valid_to = metadata.get("effective_date"), metadata.get("valid_to")
        if eff is not None and str(eff) > str(filters.as_of):
            return False
        if valid_to is not None and str(valid_to) <= str(filters.as_of):
            return False
    elif filters.is_active is not None and metadata.get("is_active", True) != filters.is_active:
        return False
    effective_date = metadata.get("effective_date")
    if filters.effective_date_from is not None and (effective_date is None or effective_date < filters.effective_date_from):
        return False
    if filters.effective_date_to is not None and (effective_date is None or effective_date > filters.effective_date_to):
        return False
    return True


# =====================================================================================
# INTERFACE
# =====================================================================================

class VectorStore(ABC):
    @abstractmethod
    def upsert(self, records):
        """list[VectorRecord] -> None. Idempotent theo record.id (ghi đè nếu id đã tồn tại)."""
        raise NotImplementedError

    @abstractmethod
    def search(self, query_vec, top_k, filters=None):
        """-> list[SearchResult], tối đa top_k phần tử, sắp theo score giảm dần (càng gần query
        càng cao). filters (Filters | None) — None/rỗng nghĩa là không lọc gì."""
        raise NotImplementedError

    @abstractmethod
    def delete(self, ids):
        """list[str] -> None. Xoá theo id, bỏ qua id không tồn tại (không raise)."""
        raise NotImplementedError

    @abstractmethod
    def count(self):
        """-> int, tổng số record hiện có trong store."""
        raise NotImplementedError

    def close(self):
        """Đóng connection/client bên dưới nếu store tự tạo (không phải được tiêm sẵn qua tham số
        conn=/client=) — mặc định no-op, override ở subclass cần dọn tài nguyên."""


# =====================================================================================
# PGVECTOR
# =====================================================================================

def _build_where_clause(filters):
    """Filters -> (sql_fragment, params) — SQL luôn tham số hoá (%s), KHÔNG bao giờ nội suy giá trị
    filter trực tiếp vào chuỗi SQL. Trả về ("", []) nếu filters rỗng. Thuần Python, test được không
    cần kết nối DB thật."""
    clauses, params = [], []
    if filters.doc_type is not None:
        clauses.append("doc_type = ANY(%s)")
        params.append(_as_list(filters.doc_type))
    if filters.store_code is not None:
        clauses.append("store_code = ANY(%s)")
        params.append(_as_list(filters.store_code))
    if filters.sku is not None:
        clauses.append("sku && %s")  # && = toán tử overlap của Postgres array
        params.append(_as_list(filters.sku))
    if filters.is_active is not None:
        clauses.append("is_active = %s")
        params.append(filters.is_active)
    if filters.effective_date_from is not None:
        clauses.append("effective_date >= %s")
        params.append(filters.effective_date_from)
    if filters.effective_date_to is not None:
        clauses.append("effective_date <= %s")
        params.append(filters.effective_date_to)
    return " AND ".join(clauses), params


class PgVectorStore(VectorStore):
    def __init__(self, dsn=None, dim=None, table=DEFAULT_TABLE, conn=None, ensure_schema=True):
        """conn (tuỳ chọn, tiêm sẵn cho test/tái dùng connection) PHẢI đã register_vector() (xem
        pgvector.psycopg.register_vector) — nếu không, tham số embedding truyền cho execute() sẽ
        không được adapt đúng sang kiểu vector của Postgres. _connect() ở dưới tự làm việc này khi
        không có conn tiêm sẵn."""
        self._table = _validate_identifier(table, "Tên bảng")
        self._dim = dim or _DEFAULT_EMBED_DIM
        self._owns_conn = conn is None
        self._conn = conn if conn is not None else self._connect(dsn)
        if ensure_schema:
            self.ensure_schema()

    def _connect(self, dsn):
        import psycopg
        from pgvector.psycopg import register_vector

        conn = psycopg.connect(dsn or DEFAULT_PG_DSN, autocommit=True)
        register_vector(conn)
        return conn

    def ensure_schema(self):
        with self._conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {self._table} ("
                f"  id TEXT PRIMARY KEY,"
                f"  text TEXT NOT NULL,"
                f"  embedding VECTOR({int(self._dim)}) NOT NULL,"
                f"  doc_type TEXT,"
                f"  store_code TEXT,"
                f"  sku TEXT[] NOT NULL DEFAULT '{{}}',"
                f"  is_active BOOLEAN NOT NULL DEFAULT TRUE,"
                f"  effective_date DATE,"
                f"  metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb"
                f")"
            )
            # HNSW cho ANN + index thường cho mọi field filter — pre-filter chỉ nhanh hơn post-filter
            # thật sự khi các index này tồn tại, không thì WHERE vẫn phải seq scan.
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {self._table}_embedding_hnsw ON {self._table} "
                f"USING hnsw (embedding vector_cosine_ops)"
            )
            cur.execute(f"CREATE INDEX IF NOT EXISTS {self._table}_store_code_idx ON {self._table} (store_code)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS {self._table}_doc_type_idx ON {self._table} (doc_type)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS {self._table}_is_active_idx ON {self._table} (is_active)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS {self._table}_effective_date_idx ON {self._table} (effective_date)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS {self._table}_sku_gin ON {self._table} USING gin (sku)")

    def drop_schema(self):
        """Xoá hẳn bảng (kể cả index) — chỉ dùng cho bench/test cần khởi tạo lại từ đầu, KHÔNG dùng
        trong pipeline reindex thật (reindex chỉ upsert, không bao giờ tự xoá bảng)."""
        with self._conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {self._table}")

    def _to_row(self, record):
        from psycopg.types.json import Jsonb

        meta = record.metadata or {}
        sku = meta.get("sku") or []
        sku = sku if isinstance(sku, list) else [sku]
        return {
            "id": record.id,
            "text": record.text,
            "embedding": record.vector,
            "doc_type": meta.get("doc_type"),
            "store_code": meta.get("store_code"),
            "sku": sku,
            "is_active": meta.get("is_active", True),
            "effective_date": meta.get("effective_date"),
            "metadata": Jsonb(meta),
        }

    def upsert(self, records):
        if not records:
            return
        rows = [self._to_row(r) for r in records]
        with self._conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO {self._table} "
                f"(id, text, embedding, doc_type, store_code, sku, is_active, effective_date, metadata) "
                f"VALUES (%(id)s, %(text)s, %(embedding)s, %(doc_type)s, %(store_code)s, %(sku)s, "
                f"%(is_active)s, %(effective_date)s, %(metadata)s) "
                f"ON CONFLICT (id) DO UPDATE SET "
                f"text = EXCLUDED.text, embedding = EXCLUDED.embedding, doc_type = EXCLUDED.doc_type, "
                f"store_code = EXCLUDED.store_code, sku = EXCLUDED.sku, is_active = EXCLUDED.is_active, "
                f"effective_date = EXCLUDED.effective_date, metadata = EXCLUDED.metadata",
                rows,
            )

    def search(self, query_vec, top_k, filters=None, prefilter=True, oversample_factor=DEFAULT_OVERSAMPLE_FACTOR):
        filters = filters or Filters()
        if prefilter:
            where_sql, where_params = _build_where_clause(filters)
            limit = top_k
        else:
            # post-filter — CHỈ để bench so sánh (index/bench_prefilter.py), xem docstring đầu file.
            where_sql, where_params = "", []
            limit = top_k * oversample_factor

        sql = f"SELECT id, text, metadata, 1 - (embedding <=> %s) AS score FROM {self._table}"
        args = [query_vec]
        if where_sql:
            sql += f" WHERE {where_sql}"
            args.extend(where_params)
        sql += " ORDER BY embedding <=> %s LIMIT %s"
        args.extend([query_vec, limit])

        with self._conn.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall()

        results = [SearchResult(id=row[0], text=row[1], metadata=row[2], score=row[3]) for row in rows]
        if not prefilter:
            results = [r for r in results if _matches_filters(r.metadata, filters)][:top_k]
        return results

    def delete(self, ids):
        if not ids:
            return
        with self._conn.cursor() as cur:
            cur.execute(f"DELETE FROM {self._table} WHERE id = ANY(%s)", [list(ids)])

    def count(self):
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {self._table}")
            return cur.fetchone()[0]

    def close(self):
        if self._owns_conn:
            self._conn.close()


# =====================================================================================
# QDRANT
# =====================================================================================

def _qdrant_point_id(record_id):
    """Qdrant chỉ nhận point id dạng số nguyên không dấu hoặc UUID — chunk_id của ta là chuỗi tự do
    (vd 'record123#0004'), nên băm ổn định sang UUID5 (namespace cố định -> cùng record_id luôn ra
    cùng point id, upsert vẫn idempotent). id gốc được lưu lại trong payload["id"] để trả về nguyên
    vẹn trong SearchResult, không lộ ra ngoài."""
    import uuid

    namespace = uuid.UUID("6f6a8f1e-7b4b-4b8a-9b1a-9a7f3f6b6a0b")
    return str(uuid.uuid5(namespace, record_id))


def _build_qdrant_filter(filters):
    """Filters -> qdrant_client.models.Filter | None. Thuần dựng object (không gọi server), test
    được mà không cần Qdrant thật đang chạy."""
    from qdrant_client import models

    must = []
    if filters.doc_type is not None:
        must.append(models.FieldCondition(key="doc_type", match=models.MatchAny(any=_as_list(filters.doc_type))))
    if filters.store_code is not None:
        must.append(models.FieldCondition(key="store_code", match=models.MatchAny(any=_as_list(filters.store_code))))
    if filters.sku is not None:
        must.append(models.FieldCondition(key="sku", match=models.MatchAny(any=_as_list(filters.sku))))
    if filters.is_active is not None:
        must.append(models.FieldCondition(key="is_active", match=models.MatchValue(value=filters.is_active)))
    if filters.effective_date_from is not None or filters.effective_date_to is not None:
        must.append(models.FieldCondition(
            key="effective_date",
            range=models.DatetimeRange(gte=filters.effective_date_from, lte=filters.effective_date_to),
        ))
    return models.Filter(must=must) if must else None


class QdrantStore(VectorStore):
    def __init__(self, url=None, dim=None, collection=DEFAULT_COLLECTION, client=None, ensure_schema=True, distance="Cosine"):
        _validate_identifier(collection, "Tên collection")
        self._collection = collection
        self._dim = dim or _DEFAULT_EMBED_DIM
        self._client = client if client is not None else self._connect(url)
        if ensure_schema:
            self.ensure_schema(distance)

    def _connect(self, url):
        from qdrant_client import QdrantClient

        return QdrantClient(url=url or DEFAULT_QDRANT_URL)

    def ensure_schema(self, distance="Cosine"):
        from qdrant_client import models

        if not self._client.collection_exists(self._collection):
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=models.VectorParams(size=int(self._dim), distance=distance),
            )
            for field_name, schema in (
                ("doc_type", models.PayloadSchemaType.KEYWORD),
                ("store_code", models.PayloadSchemaType.KEYWORD),
                ("sku", models.PayloadSchemaType.KEYWORD),
                ("is_active", models.PayloadSchemaType.BOOL),
                ("effective_date", models.PayloadSchemaType.DATETIME),
            ):
                self._client.create_payload_index(self._collection, field_name=field_name, field_schema=schema)

    def drop_schema(self):
        """Xoá hẳn collection — chỉ dùng cho bench/test, xem PgVectorStore.drop_schema."""
        if self._client.collection_exists(self._collection):
            self._client.delete_collection(self._collection)

    def _to_point(self, record):
        from qdrant_client import models

        meta = record.metadata or {}
        sku = meta.get("sku") or []
        sku = sku if isinstance(sku, list) else [sku]
        payload = {
            "id": record.id,
            "text": record.text,
            "doc_type": meta.get("doc_type"),
            "store_code": meta.get("store_code"),
            "sku": sku,
            "is_active": meta.get("is_active", True),
            "effective_date": meta.get("effective_date"),
            "metadata": meta,
        }
        return models.PointStruct(id=_qdrant_point_id(record.id), vector=record.vector, payload=payload)

    def upsert(self, records):
        if not records:
            return
        self._client.upsert(collection_name=self._collection, points=[self._to_point(r) for r in records])

    def search(self, query_vec, top_k, filters=None, prefilter=True, oversample_factor=DEFAULT_OVERSAMPLE_FACTOR):
        filters = filters or Filters()
        qdrant_filter = _build_qdrant_filter(filters) if prefilter else None
        limit = top_k if prefilter else top_k * oversample_factor

        hits = self._client.query_points(
            collection_name=self._collection, query=query_vec, limit=limit,
            query_filter=qdrant_filter, with_payload=True,
        ).points
        results = [
            SearchResult(id=h.payload.get("id", str(h.id)), score=h.score, text=h.payload.get("text", ""),
                         metadata=h.payload.get("metadata", {}))
            for h in hits
        ]
        if not prefilter:
            results = [r for r in results if _matches_filters(r.metadata, filters)][:top_k]
        return results

    def delete(self, ids):
        if not ids:
            return
        from qdrant_client import models

        self._client.delete(
            collection_name=self._collection,
            points_selector=models.PointIdsList(points=[_qdrant_point_id(i) for i in ids]),
        )

    def count(self):
        return self._client.count(collection_name=self._collection, exact=True).count

    def close(self):
        self._client.close()
