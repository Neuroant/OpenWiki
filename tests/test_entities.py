"""Tests for the configurable entity ontology (offline — no Kuzu, fake chat)."""

from __future__ import annotations

import json

from openwiki.graph.entities import DEFAULT_ENTITY_TYPES, coerce_types, extract_entities
from openwiki.wiki import Wiki, WikiPage


def test_coerce_types():
    assert coerce_types(None) == DEFAULT_ENTITY_TYPES
    assert coerce_types(["Concept", "Method"]) == {"Concept": "", "Method": ""}
    assert coerce_types(["Concept: a concept", "Method"]) == {"Concept": "a concept", "Method": ""}
    assert coerce_types({"A": "x"}) == {"A": "x"}
    assert coerce_types([]) == DEFAULT_ENTITY_TYPES        # empty → default


class FakeChat:
    name = "fake"

    def __init__(self, reply):
        self._reply = reply
        self.last = None

    def chat(self, messages):
        self.last = messages
        return self._reply


def _wiki(text):
    return Wiki(
        title="T",
        pages=[WikiPage(slug="000-a", title="A", level=1, order=0,
                        pdf_page_start=1, pdf_page_end=1, text=text)],
        source="x.pdf", split_level=1,
    )


def test_custom_ontology_used_and_foreign_types_dropped():
    reply = json.dumps([
        {"name": "Rekursion", "type": "Concept"},
        {"name": "Quicksort", "type": "Algorithmus"},
        {"name": "Reverb", "type": "Effect"},      # not in the custom ontology → dropped
    ])
    chat = FakeChat(reply)
    ents = extract_entities(_wiki("text"), chat, types=["Concept", "Algorithmus"])

    assert {(e.name, e.type) for e in ents} == {("Rekursion", "Concept"), ("Quicksort", "Algorithmus")}
    system = chat.last[0]["content"]
    assert "Concept" in system and "Algorithmus" in system and "Mode" not in system  # our types, not the default


def test_max_chars_truncates_page_text():
    chat = FakeChat("[]")
    extract_entities(_wiki("wort " * 5000), chat, types=["Concept"], max_chars=50)
    user = chat.last[1]["content"]
    assert len(user) < 200   # only ~50 chars of page text sent
