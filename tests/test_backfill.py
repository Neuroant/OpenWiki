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


def test_capture_windows_since_a_watermark():
    from openwiki.claude_code_template import capture_windows

    text = _transcript()
    allw = capture_windows(text, "", max_chars=60)       # day 1 fits one window; day 2 needs two
    assert [(a, b) for a, b, _ in allw] == [("2026-08-01T09:00:00Z", "2026-08-01T09:00:05Z"),
                                            ("2026-08-02T09:00:00Z", "2026-08-02T09:00:00Z"),
                                            ("2026-08-02T09:00:03Z", "2026-08-02T09:00:03Z")]
    assert "CLAUDE.md" not in " ".join(w for _, _, w in allw)       # host blocks stay stripped
    since = capture_windows(text, "2026-08-01T09:00:05Z", max_chars=60)
    assert [a for a, _, _ in since] == ["2026-08-02T09:00:00Z", "2026-08-02T09:00:03Z"]
    assert capture_windows(text, "2026-08-02T09:00:03Z") == []      # nothing new


def _capture_fakes(monkeypatch, cli):
    """Fake the model, embedder and graph around the capture worker; returns what it captured."""
    seen = {"windows": [], "remembered": []}

    class _Fact:
        def __init__(self, text):
            self.subject, self.predicate, self.object = "user", "said", text

    def fake_capture(chat, window, session_date=None, **kw):
        seen["windows"].append((window, session_date))
        return [_Fact(window[:20])], []

    class _Graph:
        writable = True

        def remember(self, sid, facts, embedder, session_date=None, **kw):
            seen["remembered"].append((sid, len(facts), session_date))

        def fold_journal(self, *a, **kw):
            return {}

        def close(self):
            pass

    monkeypatch.setattr(cli, "capture_session_detailed", fake_capture)
    monkeypatch.setattr(cli, "_hook_embedder", lambda project: object())
    monkeypatch.setattr(cli, "_capture_chat", lambda model, host: None)
    monkeypatch.setattr(cli, "_open_graph", lambda *a, **kw: _Graph())
    monkeypatch.setattr(cli, "_coexist_check", lambda *a: None)
    monkeypatch.setattr(cli, "_attribute_resolver", lambda *a: None)
    return seen


def _turns(n, day="2026-08-03", start_hour=9):
    return "\n".join(_line("user" if i % 2 == 0 else "assistant", f"turn {i} " + "x" * 30,
                            f"{day}T{start_hour + i // 60:02d}:{i % 60:02d}:00Z") for i in range(n))


def test_hook_worker_captures_every_turn_since_the_watermark(tmp_path, monkeypatch):
    from openwiki import cli
    from openwiki.project import Project

    proj = Project.load(_project(tmp_path))
    proj.graph_path.parent.mkdir(parents=True, exist_ok=True)
    proj.graph_path.write_text("", encoding="utf-8")
    seen = _capture_fakes(monkeypatch, cli)
    monkeypatch.setattr(cli, "CAPTURE_WINDOW_CHARS", 120)          # ~2 turns per window
    tpath = tmp_path / "t.jsonl"
    tpath.write_text(_turns(6), encoding="utf-8")
    payload = {"_worker": True, "session_id": "s1", "transcript_path": str(tpath)}

    cli._hook_capture(proj, payload)
    first = len(seen["windows"])
    assert first == 3 and len(seen["remembered"]) == 3               # the whole session, not a tail
    assert "turn 0" in seen["windows"][0][0] and "turn 5" in seen["windows"][-1][0]
    assert all(sid == "s1" and d for sid, _, d in seen["remembered"])  # dated by their first turn
    assert cli._capture_watermark(proj, "s1") == "2026-08-03T09:05:00Z"

    tpath.write_text(_turns(10), encoding="utf-8")                   # the session went on
    cli._hook_capture(proj, payload)
    new = [w for w, _ in seen["windows"][first:]]
    assert new and all("turn 0 " not in w for w in new) and "turn 9" in new[-1]   # only new turns
    cli._hook_capture(proj, payload)                                 # nothing new → nothing captured
    assert len(seen["windows"]) == first + len(new)
    assert not list(proj.state_dir.glob("capture-*.lock"))           # lock released


def test_hook_worker_first_seen_long_session_keeps_recent_windows(tmp_path, monkeypatch):
    from openwiki import cli
    from openwiki.project import Project

    proj = Project.load(_project(tmp_path))
    proj.graph_path.parent.mkdir(parents=True, exist_ok=True)
    proj.graph_path.write_text("", encoding="utf-8")
    seen = _capture_fakes(monkeypatch, cli)
    monkeypatch.setattr(cli, "CAPTURE_WINDOW_CHARS", 60)            # one turn per window
    monkeypatch.setattr(cli, "CAPTURE_FIRST_WINDOWS", 3)
    tpath = tmp_path / "t.jsonl"
    tpath.write_text(_turns(10), encoding="utf-8")
    cli._hook_capture(proj, {"_worker": True, "session_id": "old", "transcript_path": str(tpath)})
    assert len(seen["windows"]) == 3 and "turn 9" in seen["windows"][-1][0]   # the most recent ones


def test_hook_worker_defers_to_a_running_worker(tmp_path, monkeypatch):
    from openwiki import cli
    from openwiki.project import Project

    proj = Project.load(_project(tmp_path))
    proj.graph_path.parent.mkdir(parents=True, exist_ok=True)
    proj.graph_path.write_text("", encoding="utf-8")
    seen = _capture_fakes(monkeypatch, cli)
    tpath = tmp_path / "t.jsonl"
    tpath.write_text(_turns(4), encoding="utf-8")
    proj.state_dir.mkdir(parents=True, exist_ok=True)
    (proj.state_dir / "capture-s1.lock").write_text("123", encoding="utf-8")   # a live worker
    cli._hook_capture(proj, {"_worker": True, "session_id": "s1", "transcript_path": str(tpath)})
    assert seen["windows"] == []


def test_split_by_window_carries_each_windows_start():
    from openwiki.claude_code_template import split_transcripts_by_window

    days = split_transcripts_by_window([_transcript()], max_chars=40)
    (day1, w1), (day2, w2) = days
    assert w1[0][0] == "2026-08-01T09:00:00Z"                   # window = (first turn's ts, text)
    assert [ts for ts, _ in w2] == ["2026-08-02T09:00:00Z", "2026-08-02T09:00:03Z"]
