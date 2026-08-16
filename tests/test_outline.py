"""Tests for outline synthesis from heading patterns (offline)."""

from __future__ import annotations

from openwiki.models import DocumentMetadata, Page, ParsedDocument
from openwiki.outline import synthesize_outline


def _doc(page_texts):
    return ParsedDocument(
        metadata=DocumentMetadata(source_path="x", page_count=len(page_texts)),
        outline=[],
        pages=[Page(number=i + 1, text=t) for i, t in enumerate(page_texts)],
    )


def test_synthesize_from_running_headers():
    # A chapter running header (depth 1) + section headers (depth 2) that change.
    pages = [
        "10 Objektorientiertes Programmieren\nbody body",
        "10.1 Vererbung und Polymorphie\nbody",
        "10.1 Vererbung und Polymorphie\nmore",
        "10.2 Grundbegriffe der Modellierung\nbody",
        "10.2 Grundbegriffe der Modellierung\nmore",
        "10.3 Objektorientiertes Modellieren\nbody",
    ]
    out = synthesize_outline(_doc(pages), min_sections=3)
    assert [o.title for o in out] == [
        "Vererbung und Polymorphie", "Grundbegriffe der Modellierung", "Objektorientiertes Modellieren"]
    assert [o.page for o in out] == [2, 4, 6]        # first-appearance page of each section
    assert all(o.level == 1 for o in out)            # flat (chapter running header excluded)


def test_strips_trailing_page_numbers():
    out = synthesize_outline(_doc(["1.1 Alpha 5", "1.2 Beta 6", "1.3 Gamma 7"]), min_sections=3)
    assert [o.title for o in out] == ["Alpha", "Beta", "Gamma"]


def test_needs_enough_distinct_sections():
    assert synthesize_outline(_doc(["1.1 Einleitung 5", "1.1 Einleitung 6"]), min_sections=3) == []


def test_empty_when_no_headings():
    assert synthesize_outline(_doc(["just some prose here", "and more prose"])) == []
    assert synthesize_outline(_doc([])) == []
