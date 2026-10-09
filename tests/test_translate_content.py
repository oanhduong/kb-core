"""knowledge.translate: dịch theo lô (giữ thứ tự, bỏ lô sai số lượng) và thay chuỗi trong JSON trả về."""
from types import SimpleNamespace

from knowledge import translate as tr


class FakeLLM:
    def __init__(self, broken_batches=()):
        self.calls, self.broken = [], set(broken_batches)

    def complete_json(self, role, *, system, user, schema, max_tokens, max_retries):
        import json

        items = json.loads(user)["items"]
        self.calls.append((role, system, items))
        n = len(self.calls) - 1
        out = [f"EN:{x}" for x in items] if n not in self.broken else ["only one"]
        return SimpleNamespace(t=out)


def test_translate_strings_batches_and_skips_bad_batch(monkeypatch):
    monkeypatch.setattr(tr, "BATCH", 2)
    llm = FakeLLM(broken_batches={1})
    out = tr.translate_strings(llm, ["có giá", "thuộc ngành", "áp dụng cho", "tham chiếu đến", "là một phần của"], "en")
    assert llm.calls[0][0] == "translate" and "into English" in llm.calls[0][1]
    assert out == {"có giá": "EN:có giá", "thuộc ngành": "EN:thuộc ngành", "là một phần của": "EN:là một phần của"}


def test_replace_only_touches_translated_strings():
    table = {tr.text_hash("Sản phẩm điện tử"): "Electronics product", tr.text_hash("có giá"): "is priced at"}
    payload = {"id": "abc123", "name": "ĐT-018", "description": "Sản phẩm điện tử ",
               "edges": [{"label": "có giá", "type": "priced_at"}], "count": 3}
    out = tr._replace(payload, table)
    assert out == {"id": "abc123", "name": "ĐT-018", "description": "Electronics product",
                   "edges": [{"label": "is priced at", "type": "priced_at"}], "count": 3}


def test_long_answers_get_their_own_batch(monkeypatch):
    monkeypatch.setattr(tr, "BATCH_CHARS", 100)
    llm = FakeLLM()
    tr.translate_strings(llm, ["ngắn một", "a" * 95, "ngắn hai", "ngắn ba"], "en")
    assert [c[2] for c in llm.calls] == [["ngắn một"], ["a" * 95], ["ngắn hai", "ngắn ba"]]


def test_qa_strings_cover_question_title_answer_and_explanation():
    q = "What is the warranty period for drones sold at the airport kiosk? Please include battery terms too."
    out = tr.qa_strings(q, "Không có thông tin.", {"summary": ["Đã hiểu: tra cứu"], "degraded": [],
                                                  "confidence": {"level": "low", "explanation": "không có điểm retrieval"}})
    assert out == [q, q[:80], "Không có thông tin.", "Đã hiểu: tra cứu", "không có điểm retrieval"]
    assert tr.qa_strings(None) == []


def test_translate_later_skips_strings_tried_recently(monkeypatch):
    submitted = []
    monkeypatch.setattr(tr, "_recent", {})
    monkeypatch.setattr(tr._POOL, "submit", lambda fn: submitted.append(fn))
    tr.translate_later(object(), "ws1", ["Hello there, how are you?", "Xin chào"])
    tr.translate_later(object(), "ws1", ["Hello there, how are you?"])
    tr.translate_later(None, "ws1", ["Something else"])
    assert len(submitted) == 1
