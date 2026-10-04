"""Time-window recall (v0.104) — the window a question names, and recall favouring facts from it.

The parser and the match are pure; the store round trip is Kuzu-gated.
"""

from __future__ import annotations

import numpy as np
import pytest

from openwiki.graph.memory import MemoryFact
from openwiki.graph.temporal import format_date, parse_date, question_window, window_match

DAY = 86_400


def _w(text, now=None):
    w = question_window(text, now=parse_date(now) if now else None)
    return None if w is None else (format_date(w[0]), format_date(w[1]), w[2] // DAY)


@pytest.mark.parametrize("text,window", [
    ("Who did Maria have dinner with on May 3, 2023?", ("2023-05-03", "2023-05-04", 3)),
    ("What did Jon find for his store on 1 February, 2023?", ("2023-02-01", "2023-02-02", 3)),
    ("What did John attend in March 2023?", ("2023-03-01", "2023-04-01", 7)),
    ("What did she do in early July 2023?", ("2023-07-01", "2023-07-11", 5)),
    ("Was the first half of September 2022 good?", ("2022-09-01", "2022-09-16", 5)),
    ("What state did Joanna visit in summer 2021?", ("2021-06-01", "2021-09-01", 14)),
    ("What happened in winter 2022?", ("2022-12-01", "2023-03-01", 14)),
    ("How often did Melanie go to the beach in 2023?", ("2023-01-01", "2024-01-01", 0)),
    ("What did Mel do the week before 9 June 2023?", ("2023-06-02", "2023-06-09", 2)),
    ("What did X do before May 2023?", ("2023-01-31", "2023-05-01", 0)),
    ("Between March 2023 and May 2023, what changed?", ("2023-03-01", "2023-06-01", 7)),
    ("When did Melanie paint a sunrise?", None),
    ("What did we decide last week?", None),                                 # relative needs a reference time
])
def test_explicit_windows(text, window):
    assert _w(text) == window


@pytest.mark.parametrize("text,window", [
    ("What did we change yesterday?", ("2023-10-19", "2023-10-20", 1)),
    ("What did we decide last week?", ("2023-10-06", "2023-10-20", 2)),
    ("What did they see on the camping trip last year?", ("2022-01-01", "2023-01-01", 0)),
    ("What happened last month?", ("2023-09-01", "2023-10-01", 7)),
    ("What did we do last summer?", ("2023-06-01", "2023-09-01", 14)),
    ("What did we do 2 weeks ago?", ("2023-10-02", "2023-10-11", 0)),
    ("What happened in the past 3 days?", ("2023-10-17", "2023-10-21", 0)),
])
def test_relative_windows_resolve_against_now(text, window):
    assert _w(text, now="2023-10-20") == window


def test_window_match_is_tolerant_at_the_edges():
    w = question_window("on May 3, 2023")
    start = parse_date("2023-05-03")
    assert window_match(start + 3600, w) == 1.0
    assert 0.0 < window_match(start + 2 * DAY, w) < 1.0                    # recorded a day later: still counts
    assert window_match(start + 5 * DAY, w) == 0.0 and window_match(None, w) == 0.0
    year = question_window("in 2023")
    assert window_match(parse_date("2023-12-31"), year) == 1.0 and window_match(parse_date("2024-01-02"), year) == 0.0


class _Emb:
    """Every fact about the dog looks alike to the embedding — only the dates differ."""
    name = "fake:window"

    def _vec(self, text):
        low = text.lower()
        return np.array([float("dog" in low or "pet" in low), float("walk" in low), 0.1], dtype=np.float32)

    def embed_documents(self, texts):
        return np.vstack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


def _store(tmp_path):
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphBuilder, GraphStore
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki, WikiPage

    pages = [WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=1, pdf_page_end=1, text="dog walk")]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=1)
    GraphBuilder(tmp_path / "graph").build(wiki, SemanticIndex.build(wiki, _Emb(), size_words=50, overlap_words=10))
    return GraphStore(tmp_path / "graph", writable=True)


def test_recall_favours_facts_from_the_questions_window(tmp_path):
    store, emb = _store(tmp_path), _Emb()
    q = "Where did Ann walk the dog in July 2023?"
    try:
        for n, (place, date) in enumerate([("the park", "2023-03-10"), ("the beach", "2023-07-14"),
                                           ("the forest", "2023-11-02")]):
            store.remember(f"s{n}", [MemoryFact(f"Ann ({place})", "walked the dog at", place,
                                                valid_from=parse_date(date))],
                           emb, session_date=parse_date(date), now=parse_date("2023-12-01"))
        now = parse_date("2023-12-01")
        dense = store.recall(q, emb, k=1, now=now)
        assert dense[0]["object"] != "the beach"                          # the embedding can't see dates
        hits = store.recall(q, emb, k=1, now=now, temporal=0.1)
        assert hits[0]["object"] == "the beach" and hits[0]["in_window"] == 1.0
        three = store.recall(q, emb, k=3, now=now, temporal=0.1)
        assert [h["id"] for h in three] == [h["id"] for h in store.recall(q, emb, k=3, now=now)]   # dense order
        assert store.recall("Where did Ann walk the dog?", emb, k=1, now=now, temporal=0.1)[0]["id"] == dense[0]["id"]
        ctx = store.context_for(q, emb, k=1, temporal=0.1)
        assert "the beach" in ctx
    finally:
        store.close()


def test_the_window_promotes_only_from_the_dense_pool(tmp_path):
    store, emb = _store(tmp_path), _Emb()
    try:                                      # five dog facts outrank an unrelated fact from the window
        for n in range(5):
            store.remember(f"d{n}", [MemoryFact(f"dog {n}", "was walked on", f"route {n}")], emb,
                           session_date=parse_date("2023-01-15"), now=parse_date("2023-12-01"))
        store.remember("x", [MemoryFact("Bob", "bought", "a lamp")], emb,
                       session_date=parse_date("2023-07-10"), now=parse_date("2023-12-01"))
        hits = store.recall("which dog was walked in July 2023?", emb, k=1, now=parse_date("2023-12-01"),
                            temporal=5.0)                              # pool = 4 × k = 4: the lamp is rank 6
        assert hits[0]["object"].startswith("route")
    finally:
        store.close()


def test_the_project_setting_reaches_the_inject_hook(tmp_path, monkeypatch, capsys):
    from openwiki import cli
    from openwiki.project import Project

    root = tmp_path / "brain"
    root.mkdir()
    manifest = root / "openwiki.toml"
    manifest.write_text('[project]\nname = "brain"\n\n[memory]\nenabled = true\n', encoding="utf-8")
    assert Project.load(root).temporal_weight == 0.1                    # on by default
    manifest.write_text('[project]\nname = "brain"\n\n[memory]\nenabled = true\ntemporal_weight = 0\n',
                        encoding="utf-8")
    project = Project.load(root)
    assert project.temporal_weight == 0.0
    project.graph_path.parent.mkdir(parents=True, exist_ok=True)
    project.graph_path.write_text("", encoding="utf-8")
    seen = []

    class _Graph:
        def context_for(self, prompt, embedder, **kw):
            seen.append(kw)
            return "CTX"

        def close(self):
            pass
    monkeypatch.setattr(cli, "_hook_embedder", lambda p: object())
    monkeypatch.setattr(cli, "_open_reader", lambda *a, **kw: _Graph())
    cli._hook_inject(project, {"prompt": "what did we change yesterday?"})
    assert seen[-1]["temporal"] == 0.0
    manifest.write_text('[project]\nname = "brain"\n\n[memory]\nenabled = true\n', encoding="utf-8")
    cli._hook_inject(Project.load(root), {"prompt": "what did we change yesterday?"})
    assert seen[-1]["temporal"] == 0.1 and "CTX" in capsys.readouterr().out
