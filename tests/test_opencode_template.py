"""Tests for the OpenCode config scaffolder (``openwiki opencode`` / ``init --opencode``)."""

from __future__ import annotations

import json

from openwiki.opencode_template import OPENCODE_FILES, render_files, scaffold_opencode

MODEL = "qwen3:30b-a3b-instruct-2507-q4_K_M"
EMBED = "bge-m3"
HOST = "http://localhost:11434"
CMD = ["owiki", "mcp"]


def test_render_opencode_json_reflects_model_host_and_mcp():
    files = render_files(MODEL, EMBED, HOST, CMD)
    assert set(files) == set(OPENCODE_FILES)
    config = json.loads(files["opencode.json"])
    assert config["model"] == f"ollama/{MODEL}"
    assert config["provider"]["ollama"]["options"]["baseURL"] == "http://localhost:11434/v1"
    assert config["mcp"]["openwiki"]["command"] == CMD
    assert config["mcp"]["openwiki"]["enabled"] is True
    assert MODEL in config["provider"]["ollama"]["models"]


def test_generated_docs_are_project_agnostic_and_use_owiki():
    files = render_files(MODEL, EMBED, HOST, CMD)
    docs = "\n".join(v for k, v in files.items() if k.endswith(".md")).lower()
    # The whole point of the template: no leakage of the sample synth corpus.
    assert "nautilus" not in docs
    assert "korg" not in docs
    assert "smooth sound" not in docs
    # It should teach the project-aware CLI and name the configured models.
    assert "owiki" in docs
    assert MODEL in files[".opencode/agent/openwiki.md"]
    assert "wiki_ask" in files[".opencode/command/openwiki-help.md"]


def test_scaffold_writes_then_skips_without_force(tmp_path):
    written, skipped = scaffold_opencode(
        tmp_path, chat_model=MODEL, embed_model=EMBED, host=HOST, mcp_command=CMD)
    assert len(written) == len(OPENCODE_FILES) and not skipped
    for rel in OPENCODE_FILES:
        assert (tmp_path / rel).is_file()

    # A second run leaves files untouched (no overwrite) unless forced.
    (tmp_path / "opencode.json").write_text("EDITED", encoding="utf-8")
    written2, skipped2 = scaffold_opencode(
        tmp_path, chat_model=MODEL, embed_model=EMBED, host=HOST, mcp_command=CMD)
    assert not written2 and len(skipped2) == len(OPENCODE_FILES)
    assert (tmp_path / "opencode.json").read_text(encoding="utf-8") == "EDITED"

    written3, _ = scaffold_opencode(
        tmp_path, chat_model=MODEL, embed_model=EMBED, host=HOST, mcp_command=CMD, force=True)
    assert len(written3) == len(OPENCODE_FILES)
    assert json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8"))["model"] == f"ollama/{MODEL}"
