"""A project's scheduled tasks (track O, O.1): ``owiki schedule install / remove / status``.

Today one task — the nightly ``sleep``; Path C's C1 adds ``world observe`` every few minutes (ADR-54). On Windows a
task goes into the Task Scheduler from an XML definition (``schtasks /Create /XML``), because ``/SC DAILY`` alone
leaves a missed run (the machine was off or asleep at 03:30) unrun and refuses to start on battery; elsewhere it's a
crontab line with a marker comment. Either way the task runs the interpreter that installed it — as
``claude-code --hooks --into`` pins the hooks — windowless on Windows (``pythonw.exe``), with ``--trigger schedule``,
so the run log marks it scheduled and the CLI writes its output to ``.openwiki/schedule.log``.

The commands that touch the scheduler run through an injectable ``runner`` (``subprocess.run``), with fixed
arguments — nothing from a manifest reaches a command line except the project's own path, as an argument. The
rendering is pure and tested; the real scheduler is not touched by the tests.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional
from xml.etree import ElementTree
from xml.sax.saxutils import escape

TASKS = {
    "sleep": {"args": ["sleep"], "at": "03:30", "about": "nightly memory maintenance (owiki sleep)",
              "limit": "PT2H"},
    # Path C, C1 (ADR-54): "world-observe": {"args": ["world", "observe"], "every_minutes": 15, ...}
}
TASK_FOLDER = "OpenWiki"
CRON_MARK = "# openwiki-schedule"
TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
# Task Scheduler's result codes worth a word; the rest are shown in hex
RESULTS = {0: "ok", 0x41301: "running", 0x41303: "not run yet", 0x41306: "terminated", 0x800710E0: "refused"}

Runner = Callable[..., subprocess.CompletedProcess]


def task_id(name: str, root) -> str:
    """A project's folder name in the scheduler: its name, made safe, and a short hash of its path — two projects
    with one name never share tasks."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name or "project")).strip("-.") or "project"
    digest = hashlib.sha1(str(Path(root).resolve()).lower().encode("utf-8")).hexdigest()[:6]
    return f"{slug[:40]}-{digest}"


def task_name(name: str, root, task: str) -> str:
    """The Task Scheduler path of a project's task: ``\\OpenWiki\\<id>\\<task>``."""
    return f"\\{TASK_FOLDER}\\{task_id(name, root)}\\{task}"


def interpreter(python=None, platform: Optional[str] = None) -> Path:
    """The interpreter a task runs: the installing one; on Windows its windowless twin (``pythonw.exe``) when there
    is one, so a nightly run doesn't open a console window."""
    exe = Path(python or sys.executable)
    if (platform or sys.platform) == "win32" and exe.name.lower() == "python.exe":
        windowless = exe.with_name("pythonw.exe")
        if windowless.exists():
            return windowless
    return exe


def task_arguments(root, task: str) -> list:
    """The task's arguments after the interpreter: the CLI command, bound to the project, marked scheduled."""
    return ["-m", "openwiki", *TASKS[task]["args"], "--project", str(root), "--trigger", "schedule"]


def parse_at(at: str) -> tuple:
    """``"03:30"`` → ``(3, 30)``; anything else is a ``ValueError``."""
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(at or "").strip())
    if not m:
        raise ValueError(f"not a time of day (HH:MM): {at!r}")
    return int(m.group(1)), int(m.group(2))


def render_task_xml(command, arguments: list, workdir, at: str = "03:30", description: str = "",
                    every_minutes: Optional[int] = None, limit: str = "PT2H", today: Optional[str] = None) -> str:
    """The Task Scheduler definition: a daily trigger at ``at`` (or every ``every_minutes``), run as the logged-on
    user with least privilege; a missed run starts as soon as it can, battery power doesn't stop it, a second
    instance is not started while one runs, and a run longer than ``limit`` is stopped."""
    hour, minute = parse_at(at)
    day = today or time.strftime("%Y-%m-%d")
    start = f"{day}T{hour:02d}:{minute:02d}:00"
    if every_minutes:
        trigger = (f"<TimeTrigger><Repetition><Interval>PT{int(every_minutes)}M</Interval></Repetition>"
                   f"<StartBoundary>{start}</StartBoundary><Enabled>true</Enabled></TimeTrigger>")
    else:
        trigger = (f"<CalendarTrigger><StartBoundary>{start}</StartBoundary><Enabled>true</Enabled>"
                   f"<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>")
    args = subprocess.list2cmdline([str(a) for a in arguments])
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="{TASK_NS}">
  <RegistrationInfo>
    <Description>{escape(description)}</Description>
  </RegistrationInfo>
  <Triggers>
    {trigger}
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>{escape(limit)}</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(str(command))}</Command>
      <Arguments>{escape(args)}</Arguments>
      <WorkingDirectory>{escape(str(workdir))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def parse_task_xml(xml: str) -> dict:
    """What a Task Scheduler definition runs and when: command, arguments, start time, interval."""
    root = ElementTree.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", xml.lstrip("﻿")))   # str: no declaration
    ns = {"t": TASK_NS}

    def text(path):
        el = root.find(path, ns)
        return el.text if el is not None else None
    start = text(".//t:StartBoundary") or ""
    return {"command": text(".//t:Exec/t:Command"), "arguments": text(".//t:Exec/t:Arguments") or "",
            "at": start[11:16] if len(start) >= 16 else None, "interval": text(".//t:Repetition/t:Interval"),
            "enabled": (text(".//t:Settings/t:Enabled") or "true").lower() == "true"}


def cron_marker(name: str, root, task: str) -> str:
    return f"{CRON_MARK} {task_id(name, root)} {task}"


def cron_line(python, arguments: list, root, at: str, marker: str, every_minutes: Optional[int] = None) -> str:
    """A crontab line for the task, ending in its marker comment (how ``remove`` and ``status`` find it)."""
    hour, minute = parse_at(at)
    when = f"*/{int(every_minutes)} * * * *" if every_minutes else f"{minute} {hour} * * *"
    cmd = " ".join(shlex.quote(str(a)) for a in [python, *arguments])
    return f"{when} cd {shlex.quote(str(root))} && {cmd} >/dev/null 2>&1 {marker}"


def _ps_date(value) -> Optional[float]:
    """A date from PowerShell's JSON — ``"/Date(1761363000000)/"`` (5.1) or ISO (7) — as epoch seconds; Task
    Scheduler's "never" (1999-11-30) is ``None``."""
    if not value:
        return None
    m = re.search(r"Date\((-?\d+)", str(value))
    if m:
        t = int(m.group(1)) / 1000.0
    else:
        try:
            from datetime import datetime
            t = datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return t if t > 946684800 else None          # before 2000 = the scheduler's "never"


def result_label(code) -> str:
    if code is None:
        return ""
    try:
        code = int(code) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return str(code)
    return RESULTS.get(code, f"0x{code:X}" if code > 255 else f"exit {code}")


class Scheduler:
    """Install, remove and inspect a project's tasks — Task Scheduler on Windows, cron elsewhere."""

    def __init__(self, name: str, root, runner: Runner = subprocess.run, platform: Optional[str] = None,
                 python=None) -> None:
        self.name, self.root = name, Path(root)
        self.runner = runner
        self.platform = platform or sys.platform
        self.python = interpreter(python, self.platform)

    def _run(self, cmd: list, input_text: Optional[str] = None) -> subprocess.CompletedProcess:
        return self.runner(cmd, input=input_text, capture_output=True, text=True, errors="replace", timeout=60)

    # -- install / remove ---------------------------------------------------------------------------------------

    def definition(self, task: str, at: Optional[str] = None) -> str:
        """What ``install`` would register — the XML on Windows, the crontab line elsewhere."""
        spec = TASKS[task]
        at = at or spec.get("at", "00:00")
        args = task_arguments(self.root, task)
        if self.platform == "win32":
            return render_task_xml(self.python, args, self.root, at,
                                   f"OpenWiki — {spec['about']} for the project {self.name} ({self.root})",
                                   spec.get("every_minutes"), spec.get("limit", "PT2H"))
        return cron_line(self.python, args, self.root, at, cron_marker(self.name, self.root, task),
                         spec.get("every_minutes"))

    def install(self, task: str, at: Optional[str] = None) -> str:
        """Register the task (replacing an earlier one of this project); the scheduler's name for it."""
        definition = self.definition(task, at)
        if self.platform == "win32":
            name = task_name(self.name, self.root, task)
            with tempfile.TemporaryDirectory() as tmp:
                xml = Path(tmp) / "task.xml"
                xml.write_text(definition, encoding="utf-16")
                res = self._run(["schtasks", "/Create", "/F", "/TN", name, "/XML", str(xml)])
            if res.returncode != 0:
                raise RuntimeError((res.stderr or res.stdout or "schtasks failed").strip())
            return name
        marker = cron_marker(self.name, self.root, task)
        lines = [ln for ln in self._crontab() if not ln.endswith(marker)] + [definition]
        self._write_crontab(lines)
        return marker

    def remove(self, task: str) -> bool:
        """Unregister the task; ``False`` if it wasn't there."""
        if self.platform == "win32":
            res = self._run(["schtasks", "/Delete", "/F", "/TN", task_name(self.name, self.root, task)])
            if res.returncode == 0:
                self._drop_empty_folders()
            return res.returncode == 0
        marker = cron_marker(self.name, self.root, task)
        lines = self._crontab()
        kept = [ln for ln in lines if not ln.endswith(marker)]
        if len(kept) == len(lines):
            return False
        self._write_crontab(kept)
        return True

    def _drop_empty_folders(self) -> None:
        """Task Scheduler keeps a folder after its last task is deleted: drop the project's folder, and OpenWiki's,
        once they're empty. Best-effort (the folder names are made of safe characters only — ``task_id``)."""
        tid = task_id(self.name, self.root)
        script = ("$s = New-Object -ComObject Schedule.Service; $s.Connect(); "
                  f"$f = $s.GetFolder('\\{TASK_FOLDER}'); "
                  f"try {{ $p = $f.GetFolder('{tid}'); "
                  f"if ($p.GetTasks(1).Count -eq 0 -and $p.GetFolders(0).Count -eq 0) {{ $f.DeleteFolder('{tid}', 0) }} }} "
                  "catch {}; "
                  "if ($f.GetTasks(1).Count -eq 0 -and $f.GetFolders(0).Count -eq 0) "
                  f"{{ $s.GetFolder('\\').DeleteFolder('{TASK_FOLDER}', 0) }}")
        try:
            self._run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
        except (OSError, subprocess.SubprocessError):
            pass

    def _crontab(self) -> list:
        res = self._run(["crontab", "-l"])
        return res.stdout.splitlines() if res.returncode == 0 else []       # "no crontab for …" → empty

    def _write_crontab(self, lines: list) -> None:
        res = self._run(["crontab", "-"], input_text="\n".join(lines) + "\n")
        if res.returncode != 0:
            raise RuntimeError((res.stderr or "crontab failed").strip())

    # -- status -------------------------------------------------------------------------------------------------

    def status(self, task: str) -> dict:
        """Whether the task is installed, what it runs, when, and — on Windows — its last and next run."""
        out = {"task": task, "installed": False}
        if self.platform == "win32":
            name = task_name(self.name, self.root, task)
            out["name"] = name
            res = self._run(["schtasks", "/Query", "/TN", name, "/XML"])
            if res.returncode != 0 or not res.stdout.strip():
                return out
            out.update(installed=True, **parse_task_xml(res.stdout))
            out.update(self._run_info(name))
        else:
            marker = cron_marker(self.name, self.root, task)
            line = next((ln for ln in self._crontab() if ln.endswith(marker)), None)
            if line is None:
                return out
            fields = line.split()
            out.update(installed=True, name=marker, line=line,
                       at=f"{int(fields[1]):02d}:{int(fields[0]):02d}" if fields[0].isdigit() and fields[1].isdigit()
                       else None)
            m = re.search(r"&& (\S+)", line)
            out["command"] = shlex.split(m.group(1))[0] if m else None
        cmd = out.get("command")
        out["interpreter_ok"] = bool(cmd) and Path(str(cmd).strip('"')).exists()
        return out

    def _run_info(self, name: str) -> dict:
        """Last run, its result and the next run, from ``Get-ScheduledTaskInfo`` (locale-independent, unlike
        ``schtasks /Query /V``); ``{}`` when PowerShell can't tell."""
        folder, _, leaf = name.rpartition("\\")
        script = (f"Get-ScheduledTaskInfo -TaskPath '{folder}\\' -TaskName '{leaf}' | "
                  "Select-Object LastRunTime, LastTaskResult, NextRunTime | ConvertTo-Json -Compress")
        try:
            res = self._run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
            data = json.loads(res.stdout) if res.returncode == 0 and res.stdout.strip() else {}
        except (OSError, ValueError, subprocess.SubprocessError):
            return {}
        last = _ps_date(data.get("LastRunTime"))
        return {"last_run": last, "last_result": result_label(data.get("LastTaskResult")) if last else "",
                "next_run": _ps_date(data.get("NextRunTime"))}
