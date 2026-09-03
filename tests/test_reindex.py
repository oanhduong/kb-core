"""Test cho index/reindex.py — tiêm FakeStore/FakeEmbedder (cùng phong cách dependency-injection với
security/gateway.py, tests/test_security.py), không cần Postgres/Qdrant thật đang chạy."""
import json
import os

from index.embeddings import HashingEmbedder
from index.reindex import _load_state, _save_state, _state_path, reindex
from index.store import VectorStore


class FakeStore(VectorStore):
    def __init__(self):
        self.records = {}
        self.upsert_batches = []

    def upsert(self, records):
        self.upsert_batches.append(len(records))
        for r in records:
            self.records[r.id] = r

    def search(self, query_vec, top_k, filters=None):
        raise NotImplementedError

    def delete(self, ids):
        for i in ids:
            self.records.pop(i, None)

    def count(self):
        return len(self.records)


def _write_chunks_jsonl(path, n):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n):
            chunk = {
                "chunk_id": f"r1#{i:04d}",
                "parent_id": "r1",
                "text": f"nội dung chunk {i}",
                "location": "p.1",
                "metadata": {"store_code": "HCM01"},
            }
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")


def test_reindex_upserts_every_chunk_exactly_once(tmp_path):
    input_path = str(tmp_path / "chunks.jsonl")
    _write_chunks_jsonl(input_path, 25)
    store = FakeStore()

    result = reindex(
        input_path, "fake", store=store, embedder=HashingEmbedder(dim=8),
        batch_size=10, progress_fn=lambda msg: None,
    )

    assert result == {"total": 25, "upserted": 25, "skipped": 0}
    assert store.count() == 25
    assert sum(store.upsert_batches) == 25
    assert store.upsert_batches == [10, 10, 5]  # 3 batch theo batch_size=10


def test_reindex_is_idempotent_when_run_twice_without_resume(tmp_path):
    input_path = str(tmp_path / "chunks.jsonl")
    _write_chunks_jsonl(input_path, 10)
    store = FakeStore()

    reindex(input_path, "fake", store=store, embedder=HashingEmbedder(dim=8), progress_fn=lambda msg: None)
    reindex(input_path, "fake", store=store, embedder=HashingEmbedder(dim=8), progress_fn=lambda msg: None)

    assert store.count() == 10  # ghi đè theo id, không nhân đôi


def test_reindex_resume_skips_already_done_chunks(tmp_path):
    input_path = str(tmp_path / "chunks.jsonl")
    _write_chunks_jsonl(input_path, 20)
    store = FakeStore()

    reindex(
        input_path, "fake", store=store, embedder=HashingEmbedder(dim=8),
        batch_size=8, resume=True, progress_fn=lambda msg: None,
    )
    assert store.count() == 20
    first_run_batches = list(store.upsert_batches)

    # Chạy lại với resume=True, KHÔNG xoá state -> phải bỏ qua toàn bộ (đã "done" hết ở lần trước).
    result = reindex(
        input_path, "fake", store=store, embedder=HashingEmbedder(dim=8),
        batch_size=8, resume=True, progress_fn=lambda msg: None,
    )
    assert result["upserted"] == 0
    assert result["skipped"] == 20
    assert store.upsert_batches == first_run_batches  # không upsert thêm


def test_reindex_resume_continues_from_interrupted_state(tmp_path):
    input_path = str(tmp_path / "chunks.jsonl")
    _write_chunks_jsonl(input_path, 20)
    store = FakeStore()

    # Mô phỏng lần chạy trước bị ngắt giữa chừng: state báo đã xong 8/20.
    state_path = _state_path(input_path, "fake")
    _save_state(state_path, {"done": 8})

    result = reindex(
        input_path, "fake", store=store, embedder=HashingEmbedder(dim=8),
        batch_size=5, resume=True, progress_fn=lambda msg: None,
    )
    assert result["skipped"] == 8
    assert result["upserted"] == 12
    assert store.count() == 12
    assert "r1#0000" not in store.records  # đã bỏ qua 8 chunk đầu
    assert "r1#0008" in store.records


def test_reindex_without_resume_ignores_existing_state(tmp_path):
    input_path = str(tmp_path / "chunks.jsonl")
    _write_chunks_jsonl(input_path, 10)
    state_path = _state_path(input_path, "fake")
    _save_state(state_path, {"done": 10})
    store = FakeStore()

    result = reindex(
        input_path, "fake", store=store, embedder=HashingEmbedder(dim=8),
        resume=False, progress_fn=lambda msg: None,
    )
    assert result == {"total": 10, "upserted": 10, "skipped": 0}


def test_state_file_saved_via_tmp_then_replace_is_readable(tmp_path):
    path = str(tmp_path / "state.json")
    _save_state(path, {"done": 42})
    assert _load_state(path) == {"done": 42}
    assert not os.path.isfile(f"{path}.tmp")


def test_load_state_missing_file_defaults_to_zero(tmp_path):
    assert _load_state(str(tmp_path / "nope.json")) == {"done": 0}


def test_reindex_progress_fn_called_with_batch_updates(tmp_path):
    input_path = str(tmp_path / "chunks.jsonl")
    _write_chunks_jsonl(input_path, 5)
    store = FakeStore()
    messages = []

    reindex(input_path, "fake", store=store, embedder=HashingEmbedder(dim=8), batch_size=5, progress_fn=messages.append)

    assert any("batch 1/1" in m for m in messages)
    assert any("Hoàn tất" in m for m in messages)
