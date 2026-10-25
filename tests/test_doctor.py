"""``owiki doctor`` (track O, O.2): one health check, pass / warn / fail per line. Every probe that leaves the
process — an interpreter's version, Ollama, the graph, the scheduler — is a fake here."""

from __future__ import annotations

import json
import time

import pytest

from openwiki import doctor, runlog
from openwiki.doctor import Check, Probes, bound_hooks, parse_version
from openwiki.project import Project

NOW = 1_792_900_000.0


def _project(tmp_path, memory=True, extra="", name="proj"):
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    (root / "openwiki.toml").write_text(
        f'[project]\nname = "proj"\n\n[memory]\nenabled = {"true" if memory else "false"}\n{extra}', encoding="utf-8")
    return Project.load(root)


class _Sched:
    def __init__(self, info):
        self.info = info

    def status(self, task):
        return dict(self.info, task=task)


def _probes(**kw):
    base = dict(which=lambda name: None, version_of=lambda p: "0.120.0",
                ollama=lambda host, models: {"reachable": True, "models": {m: True for m in models}},
                open_graph=lambda p: None, scheduler=lambda name, root: _Sched({"installed": False}), now=NOW)
    base.update(kw)
    return Probes(**base)


def _levels(checks, area):
    return [c.level for c in checks if c.area == area]


def test_parse_version():
    assert parse_version("openwiki 0.120.0") == (0, 120, 0) and parse_version("v0.45.0") == (0, 45, 0)
    assert parse_version("") is None


@pytest.mark.parametrize("found, expect", [
    ("0.45.0", "fail"),          # older than v0.48: its build erases a project's memory
    ("0.119.0", "warn"),         # differs from this one
    ("0.121.0", "ok"),
    (None, "warn"),              # didn't answer
])
def test_install_checks_the_owiki_on_the_path(found, expect):
    probes = _probes(which=lambda n: r"C:\bin\owiki.exe" if n == "owiki" else None, version_of=lambda p: found)
    checks = doctor.check_install("0.121.0", probes)
    assert checks[0].area == "openwiki" and _levels(checks, "PATH") == [expect]


def test_install_notes_a_missing_owiki_and_a_shadowing_openwiki():
    assert _levels(doctor.check_install("0.121.0", _probes()), "PATH") == ["info"]
    paths = {"owiki": r"C:\Users\u\.local\bin\owiki.exe", "openwiki": r"C:\Users\u\AppData\Roaming\npm\openwiki.cmd"}
    checks = doctor.check_install("0.121.0", _probes(which=paths.get, version_of=lambda p: "0.121.0"))
    assert _levels(checks, "PATH") == ["ok", "warn"] and "another program" in checks[-1].message
    same = {"owiki": r"C:\bin\owiki.exe", "openwiki": r"C:\bin\openwiki.exe"}
    assert _levels(doctor.check_install("0.121.0", _probes(which=same.get, version_of=lambda p: "0.121.0")),
                   "PATH") == ["ok"]


def _settings(repo, cmds):
    (repo / ".claude").mkdir(parents=True, exist_ok=True)
    hooks = {event: [{"hooks": [{"type": "command", "command": cmd}]}] for event, cmd in cmds.items()}
    (repo / ".claude" / "settings.local.json").write_text(json.dumps({"hooks": hooks}), encoding="utf-8")


def test_bound_hooks_and_their_interpreters(tmp_path):
    p = _project(tmp_path)
    repo = tmp_path / "repo"
    py = tmp_path / "venv" / "python.exe"
    py.parent.mkdir()
    py.write_bytes(b"")
    root = p.root.as_posix()
    _settings(repo, {"UserPromptSubmit": f'"{py.as_posix()}" -m openwiki hook inject --project "{root}"',
                     "SessionEnd": f'{py.as_posix()} -m openwiki hook capture --project {root}',
                     "Stop": f'"{py.as_posix()}" -m openwiki hook capture --project "C:/elsewhere"',
                     "PreCompact": "echo unrelated"})
    hooks = bound_hooks(repo, p.root)
    assert sorted(h["event"] for h in hooks) == ["SessionEnd", "UserPromptSubmit"]
    assert {h["python"] for h in hooks} == {py.as_posix()}
    assert _levels(doctor.check_hooks(p, [repo], "0.121.0", _probes(version_of=lambda x: "0.121.0")),
                   "hooks") == ["ok"]
    assert _levels(doctor.check_hooks(p, [repo], "0.121.0", _probes(version_of=lambda x: "0.119.0")),
                   "hooks") == ["warn"]
    py.unlink()
    assert _levels(doctor.check_hooks(p, [repo], "0.121.0", _probes()), "hooks") == ["fail"]
    assert _levels(doctor.check_hooks(p, [tmp_path / "none"], "0.121.0", _probes()), "hooks") == ["info"]


def test_ollama(tmp_path):
    p = _project(tmp_path)
    assert _levels(doctor.check_ollama(p, _probes()), "ollama") == ["ok"]
    down = _probes(ollama=lambda h, m: {"reachable": False, "error": "refused"})
    assert _levels(doctor.check_ollama(p, down), "ollama") == ["fail"]
    missing = _probes(ollama=lambda h, m: {"reachable": True, "models": {m[0]: True, m[1]: False}})
    [c] = doctor.check_ollama(p, missing)
    assert c.level == "fail" and "ollama pull" in c.hint


class _Graph:
    def __init__(self, memory=True):
        self.memory, self.closed = memory, False

    def has_memory(self):
        return self.memory

    def memory_overview(self):
        return {"assertions": 1234, "sessions": 5, "themes": 7}

    def close(self):
        self.closed = True


def test_graph(tmp_path):
    p = _project(tmp_path)
    assert _levels(doctor.check_graph(p, _probes()), "graph") == ["warn"]          # not built
    p.graph_path.parent.mkdir(parents=True)
    p.graph_path.write_bytes(b"")
    assert _levels(doctor.check_graph(p, _probes()), "graph") == ["fail"]          # can't be opened
    g = _Graph()
    [c] = doctor.check_graph(p, _probes(open_graph=lambda path: g))
    assert c.level == "ok" and "1,234 current fact(s)" in c.message and g.closed


def test_queued_writes_and_approvals(tmp_path):
    from openwiki.graph.journal import journal_path, staged_path
    p = _project(tmp_path)
    assert _levels(doctor.check_writes(p, _probes()), "journal") == ["ok"]
    journal_path(p.graph_path).parent.mkdir(parents=True, exist_ok=True)
    journal_path(p.graph_path).write_text(json.dumps({"op": "remember", "t": NOW - 3 * 86400, "facts": []}) + "\n",
                                          encoding="utf-8")
    staged_path(p.graph_path).write_text(json.dumps({"op": "remember", "id": "a1", "t": NOW, "facts": []}) + "\n",
                                         encoding="utf-8")
    checks = doctor.check_writes(p, _probes())
    assert _levels(checks, "journal") == ["warn"] and _levels(checks, "approvals") == ["info"]


def _sleep_run(p, t, outcome="ok", trigger="schedule"):
    runlog.append_run(runlog.runs_path(p.state_dir), {"name": "sleep", "t": t, "duration_s": 60, "outcome": outcome,
                                                      "trigger": trigger})


def test_schedule(tmp_path):
    p = _project(tmp_path)
    assert _levels(doctor.check_schedule(p, _probes()), "schedule") == ["warn"]    # not installed
    installed = {"installed": True, "interpreter_ok": True, "at": "03:30"}
    probes = _probes(scheduler=lambda n, r: _Sched(installed))
    [c] = doctor.check_schedule(p, probes)
    assert c.level == "ok" and "not run yet" in c.message
    _sleep_run(p, NOW - 3600, trigger="test")                                      # a manual run doesn't count
    assert "not run yet" in doctor.check_schedule(p, probes)[0].message
    _sleep_run(p, NOW - 3 * 86400)
    assert _levels(doctor.check_schedule(p, probes), "schedule") == ["warn"]        # stale
    _sleep_run(p, NOW - 3600, outcome="exit 2")
    assert "exit 2" in doctor.check_schedule(p, probes)[0].message
    _sleep_run(p, NOW - 60)
    assert _levels(doctor.check_schedule(p, probes), "schedule") == ["ok"]
    gone = _probes(scheduler=lambda n, r: _Sched(dict(installed, interpreter_ok=False)))
    assert _levels(doctor.check_schedule(p, gone), "schedule") == ["fail"]


def test_backups(tmp_path):
    from openwiki.memory_export import backup_name
    p = _project(tmp_path)
    assert _levels(doctor.check_backups(p, _probes()), "backups") == ["warn"]      # none yet
    p.backups_dir.mkdir(parents=True)
    (p.backups_dir / backup_name(NOW - 5 * 86400)).write_bytes(b"x" * 1000)
    assert _levels(doctor.check_backups(p, _probes()), "backups") == ["warn"]      # too old
    (p.backups_dir / backup_name(NOW - 3600)).write_bytes(b"x" * 1000)
    assert _levels(doctor.check_backups(p, _probes()), "backups") == ["ok"]
    off = _project(tmp_path, extra="backups = 0\n", name="off")
    assert _levels(doctor.check_backups(off, _probes()), "backups") == ["info"]


def test_runs(tmp_path):
    p = _project(tmp_path)
    assert _levels(doctor.check_runs(p, _probes()), "runs") == ["info"]
    path = runlog.runs_path(p.state_dir)
    runlog.append_run(path, {"name": "hook inject", "t": NOW - 60, "duration_s": 0.2, "outcome": "ok"})
    assert _levels(doctor.check_runs(p, _probes()), "runs") == ["ok"]
    runlog.append_run(path, {"name": "hook capture worker", "t": NOW - 30, "duration_s": 9, "outcome": "error: X"})
    [c] = doctor.check_runs(p, _probes())
    assert c.level == "warn" and "hook capture worker" in c.message


def test_a_broken_check_is_reported_and_the_rest_still_run(tmp_path):
    p = _project(tmp_path)

    def boom(host, models):
        raise RuntimeError("probe exploded")
    checks = doctor.run_checks(p, _probes(ollama=boom))
    assert any(c.area == "doctor" and "probe exploded" in c.message for c in checks)
    assert {"openwiki", "graph", "backups", "runs", "hook log"} <= {c.area for c in checks}
    wiki_mode = doctor.run_checks(_project(tmp_path, memory=False, name="wiki"), _probes())
    assert not {"journal", "schedule", "backups"} & {c.area for c in wiki_mode}


def test_render_and_json(tmp_path):
    p = _project(tmp_path)
    checks = [Check("a", "ok", "fine"), Check("b", "warn", "hmm", "do this"), Check("c", "fail", "broken", "fix")]
    text = doctor.render(p, checks)
    assert "→ do this" in text and "1 problem(s) to fix, 1 warning(s)." in text
    assert doctor.render(p, [Check("a", "ok", "fine")]).endswith("All good.")
    data = doctor.to_json(p, checks)
    assert data["fail"] == 1 and data["warn"] == 1 and data["checks"][1]["hint"] == "do this"


def test_candidate_repos(tmp_path):
    p = _project(tmp_path)
    cwd = tmp_path / "work"
    (cwd / ".git").mkdir(parents=True)
    assert doctor.candidate_repos(p, cwd) == [cwd]
    assert doctor.candidate_repos(p, tmp_path) == []                               # not a git repository


def test_cli_doctor_exit_code_and_json(tmp_path, capsys, monkeypatch):
    from openwiki import cli, handoff
    root = _project(tmp_path).root
    monkeypatch.setattr(doctor, "program_version", lambda p: "0.120.0")
    monkeypatch.setattr(handoff, "ollama_state", lambda host, models: {"reachable": False, "error": "refused"})
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)

    class _Fake:
        def __init__(self, name, root):
            pass

        def status(self, task):
            return {"task": task, "installed": False}
    monkeypatch.setattr(cli, "Scheduler", _Fake)
    assert cli.main(["doctor", "--project", str(root)]) == 1                       # Ollama down → fail
    out = capsys.readouterr().out
    assert "fail  ollama" in out and "sleep not scheduled" in out
    assert cli.main(["doctor", "--json", "--project", str(root)]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["fail"] >= 1 and any(c["area"] == "ollama" for c in data["checks"])
