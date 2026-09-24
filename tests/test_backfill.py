"""Second Brain on a real project: the pieces that wire Claude Code sessions into a memory
project — transcript cleaning + per-day splitting (backfill), hook installation bound to a
project (``claude-code --hooks --into``), the hook's explicit ``--project`` binding, and the
code parser honoring ``.gitignore``. All offline."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
import subprocess
import sys

import pytest

from openwiki.claude_code_template import (
    install_hooks, iter_claude_turns, parse_claude_transcript, split_transcripts_by_day,
)


def _line(role, text, ts, **extra):
    return json.dumps({"type": role, "timestamp": ts,
                       "message": {"role": role, "content": [{"type": "text", "text": text}]},
                       **extra})


def _transcript():
    return "\n".join([
        _line("user", "<system-reminder>CLAUDE.md says a lot</system-reminder>Use port 8137.",
              "2026-08-01T09:00:00Z"),
        _line("assistant", "Noted: port 8137.", "2026-08-01T09:00:05Z"),
        json.dumps({"type": "user", "timestamp": "2026-08-01T10:00:00Z", "message": {
            "role": "user", "content": [{"type": "tool_result", "content": "big tool output"}]}}),
        _line("user", "summary of earlier turns", "2026-08-02T08:00:00Z", isCompactSummary=True),
        _line("user", "<command-name>/compact</command-name>", "2026-08-02T08:00:01Z"),
        _line("user", "Please analyze this codebase and create a CLAUDE.md …", "2026-08-02T08:30:00Z",
              isMeta=True),                                  # an expanded skill body, not the user
        _line("user", "We moved to port 9000.", "2026-08-02T09:00:00Z"),
        _line("assistant", "Port 9000 from today.", "2026-08-02T09:00:03Z"),
        "not json",
    ])


def test_iter_claude_turns_strips_host_blocks_and_summaries():
    turns = [t for _, t in iter_claude_turns(_transcript())]
    assert turns == ["User: Use port 8137.", "Assistant: Noted: port 8137.",
                     "User: We moved to port 9000.", "Assistant: Port 9000 from today."]
    assert "CLAUDE.md" not in parse_claude_transcript(_transcript())


def test_split_transcripts_by_day_groups_and_windows():
    days = split_transcripts_by_day([_transcript()])
    assert [d for d, _ in days] == ["2026-08-01", "2026-08-02"]
    assert days[0][1] == ["User: Use port 8137.\n\nAssistant: Noted: port 8137."]
    small = split_transcripts_by_day([_transcript()], max_chars=25)
    assert small[1][1] == ["User: We moved to port 9000"[:25], "Assistant: Port 9000 from"[:25]]


def test_install_hooks_merges_into_local_settings(tmp_path):
    settings = tmp_path / ".claude" / "settings.local.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}), encoding="utf-8")
    install_hooks(settings, "inj --project X", "cap --project X")
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["permissions"] == {"allow": ["Bash(ls)"]}                 # preserved
    assert data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == "inj --project X"
    assert data["hooks"]["SessionEnd"][0]["hooks"][0]["command"] == "cap --project X"


def _project(tmp_path, name="brain", memory=True):
    root = tmp_path / name
    root.mkdir()
    (root / "openwiki.toml").write_text(
        f'[project]\nname = "{name}"\n\n[memory]\nenabled = {"true" if memory else "false"}\n',
        encoding="utf-8")
    return root


def test_claude_code_into_installs_bound_pinned_hooks(tmp_path):
    from openwiki.cli import main

    proj = _project(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    assert main(["claude-code", "--hooks", "--into", str(repo), "--project", str(proj)]) == 0
    data = json.loads((repo / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    cmd = data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert f'--project "{proj.as_posix()}"' in cmd and "hook inject" in cmd
    assert "-m openwiki" in cmd                       # pinned to this interpreter, not a PATH owiki
    assert not (repo / ".mcp.json").exists()          # hooks only — nothing else written
    assert main(["claude-code", "--into", str(repo), "--project", str(proj)]) == 2   # needs --hooks


def test_hook_uses_the_explicit_project_binding(tmp_path, monkeypatch):
    from openwiki import cli

    proj = _project(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    seen = []
    monkeypatch.setattr(cli, "_hook_inject", lambda project, payload: seen.append(project.root))
    cli._run_hook("inject", {"cwd": str(elsewhere), "prompt": "hi"}, str(proj))
    assert seen == [proj]                                   # bound project, not the cwd
    cli._run_hook("inject", {"cwd": str(elsewhere), "prompt": "hi"})
    assert seen == [proj]                                   # no binding + no project at cwd → nothing
    off = _project(tmp_path, "off", memory=False)
    cli._run_hook("inject", {"cwd": str(elsewhere), "prompt": "hi"}, str(off))
    assert seen == [proj]                                   # Wiki mode → no inject


def test_backfill_dry_run_lists_days(tmp_path, capsys):
    from openwiki.cli import main

    proj = _project(tmp_path)
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "s.jsonl").write_text(_transcript(), encoding="utf-8")
    assert main(["backfill", str(tdir), "--dry-run", "--project", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "claude-2026-08-01: 1 window(s)" in out and "claude-2026-08-02: 1 window(s)" in out
    assert main(["backfill", str(tdir), "--dry-run", "--since", "2026-08-02",
                 "--project", str(proj)]) == 0
    assert "claude-2026-08-01" not in capsys.readouterr().out


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
def test_code_parser_honors_gitignore(tmp_path):
    from openwiki.code_parser import collect_files

    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "output").mkdir()
    (repo / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "output" / "dump.md").write_text("# generated\n", encoding="utf-8")
    (repo / "notes.md").write_text("# untracked but not ignored\n", encoding="utf-8")
    (repo / ".gitignore").write_text("output/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "pkg/mod.py", ".gitignore"], check=True)
    rel = sorted(p.relative_to(repo).as_posix() for p in collect_files(repo))
    assert rel == ["notes.md", "pkg/mod.py"]              # output/ ignored; untracked kept
    shutil.rmtree(repo / ".git", ignore_errors=True)       # not a git repo → plain walk
    assert "output/dump.md" in [p.relative_to(repo).as_posix() for p in collect_files(repo)]


def test_capture_hook_spawns_a_detached_worker(tmp_path, monkeypatch):
    import subprocess

    from openwiki import cli
    from openwiki.project import Project

    proj = Project.load(_project(tmp_path))
    calls = []

    class _Popen:
        def __init__(self, cmd, **kw):
            calls.append((cmd, kw))

    monkeypatch.setattr(subprocess, "Popen", _Popen)
    cli._hook_capture(proj, {"session_id": "s1", "transcript_path": "t.jsonl"})
    (cmd, kw), = calls
    job = Path(cmd[cmd.index("--payload") + 1])
    assert cmd[cmd.index("--project") + 1] == str(proj.root)
    assert json.loads(job.read_text(encoding="utf-8"))["session_id"] == "s1"   # event parked
    assert kw["stdin"] is subprocess.DEVNULL
    assert kw.get("start_new_session") or kw.get("creationflags")             # detached


def test_hook_worker_mode_reads_and_removes_its_payload(tmp_path, monkeypatch):
    from openwiki import cli

    proj = _project(tmp_path)
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"session_id": "s1", "transcript_path": "t.jsonl"}), encoding="utf-8")
    seen = []
    monkeypatch.setattr(cli, "_run_hook", lambda event, payload, project_dir: seen.append((event, payload)))
    assert cli.main(["hook", "capture", "--project", str(proj), "--payload", str(job)]) == 0
    assert seen == [("capture", {"session_id": "s1", "transcript_path": "t.jsonl", "_worker": True})]
    assert not job.exists()
