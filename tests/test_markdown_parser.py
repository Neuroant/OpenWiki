"""Tests for the Markdown/text parser + source dispatch."""

from __future__ import annotations

import pytest

from openwiki.markdown_parser import MarkdownParser
from openwiki.sources import is_supported, parse_source, source_type
from openwiki.wiki import WikiBuilder

SAMPLE = """\
Preamble text.

# Intro

Intro body.

```
# not a heading (inside a code fence)
```

## Sub

Sub body.

# Methods

Methods body.
"""


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_markdown_sections_become_pages_and_outline(tmp_path):
    doc = MarkdownParser().parse(_write(tmp_path, "d.md", SAMPLE))
    assert doc.metadata.title == "Intro"          # first level-1 heading
    assert doc.metadata.format == "markdown"
    assert doc.metadata.page_count == 4            # preamble + 3 heading sections
    assert [(o.level, o.title, o.page) for o in doc.outline] == [
        (1, "Intro", 2), (2, "Sub", 3), (1, "Methods", 4)]
    assert doc.pages[0].text.startswith("Preamble")   # titleless preamble is page 1


def test_heading_inside_code_fence_is_ignored(tmp_path):
    doc = MarkdownParser().parse(_write(tmp_path, "d.md", SAMPLE))
    assert all("not a heading" not in o.title for o in doc.outline)
    assert "not a heading" in doc.pages[1].text        # kept as body of the Intro page


def test_plain_text_without_headings_is_one_page(tmp_path):
    doc = MarkdownParser().parse(_write(tmp_path, "notes.txt", "just some text\nmore text"))
    assert doc.metadata.page_count == 1
    assert doc.outline == []
    assert doc.metadata.title == "notes"               # falls back to the filename stem


def test_markdown_builds_a_wiki(tmp_path):
    doc = MarkdownParser().parse(_write(tmp_path, "d.md", SAMPLE))
    wiki = WikiBuilder(split_level=2).build(doc)
    titles = [p.title for p in wiki.pages]
    assert {"Intro", "Sub", "Methods"} <= set(titles)


def test_source_dispatch_and_types(tmp_path):
    md = _write(tmp_path, "a.md", "# H\nbody")
    assert parse_source(md).metadata.format == "markdown"
    assert source_type(md) == "markdown"
    assert source_type(tmp_path / "x.txt") == "text"
    assert source_type(tmp_path / "x.pdf") == "pdf"
    assert is_supported(md) and not is_supported(tmp_path / "x.docx")
    with pytest.raises(ValueError):
        parse_source(_write(tmp_path, "x.docx", "unsupported"))
