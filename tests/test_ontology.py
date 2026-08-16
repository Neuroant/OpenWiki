"""Tests for the `owiki ontology` proposer (offline) + manifest writing."""

from __future__ import annotations

import json

import pytest

from openwiki.ontology import format_entity_types, propose_ontology, sample_corpus
from openwiki.project import MANIFEST, Project, render_manifest


def test_sample_corpus_spread_and_budget():
    texts = [f"chunk {i} " + "x" * 500 for i in range(100)]
    out = sample_corpus(texts, n=10, budget=1000)
    assert 0 < len(out) <= 1000
    assert sample_corpus([]) == ""
    assert sample_corpus(["", "   "]) == ""


class FakeChat:
    name = "fake"

    def __init__(self, reply):
        self._reply = reply
        self.last = None

    def chat(self, messages):
        self.last = messages
        return self._reply


def test_propose_ontology_parses_dedups_and_survives_junk():
    reply = "here you go: " + json.dumps([
        {"name": "Konzept", "description": "a concept (Rekursion)"},
        {"name": "Satz", "description": "a theorem"},
        {"name": "konzept", "description": "dup by case → dropped"},
        {"nope": "no name → dropped"},
    ]) + " (done)"
    items = propose_ontology(FakeChat(reply), "sample text")
    assert [i["name"] for i in items] == ["Konzept", "Satz"]
    assert propose_ontology(FakeChat("not json at all"), "x") == []


def test_format_entity_types():
    items = [{"name": "Konzept", "description": "a concept"}, {"name": "Satz", "description": ""}]
    assert format_entity_types(items) == ["Konzept: a concept", "Satz"]


# --------------------------------------------------------------- manifest write
cli = pytest.importorskip("openwiki.cli")


def _project(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "sources").mkdir(exist_ok=True)
    (root / "sources" / "m.pdf").write_bytes(b"%PDF x")
    (root / MANIFEST).write_text(
        render_manifest(name="p", sources=[{"type": "pdf", "path": "sources/m.pdf"}]),
        encoding="utf-8")
    return Project.load(root)


def test_write_entity_types_insert_then_replace(tmp_path):
    proj = _project(tmp_path / "p")

    cli._write_entity_types(proj, ["Konzept: a concept", "Satz"])
    assert Project.load(proj.root).section("graph").get("entity_types") == ["Konzept: a concept", "Satz"]

    cli._write_entity_types(proj, ["A", "B", "C"])   # replaces the existing array
    reloaded = Project.load(proj.root)
    assert reloaded.section("graph").get("entity_types") == ["A", "B", "C"]
    # the rest of [graph] is intact
    assert reloaded.section("graph").get("similar_k") == 6
