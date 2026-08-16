"""Tests for the Claude Code config scaffolder (``openwiki claude-code``)."""

from __future__ import annotations

import json

from openwiki.claude_code_template import (
    CLAUDE_CODE_FILES, render_files, scaffold_claude_code,
)

MODEL = "qwen3:30b-a3b-instruct-2507-q4_K_M"
EMBED = "bge-m3"
CMD = ["owiki", "mcp"]


def test_render_mcp_json_registers_openwiki_server():
    files = render_files(MODEL, EMBED, CMD)
    assert set(files) == set(CLAUDE_CODE_FILES)
    mcp = json.loads(files[".mcp.json"])
    server = mcp["mcpServers"]["openwiki"]
    assert server["command"] == "owiki"
    assert server["args"] == ["mcp"]  # discovery: no hardcoded --wiki/--index/--graph paths


def test_mcp_json_splits_python_fallback_command():
    files = render_files(MODEL, EMBED, ["/py/python", "-m", "openwiki", "mcp"])
    server = json.loads(files[".mcp.json"])["mcpServers"]["openwiki"]
    assert server["command"] == "/py/python"
    assert server["args"] == ["-m", "openwiki", "mcp"]


def test_generated_docs_are_project_agnostic():
    docs = "\n".join(v for k, v in render_files(MODEL, EMBED, CMD).items()
                     if k.endswith(".md")).lower()
    assert "nautilus" not in docs and "korg" not in docs and "smooth sound" not in docs
    assert "wiki_ask" in docs  # the commands/skill reference the MCP tools


def test_scaffold_writes_then_skips_without_force(tmp_path):
    written, skipped = scaffold_claude_code(
        tmp_path, chat_model=MODEL, embed_model=EMBED, mcp_command=CMD)
    assert len(written) == len(CLAUDE_CODE_FILES) and not skipped
    for rel in CLAUDE_CODE_FILES:
        assert (tmp_path / rel).is_file()

    (tmp_path / ".mcp.json").write_text("EDITED", encoding="utf-8")
    written2, skipped2 = scaffold_claude_code(
        tmp_path, chat_model=MODEL, embed_model=EMBED, mcp_command=CMD)
    assert not written2 and len(skipped2) == len(CLAUDE_CODE_FILES)
    assert (tmp_path / ".mcp.json").read_text(encoding="utf-8") == "EDITED"

    written3, _ = scaffold_claude_code(
        tmp_path, chat_model=MODEL, embed_model=EMBED, mcp_command=CMD, force=True)
    assert len(written3) == len(CLAUDE_CODE_FILES)
    assert json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["openwiki"]
