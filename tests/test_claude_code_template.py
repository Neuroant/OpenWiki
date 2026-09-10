"""Tests for the Claude Code config scaffolder (``openwiki claude-code``)."""

from __future__ import annotations

import json

from openwiki.claude_code_template import (
    CLAUDE_CODE_FILES, hooks_config, merge_hooks, parse_claude_transcript,
    render_files, scaffold_claude_code,
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


# -- B6 host-lifecycle hooks ---------------------------------------------------

def test_parse_claude_transcript_extracts_user_and_assistant_text():
    jsonl = "\n".join([
        json.dumps({"type": "user", "message": {"role": "user", "content": "we use qwen3"}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "noted."}, {"type": "tool_use", "name": "x", "input": {}}]}}),
        "not json",
        json.dumps({"type": "system", "message": {"role": "system", "content": "ignore me"}}),
    ])
    out = parse_claude_transcript(jsonl)
    assert "User: we use qwen3" in out and "Assistant: noted." in out
    assert "tool_use" not in out and "ignore me" not in out      # tool blocks + system skipped


def test_parse_claude_transcript_caps_and_tolerates_garbage():
    assert parse_claude_transcript("") == ""
    assert parse_claude_transcript("garbage\n{bad}") == ""
    big = "\n".join(json.dumps({"type": "user", "message": {"role": "user", "content": "x" * 100}})
                    for _ in range(500))
    assert len(parse_claude_transcript(big, max_chars=1000)) <= 1000   # keeps the last max_chars


def test_hooks_config_wires_the_three_events():
    h = hooks_config("owiki hook inject", "owiki hook capture")
    assert h["UserPromptSubmit"][0]["hooks"][0]["command"] == "owiki hook inject"
    assert h["SessionEnd"][0]["hooks"][0]["command"] == "owiki hook capture"
    assert h["PreCompact"][0]["hooks"][0]["command"] == "owiki hook capture"


def test_merge_hooks_preserves_other_settings_and_events():
    existing = {"env": {"X": "1"},
                "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "keep"}]}]}}
    merged = merge_hooks(existing, "inject-cmd", "capture-cmd")
    assert merged["env"] == {"X": "1"}                                       # other settings kept
    assert merged["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "keep"  # other hooks kept
    assert merged["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == "inject-cmd"


def test_scaffold_merges_hooks_into_settings(tmp_path):
    scaffold_claude_code(tmp_path, chat_model=MODEL, embed_model=EMBED, mcp_command=CMD,
                         inject_command="owiki hook inject", capture_command="owiki hook capture")
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert set(settings["hooks"]) >= {"UserPromptSubmit", "SessionEnd", "PreCompact"}
    # a pre-existing unrelated setting survives a re-scaffold
    (tmp_path / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"KEEP": "1"}}), encoding="utf-8")
    scaffold_claude_code(tmp_path, chat_model=MODEL, embed_model=EMBED, mcp_command=CMD,
                         inject_command="owiki hook inject", capture_command="owiki hook capture")
    s2 = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert s2["env"] == {"KEEP": "1"} and "UserPromptSubmit" in s2["hooks"]
