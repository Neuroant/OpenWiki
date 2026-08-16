"""Tests for Phase 3: the ~/.openwiki global config + project registry, and
their place in setting/project resolution."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from openwiki.project import MANIFEST, Project, render_manifest
from openwiki.userconfig import Registry, UserConfig


def _mk_project(root: Path, **kw) -> Project:
    root.mkdir(parents=True, exist_ok=True)
    (root / "sources").mkdir(exist_ok=True)
    (root / "sources" / "m.pdf").write_bytes(b"%PDF-1.4 x")
    (root / MANIFEST).write_text(
        render_manifest(
            name=kw.get("name", "p"),
            sources=[{"type": "pdf", "path": "sources/m.pdf"}],
            chat=kw.get("chat", "proj-chat"),
        ),
        encoding="utf-8",
    )
    return Project.load(root)


def test_userconfig_setting(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[models]\nhost = "http://uc:1"\nchat = "uc-chat"\n', encoding="utf-8"
    )
    uc = UserConfig.load(home=tmp_path)
    assert uc.setting("models", "host") == "http://uc:1"
    assert uc.setting("models", "chat") == "uc-chat"
    assert uc.setting("models", "embed", "fallback") == "fallback"


def test_userconfig_missing_is_empty(tmp_path):
    uc = UserConfig.load(home=tmp_path)   # no config.toml
    assert uc.setting("models", "chat", "def") == "def"


def test_registry_roundtrip(tmp_path):
    reg = Registry.load(home=tmp_path)
    assert reg.projects() == {}
    reg.add("nautilus", tmp_path / "proj")
    reg.add("specs", tmp_path / "specs")
    assert reg.use("nautilus") is True
    assert reg.use("nope") is False

    reloaded = Registry.load(home=tmp_path)
    assert set(reloaded.projects()) == {"nautilus", "specs"}
    assert reloaded.active() == "nautilus"
    assert reloaded.active_path() == Path((tmp_path / "proj").as_posix())

    assert reloaded.remove("nautilus") is True
    after = Registry.load(home=tmp_path)
    assert after.active() is None and "nautilus" not in after.projects()


# ------------------------------------------------------------------- CLI
cli = pytest.importorskip("openwiki.cli")


def test_apply_project_setting_precedence(tmp_path):
    proj = _mk_project(tmp_path / "p", chat="proj-chat")
    proj.data["models"].pop("host")   # manifest omits host → user config should fill it
    uc = UserConfig({"models": {"chat": "uc-chat", "host": "http://uc:1"}})

    ns = argparse.Namespace(command="ask", index=None, graph=None, model=None, host=None)
    cli._apply_project(ns, proj, uc)
    assert ns.model == "proj-chat"       # manifest beats user config
    assert ns.host == "http://uc:1"      # manifest missing → user config

    ns2 = argparse.Namespace(command="ask", index=None, graph=None, model="cli", host=None)
    cli._apply_project(ns2, proj, uc)
    assert ns2.model == "cli"            # explicit flag beats everything

    ns3 = argparse.Namespace(command="ask", index=None, graph=None, model=None, host=None)
    cli._apply_project(ns3, None, None)
    assert ns3.host == "http://localhost:11434"   # nothing set → built-in default


def test_resolve_project_registry_fallback(tmp_path, monkeypatch):
    proj = _mk_project(tmp_path / "proj")
    monkeypatch.setenv("OPENWIKI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPENWIKI_PROJECT", raising=False)
    monkeypatch.chdir(tmp_path)   # not inside any project → discovery yields None

    reg = Registry.load()
    reg.add("proj", proj.root)
    reg.use("proj")

    resolved = cli._resolve_project(None)
    assert resolved is not None and resolved.root == proj.root


def test_add_source_scans_folder_and_is_idempotent(tmp_path):
    proj = _mk_project(tmp_path / "p")   # declares sources/m.pdf
    more = tmp_path / "more"
    more.mkdir()
    (more / "x.pdf").write_bytes(b"%PDF x")
    (more / "y.pdf").write_bytes(b"%PDF y")

    ns = argparse.Namespace(command="project", project_cmd="add-source", path=more, project_obj=proj)
    assert cli._cmd_project(ns) == 0
    assert {s.path for s in Project.load(proj.root).sources} == {
        "sources/m.pdf", "sources/x.pdf", "sources/y.pdf"}

    # adding the same folder again declares nothing new — each real `owiki` invocation
    # loads a fresh project, so reload to mirror that.
    ns2 = argparse.Namespace(command="project", project_cmd="add-source", path=more,
                             project_obj=Project.load(proj.root))
    assert cli._cmd_project(ns2) == 0
    assert len(Project.load(proj.root).sources) == 3


def test_add_source_appends_and_stays_valid_toml(tmp_path):
    proj = _mk_project(tmp_path / "p")
    extra = tmp_path / "extra.pdf"
    extra.write_bytes(b"%PDF-1.4 y")
    ns = argparse.Namespace(command="project", project_cmd="add-source",
                            path=extra, project_obj=proj)
    assert cli._cmd_project(ns) == 0
    assert (proj.root / "sources" / "extra.pdf").is_file()
    # Manifest still parses and now lists two sources.
    reloaded = Project.load(proj.root)
    assert {s.path for s in reloaded.sources} == {"sources/m.pdf", "sources/extra.pdf"}
