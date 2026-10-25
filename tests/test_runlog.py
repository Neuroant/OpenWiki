"""The saved run log (track O, O.3): one JSON line per hook, worker, scheduled run and CLI write pass in
``<project>/.openwiki/runs.jsonl``, so their timings and model spend outlive the process — ``metrics.COLLECTOR`` keeps
them only in memory. Pure and offline: no Kuzu, no Ollama."""

from __future__ import annotations

import json

import pytest

from openwiki import metrics, runlog
from openwiki.metrics import Event


def _project(tmp_path, memory=False):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "openwiki.toml").write_text(
        f'[project]\nname = "proj"\n\n[memory]\nenabled = {"true" if memory else "false"}\n', encoding="utf-8")
    return root


def _lines(state_dir):
    path = runlog.runs_path(state_dir)
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_llm_spend_sums_chat_and_embed_only():
    evs = [Event(1, 0, "chat", "m", 100.0, 10, 20), Event(2, 0, "embed", "e", 30.0, 5, None),
           Event(3, 0, "http", "GET /api/x", 2.0)]
    assert runlog.llm_spend(evs) == {"calls": 2, "prompt_tokens": 15, "eval_tokens": 20,
                                     "chat_ms": 100.0, "embed_ms": 30.0}
    assert runlog.llm_spend([Event(1, 0, "http", "x", 1.0)]) == {}


def test_a_run_records_duration_notes_and_model_spend(tmp_path):
    with runlog.run("sleep", tmp_path, trigger="schedule") as r:
        metrics.COLLECTOR.record("chat", "fake", 12.0, prompt_tokens=3, eval_tokens=4)
        runlog.note(forgotten=2, themes=None)          # None values are left out
    [line] = _lines(tmp_path)
    assert line["name"] == "sleep" and line["outcome"] == "ok" and line["trigger"] == "schedule"
    assert line["duration_s"] >= 0 and line["start"].endswith("Z") and line["pid"] > 0
    assert line["llm"]["calls"] == 1 and line["llm"]["eval_tokens"] == 4
    assert line["notes"] == {"forgotten": 2}
    assert r.entry == line


def test_outcomes_exit_codes_errors_and_interrupts(tmp_path):
    with runlog.run("a", tmp_path) as r:
        r.exit(2)
    with pytest.raises(ValueError):
        with runlog.run("b", tmp_path):
            raise ValueError("boom")
    with pytest.raises(SystemExit):
        with runlog.run("c", tmp_path):
            raise SystemExit(3)
    with pytest.raises(KeyboardInterrupt):
        with runlog.run("d", tmp_path):
            raise KeyboardInterrupt
    out = {x["name"]: x["outcome"] for x in _lines(tmp_path)}
    assert out == {"a": "exit 2", "b": "error: ValueError: boom", "c": "exit 3", "d": "interrupted"}


def test_note_outside_a_run_is_a_no_op_and_nested_runs_note_the_innermost(tmp_path):
    runlog.note(x=1)                                   # nothing in progress
    with runlog.run("outer", tmp_path):
        with runlog.run("inner", tmp_path):
            runlog.note(k="inner")
        runlog.note(k="outer")
    notes = {x["name"]: x.get("notes") for x in _lines(tmp_path)}
    assert notes == {"inner": {"k": "inner"}, "outer": {"k": "outer"}}


def test_a_log_that_cannot_be_written_never_breaks_the_run(tmp_path):
    blocker = tmp_path / "state"
    blocker.write_text("a file where the directory should be", encoding="utf-8")
    with runlog.run("x", blocker / "sub") as r:
        pass
    assert r.entry is not None and r.outcome == "ok"   # recorded in memory, not on disk


def test_rotation_keeps_two_files_and_reading_spans_both(tmp_path, monkeypatch):
    monkeypatch.setattr(runlog, "MAX_BYTES", 200)
    for i in range(12):
        runlog.append_run(runlog.runs_path(tmp_path), {"name": "hook inject", "t": 1000.0 + i, "duration_s": 0.1})
    assert (tmp_path / runlog.ROTATED_FILE).exists()
    runs = runlog.read_runs(tmp_path)
    assert [r["t"] for r in runs] == sorted(r["t"] for r in runs)
    assert runs[-1]["t"] == 1011.0
    assert len(runs) < 12                              # the oldest rotated out; two files kept


def test_read_runs_filters_and_skips_bad_lines(tmp_path):
    path = runlog.runs_path(tmp_path)
    for rec in ({"name": "hook inject", "t": 10.0}, {"name": "hook capture worker", "t": 20.0},
                {"name": "sleep", "t": 30.0}):
        runlog.append_run(path, rec)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("not json\n{\"name\": \"no time\"}\n")
    assert [r["name"] for r in runlog.read_runs(tmp_path)] == ["hook inject", "hook capture worker", "sleep"]
    assert [r["t"] for r in runlog.read_runs(tmp_path, since=15)] == [20.0, 30.0]
    assert [r["name"] for r in runlog.read_runs(tmp_path, name="hook ")] == ["hook inject", "hook capture worker"]
    assert [r["name"] for r in runlog.read_runs(tmp_path, name="hook inject")] == ["hook inject"]
    assert runlog.last_run(tmp_path, "sleep")["t"] == 30.0
    assert runlog.last_run(tmp_path, "doctor") is None


def test_summarize_per_name_newest_first():
    runs = [{"name": "hook inject", "t": float(i), "duration_s": d, "outcome": "ok"}
            for i, d in enumerate([0.1, 0.2, 0.3, 0.4, 2.0])]
    runs.append({"name": "sleep", "t": 100.0, "duration_s": 60.0, "outcome": "exit 2",
                 "llm": {"calls": 5, "prompt_tokens": 50, "eval_tokens": 70}})
    [sleep, inject] = runlog.summarize(runs)
    assert sleep["name"] == "sleep" and sleep["failures"] == 1 and sleep["calls"] == 5 and sleep["eval_tokens"] == 70
    assert sleep["last"]["outcome"] == "exit 2"
    assert inject["count"] == 5 and inject["p50_s"] == 0.3 and inject["p95_s"] == 2.0 and inject["failures"] == 0


def test_format_age_and_duration():
    assert runlog.format_age(42) == "42 s" and runlog.format_age(7 * 60) == "7 min"
    assert runlog.format_age(3 * 3600) == "3 h" and runlog.format_age(2 * 86400) == "2 d"
    assert runlog.format_duration(0.12) == "120 ms" and runlog.format_duration(4.2) == "4.2 s"
    assert runlog.format_duration(210) == "3.5 min"


# -- wiring -----------------------------------------------------------------------------------------------------


def test_cli_write_commands_are_logged_with_their_trigger(tmp_path, capsys):
    from openwiki import cli
    root = _project(tmp_path)                          # Wiki mode: sleep returns at once
    assert cli.main(["sleep", "--project", str(root), "--trigger", "test"]) == 0   # "schedule" would log to a file
    assert cli.main(["status", "--project", str(root)]) == 0          # read-only: not logged
    [line] = _lines(root / ".openwiki")
    assert line["name"] == "sleep" and line["outcome"] == "ok" and line["trigger"] == "test"
    out = capsys.readouterr().out
    assert "runs   : last 7 days" in out and "sleep" in out and "last" in out


def test_run_names():
    from argparse import Namespace
    from openwiki.cli import _run_name
    assert _run_name(Namespace(command="sleep")) == "sleep"
    assert _run_name(Namespace(command="memory", memory_cmd="export")) == "memory export"
    assert _run_name(Namespace(command="memory", memory_cmd="pending")) is None
    assert _run_name(Namespace(command="handoff", mode="prepare")) == "handoff prepare"
    assert _run_name(Namespace(command="handoff", mode="resume")) is None
    assert _run_name(Namespace(command="ask")) is None
    assert _run_name(Namespace(command="hook")) is None          # hooks record themselves


def test_hooks_record_themselves_in_their_project(tmp_path):
    from openwiki import cli
    root = _project(tmp_path)                          # Wiki mode: the hook does nothing, but is recorded
    cli._run_hook("inject", {"prompt": "hello", "cwd": str(root)})
    cli._run_hook("fold", {"_worker": True}, project_dir=str(root))
    lines = _lines(root / ".openwiki")
    assert [x["name"] for x in lines] == ["hook inject", "hook fold worker"]
    assert lines[0]["notes"] == {"skipped": "wiki mode"}


def test_inject_hook_notes_why_it_skipped(tmp_path):
    from openwiki import cli
    root = _project(tmp_path, memory=True)             # no index, no graph
    cli._run_hook("inject", {"prompt": "how do we test?", "cwd": str(root)})
    cli._run_hook("inject", {"prompt": "push", "cwd": str(root)})
    notes = [x.get("notes") for x in _lines(root / ".openwiki")]
    assert notes == [{"skipped": "no index or graph"}, {"skipped": "chore"}]


def test_web_runs_endpoint(tmp_path):
    from openwiki.project import Project
    from openwiki.web import WikiWebApp
    root = _project(tmp_path)
    app = WikiWebApp(tmp_path / "wiki", project=Project.load(root))
    assert app.runs()["summary"] == []
    import time
    runlog.append_run(runlog.runs_path(root / ".openwiki"),
                      {"name": "sleep", "t": time.time(), "duration_s": 3.0, "outcome": "ok"})
    data = app.runs()
    assert data["available"] and data["total"] == 1 and data["summary"][0]["name"] == "sleep"
    assert data["recent"][0]["duration_s"] == 3.0
    assert WikiWebApp(tmp_path / "wiki").runs() == {"available": False, "reason": "no_project"}
