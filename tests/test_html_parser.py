"""Tests for the HTML/web parser + URL dispatch (offline; urlopen is faked)."""

from __future__ import annotations

from openwiki.html_parser import WebParser
from openwiki.sources import is_supported, is_url, parse_source, source_stem, source_type
from openwiki.wiki import WikiBuilder

HTML = """<!DOCTYPE html><html><head><title>Graph Theory — Wiki</title>
<style>.x{ color: red }</style></head><body>
<nav>Home | About | Contact</nav>
<p>Preamble text before any heading.</p>
<h1>Graphs</h1><p>A graph has <b>vertices</b> and edges.</p>
<script>var x = "#not a heading";</script>
<h2>Directed</h2><p>Edges have a direction.</p>
<h1>Trees</h1><p>An acyclic connected graph.</p>
<footer>(c) 2026 example</footer></body></html>"""


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_html_headings_become_pages_and_boilerplate_is_dropped(tmp_path):
    doc = WebParser().parse(_write(tmp_path, "d.html", HTML))
    assert doc.metadata.format == "html"
    assert doc.metadata.title == "Graphs"                      # first <h1>
    assert doc.metadata.page_count == 4                        # preamble + 3 headings
    assert [(o.level, o.title) for o in doc.outline] == [
        (1, "Graphs"), (2, "Directed"), (1, "Trees")]
    assert doc.pages[0].text.startswith("Preamble")
    body = " ".join(p.text for p in doc.pages)
    for junk in ("Home | About", "#not a heading", "2026"):    # nav / script / footer
        assert junk not in body


def test_html_builds_a_wiki(tmp_path):
    doc = WebParser().parse(_write(tmp_path, "d.html", HTML))
    titles = [p.title for p in WikiBuilder(split_level=2).build(doc).pages]
    assert {"Graphs", "Directed", "Trees"} <= set(titles)


def test_url_detection_type_and_stem():
    url = "https://en.wikipedia.org/wiki/Graph_theory"
    assert is_url(url) and source_type(url) == "web" and is_supported(url)
    assert source_stem(url) == "Graph_theory"
    assert source_type("page.html") == "web"
    assert source_stem("/x/y/notes.md") == "notes"
    assert not is_url("/local/file.html")


class _FakeResponse:
    class _Headers:
        def get_content_charset(self):
            return "utf-8"
    headers = _Headers()

    def read(self):
        return HTML.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_parse_source_fetches_a_url(monkeypatch):
    import openwiki.html_parser as hp
    monkeypatch.setattr(hp, "urlopen", lambda request, timeout=None: _FakeResponse())
    doc = parse_source("https://example.com/graph")
    assert doc.metadata.format == "html"
    assert doc.metadata.title == "Graphs"
    assert doc.metadata.source_path == "https://example.com/graph"
