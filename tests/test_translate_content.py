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
