"""Test cho index/store.py.

Không cần Postgres/Qdrant thật đang chạy: chỉ test phần THUẦN PYTHON không đụng DB/engine —
Filters, _build_where_clause (build SQL, không execute), _matches_filters (lọc hậu kỳ),
_build_qdrant_filter (dựng object qdrant_client.models, không gọi server), _validate_identifier,
VectorRecord.from_chunk, _qdrant_point_id. PgVectorStore/QdrantStore.__init__ thật (ensure_schema,
upsert, search qua network) cần docker compose (ops/docker-compose.yml) nên KHÔNG test ở đây — đó là
việc của index/bench_prefilter.py (chạy tay khi có docker) và kiểm thử thủ công."""
import pytest

from index.store import (
    Filters,
    SearchResult,
    VectorRecord,
    _build_qdrant_filter,
    _build_where_clause,
    _matches_filters,
    _qdrant_point_id,
    _validate_identifier,
)


# =====================================================================================
# Filters
# =====================================================================================

def test_filters_is_empty_true_when_all_none():
    assert Filters().is_empty()


def test_filters_is_empty_false_when_any_field_set():
    assert not Filters(store_code="HCM01").is_empty()
    assert not Filters(is_active=False).is_empty()
    assert not Filters(effective_date_from="2026-01-01").is_empty()


# =====================================================================================
# VectorRecord.from_chunk
# =====================================================================================

def test_vector_record_from_chunk_copies_id_text_metadata():
    from ingest.chunkers import Chunk

    chunk = Chunk(chunk_id="r1#0000", parent_id="r1", text="giá 100.000đ", location="p.1",
                  metadata={"store_code": "HCM01", "sku": ["ABC123"]})
    record = VectorRecord.from_chunk(chunk, vector=[0.1, 0.2])

    assert record.id == "r1#0000"
    assert record.vector == [0.1, 0.2]
    assert record.text == "giá 100.000đ"
    assert record.metadata == {"store_code": "HCM01", "sku": ["ABC123"]}


def test_vector_record_from_chunk_metadata_is_a_copy_not_same_object():
    from ingest.chunkers import Chunk

    chunk = Chunk(chunk_id="r1#0000", parent_id="r1", text="x", location="p.1", metadata={"a": 1})
    record = VectorRecord.from_chunk(chunk, vector=[0.0])
    record.metadata["a"] = 2
    assert chunk.metadata["a"] == 1


# =====================================================================================
# _validate_identifier
# =====================================================================================

@pytest.mark.parametrize("name", ["chunks", "bench_prefilter", "_ok", "a1"])
def test_validate_identifier_accepts_safe_names(name):
    assert _validate_identifier(name, "Tên") == name


@pytest.mark.parametrize("name", ["chunks; DROP TABLE x", "1chunks", "chunks-bad", "", "chunks x"])
def test_validate_identifier_rejects_unsafe_names(name):
    with pytest.raises(ValueError):
        _validate_identifier(name, "Tên")


# =====================================================================================
# _build_where_clause (PgVectorStore pre-filter, thuần build SQL)
# =====================================================================================

def test_build_where_clause_empty_filters():
    sql, params = _build_where_clause(Filters())
    assert sql == ""
    assert params == []


def test_build_where_clause_single_store_code():
    sql, params = _build_where_clause(Filters(store_code="HCM01"))
    assert sql == "store_code = ANY(%s)"
    assert params == [["HCM01"]]


def test_build_where_clause_list_values_stay_as_list():
    sql, params = _build_where_clause(Filters(doc_type=["policy", "price_list"]))
    assert params == [["policy", "price_list"]]


def test_build_where_clause_sku_uses_overlap_operator():
    sql, params = _build_where_clause(Filters(sku="ABC123"))
    assert "sku && %s" in sql
    assert params == [["ABC123"]]


def test_build_where_clause_effective_date_range():
    sql, params = _build_where_clause(Filters(effective_date_from="2026-01-01", effective_date_to="2026-12-31"))
    assert "effective_date >= %s" in sql
    assert "effective_date <= %s" in sql
    assert params == ["2026-01-01", "2026-12-31"]


def test_build_where_clause_combines_multiple_fields_with_and():
    sql, params = _build_where_clause(Filters(store_code="HCM01", is_active=True))
    assert " AND " in sql
    assert params == [["HCM01"], True]


def test_build_where_clause_never_inlines_values_into_sql():
    sql, _ = _build_where_clause(Filters(store_code="HCM01; DROP TABLE chunks;--"))
    assert "DROP TABLE" not in sql
    assert sql == "store_code = ANY(%s)"


# =====================================================================================
# _matches_filters (post-filter Python, dùng bởi cả PgVectorStore và QdrantStore khi prefilter=False)
# =====================================================================================

def _meta(**overrides):
    base = {
        "doc_type": "price_list", "store_code": "HCM01", "sku": ["ABC123"],
        "is_active": True, "effective_date": "2026-06-15",
    }
    base.update(overrides)
    return base


def test_matches_filters_empty_filters_matches_everything():
    assert _matches_filters(_meta(), Filters())


def test_matches_filters_store_code_mismatch():
    assert not _matches_filters(_meta(store_code="HN01"), Filters(store_code="HCM01"))


def test_matches_filters_store_code_match():
    assert _matches_filters(_meta(store_code="HCM01"), Filters(store_code="HCM01"))


def test_matches_filters_store_code_list_or_semantics():
    assert _matches_filters(_meta(store_code="HN01"), Filters(store_code=["HCM01", "HN01"]))


def test_matches_filters_sku_overlap_true_when_any_common():
    assert _matches_filters(_meta(sku=["ABC123", "XYZ999"]), Filters(sku=["XYZ999"]))


def test_matches_filters_sku_overlap_false_when_disjoint():
    assert not _matches_filters(_meta(sku=["ABC123"]), Filters(sku=["ZZZ000"]))


def test_matches_filters_sku_string_metadata_treated_as_single_item_list():
    assert _matches_filters(_meta(sku="ABC123"), Filters(sku="ABC123"))


def test_matches_filters_is_active_defaults_true_when_missing_from_metadata():
    meta = _meta()
    del meta["is_active"]
    assert _matches_filters(meta, Filters(is_active=True))
    assert not _matches_filters(meta, Filters(is_active=False))


def test_matches_filters_effective_date_within_range():
    assert _matches_filters(_meta(effective_date="2026-06-15"),
                             Filters(effective_date_from="2026-01-01", effective_date_to="2026-12-31"))


def test_matches_filters_effective_date_before_range_excluded():
    assert not _matches_filters(_meta(effective_date="2025-12-31"), Filters(effective_date_from="2026-01-01"))


def test_matches_filters_effective_date_after_range_excluded():
    assert not _matches_filters(_meta(effective_date="2027-01-01"), Filters(effective_date_to="2026-12-31"))


def test_matches_filters_missing_effective_date_excluded_when_range_filter_set():
    meta = _meta()
    del meta["effective_date"]
    assert not _matches_filters(meta, Filters(effective_date_from="2026-01-01"))


def test_matches_filters_all_fields_must_match_and_semantics():
    meta = _meta(store_code="HCM01", sku=["ABC123"], is_active=True)
    filters = Filters(store_code="HCM01", sku="ABC123", is_active=True)
    assert _matches_filters(meta, filters)
    assert not _matches_filters(meta, Filters(store_code="HCM01", sku="OTHER"))


# =====================================================================================
# _qdrant_point_id — băm chunk_id (chuỗi tự do) sang UUID ổn định
# =====================================================================================

def test_qdrant_point_id_is_deterministic():
    assert _qdrant_point_id("r1#0000") == _qdrant_point_id("r1#0000")


def test_qdrant_point_id_differs_for_different_ids():
    assert _qdrant_point_id("r1#0000") != _qdrant_point_id("r1#0001")


def test_qdrant_point_id_is_valid_uuid_string():
    import uuid

    parsed = uuid.UUID(_qdrant_point_id("some-chunk-id"))
    assert str(parsed) == _qdrant_point_id("some-chunk-id")


# =====================================================================================
# _build_qdrant_filter — dựng qdrant_client.models.Filter, không gọi server thật
# =====================================================================================

def test_build_qdrant_filter_empty_filters_returns_none():
    assert _build_qdrant_filter(Filters()) is None


def test_build_qdrant_filter_store_code_uses_match_any():
    qdrant_client = pytest.importorskip("qdrant_client")
    from qdrant_client import models

    f = _build_qdrant_filter(Filters(store_code="HCM01"))
    assert isinstance(f, models.Filter)
    assert len(f.must) == 1
    condition = f.must[0]
    assert condition.key == "store_code"
    assert condition.match.any == ["HCM01"]


def test_build_qdrant_filter_effective_date_range_uses_datetime_range():
    import datetime

    pytest.importorskip("qdrant_client")
    from qdrant_client import models

    f = _build_qdrant_filter(Filters(effective_date_from="2026-01-01", effective_date_to="2026-12-31"))
    condition = f.must[0]
    assert condition.key == "effective_date"
    assert isinstance(condition.range, models.DatetimeRange)
    # DatetimeRange (pydantic) tự parse chuỗi ISO 'YYYY-MM-DD' thành datetime — xác nhận giá trị
    # truyền vào (Filters.effective_date_from/to) được model chấp nhận và giữ đúng ngày.
    assert condition.range.gte == datetime.datetime(2026, 1, 1)
    assert condition.range.lte == datetime.datetime(2026, 12, 31)


def test_build_qdrant_filter_combines_multiple_fields():
    pytest.importorskip("qdrant_client")

    f = _build_qdrant_filter(Filters(store_code="HCM01", is_active=True, sku="ABC123"))
    keys = {c.key for c in f.must}
    assert keys == {"store_code", "is_active", "sku"}
