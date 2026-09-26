"""Sleep + forgetting (Path B++): the nightly pass archives what the memory policy says not to keep.

The forgetting policy is pure rules (``memory.is_ephemeral`` — one-off session events, commit hashes,
tautologies — plus the P0 ``is_unsafe_instruction`` re-applied to old facts); measured on the dogfooding
memory against hand labels, an LLM review was unstable and dropped keep-facts. Forgetting archives
(``forgotten_at`` + reason): out of every view, kept in the graph, visible to earlier ``known_at`` views,
carried across a rebuild. Pure tests run without Kuzu; store + CLI round-trips are Kuzu-gated.
"""

from __future__ import annotations

import numpy as np
import pytest

from openwiki.graph.memory import MemoryFact, is_ephemeral
from openwiki.graph.temporal import believed_at, status


@pytest.mark.parametrize("fact", [
    ("v0.74.0", "was pushed and tagged", "yes"),
    ("tag v0.53.0", "is created and pushed", "annotated"),
    ("commit", "was made", "c211fcf"),
    ("local commit", "is", "1d2e773"),
    ("local commit", "is not pushed", "true"),
    ("main branch", "is pushed to", "fbae1e4..32fda39"),
    ("server", "is serving", "v0.78.0"),
    ("OAuth access token", "has expired", "true"),
    ("CLAUDE.md", "has updated command section", "after communities"),
    ("v0.82.0", "has version", "v0.82.0"),                       # a tautology
    ("die Doku", "wurde aktualisiert", "gestern"),
])
def test_one_off_events_are_ephemeral(fact):
    assert is_ephemeral(MemoryFact(*fact))


@pytest.mark.parametrize("fact", [
    # states and history — measured false positives of looser rules, must be kept
    ("openwiki", "is installed once", "in a Python 3.13 venv"),
    ("Phase 2", "is committed as", "0.30.0"),
    ("Web UI", "has CI running offline suite and Docker build", "yes"),
    ("OpenWiki help", "is available as command", "/openwiki-help"),
    ("CI", "is triggered by", "push or PR to main"),
    ("v0.83.0", "includes feature", "inline citation links"),
    ("B7", "supports", "as-of queries"),
    ("the server", "listens on", "port 8137"),
])
def test_durable_facts_are_kept(fact):
    assert not is_ephemeral(MemoryFact(*fact))


def test_a_forgotten_record_is_archived_not_disbelieved():
    rec = {"created_at": 100, "expired_at": None, "forgotten_at": 200, "valid_from": 100}
    assert status(rec, 300) == "forgotten"
    assert not believed_at(rec) and not believed_at(rec, 250)
    assert believed_at(rec, 150)                       # before it was forgotten, it was held


# -- Kuzu-gated: the store + the command -------------------------------------------

class _Emb:
    VOCAB = ["server", "port", "pushed", "tagged", "kuzu", "graph"]
    name = "fake:sleep"

    def _vec(self, text):
        low = text.lower()
        v = np.array([float(low.count(w)) for w in self.VOCAB], dtype=np.float32)
        return v if v.any() else v + 1e-3

    def embed_documents(self, texts):
        return np.vstack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


def _build(graph_path):
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphBuilder
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki, WikiPage

    pages = [WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=1,
                      pdf_page_end=1, text="server port kuzu graph")]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=1)
    index = SemanticIndex.build(wiki, _Emb(), size_words=50, overlap_words=10)
    GraphBuilder(graph_path).build(wiki, index)


def _open(graph_path):
    from openwiki.graph import GraphStore
    return GraphStore(graph_path, writable=True)


FACTS = [MemoryFact("the server", "listens on", "port 8137"),
         MemoryFact("the graph", "is stored in", "Kuzu"),
         MemoryFact("v0.74.0", "was pushed and tagged", "yes")]


def test_forget_archives_out_of_every_view(tmp_path):
    _build(tmp_path / "graph")
    store, emb = _open(tmp_path / "graph"), _Emb()
    try:
        store.remember("s1", FACTS, emb, now=1000)
        cands = store.forget_candidates()
        assert [(c["subject"], c["reason"]) for c in cands] == [("v0.74.0", "ephemeral")]
        assert store.forget([c["id"] for c in cands], "ephemeral", now=2000) == 1
        assert store.forget([c["id"] for c in cands], "ephemeral", now=3000) == 0   # idempotent
        assert store.forget_candidates() == []
        hits = store.recall("server pushed tagged", emb, k=5, now=2500)
        assert "yes" not in [h["object"] for h in hits]                             # out of recall
        assert "v0.74.0" not in store.context_for("pushed tagged", emb)
        assert any(h["object"] == "yes" for h in store.recall(
            "pushed tagged", emb, k=5, now=2500, known_at=1500))                   # held back then
        ov = store.memory_overview()
        assert (ov["assertions"], ov["forgotten"]) == (2, 1)
        row = next(a for a in store.list_assertions() if a["subject"] == "v0.74.0")
        assert (row["status"], row["forgotten"], row["superseded"]) == ("forgotten", "ephemeral", True)
        # said again later → added afresh (a forgotten record is out of the merge), current again
        r = store.remember("s2", [MemoryFact("v0.74.0", "was pushed and tagged", "yes")], emb, now=4000)
        assert r["added"] == 1 and store.memory_overview()["assertions"] == 3
    finally:
        store.close()


def test_forgotten_survives_a_rebuild(tmp_path):
    _build(tmp_path / "graph")
    store, emb = _open(tmp_path / "graph"), _Emb()
    try:
        store.remember("s1", FACTS, emb, now=1000)
        store.forget([c["id"] for c in store.forget_candidates()], "ephemeral", now=2000)
    finally:
        store.close()
    _build(tmp_path / "graph")                            # a doc rebuild snapshots + restores memory
    store = _open(tmp_path / "graph")
    try:
        ov = store.memory_overview()
        assert (ov["assertions"], ov["forgotten"]) == (2, 1)
    finally:
        store.close()


def _project(tmp_path):
    root = tmp_path / "brain"
    root.mkdir()
    (root / "openwiki.toml").write_text(
        '[project]\nname = "brain"\n\n[memory]\nenabled = true\n', encoding="utf-8")
    return root


class _ThemeChat:
    def __init__(self, *a, **kw):
        self.name = "fake:theme"

    def chat(self, messages):
        return "Thema: Server\nThe server listens on port 8137 and the graph is stored in Kuzu."


def test_sleep_command_dry_run_then_forgets_and_consolidates(tmp_path, capsys, monkeypatch):
    from openwiki import cli

    proj = _project(tmp_path)
    graph = proj / "output" / "graph"
    _build(graph)
    store, emb = _open(graph), _Emb()
    try:
        store.remember("s1", FACTS + [MemoryFact("the server", "runs on", "port 8137 with kuzu")],
                       emb, now=1000)
    finally:
        store.close()
    assert cli.main(["sleep", "--dry-run", "--project", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "Would forget 1 fact(s)" in out and "[ephemeral] v0.74.0" in out
    store = _open(graph)
    try:
        assert store.memory_overview()["forgotten"] == 0               # dry run wrote nothing
    finally:
        store.close()

    monkeypatch.setattr(cli, "OllamaChat", _ThemeChat)
    assert cli.main(["sleep", "--project", str(proj), "--similar-k", "2"]) == 0
    out = capsys.readouterr().out
    assert "forgot 1 fact(s): 1 one-off event(s)" in out and "consolidated" in out
    store = _open(graph)
    try:
        ov = store.memory_overview()
        assert ov["forgotten"] == 1 and ov["themes"] >= 1
        members = set().union(*store.concept_members().values())
        forgotten = {a["id"] for a in store.list_assertions() if a["status"] == "forgotten"}
        assert members and not (members & forgotten)                  # themes only over kept facts
    finally:
        store.close()
