"""Tests for the Phase 2 build state: fingerprint chain, lockfile, staleness."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from openwiki.pipeline import (
    STAGES, BuildState, compute_fingerprints, file_sig, sources_signature, stale_stages,
)
from openwiki.project import MANIFEST, Project, render_manifest


def test_file_sig_url_and_directory(tmp_path):
    assert file_sig("https://example.com/page")[0] == "url"     # URLs sign by string
    repo = tmp_path / "repo" / "src"
    repo.mkdir(parents=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "big.bin").write_bytes(b"\x00" * 10)                 # binary → excluded
    sig = file_sig(tmp_path / "repo")
    assert sig[0] == "dir"
    entries = sig[2]
    assert any("a.py" in entry[0] for entry in entries)
    assert not any("big.bin" in entry[0] for entry in entries)  # pruned from the signature


def _project(root: Path, **kw) -> Project:
    root.mkdir(parents=True, exist_ok=True)
    (root / "sources").mkdir(exist_ok=True)
    (root / "sources" / "m.pdf").write_bytes(b"%PDF-1.4 hello")
    (root / MANIFEST).write_text(
        render_manifest(
            name="p",
            sources=[{"type": "pdf", "path": "sources/m.pdf"}],
            split_level=kw.get("split_level", 2),
            chunk_size=kw.get("chunk_size", 180),
            similar_k=kw.get("similar_k", 6),
        ),
        encoding="utf-8",
    )
    return Project.load(root)


def test_sources_signature_tracks_content(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"one")
    sig1 = sources_signature([f])
    f.write_bytes(b"two-longer")
    assert sources_signature([f]) != sig1


def test_fingerprints_change_with_params(tmp_path):
    # One project / one source file: mutate manifest params in place so the source
    # signature (which includes mtime) stays fixed and we isolate each param.
    proj = _project(tmp_path / "p")
    srcs = proj.source_paths()
    base = compute_fingerprints(proj, srcs)

    proj.data["build"]["split_level"] = 3   # touches wiki + index + graph, not ingest
    fp = compute_fingerprints(proj, srcs)
    assert fp["ingest"] == base["ingest"]
    assert fp["wiki"] != base["wiki"]
    assert fp["index"] != base["index"]
    assert fp["graph"] != base["graph"]
    proj.data["build"]["split_level"] = 2

    proj.data["build"]["chunk_size"] = 250   # touches index + graph, not wiki
    fp = compute_fingerprints(proj, srcs)
    assert fp["wiki"] == base["wiki"]
    assert fp["index"] != base["index"]
    assert fp["graph"] != base["graph"]
    proj.data["build"]["chunk_size"] = 180

    proj.data["graph"]["similar_k"] = 10     # touches only graph
    fp = compute_fingerprints(proj, srcs)
    assert fp["index"] == base["index"]
    assert fp["graph"] != base["graph"]


def test_graph_fingerprint_tracks_entity_ontology(tmp_path):
    proj = _project(tmp_path / "p")
    proj.data["graph"]["entities"] = True
    srcs = proj.source_paths()
    base = compute_fingerprints(proj, srcs)["graph"]
    proj.data["graph"]["entity_types"] = ["Concept", "Method"]   # changing the ontology…
    assert compute_fingerprints(proj, srcs)["graph"] != base      # …invalidates the graph stage


def test_buildstate_roundtrip(tmp_path):
    proj = _project(tmp_path / "p")
    state = BuildState.load(proj)
    assert state.fingerprint("ingest") is None
    state.record("ingest", "abc123", proj.parsed_dir / "m.json", {"pages": 5})
    state.save()

    reloaded = BuildState.load(proj)
    assert reloaded.fingerprint("ingest") == "abc123"
    assert reloaded.get("ingest")["stats"]["pages"] == 5


def test_stale_stages_logic(tmp_path):
    proj = _project(tmp_path / "p")
    fps = compute_fingerprints(proj, proj.source_paths())
    state = BuildState.load(proj)
    all_exist = {s: True for s in STAGES}

    # No recorded state → everything is stale.
    assert stale_stages(state, fps, all_exist) == list(STAGES)

    # Record all up to date → nothing stale.
    for s in STAGES:
        state.record(s, fps[s], "out")
    assert stale_stages(state, fps, all_exist) == []

    # Missing output → that stage is stale again.
    exists = dict(all_exist, graph=False)
    assert stale_stages(state, fps, exists) == ["graph"]

    # --force reruns everything in scope.
    assert stale_stages(state, fps, all_exist, force=True) == list(STAGES)

    # --only restricts the scope.
    assert stale_stages(state, fps, all_exist, only={"index"}, force=True) == ["index"]


# --------------------------------------------------------------- CLI status
cli = pytest.importorskip("openwiki.cli")


def test_status_reports_missing_before_build(tmp_path, capsys):
    proj = _project(tmp_path / "p")
    ns = argparse.Namespace(command="status", project_obj=proj)
    assert cli._cmd_status(ns) == 0
    out = capsys.readouterr().out
    assert "Project: p" in out
    assert "missing" in out          # no artifacts yet
    assert "ingest" in out and "graph" in out


def test_build_requires_project(capsys):
    ns = argparse.Namespace(command="build", project_obj=None, only=None, force=False, verbose=False)
    assert cli._cmd_build(ns) == 2
    assert "not in an OpenWiki project" in capsys.readouterr().err
