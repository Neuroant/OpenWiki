"""Tests for the source-code repository parser + directory dispatch."""

from __future__ import annotations

from openwiki.code_parser import CodeParser
from openwiki.sources import is_supported, parse_source, source_stem, source_type
from openwiki.wiki import WikiBuilder


def _make_repo(tmp_path):
    (tmp_path / "src" / "utils").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / ".git").mkdir()            # dotdir → pruned
    (tmp_path / "node_modules").mkdir()    # excluded dir
    (tmp_path / "README.md").write_text("# Repo\nDocs.\n", encoding="utf-8")
    (tmp_path / "src" / "main.py").write_text('print("hi")\n', encoding="utf-8")
    (tmp_path / "src" / "utils" / "helpers.py").write_text("def h(): pass\n", encoding="utf-8")
    (tmp_path / "tests" / "test_main.py").write_text("def t(): pass\n", encoding="utf-8")
    (tmp_path / "node_modules" / "lib.js").write_text("nope\n", encoding="utf-8")
    (tmp_path / ".git" / "config").write_text("secret\n", encoding="utf-8")
    (tmp_path / "src" / "data.bin").write_bytes(b"\x00\x01binary blob")   # binary → skipped
    return tmp_path


def test_repo_becomes_overview_plus_file_pages(tmp_path):
    repo = _make_repo(tmp_path)
    doc = CodeParser().parse(repo)
    assert doc.metadata.format == "code"
    assert doc.metadata.title == repo.name             # from the overview (level-1 root)
    titles = [o.title for o in doc.outline]
    assert titles[0] == repo.name
    assert set(titles[1:]) == {"README.md", "src/main.py",
                               "src/utils/helpers.py", "tests/test_main.py"}
    assert all(o.level == 2 for o in doc.outline[1:])
    body = " ".join(p.text for p in doc.pages)
    for junk in ("nope", "secret", "binary"):          # node_modules / .git / binary excluded
        assert junk not in body
    assert "src/main.py" in doc.pages[0].text          # overview lists the file tree


def test_repo_builds_a_wiki(tmp_path):
    doc = CodeParser().parse(_make_repo(tmp_path))
    assert len(WikiBuilder(split_level=2).build(doc).pages) == 5


def test_directory_dispatch(tmp_path):
    repo = _make_repo(tmp_path)
    assert source_type(repo) == "code" and is_supported(repo)
    assert source_stem(repo) == repo.name
    assert parse_source(repo).metadata.format == "code"


def test_max_pages_caps_files(tmp_path):
    doc = CodeParser().parse(_make_repo(tmp_path), max_pages=3)
    assert doc.metadata.page_count == 3                # overview + 2 files
