"""Tests for the wiki editing tools (offline; operate on a temp wiki)."""

from __future__ import annotations

import pytest

from openwiki.tools import WikiTools

PAGE = """# Test Page

[🏠 Home](../index.md) › Test Page

*PDF pages 1–1*

---

Hello world. The MASTER VOLUME sets the level.

---

← [Prev](000-x.md)
"""


@pytest.fixture
def tools(tmp_path) -> WikiTools:
    pages = tmp_path / "wiki" / "pages"
    pages.mkdir(parents=True)
    (pages / "001-test.md").write_text(PAGE, encoding="utf-8")
    return WikiTools(tmp_path / "wiki")


def test_read_page(tools):
    assert "MASTER VOLUME" in tools.read_page("001-test")


def test_read_missing_page(tools):
    assert tools.read_page("404-none").startswith("ERROR")


def test_list_pages(tools):
    assert "001-test — Test Page" in tools.list_pages()


def test_edit_page_success(tools):
    assert tools.edit_page("001-test", "Hello world.", "Hallo Welt.").startswith("OK")
    assert "Hallo Welt." in tools.read_page("001-test")


def test_edit_page_not_found(tools):
    assert "not found" in tools.edit_page("001-test", "absent snippet", "x")


def test_edit_page_not_unique(tools):
    # the '\n---\n' separator appears twice, so it isn't a safe unique target
    assert "appears" in tools.edit_page("001-test", "\n---\n", "\n***\n")


def test_append_section_lands_before_footer(tools):
    tools.append_section("001-test", "Extra", "More info.")
    content = tools.read_page("001-test")
    assert "## Extra" in content
    assert content.index("## Extra") < content.index("← [Prev]")


def test_create_page(tools):
    assert tools.create_page("050-new", "New", "Body.").startswith("OK")
    assert tools.read_page("050-new").startswith("# New")


def test_create_existing_page_refused(tools):
    assert "already exists" in tools.create_page("001-test", "X", "Y")


def test_create_page_avoids_double_heading(tools):
    tools.create_page("060-x", "Title", "# Title\n\nBody.")
    assert tools.read_page("060-x").count("# Title") == 1


def test_create_invalid_slug(tools):
    assert "invalid slug" in tools.create_page("bad/slug", "X", "Y")


def test_dry_run_does_not_write(tmp_path):
    pages = tmp_path / "wiki" / "pages"
    pages.mkdir(parents=True)
    (pages / "001-test.md").write_text(PAGE, encoding="utf-8")
    tools = WikiTools(tmp_path / "wiki", dry_run=True)

    assert "dry-run" in tools.edit_page("001-test", "Hello world.", "Changed.")
    assert "Hello world." in (pages / "001-test.md").read_text(encoding="utf-8")
    assert tools.edits and tools.edits[0].startswith("[dry-run]")


def test_dispatch_catches_bad_slug(tools):
    assert tools.dispatch("read_page", {"slug": "../secret"}).startswith("ERROR")


def test_dispatch_unknown_tool(tools):
    assert "unknown tool" in tools.dispatch("nope", {})


def test_path_traversal_blocked(tools):
    with pytest.raises(ValueError):
        tools._page_path("../../etc/passwd")


def test_search_without_index(tools):
    assert "unavailable" in tools.search_wiki("anything")
