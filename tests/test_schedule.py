"""A project's scheduled tasks (track O, O.1): ``owiki schedule install / remove / status``. The rendering — the Task
Scheduler XML, the crontab line — is pure; the scheduler commands run through a fake runner here, so the tests never
touch the machine's real scheduler."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from openwiki import schedule
from openwiki.schedule import (
    Scheduler, cron_line, cron_marker, parse_at, parse_task_xml, render_task_xml, result_label, task_arguments,
    task_id, task_name,
)


def test_task_ids_are_safe_and_tell_two_projects_of_one_name_apart(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assert task_id("openwiki-dev", a) == task_id("openwiki-dev", a)
    assert task_id("openwiki-dev", a) != task_id("openwiki-dev", b)
    tid = task_id("My Wiki: \"äöü\" & co", a)
    assert all(c.isalnum() or c in "._-" for c in tid)
    assert task_name("p", a, "sleep") == f"\\OpenWiki\\{task_id('p', a)}\\sleep"


def test_interpreter_prefers_the_windowless_twin_on_windows(tmp_path):
    exe = tmp_path / "python.exe"
    exe.write_bytes(b"")
    assert schedule.interpreter(exe, "win32") == exe                 # no pythonw next to it
    (tmp_path / "pythonw.exe").write_bytes(b"")
    assert schedule.interpreter(exe, "win32") == tmp_path / "pythonw.exe"
    assert schedule.interpreter(exe, "linux") == exe


def test_arguments_and_times(tmp_path):
    assert task_arguments(tmp_path, "sleep") == ["-m", "openwiki", "sleep", "--project", str(tmp_path),
                                                 "--trigger", "schedule"]
    assert parse_at("03:30") == (3, 30) and parse_at("7:05") == (7, 5)
    for bad in ("24:00", "3.30", "", "noon"):
        with pytest.raises(ValueError):
            parse_at(bad)


def test_task_xml_round_trips_and_escapes():
    xml = render_task_xml(r"C:\Py 3\pythonw.exe", ["-m", "openwiki", "sleep", "--project", r"D:\K & B\wiki"],
                          r"D:\K & B\wiki", "04:15", "a & b", today="2026-10-25")
    assert "<StartWhenAvailable>true</StartWhenAvailable>" in xml
    assert "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in xml
    assert "<LogonType>InteractiveToken</LogonType>" in xml and "&amp;" in xml
    got = parse_task_xml(xml)
    assert got["command"] == r"C:\Py 3\pythonw.exe" and got["at"] == "04:15" and got["enabled"]
    assert got["arguments"] == r'-m openwiki sleep --project "D:\K & B\wiki"'
    assert got["interval"] is None
    every = parse_task_xml(render_task_xml("py", ["-m"], ".", "00:00", every_minutes=15, today="2026-10-25"))
    assert every["interval"] == "PT15M"
    assert parse_task_xml("\ufeff" + xml)["at"] == "04:15"          # a BOM or the UTF-16 declaration is fine


def test_cron_lines(tmp_path):
    mark = cron_marker("p", tmp_path, "sleep")
    line = cron_line("/usr/bin/python3", ["-m", "openwiki", "sleep"], "/srv/my wiki", "03:30", mark)
    assert line.startswith("30 3 * * * cd '/srv/my wiki' && /usr/bin/python3 -m openwiki sleep")
    assert line.endswith(mark)
    assert cron_line("py", [], "/r", "00:00", mark, every_minutes=15).startswith("*/15 * * * *")


def test_result_labels_and_powershell_dates():
    assert result_label(0) == "ok" and result_label(267011) == "not run yet" and result_label(2) == "exit 2"
    assert result_label(-2147024891) == "0x80070005" and result_label(None) == ""
    assert schedule._ps_date("/Date(1792921383000)/") == 1792921383.0
    assert schedule._ps_date("2026-10-25T09:43:03Z") == pytest.approx(1792921383.0)
    assert schedule._ps_date("/Date(943916400000)/") is None        # 1999-11-30: "never"
    assert schedule._ps_date(None) is None


class FakeRunner:
    """Records the scheduler commands; answers like schtasks / PowerShell / crontab would."""

    def __init__(self):
        self.calls, self.tasks, self.crontab, self.info = [], {}, None, {}

    def __call__(self, cmd, input=None, **kw):
        self.calls.append(cmd)
        ok = lambda out="": subprocess.CompletedProcess(cmd, 0, out, "")
        fail = lambda err="": subprocess.CompletedProcess(cmd, 1, "", err)
        if cmd[0] == "schtasks":
            name = cmd[cmd.index("/TN") + 1]
            if cmd[1] == "/Create":
                self.tasks[name] = Path(cmd[cmd.index("/XML") + 1]).read_text(encoding="utf-16")
                return ok("SUCCESS")
            if cmd[1] == "/Delete":
                return ok() if self.tasks.pop(name, None) else fail("not found")
            if cmd[1] == "/Query":
                return ok(self.tasks[name]) if name in self.tasks else fail("not found")
        if cmd[0] == "powershell":
            return ok(json.dumps(self.info)) if "Get-ScheduledTaskInfo" in cmd[-1] else ok()
        if cmd[0] == "crontab":
            if cmd[1] == "-l":
                return ok(self.crontab) if self.crontab is not None else fail("no crontab for user")
            self.crontab = input
            return ok()
        raise AssertionError(cmd)


def test_windows_install_status_remove(tmp_path):
    run = FakeRunner()
    exe = tmp_path / "python.exe"
    exe.write_bytes(b"")
    s = Scheduler("proj", tmp_path / "proj", runner=run, platform="win32", python=exe)
    assert s.status("sleep") == {"task": "sleep", "installed": False, "name": task_name("proj", tmp_path / "proj",
                                                                                         "sleep")}
    name = s.install("sleep", "02:45")
    assert name in run.tasks and "<StartBoundary>" in run.tasks[name]
    run.info = {"LastRunTime": "/Date(1792921383000)/", "LastTaskResult": 0, "NextRunTime": "/Date(1792982700000)/"}
    st = s.status("sleep")
    assert st["installed"] and st["at"] == "02:45" and st["command"] == str(exe) and st["interpreter_ok"]
    assert st["last_result"] == "ok" and st["last_run"] == 1792921383.0 and st["next_run"] == 1792982700.0
    assert "--trigger schedule" in st["arguments"]
    assert s.install("sleep") == name and len(run.tasks) == 1          # replaces, /F
    assert s.remove("sleep") and not s.remove("sleep")
    assert any(c[0] == "powershell" and "DeleteFolder" in c[-1] for c in run.calls)   # empty folders dropped


def test_windows_status_flags_a_missing_interpreter(tmp_path):
    run = FakeRunner()
    s = Scheduler("proj", tmp_path, runner=run, platform="win32", python=tmp_path / "gone" / "python.exe")
    s.install("sleep")
    assert s.status("sleep")["interpreter_ok"] is False


def test_install_failure_raises(tmp_path):
    def refusing(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "ERROR: Access is denied.")
    s = Scheduler("proj", tmp_path, runner=refusing, platform="win32")
    with pytest.raises(RuntimeError, match="Access is denied"):
        s.install("sleep")


def test_cron_install_keeps_other_lines_and_replaces_its_own(tmp_path):
    run = FakeRunner()
    run.crontab = "0 * * * * /usr/bin/backup  # someone else's\n"
    exe = tmp_path / "python3"
    exe.write_bytes(b"")
    s = Scheduler("proj", tmp_path / "proj", runner=run, platform="linux", python=exe)
    s.install("sleep", "03:30")
    s.install("sleep", "04:00")
    lines = run.crontab.splitlines()
    assert lines[0].startswith("0 * * * * /usr/bin/backup") and len(lines) == 2
    st = s.status("sleep")
    assert st["installed"] and st["at"] == "04:00" and st["interpreter_ok"]
    assert s.remove("sleep") and run.crontab.splitlines() == lines[:1]
    assert not s.remove("sleep") and not s.status("sleep")["installed"]


# -- the CLI ----------------------------------------------------------------------------------------------------


def _project(tmp_path, memory=True):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "openwiki.toml").write_text(
        f'[project]\nname = "proj"\n\n[memory]\nenabled = {"true" if memory else "false"}\n', encoding="utf-8")
    return root


@pytest.fixture
def fake_scheduler(monkeypatch):
    from openwiki import cli
    run = FakeRunner()

    class _Fake(Scheduler):
        def __init__(self, name, root, **kw):
            super().__init__(name, root, runner=run, platform="win32")
    monkeypatch.setattr(cli, "Scheduler", _Fake)
    return run


def test_cli_install_status_remove(tmp_path, capsys, fake_scheduler):
    from openwiki import cli
    root = _project(tmp_path)
    assert cli.main(["schedule", "install", "--dry-run", "--project", str(root)]) == 0
    assert "<CalendarTrigger>" in capsys.readouterr().out and not fake_scheduler.tasks
    assert cli.main(["schedule", "install", "--at", "02:00", "--project", str(root)]) == 0
    assert "daily at 02:00" in capsys.readouterr().out and len(fake_scheduler.tasks) == 1
    assert cli.main(["schedule", "status", "--project", str(root)]) == 0
    out = capsys.readouterr().out
    assert "sleep    installed · daily at 02:00" in out and "no scheduled run recorded yet" in out
    assert cli.main(["schedule", "remove", "--project", str(root)]) == 0
    assert "Removed sleep" in capsys.readouterr().out and not fake_scheduler.tasks
    assert cli.main(["schedule", "status", "--project", str(root)]) == 0
    assert "not installed" in capsys.readouterr().out


def test_cli_refuses_sleep_in_wiki_mode_and_a_bad_time(tmp_path, capsys, fake_scheduler):
    from openwiki import cli
    assert cli.main(["schedule", "install", "--project", str(_project(tmp_path, memory=False))]) == 2
    assert "Wiki mode" in capsys.readouterr().err
    (tmp_path / "b").mkdir()
    assert cli.main(["schedule", "install", "--at", "25:00", "--project", str(_project(tmp_path / "b"))]) == 2
    assert not fake_scheduler.tasks


def test_a_scheduled_run_writes_its_output_to_the_schedule_log(tmp_path, monkeypatch):
    from openwiki import cli, runlog
    root = _project(tmp_path, memory=False)
    monkeypatch.setattr(sys, "stdout", sys.stdout)                 # restored after the test
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    try:
        assert cli.main(["sleep", "--project", str(root), "--trigger", "schedule"]) == 0
    finally:
        if getattr(sys.stdout, "name", "").endswith("schedule.log"):
            sys.stdout.close()
    log = (root / ".openwiki" / "schedule.log").read_text(encoding="utf-8")
    assert "=== " in log and "--trigger schedule" in log and "memory is disabled" in log
    assert runlog.last_run(root / ".openwiki", "sleep")["trigger"] == "schedule"
