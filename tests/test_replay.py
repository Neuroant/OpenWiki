"""Replaying real prompts at their own time (Path C, phase M, M.1): each prompt of a transcript gets the memory the
inject hook would have given it then — ``as_of`` = its time minus a capture lag, ``now`` = its time — on a snapshot of
the graph; a blind judge pass is optional. Offline: fake graphs, chats and embedders; the round trip through a real
graph is Kuzu-gated."""

from __future__ import annotations

import json

import numpy as np
import pytest

from openwiki import replay
from openwiki.replay import Prompt, Settings


def _line(role, text, ts):
    return json.dumps({"type": role, "timestamp": ts,
                       "message": {"role": role, "content": [{"type": "text", "text": text}]}})


def _transcript(tmp_path, lines, name="sess-a.jsonl"):
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


T0 = "2026-09-01T10:00:00Z"
T0_EPOCH = 1788256800


def test_load_prompts_user_turns_with_their_times(tmp_path):
    path = _transcript(tmp_path, [
        _line("user", "<system-reminder>host text</system-reminder>How do we test the parser?", T0),
        _line("assistant", "With pytest.", "2026-09-01T10:00:05Z"),
        _line("user", "ok", "2026-09-01T11:00:00Z"),
        _line("user", "<task-notification>\n<task-id>b1</task-id> done</task-notification>", "2026-09-02T09:00:00Z"),
    ])
    prompts = replay.load_prompts([path])
    assert [p.text for p in prompts][:2] == ["How do we test the parser?", "ok"]   # the host block is stripped
    assert prompts[0].t == T0_EPOCH and prompts[0].first and not prompts[1].first
    assert prompts[0].session == "sess-a" and prompts[0].key == "sess-a#0"
    assert replay.harness_kind(prompts[2].text) == "task notification"
    assert [p.text for p in replay.load_prompts([path], since=T0_EPOCH + 1, until=T0_EPOCH + 3600)] == ["ok"]


def test_evenly_spaced_sample():
    assert replay.evenly(list(range(10)), 5) == [0, 2, 4, 6, 8]
    assert replay.evenly(list(range(3)), 5) == [0, 1, 2] and replay.evenly([1, 2], None) == [1, 2]


class _Graph:
    """Answers context_for like GraphStore does and remembers how it was asked."""

    def __init__(self):
        self.calls = []

    def context_for(self, query, embedder, report=None, **kw):
        self.calls.append(kw)
        hits = [{"id": "f1", "subject": "the parser", "predicate": "is tested with", "object": "pytest",
                 "valid_from": kw.get("now", 0) - 3 * 86400, "score": 0.8, "parts": {"cos": 0.8}},
                {"id": "f2", "subject": "the wiki", "predicate": "has", "object": "51 pages",
                 "valid_from": kw.get("now", 0) - 30 * 86400, "score": 0.5}]
        if report is not None:
            report.update({"facts": ["f1"], "themes": ["c1"], "identity": True, "recalled": hits,
                           "theme_labels": {"c1": "Testing"}})
        return "## What I remember\n- the parser is tested with pytest"


def _prompt(text="How do we test the parser?", t=T0_EPOCH, n=0, first=True):
    return Prompt("sess-a", n, t, text, first)


def test_a_prompt_is_replayed_at_its_own_time():
    g = _Graph()
    rec = replay.replay_prompt(g, None, _prompt(), Settings(identity="me", k=16, budget=3000), lag_s=6 * 3600)
    assert g.calls[0]["as_of"] == T0_EPOCH - 6 * 3600 and g.calls[0]["now"] == T0_EPOCH
    assert g.calls[0]["k"] == 16 and g.calls[0]["max_chars"] == 3000 and g.calls[0]["identity"] == "me"
    assert "known_at" not in g.calls[0]
    assert rec["cut"] == T0_EPOCH - 6 * 3600 and rec["skipped"] is None and rec["chars"] == len(rec["context"])
    assert [(f["id"], f["rank"], f["shown"]) for f in rec["facts"]] == [("f1", 1, True), ("f2", 2, False)]
    assert rec["facts"][0]["line"].startswith("- the parser is tested with pytest")
    assert rec["themes"] == [{"id": "c1", "label": "Testing"}] and rec["identity"]
    replay.replay_prompt(g, None, _prompt(), Settings(), lag_s=3600, view="known")
    assert g.calls[1]["known_at"] == T0_EPOCH - 3600 and "as_of" not in g.calls[1]


def test_skipped_prompts_ask_no_memory():
    g = _Graph()
    rec = replay.replay_prompt(g, None, _prompt("push"), Settings(), 0, skip=lambda p: "chore: git")
    note = replay.replay_prompt(g, None, _prompt("<task-notification>x</task-notification>"), Settings(), 0)
    assert rec["skipped"] == "chore: git" and note["skipped"] == "task notification" and not g.calls


class _Emb:
    def __init__(self):
        self.warmed = []

    def warm(self, queries=()):
        self.warmed += list(queries)


def test_run_replay_is_resumable_and_time_budgeted(tmp_path):
    out = tmp_path / "pathc" / "r.jsonl"
    prompts = [_prompt(f"question {i}?", T0_EPOCH + i * 60, n=i, first=i == 0) for i in range(4)]
    emb = _Emb()
    assert replay.run_replay(_Graph(), emb, prompts, Settings(), out, 0, time_budget=-1) == \
        {"done": 0, "skipped_existing": 0, "left": 4}
    res = replay.run_replay(_Graph(), emb, prompts[:2], Settings(), out, 0)
    assert res == {"done": 2, "skipped_existing": 0, "left": 0}
    res = replay.run_replay(_Graph(), emb, prompts, Settings(), out, 0)
    assert res == {"done": 2, "skipped_existing": 2, "left": 0}
    assert [r["n"] for r in replay.read_jsonl(out)] == [0, 1, 2, 3]
    assert "question 3?" in emb.warmed                             # embedded up front, in one go


class _Judge:
    name = "fake:judge"

    def __init__(self, answers):
        self.answers, self.seen = list(answers), []

    def chat(self, messages):
        self.seen.append(messages)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def _rec(n, shown):
    facts = [{"id": f"f{i}", "rank": i, "shown": i in shown, "line": f"- fact {i} is true  (since 2026-08-01; s)"}
             for i in range(1, 5)]
    return {"session": "s", "n": n, "t": T0_EPOCH, "time": "2026-09-01T10:00:00Z", "prompt": "a request",
            "skipped": None, "facts": facts, "chars": 100}


def test_parse_numbers():
    assert replay.parse_numbers("[2, 5]", 4) == [2]
    assert replay.parse_numbers("<think>[9]</think>Helpful: [3, 1, 3]", 4) == [3, 1]
    assert replay.parse_numbers("facts 1 and 4", 4) == [1, 4] and replay.parse_numbers("[]", 4) == []


def test_the_judge_is_blind_and_resumable(tmp_path):
    out = tmp_path / "v.jsonl"
    recs = [_rec(0, {1, 2, 3}), _rec(1, {2}), dict(_rec(2, {1}), skipped="chore: git"), _rec(3, set())]
    judge = _Judge(["[1]", RuntimeError("timeout")])
    assert replay.run_judge(recs, judge, out) == {"done": 2, "left": 0}
    first, second = replay.read_jsonl(out)
    order = [f["id"] for f in replay.judge_order(recs[0])]
    assert first["order"] == order and first["helpful"] == [order[0]]          # numbers map back through the shuffle
    assert sorted(order) == ["f1", "f2", "f3"]                                  # only what the text holds
    assert second["error"] == "timeout" and second["helpful"] == []
    text = judge.seen[0][1]["content"]
    assert "a request" in text and "fact" in text and "since" not in text     # no provenance, no rank, no score
    assert replay.run_judge(recs, _Judge([]), out) == {"done": 0, "left": 0}  # nothing left to judge
    assert replay.judge_order(recs[0]) == replay.judge_order(recs[0])          # a fixed order per prompt


def test_summary():
    recs = [_rec(0, {1, 2}), _rec(1, {1}), dict(_rec(2, set()), skipped="chore: git")]
    recs[0]["facts"][0]["valid_from"] = T0_EPOCH - 86400                          # a day old at the prompt
    verdicts = [{"session": "s", "n": 0, "helpful": ["f1"]}, {"session": "s", "n": 1, "helpful": []}]
    s = replay.summarize(recs, verdicts)
    assert s["prompts"] == 3 and s["replayed"] == 2 and s["skipped"] == {"chore: git": 1}
    assert s["facts_per_prompt"] == 1.5 and s["young_share"] == round(1 / 3, 3)
    assert s["judge"]["facts"] == 3 and s["judge"]["helpful_rate"] == round(1 / 3, 3)
    assert s["judge"]["prompts_helped"] == 1 and s["judge"]["by_rank"]["1-4"]["facts"] == 3
    text = replay.format_summary(s)
    assert "replayed 2, skipped 1 chore: git" in text and "helpful 33.3%" in text


def test_snapshot_copies_the_file_and_its_wal_under_a_reader(tmp_path):
    src = tmp_path / "output" / "graph"
    src.parent.mkdir()
    src.write_bytes(b"db")
    (tmp_path / "output" / "graph.wal").write_bytes(b"wal")
    (tmp_path / "output" / "graph.journal.jsonl").write_text("{}", encoding="utf-8")
    opened = []

    class _Reader:
        def close(self):
            opened.append("closed")
    dest = replay.snapshot_graph(src, tmp_path / "snap" / "r.graph", lambda p: opened.append(p) or _Reader())
    assert dest.read_bytes() == b"db" and (tmp_path / "snap" / "r.graph.wal").read_bytes() == b"wal"
    assert not (tmp_path / "snap" / "r.graph.journal.jsonl").exists()          # the journal isn't the graph
    assert opened == [src, "closed"]
    with pytest.raises(RuntimeError):
        replay.snapshot_graph(src, tmp_path / "snap2" / "g", lambda p: None)


# -- through a real graph and the CLI --------------------------------------------------------------------------


class _VecEmb:
    VOCAB = ["parser", "pytest", "port", "server", "wiki"]
    name = "fake:replay"

    def _vec(self, text):
        low = text.lower()
        v = np.array([float(low.count(w)) for w in self.VOCAB], dtype=np.float32)
        return v if v.any() else v + 1e-3

    def embed_documents(self, texts):
        return np.vstack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


DAY = 86400


def _brain(tmp_path):
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphBuilder, GraphStore
    from openwiki.graph.memory import MemoryFact
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki, WikiPage

    root = tmp_path / "brain"
    root.mkdir()
    (root / "openwiki.toml").write_text('[project]\nname = "brain"\n\n[memory]\nenabled = true\n', encoding="utf-8")
    pages = [WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=1, pdf_page_end=1,
                      text="parser pytest server port wiki")]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=1)
    index = SemanticIndex.build(wiki, _VecEmb(), size_words=50, overlap_words=10)
    index.save(root / "output" / "index")
    GraphBuilder(root / "output" / "graph").build(wiki, index)
    store = GraphStore(root / "output" / "graph", writable=True)
    try:
        store.remember("s1", [MemoryFact("the parser", "is tested with", "unittest")], _VecEmb(), now=T0_EPOCH - 10 * DAY)
        store.remember("s2", [MemoryFact("the parser", "is tested with", "pytest")], _VecEmb(), now=T0_EPOCH)
    finally:
        store.close()
    return root


def test_replay_through_a_real_graph(tmp_path):
    from openwiki.graph import GraphStore
    root = _brain(tmp_path)
    graph = GraphStore(root / "output" / "graph")
    try:
        early = replay.replay_prompt(graph, _VecEmb(), _prompt("how is the parser tested?", T0_EPOCH - DAY),
                                     Settings(), lag_s=6 * 3600)
        late = replay.replay_prompt(graph, _VecEmb(), _prompt("how is the parser tested?", T0_EPOCH + DAY),
                                    Settings(), lag_s=6 * 3600)
    finally:
        graph.close()
    # a day before the change the memory held unittest; a day after (6 h lag) it holds pytest, unittest is history
    assert "unittest" in early["context"] and "pytest" not in early["context"]
    assert "pytest" in late["context"] and "unittest" not in late["context"]
    assert early["facts"][0]["shown"] and early["facts"][0]["parts"]


def test_cli_replay_and_judge(tmp_path, capsys, monkeypatch):
    from openwiki import cli
    root = _brain(tmp_path)
    path = _transcript(tmp_path, [
        _line("user", "How is the parser tested?", "2026-08-31T10:00:00Z"),
        _line("assistant", "Let me check.", "2026-08-31T10:00:05Z"),
        _line("user", "push", "2026-09-02T10:00:00Z"),
        _line("user", "And the server port?", "2026-09-02T11:00:00Z"),
    ])
    monkeypatch.setattr(cli, "_replay_embedder", lambda args: _VecEmb())
    out = tmp_path / "r.jsonl"
    assert cli.main(["eval", "--replay", str(path), "--out", str(out), "--project", str(root)]) == 0
    text = capsys.readouterr().out
    assert "Replaying 3 prompt(s)" in text and "replayed 2, skipped 1 chore: git" in text
    recs = replay.read_jsonl(out)
    assert [r["skipped"] for r in recs] == [None, "chore: git", None]
    assert not list(tmp_path.glob("r.graph*"))                                  # the snapshot is gone when done

    class _FakeChat:
        def __init__(self, *a, **kw):
            self.name = "fake"

        def chat(self, messages):
            return "[1]"
    monkeypatch.setattr(cli, "OllamaChat", _FakeChat)
    assert cli.main(["eval", "--replay", str(path), "--out", str(out), "--judge", "--project", str(root)]) == 0
    text = capsys.readouterr().out
    assert "(3 already done)" in text and "Judge: 2 prompt(s)" in text
    assert len(replay.read_jsonl(tmp_path / "r.judge.jsonl")) == 2
