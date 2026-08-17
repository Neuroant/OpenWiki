"""Tests for the OpenWiki project layer (Phase 1): manifest, discovery,
layout, and CLI setting-resolution with back-compat."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from openwiki.project import MANIFEST, Project, Source, render_manifest


def _write_project(root: Path, **kw) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "sources").mkdir(exist_ok=True)
    (root / "sources" / "manual.pdf").write_bytes(b"%PDF-1.4 fake")
    text = render_manifest(
        name=kw.get("name", "demo"),
        description=kw.get("description", ""),
        sources=[{"type": "pdf", "path": "sources/manual.pdf"}],
        embed=kw.get("embed", "bge-m3"),
        chat=kw.get("chat", "mychat"),
        host=kw.get("host", "http://h:1"),
        split_level=kw.get("split_level", 3),
        similar_k=kw.get("similar_k", 9),
        port=kw.get("port", 9999),
    )
    (root / MANIFEST).write_text(text, encoding="utf-8")
    return root / MANIFEST


def test_render_manifest_roundtrips(tmp_path):
    _write_project(tmp_path / "proj", name="demo", split_level=3, chat="mychat")
    proj = Project.load(tmp_path / "proj")
    assert proj.name == "demo"
    assert proj.setting("build", "split_level") == 3
    assert proj.setting("models", "chat") == "mychat"
    assert proj.setting("graph", "similar_k") == 9
    assert proj.sources == [Source("pdf", "sources/manual.pdf")]


def test_render_manifest_escapes_quotes(tmp_path):
    root = tmp_path / "q"
    root.mkdir()
    (root / MANIFEST).write_text(
        render_manifest(name="a", description='he said "hi"'), encoding="utf-8"
    )
    proj = Project.load(root)   # would raise if the TOML were malformed
    assert proj.description == 'he said "hi"'


def test_find_walks_up(tmp_path):
    root = tmp_path / "proj"
    _write_project(root)
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    found = Project.find(sub)
    assert found is not None and found.root == root.resolve()


def test_find_none_outside_project(tmp_path):
    assert Project.find(tmp_path) is None


def test_resolve_explicit(tmp_path):
    root = tmp_path / "proj"
    _write_project(root)
    assert Project.resolve(root).root == root.resolve()


def test_layout_dirs(tmp_path):
    root = tmp_path / "proj"
    _write_project(root)
    proj = Project.load(root)
    base = root.resolve() / "output"
    assert proj.index_dir == base / "index"
    assert proj.wiki_dir == base / "wiki"
    assert proj.graph_path == base / "graph"
    assert proj.parsed_dir == base / "parsed"
    assert proj.source_paths() == [root.resolve() / "sources" / "manual.pdf"]


# --------------------------------------------------------------- CLI wiring
cli = pytest.importorskip("openwiki.cli")   # imports PyMuPDF; skip if absent


def _ns(command, **kw):
    return argparse.Namespace(command=command, **kw)


def test_apply_project_index_fills_from_manifest(tmp_path):
    root = tmp_path / "proj"
    _write_project(root, embed="myembed", host="http://h:1", split_level=4)
    proj = Project.load(root)
    ns = _ns("index", out=None, split_level=None, model=None, host=None,
             chunk_size=None, overlap=None)
    cli._apply_project(ns, proj)
    assert ns.out == proj.index_dir
    assert ns.model == "myembed"
    assert ns.host == "http://h:1"
    assert ns.split_level == 4
    assert ns.chunk_size == 180 and ns.overlap == 30


def test_apply_project_explicit_flag_wins(tmp_path):
    root = tmp_path / "proj"
    _write_project(root, chat="mychat")
    proj = Project.load(root)
    ns = _ns("ask", index=Path("/custom/idx"), graph=None, model="override", host=None)
    cli._apply_project(ns, proj)
    assert ns.index == Path("/custom/idx")      # explicit wins
    assert ns.model == "override"               # explicit wins
    assert ns.graph == proj.graph_path          # unset → manifest/project


def test_apply_project_serve_port(tmp_path):
    root = tmp_path / "proj"
    _write_project(root, port=8137)
    proj = Project.load(root)
    ns = _ns("serve", wiki=None, index=None, graph=None, model=None, host=None,
             port=None, bind=None)
    cli._apply_project(ns, proj)
    assert ns.port == 8137
    assert ns.bind == "127.0.0.1"
    assert ns.wiki == proj.wiki_dir


def test_apply_project_backcompat_no_project():
    ns = _ns("index", out=None, split_level=None, model=None, host=None,
             chunk_size=None, overlap=None)
    cli._apply_project(ns, None)
    assert ns.out == Path("output") / "index"
    assert ns.model == "bge-m3"
    assert ns.host == "http://localhost:11434"
    assert ns.split_level == 2


def test_cmd_init_scaffold(tmp_path):
    src = tmp_path / "manual.pdf"
    src.write_bytes(b"%PDF-1.4 x")
    ns = _ns("init", dir=tmp_path / "newproj", name="np", source=[src], force=False)
    assert cli._cmd_init(ns) == 0
    root = tmp_path / "newproj"
    assert (root / "openwiki.toml").is_file()
    assert (root / "sources" / "manual.pdf").is_file()
    assert (root / ".gitignore").is_file()
    proj = Project.load(root)
    assert proj.name == "np"
    assert proj.sources[0].path == "sources/manual.pdf"


def test_cmd_init_refuses_existing(tmp_path):
    root = tmp_path / "p"
    _write_project(root)
    ns = _ns("init", dir=root, name=None, source=None, force=False)
    assert cli._cmd_init(ns) == 2


def test_expand_sources_dir_glob_file(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.pdf").write_bytes(b"%PDF a")
    (docs / "b.pdf").write_bytes(b"%PDF b")
    (docs / "notes.txt").write_bytes(b"x")   # a supported source too
    (docs / "cover.png").write_bytes(b"x")   # unsupported → skipped by the dir scan

    # a directory now contributes all supported files (pdf/md/txt), not just pdf
    assert sorted(p.name for p in cli._expand_sources([docs])) == ["a.pdf", "b.pdf", "notes.txt"]
    assert sorted(p.name for p in cli._expand_sources([str(docs / "*.pdf")])) == ["a.pdf", "b.pdf"]
    assert [p.name for p in cli._expand_sources([docs / "a.pdf"])] == ["a.pdf"]

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        cli._expand_sources([empty])
    with pytest.raises(FileNotFoundError):
        cli._expand_sources([tmp_path / "missing"])


def test_resolve_source_specs_url_repo_and_file(tmp_path):
    sources_dir = tmp_path / "proj" / "sources"
    sources_dir.mkdir(parents=True)
    doc = tmp_path / "notes.md"
    doc.write_text("# H\nbody", encoding="utf-8")
    repo = tmp_path / "repo" / "src"
    repo.mkdir(parents=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")

    specs = cli._resolve_source_specs(
        ["https://example.com/p", str(tmp_path / "repo"), str(doc)], sources_dir, repo=True)
    by_type = {s["type"]: s["path"] for s in specs}
    assert by_type["web"] == "https://example.com/p"          # URL referenced in place
    assert by_type["code"].endswith("repo")                    # repo referenced (absolute here)
    assert by_type["markdown"] == "sources/notes.md"
    assert (sources_dir / "notes.md").is_file()                # only the file was copied


def test_source_paths_resolves_url_absolute_and_relative(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    absrepo = tmp_path / "extern" / "repo"
    sources = [{"type": "web", "path": "https://example.com/p"},
               {"type": "code", "path": str(absrepo)},
               {"type": "markdown", "path": "sources/a.md"}]
    (root / MANIFEST).write_text(render_manifest(name="p", sources=sources), encoding="utf-8")

    paths = Project.load(root).source_paths()
    assert paths[0] == "https://example.com/p"                 # URL stays a string
    assert Path(paths[1]) == absrepo                           # absolute path as-is
    assert paths[2] == root.resolve() / "sources" / "a.md"     # relative joined to root


def test_cmd_init_scans_folder(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.pdf").write_bytes(b"%PDF a")
    (docs / "b.pdf").write_bytes(b"%PDF b")
    ns = _ns("init", dir=tmp_path / "proj", name="multi", source=[docs], force=False)
    assert cli._cmd_init(ns) == 0
    proj = Project.load(tmp_path / "proj")
    assert {s.path for s in proj.sources} == {"sources/a.pdf", "sources/b.pdf"}
    assert (tmp_path / "proj" / "sources" / "a.pdf").is_file()
