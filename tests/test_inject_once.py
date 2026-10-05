"""Each fact once per stretch (v0.107) — the inject hook leaves out what the session already got, until a compaction
or /clear. Store round trip Kuzu-gated; the hook offline with a fake graph."""

from __future__ import annotations

import json

import numpy as np
import pytest

from openwiki import cli
from openwiki.graph.memory import MemoryFact
from openwiki.project import Project


class _Emb:
    name = "fake:once"

    def _vec(self, text):
        low = text.lower()
        return np.array([float("port" in low), float("model" in low), 0.1], dtype=np.float32)

    def embed_documents(self, texts):
        return np.vstack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


def test_assemble_context_reports_only_what_the_budget_kept():
    from openwiki.graph.memory import assemble_context
    facts = [{"id": f"a{i}", "subject": f"thing{i}", "predicate": "is", "object": "x" * 60} for i in range(8)]
    themes = [{"id": "c1", "label": "Theme", "summary": "y" * 300}]
    shown: dict = {}
    text = assemble_context("I am X.", facts, themes, max_facts=8, max_chars=400, report=shown)
    kept = [f["id"] for f in shown["facts"]]
    assert 0 < len(kept) < 8 and kept == [f"a{i}" for i in range(len(kept))]      # a prefix, the rest cut
    assert all(f"thing{i} " in text for i in range(len(kept))) and f"thing{len(kept)} " not in text
    assert shown["themes"] == [] and "Theme" not in text and shown["identity"] is True
    full: dict = {}
    assemble_context("", facts[:2], themes, report=full)                          # no budget: everything
    assert [f["id"] for f in full["facts"]] == ["a0", "a1"] and full["themes"] == themes and not full["identity"]


def test_context_for_leaves_out_what_was_given_and_reports_what_it_used(tmp_path):
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphBuilder, GraphStore
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki, WikiPage

    pages = [WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=1, pdf_page_end=1, text="port model")]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=1)
    GraphBuilder(tmp_path / "graph").build(wiki, SemanticIndex.build(wiki, _Emb(), size_words=50, overlap_words=10))
    store, emb = GraphStore(tmp_path / "graph", writable=True), _Emb()
    try:
        store.remember("s1", [MemoryFact("server", "listens on", "port 8137"),
                              MemoryFact("chat model", "is", "qwen3")], emb, now=1000)
        used: dict = {}
        first = store.context_for("which port?", emb, identity="I am X.", k=2, report=used)
        assert "port 8137" in first and "I am X." in first and len(used["facts"]) == 2 and used["identity"]
        again: dict = {}
        second = store.context_for("which port?", emb, identity="I am X.", k=2,
                                   exclude={"facts": used["facts"], "identity": True}, report=again)
        assert second == "" and again == {"facts": [], "themes": [], "identity": False}
        partial = store.context_for("which port?", emb, identity="I am X.", k=2,
                                    exclude={"facts": used["facts"][:1]})
        assert partial.count("\n- ") == 1 and "I am X." in partial
    finally:
        store.close()


def _project(tmp_path, extra=""):
    root = tmp_path / "brain"
    root.mkdir(parents=True, exist_ok=True)
    (root / "openwiki.toml").write_text(f'[project]\nname = "brain"\n\n[memory]\nenabled = true\n{extra}',
                                        encoding="utf-8")
    project = Project.load(root)
    project.graph_path.parent.mkdir(parents=True, exist_ok=True)
    project.graph_path.write_text("", encoding="utf-8")
    return project


class _Graph:
    """Recalls facts f1..f3 for every prompt and honours ``exclude`` / ``report`` like the store."""
    calls: list = []

    def context_for(self, prompt, embedder, exclude=None, report=None, **kw):
        type(self).calls.append(exclude)
        given = set((exclude or {}).get("facts") or ())
        facts = [f for f in ("f1", "f2", "f3") if f not in given]
        ident = not (exclude or {}).get("identity")
        if report is not None:
            report.update({"facts": facts, "themes": [], "identity": ident})
        return ("IDENTITY\n" if ident else "") + "".join(f"- {f}\n" for f in facts)

    def close(self):
        pass


def _inject(project, sid, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_hook_embedder", lambda p: object())
    monkeypatch.setattr(cli, "_open_reader", lambda *a, **kw: _Graph())
    cli._hook_inject(project, {"prompt": "which port do we use?", "session_id": sid})
    return capsys.readouterr().out


def test_the_hook_injects_each_fact_once_until_a_compaction(tmp_path, monkeypatch, capsys):
    project = _project(tmp_path)
    _Graph.calls = []
    first = _inject(project, "s1", monkeypatch, capsys)
    assert "- f1" in first and "IDENTITY" in first and _Graph.calls[-1] is None
    second = _inject(project, "s1", monkeypatch, capsys)
    assert second == "" and set(_Graph.calls[-1]["facts"]) == {"f1", "f2", "f3"}          # all given already
    assert "- f1" in _inject(project, "s2", monkeypatch, capsys)                          # another session
    state = json.loads((project.state_dir / cli.INJECT_STATE_FILE).read_text(encoding="utf-8"))
    assert set(state) == {"s1", "s2"} and state["s1"]["identity"] is True
    # a compaction (PreCompact → the capture hook) drops the earlier injections → everything again
    monkeypatch.setattr(cli, "_spawn_capture", lambda *a, **kw: True)
    cli._hook_capture(project, {"hook_event_name": "PreCompact", "session_id": "s1"})
    assert "- f1" in _inject(project, "s1", monkeypatch, capsys)
    # so does /clear or a compaction seen at SessionStart
    cli._hook_resume(project, {"source": "clear", "session_id": "s1", "cwd": str(tmp_path)})
    assert "- f1" in _inject(project, "s1", monkeypatch, capsys)


def test_repeat_facts_restores_the_old_behaviour(tmp_path, monkeypatch, capsys):
    project = _project(tmp_path, "repeat_facts = true\n")
    assert project.repeat_facts is True and _project(tmp_path / "x").repeat_facts is False
    _Graph.calls = []
    for _ in range(2):
        assert "- f1" in _inject(project, "s1", monkeypatch, capsys)
    assert _Graph.calls == [None, None]
