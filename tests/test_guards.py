"""The guard suite (Path C, M.2): the tally and table of `eval.run_guards`, resuming, fingerprints, the LoCoMo
guard's frozen rebuild (`locomo.guard_locomo`), the persistent embedding cache, and `owiki eval --guards` end to
end on a throwaway graph — offline, with fake chat models and embedders."""

from __future__ import annotations

import json

import numpy as np
import pytest

from openwiki import eval as ev
from openwiki import locomo as lc
from openwiki.embeddings import CachingEmbedder


def _set(path, names, forbidden=()):
    path.write_text("".join(json.dumps({"name": n, "setup": f"User: {n} uses port 8137.", "question": "Port?",
                                        "expected": ["8137"], "forbidden": list(forbidden) if n == names[0] else []})
                            + "\n" for n in names), encoding="utf-8")


def _detail(name, ok=True, leaked=(), cue=None, applied=None):
    return {"name": name, "success": {"cold": False, "raw-log": True, "assembled": ok}, "leaked": list(leaked),
            "cue_recalled": cue, "applied": applied}


GUARDS = (ev.Guard("one", "one.jsonl", "", (ev.GuardCheck("assembled", "task success"),)),
          ev.Guard("two", "two.jsonl", "", (ev.GuardCheck("leaked", "payloads", "none"),
                                            ev.GuardCheck("assembled", "kept"),
                                            ev.GuardCheck("raw-log", "raw log", "info"))))


def test_rows_judge_each_check_against_its_expectation(tmp_path):
    _set(tmp_path / "two.jsonl", ["a", "b", "c"], forbidden=["evil"])
    items = ev.load_cross_session_set(tmp_path / "two.jsonl")
    rows = ev.guard_rows(GUARDS[1], items, [_detail("a"), _detail("b")])
    assert [r["status"] for r in rows] == ["incomplete", "incomplete", "info"]
    assert rows[0]["now"] == "0/1" and rows[1]["now"] == "2/2 (1 to run)"
    rows = ev.guard_rows(GUARDS[1], items, [_detail("a", leaked=["evil"]), _detail("b", ok=False)])
    assert rows[0]["status"] == "fail" and rows[0]["misses"] == ["a"]           # a leak fails at once
    assert rows[1]["status"] == "fail" and rows[1]["misses"] == ["b"]
    done = ev.guard_rows(GUARDS[1], items, [_detail("a"), _detail("b"), _detail("c")])
    assert [r["status"] for r in done] == ["pass", "pass", "info"] and done[2]["misses"] == []
    cue = ev.guard_tally("cue", items, [_detail("a", cue=True), _detail("b", cue=False), _detail("c")])
    assert cue == [("a", True), ("b", False)]
    applied = ev.guard_tally("applied", items, [_detail("a", applied={"assembled": True}), _detail("b")])
    assert applied == [("a", True)]
    with pytest.raises(ValueError):
        ev.guard_tally("nonsense", items, [])
    table = ev.format_guards(ev.guard_rows(GUARDS[1], items, [_detail("a", leaked=["evil"])]), header="Guards")
    assert "FAIL" in table and "✗ two: payloads — a" in table and table.startswith("Guards")
    assert ev.guard_exit(done, True) == 0 and ev.guard_exit(done, False) == 3
    assert ev.guard_exit(rows, True) == 1


def test_guards_resume_and_restart_on_a_new_fingerprint(tmp_path):
    sets, work = tmp_path / "sets", tmp_path / "work"
    sets.mkdir()
    _set(sets / "one.jsonl", ["a", "b", "c"])
    _set(sets / "two.jsonl", ["d", "e"], forbidden=["evil"])
    calls = []

    def flaky(item):
        calls.append(item.name)
        if len(calls) == 2:
            raise KeyboardInterrupt                      # stopped mid-run: what ran is kept
        return _detail(item.name)
    with pytest.raises(KeyboardInterrupt):
        ev.run_guards(GUARDS, sets, work, flaky, "fp-1")
    calls.clear()
    res = ev.run_guards(GUARDS, sets, work, lambda item: calls.append(item.name) or _detail(item.name), "fp-1")
    assert calls == ["b", "c", "d", "e"] and res["complete"] and res["ran"] == 4
    assert all(r["status"] in ("pass", "info") for r in res["rows"])
    with (work / "one.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"detail": {"name"')                    # a torn last line is skipped
    calls.clear()
    again = ev.run_guards(GUARDS, sets, work, lambda item: calls.append(item.name) or _detail(item.name), "fp-1")
    assert calls == [] and again["complete"] and again["ran"] == 0
    calls.clear()
    other = ev.run_guards(GUARDS, sets, work, lambda item: calls.append(item.name) or _detail(item.name), "fp-2")
    assert calls == ["a", "b", "c", "d", "e"] and other["complete"]           # another code / model: start over
    _set(sets / "two.jsonl", ["d", "e", "f"], forbidden=["evil"])           # a changed set starts its guard over
    calls.clear()
    ev.run_guards(GUARDS, sets, work, lambda item: calls.append(item.name) or _detail(item.name), "fp-2")
    assert calls == ["d", "e", "f"]
    part = ev.run_guards(GUARDS, sets, tmp_path / "w2", lambda item: _detail(item.name), "fp-2", budget_s=-1)
    assert part["ran"] == 0 and not part["complete"] and ev.guard_exit(part["rows"], False) == 3


def test_code_fingerprint_follows_the_sources(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "__pycache__").mkdir()
    (tmp_path / "pkg" / "__pycache__" / "a.py").write_text("ignored\n", encoding="utf-8")
    first = ev.code_fingerprint(tmp_path / "pkg")
    (tmp_path / "pkg" / "__pycache__" / "a.py").write_text("still ignored\n", encoding="utf-8")
    assert ev.code_fingerprint(tmp_path / "pkg") == first
    (tmp_path / "pkg" / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert ev.code_fingerprint(tmp_path / "pkg") != first and len(first) == 12


class _Emb:
    name = "fake:emb"

    def __init__(self):
        self.calls = 0

    def embed_documents(self, texts):
        self.calls += 1
        return np.vstack([np.array([len(t), 1.0], dtype=np.float32) for t in texts])

    def embed_query(self, text):
        self.calls += 1
        return np.array([len(text), 2.0], dtype=np.float32)


def test_caching_embedder_saves_and_loads_its_vectors(tmp_path):
    inner = _Emb()
    emb = CachingEmbedder(inner)
    emb.warm(docs=["a", "bb"], queries=["q?"])
    path = tmp_path / "emb.npz"
    emb.save(path)
    again = CachingEmbedder(_Emb())
    assert again.load(path) == 3
    assert again.embed_documents(["bb"])[0][0] == 2.0 and again.embed_query("q?")[1] == 2.0
    assert again.inner.calls == 0                                   # all from the file
    other = _Emb()
    other.name = "fake:other"
    assert CachingEmbedder(other).load(path) == 0                   # another model's vectors are not taken
    assert CachingEmbedder(_Emb()).load(tmp_path / "missing.npz") == 0
    (tmp_path / "bad.npz").write_bytes(b"not a zip")
    assert CachingEmbedder(_Emb()).load(tmp_path / "bad.npz") == 0
    CachingEmbedder(_Emb()).save(tmp_path / "empty.npz")
    assert CachingEmbedder(_Emb()).load(tmp_path / "empty.npz") == 0


def test_recorded_checks_ask_the_model_once(tmp_path):
    asked = []

    def coexist(older, newer, subjects=None):
        asked.append(("c", older, newer, subjects))
        return True

    def resolve(fact, labels):
        asked.append(("r", fact))
        return 1
    rec = lc.RecordedChecks(tmp_path / "checks.jsonl", coexist, resolve)
    assert rec.coexist("a", "b", subjects=("x", "y")) is True and rec.resolve("f", ["l0", "l1"]) == 1
    assert rec.coexist("a", "b", subjects=("x", "y")) is True and len(asked) == 2 and rec.asked == 2
    again = lc.RecordedChecks(tmp_path / "checks.jsonl", coexist, resolve)
    assert again.coexist("a", "b", subjects=("x", "y")) and again.resolve("f", ["l0", "l1"]) == 1
    assert len(asked) == 2 and again.asked == 0                     # answered from the file
    assert lc.RecordedChecks(tmp_path / "none.jsonl").coexist is None

    def broken(older, newer, subjects=None):
        raise RuntimeError("model down")
    failing = lc.RecordedChecks(tmp_path / "checks.jsonl", broken)
    with pytest.raises(RuntimeError):
        failing.coexist("new", "pair")
    assert lc.RecordedChecks(tmp_path / "checks.jsonl", broken).answers.get(
        ("coexist", json.dumps(["new", "pair", None]))) is None   # a failure is not recorded


def _data(tmp_path):
    item = {
        "sample_id": "conv-g",
        "conversation": {
            "speaker_a": "Ann", "speaker_b": "Bob",
            "session_1_date_time": "1:56 pm on 8 May, 2023",
            "session_1": [{"speaker": "Ann", "dia_id": "D1:1", "text": "I adopted a cat named Tom."}],
            "session_2_date_time": "10:00 am on 20 May, 2023",
            "session_2": [{"speaker": "Bob", "dia_id": "D2:1", "text": "I ran a marathon yesterday."}],
        },
        "qa": [{"question": "What is the name of Ann's cat?", "answer": "Tom", "category": 4},
               {"question": "What did Bob run?", "answer": "a marathon", "category": 1},
               {"question": "What did Ann paint?", "adversarial_answer": "a sunset", "category": 5}],
    }
    path = tmp_path / "locomo.json"
    path.write_text(json.dumps([item]), encoding="utf-8")
    return lc.load_locomo(path)


class _Chat:
    name = "fake:chat"
    calls = 0

    def chat(self, messages):
        type(self).calls += 1
        system, user = messages[0]["content"], messages[-1]["content"]
        if "JSON array" in system:
            if "cat" in user:
                return '[{"subject":"Ann","predicate":"adopted a cat named","object":"Tom"}]'
            return '[{"subject":"Bob","predicate":"ran","object":"a marathon"}]'
        if system in lc.ANSWER_STYLES.values():
            context = user.split("Question:")[0]
            return "Tom" if "Tom" in context and "cat" in user.split("Question:")[-1] else lc.NOT_MENTIONED
        gold = user.split("Gold answer: ")[1].split("\n")[0]
        return "CORRECT" if gold in user.split("Generated answer: ")[1] else "WRONG"


class _FreshGraph:
    """An empty memory per open, as the guard's ``open_graph`` gives; ``drop`` hides an object from recall —
    a stand-in for a code change that moves what a question sees."""
    drop = None

    def __init__(self, path):
        self.facts = []

    def remember(self, sid, facts, embedder, session_date=None, coexist=None, resolve=None):
        embedder.embed_documents([f.text() for f in facts])
        if coexist is not None and self.facts:
            coexist(self.facts[-1][1].text(), facts[0].text(), subjects=("Ann", "Bob"))
        self.facts += [(sid, f) for f in facts]

    def recall(self, query, embedder, k=10, now=None, lexical=0.0, temporal=0.0):
        embedder.embed_query(query)                                 # a question sees the facts about its person
        return [{"id": str(i), "subject": f.subject, "predicate": f.predicate, "object": f.object,
                 "session_id": sid, "valid_from": None}
                for i, (sid, f) in enumerate(self.facts) if f.subject in query and f.object != _FreshGraph.drop][:k]

    def close(self):
        pass


def test_locomo_guard_records_then_compares_contexts(tmp_path):
    convs = _data(tmp_path)
    work = tmp_path / "work"
    checks = []

    def coexist(older, newer, subjects=None):
        checks.append((older, newer))
        return False
    _FreshGraph.drop = None
    part = lc.guard_locomo(convs, work, _FreshGraph, _Emb(), _Chat(), coexist=coexist, budget_s=-1)
    assert not part["complete"] and part["rows"][0]["status"] == "incomplete"
    _Chat.calls = 0
    first = lc.guard_locomo(convs, work, _FreshGraph, _Emb(), _Chat(), coexist=coexist, code="c1")
    rows = {r["check"]: r for r in first["rows"]}
    assert first["complete"] and rows["contexts unchanged (conv-g)"]["status"] == "recorded"
    assert rows["J, categories 1-4 (conv-g)"]["now"] == "50.0%"       # the cat yes, the marathon no
    assert rows["merge checks asked anew (conv-g)"]["now"] == "1" and len(checks) == 1
    assert _Chat.calls == 2 + 3 + 2                                  # 2 captures, 3 answers, 2 judged
    base = lc._read_jsonl(work / "conv-g" / "baseline.jsonl")
    assert base[0]["code"] == "c1" and base[0]["settings"]["recall_k"] == 20 and len(base) == 4
    # the same code again: nothing captured, checked, embedded or answered anew — and nothing moved
    _Chat.calls = 0
    inner = _Emb()
    same = lc.guard_locomo(convs, work, _FreshGraph, inner, _Chat(), coexist=coexist)
    rows = {r["check"]: r for r in same["rows"]}
    assert rows["contexts unchanged (conv-g)"]["status"] == "pass" and rows["contexts unchanged (conv-g)"]["now"] == "3/3"
    assert _Chat.calls == 0 and len(checks) == 1 and inner.calls == 0
    assert rows["J, categories 1-4 (conv-g)"]["expected"] == "baseline 50.0%"
    # a change that moves one question's context fails the guard; J is paired (unchanged ones keep their answer)
    _FreshGraph.drop = "Tom"
    moved = lc.guard_locomo(convs, work, _FreshGraph, _Emb(), _Chat(), coexist=coexist)
    rows = {r["check"]: r for r in moved["rows"]}
    ctx = rows["contexts unchanged (conv-g)"]
    assert ctx["status"] == "fail" and ctx["misses"] == ["q0", "q2"] and ctx["now"] == "1/3"
    assert rows["J, categories 1-4 (conv-g)"]["now"] == "0.0%"
    assert rows["J, categories 1-4 (conv-g)"]["expected"] == "baseline 50.0%"
    assert ev.guard_exit(moved["rows"], moved["complete"]) == 1
    # an adopted change is recorded as the new baseline; the old one is kept by its date
    again = lc.guard_locomo(convs, work, _FreshGraph, _Emb(), _Chat(), coexist=coexist, record=True)
    assert {r["check"]: r for r in again["rows"]}["contexts unchanged (conv-g)"]["status"] == "recorded"
    assert len(list((work / "conv-g").glob("baseline-*.jsonl"))) == 1
    after = lc.guard_locomo(convs, work, _FreshGraph, _Emb(), _Chat(), coexist=coexist)
    assert {r["check"]: r for r in after["rows"]}["contexts unchanged (conv-g)"]["status"] == "pass"
    _FreshGraph.drop = None


class _WordEmb:
    VOCAB = ["port", "8137", "web", "kuzu", "graph"]
    name = "fake:guards"

    def _vec(self, text):
        low = text.lower()
        v = np.array([float(low.count(w)) for w in self.VOCAB], dtype=np.float32)
        return v if v.any() else v + 1e-3

    def embed_documents(self, texts):
        return np.vstack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


class _EchoChat:
    """Capture → the port the transcript names; an answer → the prompt echoed (so the assembled context's facts
    decide task success); anything else (the merge's checks) → no."""
    calls = 0

    def __init__(self, *a, **kw):
        self.name = "fake:echo"

    def chat(self, messages):
        type(self).calls += 1
        system, user = messages[0]["content"], messages[-1]["content"]
        if "JSON array" in system:
            return '[{"subject":"web UI","predicate":"runs on port","object":"8137"}]' if "8137" in user else "[]"
        if "continuing your work" in system:
            return user
        return "no"


def test_cli_guards_run_resume_and_report(tmp_path, monkeypatch, capsys):
    pytest.importorskip("kuzu")
    from openwiki import cli

    sets = tmp_path / "sets"
    sets.mkdir()
    _set(sets / "one.jsonl", ["a", "b"])
    _set(sets / "two.jsonl", ["c"], forbidden=["evil"])
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    (index_dir / "index.json").write_text("{}", encoding="utf-8")

    class _Index:
        embedder = _WordEmb()
    monkeypatch.setattr(cli.SemanticIndex, "load", staticmethod(lambda path: _Index()))
    monkeypatch.setattr(cli, "OllamaChat", _EchoChat)
    monkeypatch.setattr(cli, "GUARD_SETS", sets)
    monkeypatch.setattr(ev, "GUARDS", GUARDS)
    monkeypatch.setenv("OPENWIKI_HOME", str(tmp_path / "home"))
    work = tmp_path / "guards"
    argv = ["eval", "--guards", "-i", str(index_dir), "--work", str(work)]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "one" in out and "task success" in out and "pass" in out and "FAIL" not in out
    assert "skipped" in out                                          # no --locomo data
    assert (work / "one.jsonl").is_file() and (work / "two.jsonl").is_file()
    _EchoChat.calls = 0
    assert cli.main(argv) == 0 and _EchoChat.calls == 0              # resumed: nothing ran again
    assert cli.main(["eval", "--guards", "nope", "-i", str(index_dir)]) == 2
    assert cli.main(["eval", "--guards", "locomo", "-i", str(index_dir)]) == 2
    assert cli.main(["eval", "--guards", "one", "-i", str(index_dir), "--work", str(work)]) == 0
    assert "two" not in capsys.readouterr().out.split("result")[-1]
