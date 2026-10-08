"""Worker truyền force/only của job knowledge_extract xuống pipeline (nút "Trích lại tri thức" phải chạy lại thật)."""
from types import SimpleNamespace

import knowledge.pipeline as kp
from worker.main import Worker


def test_knowledge_extract_job_passes_force_and_only(monkeypatch):
    seen = {}

    def fake(cfg, sm, llm, embedder, document_id, progress=None, only=None, force=False):
        seen.update(document_id=document_id, only=only, force=force)
        return {"ok": True}

    monkeypatch.setattr(kp, "extract_document_knowledge", fake)
    worker = Worker.__new__(Worker)
    worker.cfg = worker.sm = worker._llm = None
    monkeypatch.setattr(Worker, "embedder", None, raising=False)
    job = SimpleNamespace(id="j1", payload={"document_id": "d1", "force": True, "only": ["profile"]})
    assert worker._knowledge_extract(job) == {"ok": True}
    assert seen == {"document_id": "d1", "only": ["profile"], "force": True}
