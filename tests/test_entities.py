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


# -- typed relations (Direction B) ---------------------------------------------

from openwiki.graph.entities import Entity, _parse_relations, extract_relations   # noqa: E402


def test_parse_relations_keeps_string_triples():
    reply = ('<think>drop me</think>[{"subject":"A","predicate":"controls","object":"B"},'
             ' {"subject":"A","predicate":"","object":"B"}, {"bad":1}, "nope"]')
    assert _parse_relations(reply) == [("A", "controls", "B"), ("A", "", "B")]


def _rel_wiki(*texts):
    pages = [WikiPage(slug=f"{i:03d}-p", title=f"P{i}", level=1, order=i,
                      pdf_page_start=i + 1, pdf_page_end=i + 1, text=t) for i, t in enumerate(texts)]
    return Wiki(title="T", pages=pages, source="x.pdf", split_level=1)


def test_extract_relations_grounds_to_entities():
    wiki = _rel_wiki("A controls B here.")
    ents = [Entity(key="Feature::a", name="A", type="Feature", pages=["000-p"]),
            Entity(key="Feature::b", name="B", type="Feature", pages=["000-p"])]
    chat = FakeChat('[{"subject":"A","predicate":"controls","object":"B"},'
                    ' {"subject":"A","predicate":"uses","object":"Ghost"},'   # Ghost isn't an entity → drop
                    ' {"subject":"A","predicate":"loops","object":"A"}]')     # self-relation → drop
    rels = extract_relations(wiki, ents, chat)
    assert len(rels) == 1
    r = rels[0]
    assert (r.subject, r.predicate, r.object) == ("Feature::a", "controls", "Feature::b")
    assert r.pages == ["000-p"] and r.weight == 1


def test_extract_relations_merges_across_pages():
    wiki = _rel_wiki("A controls B.", "A controls B again.")
    ents = [Entity(key="Feature::a", name="A", type="Feature", pages=["000-p", "001-p"]),
            Entity(key="Feature::b", name="B", type="Feature", pages=["000-p", "001-p"])]
    chat = FakeChat('[{"subject":"A","predicate":"Controls","object":"B"}]')  # capital C → merges by lowercase
    rels = extract_relations(wiki, ents, chat)
    assert len(rels) == 1 and rels[0].weight == 2 and set(rels[0].pages) == {"000-p", "001-p"}


def test_extract_relations_skips_single_entity_pages():
    wiki = _rel_wiki("only A here")
    ents = [Entity(key="Feature::a", name="A", type="Feature", pages=["000-p"])]
    chat = FakeChat('[{"subject":"A","predicate":"is","object":"A"}]')
    assert extract_relations(wiki, ents, chat) == []      # <2 entities → no call, no relations
    assert chat.last is None
