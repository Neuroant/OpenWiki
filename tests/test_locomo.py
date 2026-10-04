"""LoCoMo runner (`openwiki/locomo.py`): loading, dates, scoring, the phased + resumable run — offline,
with a tiny synthetic LoCoMo-shaped file, a fake chat model, embedder and memory graph."""

from __future__ import annotations

import json

import numpy as np

from openwiki import locomo as lc


def _data(tmp_path):
    item = {
        "sample_id": "conv-x",
        "conversation": {
            "speaker_a": "Ann", "speaker_b": "Bob",
            "session_1_date_time": "1:56 pm on 8 May, 2023",
            "session_1": [{"speaker": "Ann", "dia_id": "D1:1", "text": "I adopted a cat named Tom."},
                          {"speaker": "Bob", "dia_id": "D1:2", "text": "Nice!", "blip_caption": "a grey cat"}],
            "session_2_date_time": "10:00 am on 20 May, 2023",
            "session_2": [{"speaker": "Bob", "dia_id": "D2:1", "text": "I ran a marathon yesterday."}],
        },
        "qa": [{"question": "What is the name of Ann's cat?", "answer": "Tom", "category": 4},
               {"question": "When did Bob run a marathon?", "answer": "19 May 2023", "category": 2},
               {"question": "What did Ann paint?", "adversarial_answer": "a sunset", "category": 5}],
    }
    path = tmp_path / "locomo.json"
    path.write_text(json.dumps([item]), encoding="utf-8")
    return path


def test_load_and_dates(tmp_path):
    (conv,) = lc.load_locomo(_data(tmp_path))
    assert conv.sample_id == "conv-x" and conv.speakers == ("Ann", "Bob")
    assert [s.sid for s in conv.sessions] == ["conv-x-s1", "conv-x-s2"]
    assert lc.parse_locomo_date("1:56 pm on 8 May, 2023") == conv.sessions[0].date
    assert "Session of 1:56 pm on 8 May, 2023." in conv.sessions[0].text
    assert "[shares a photo: a grey cat]" in conv.sessions[0].text
    assert conv.qa[2].answer == lc.NOT_MENTIONED and conv.qa[1].category == 2
    assert conv.present == conv.sessions[1].date + 86400
    assert lc.parse_locomo_date("not a date") is None


def test_scoring():
    assert lc.f1("7 May 2023", "7 May 2023") == 1.0
    assert lc.f1("the cat Tom", "Tom") == 2 * (1 / 2 * 1) / (1 / 2 + 1)
    assert lc.f1("", "Tom") == 0.0
    assert lc.abstains("Not mentioned.") and lc.abstains("I don't know") and not lc.abstains("Tom")
    s = lc.summarize([{"category": 4, "f1": 1.0, "j": True}, {"category": 2, "f1": 0.0, "j": False},
                      {"category": 5, "f1": 1.0, "j": True}])
    assert s["overall"] == {"n": 2, "f1": 0.5, "j": 0.5}                  # adversarial reported apart
    assert s["by_category"]["adversarial"]["n"] == 1


class _Emb:
    name = "fake:emb"

    def __init__(self):
        self.calls = 0

    def embed_documents(self, texts):
        self.calls += 1
        return np.vstack([np.array([len(t), 1.0], dtype=np.float32) for t in texts])

    def embed_query(self, text):
        self.calls += 1
        return np.array([len(text), 1.0], dtype=np.float32)


def test_caching_embedder_batches_ahead():
    inner = _Emb()
    emb = lc.CachingEmbedder(inner)
    emb.warm(docs=["a", "bb", "a"], queries=["q"])
    calls = inner.calls
    assert emb.embed_documents(["bb", "a"]).shape == (2, 2) and emb.embed_query("q")[0] == 1.0
    assert inner.calls == calls                                     # served from the cache


class _Chat:
    """Capture → facts from the session text; answer → the cat's name or a date; judge → CORRECT iff it
    contains the gold token."""

    def chat(self, messages):
        system = messages[0]["content"]
        user = messages[-1]["content"]
        if "JSON array" in system:
            if "cat" in user:
                return '[{"subject":"Ann","predicate":"adopted a cat named","object":"Tom"}]'
            return '[{"subject":"Bob","predicate":"ran","object":"a marathon"}]'
        if system in lc.ANSWER_STYLES.values():
            return "Tom" if "cat" in user.split("Question:")[-1] else lc.NOT_MENTIONED
        return "CORRECT" if user.split("Gold answer: ")[1].split("\n")[0] in user.split("Generated answer: ")[1] \
            else "WRONG"


class _Graph:
    store: dict = {}

    def __init__(self, path):
        self.path = str(path)
        _Graph.store.setdefault(self.path, [])

    def remember(self, sid, facts, embedder, **kw):
        embedder.embed_documents([f.text() for f in facts])
        _Graph.store[self.path] += [(sid, f) for f in facts]

    recall_kw = None

    def recall(self, query, embedder, k=10, now=None, **kw):
        embedder.embed_query(query)
        if _Graph.recall_kw is not None:
            _Graph.recall_kw.append(kw)
        return [{"id": str(i), "subject": f.subject, "predicate": f.predicate, "object": f.object,
                 "session_id": sid, "valid_from": None} for i, (sid, f) in enumerate(_Graph.store[self.path])][:k]

    def close(self):
        pass


def test_run_is_phased_resumable_and_scored(tmp_path):
    convs = lc.load_locomo(_data(tmp_path))
    _Graph.store = {}
    work = tmp_path / "work"
    r0 = lc.run_locomo(convs, work, _Graph, _Emb(), _Chat(), budget_s=-1)       # no time at all
    assert r0["complete"] is False and r0["records"] == []
    res = lc.run_locomo(convs, work, _Graph, _Emb(), _Chat())
    assert res["complete"] is True and len(res["records"]) == 3
    by = {r["category"]: r for r in res["records"]}
    assert by[4]["prediction"] == "Tom" and by[4]["j"] is True and by[4]["f1"] == 1.0
    assert by[5]["j"] is True                                       # abstained on the adversarial one
    assert by[2]["j"] is False
    # resumable: a second run finds everything done and redoes nothing
    again = lc.run_locomo(convs, work, _Graph, _Emb(), _Chat())
    assert again["complete"] and len(again["records"]) == 3
    assert len(_Graph.store[str(work / "conv-x" / "graph")]) == 2   # each session remembered once
    # another recall mode writes its own answers file
    today = lc.run_locomo(convs, work, _Graph, _Emb(), _Chat(), now_mode="today", categories=[4])
    assert (work / "conv-x" / "answers-today-infer.jsonl").is_file() and len(today["records"]) == 1
    strict = lc.run_locomo(convs, work, _Graph, _Emb(), _Chat(), answer_style="strict", categories=[4])
    assert (work / "conv-x" / "answers-strict.jsonl").is_file() and len(strict["records"]) == 1
    k20 = lc.run_locomo(convs, work, _Graph, _Emb(), _Chat(), recall_k=20, categories=[4])
    assert (work / "conv-x" / "answers-infer-k20.jsonl").is_file() and len(k20["records"]) == 1
    seen = []
    _Graph.recall_kw = seen
    lex = lc.run_locomo(convs, work, _Graph, _Emb(), _Chat(), recall_k=20, categories=[4], lexical=0.2)
    assert (work / "conv-x" / "answers-infer-k20-lex0.2.jsonl").is_file() and len(lex["records"]) == 1
    assert seen and all(kw.get("lexical") == 0.2 for kw in seen)        # hybrid recall reached the store
    _Graph.recall_kw = None
    # a paired re-answer: where the hybrid list equals the dense one, the dense answer is copied, not asked again
    (work / "conv-x" / "answers-infer-k20-lex0.5.jsonl").unlink(missing_ok=True)

    class _Counting(_Chat):
        calls = 0

        def chat(self, messages):
            type(self).calls += 1
            return super().chat(messages)
    reused = lc.run_locomo(convs, work, _Graph, _Emb(), _Counting(), recall_k=20, categories=[4], lexical=0.5,
                           reuse_base=True)
    assert _Counting.calls == 0 and reused["records"][0]["reused"] is True   # the fake graph ignores lexical
    assert reused["records"][0]["prediction"] == k20["records"][0]["prediction"]
    assert lc.build_answer_messages("q", "ctx")[0]["content"] == lc.ANSWER_SYSTEM_INFER
    assert lc.build_answer_messages("q", "ctx", "strict")[0]["content"] == lc.ANSWER_SYSTEM
