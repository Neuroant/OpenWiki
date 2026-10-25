"""``owiki doctor`` (track O, O.2): one health check of a project and the machine it runs on — pass / warn / fail
per line, a non-zero exit when something is broken.

What it checks: which OpenWiki runs (this interpreter, the ``owiki`` on the PATH, a different program shadowing
``openwiki``), the interpreter each bound repository's hooks run, Ollama and the project's models, the graph,
memory writes waiting in the journal or for approval, the scheduled tasks (O.1), the newest backup (O.4), failed runs
in the run log (O.3), problems in the hook log, and the wiki's freshness against its repository. From Path C's C2 on:
each twin reachable, its token set (ADR-54).

The probes are the handoff's (``handoff.ollama_state``, ``hook_log_problems``, ``wiki_freshness``) plus a few of its
own; everything that leaves the process — running an interpreter for its version, opening the graph, asking the
scheduler — is injected (``Probes``), so the checks are tested offline. No Kuzu import here.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

LEVELS = ("ok", "info", "warn", "fail")
MEMORY_WIPING_BEFORE = (0, 48, 0)     # an OpenWiki older than v0.48 erases a project's memory on `build` (B0)
QUEUE_STALE_S = 24 * 3600             # journal writes older than this: the fold isn't happening
BACKUP_STALE_S = 2 * 86400            # the newest backup older than this: sleep isn't running
SLEEP_STALE_S = 2 * 86400             # no scheduled sleep for this long: the task isn't running
RUN_WINDOW_S = 24 * 3600              # failed runs counted over this window
WIKI_BEHIND_WARN = 20                 # commits since the index was built


@dataclass
class Check:
    area: str
    level: str                  # ok | info | warn | fail
    message: str
    hint: str = ""


@dataclass
class Probes:
    """What the checks may ask the outside world — replaced by fakes in the tests."""
    which: Callable = None                  # shutil.which
    version_of: Callable = None             # a program or interpreter → its OpenWiki version, or None
    ollama: Callable = None                 # handoff.ollama_state(host, models)
    open_graph: Callable = None             # graph path → a read-only GraphStore, or None
    scheduler: Callable = None              # (name, root) → schedule.Scheduler
    now: float = field(default_factory=time.time)


def parse_version(text) -> Optional[tuple]:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", str(text or ""))
    return tuple(int(x) for x in m.groups()) if m else None


def program_version(path, timeout: float = 20.0) -> Optional[str]:
    """The OpenWiki version a program reports: an interpreter (``python … -c``) or the ``owiki`` launcher
    (``--version``). ``None`` if it can't tell."""
    p = Path(str(path))
    name = p.name.lower()
    cmd = ([str(p), "-c", "import openwiki; print(openwiki.__version__)"] if name.startswith("python")
           else [str(p), "--version"])
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    v = parse_version(res.stdout) if res.returncode == 0 else None
    return ".".join(map(str, v)) if v else None


def bound_hooks(repo, project_root) -> list:
    """The OpenWiki hooks in a repository's Claude Code settings that are bound to ``project_root``:
    ``[{"event", "python", "file"}]`` — the interpreter is the command's first word."""
    out = []
    target = _norm(project_root)
    for name in ("settings.local.json", "settings.json"):
        path = Path(repo) / ".claude" / name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        hooks = data.get("hooks") if isinstance(data, dict) else None
        for event, blocks in (hooks.items() if isinstance(hooks, dict) else []):
            for block in (blocks if isinstance(blocks, list) else []):
                for hook in ((block.get("hooks") or []) if isinstance(block, dict) else []):
                    cmd = str(hook.get("command") or "") if isinstance(hook, dict) else ""
                    if " hook " not in cmd or ("openwiki" not in cmd and "owiki" not in cmd):
                        continue
                    m = re.search(r'--project\s+(?:"([^"]+)"|(\S+))', cmd)
                    if not m or _norm(m.group(1) or m.group(2)) != target:
                        continue
                    first = re.match(r'\s*(?:"([^"]+)"|(\S+))', cmd)
                    out.append({"event": event, "python": (first.group(1) or first.group(2)) if first else "",
                                "file": str(path)})
    return out


def _norm(path) -> str:
    try:
        return str(Path(str(path)).resolve()).lower().rstrip("\\/")
    except OSError:
        return str(path).lower().rstrip("\\/")


def _age(seconds: float) -> str:
    from .runlog import format_age
    return format_age(seconds)


# -- the checks -------------------------------------------------------------------------------------------------


def check_install(version: str, probes: Probes) -> list:
    out = [Check("openwiki", "ok", f"{version} ({sys.executable})")]
    mine = parse_version(version)
    owiki = probes.which("owiki")
    if owiki is None:
        out.append(Check("PATH", "info", "no `owiki` on the PATH", "run `python -m openwiki`, or install it with "
                                                                    "`install-openwiki.ps1 -Editable`"))
    else:
        v = probes.version_of(owiki)
        pv = parse_version(v)
        if pv is None:
            out.append(Check("PATH", "warn", f"`owiki` ({owiki}) didn't report a version"))
        elif pv < MEMORY_WIPING_BEFORE:
            out.append(Check("PATH", "fail", f"`owiki` is {v} ({owiki}) — older than v0.48, its `build` erases a "
                                             "project's memory", "`install-openwiki.ps1 -Editable` (or remove it)"))
        elif mine and pv != mine:
            out.append(Check("PATH", "warn", f"`owiki` is {v}, this one {version} ({owiki})",
                             "`install-openwiki.ps1 -Editable` keeps it in step with the checkout"))
        else:
            out.append(Check("PATH", "ok", f"`owiki` → {v} ({owiki})"))
    other = probes.which("openwiki")
    if other and owiki and Path(other).parent != Path(owiki).parent:
        out.append(Check("PATH", "warn", f"`openwiki` runs another program ({other})", "use `owiki`"))
    return out


def check_hooks(project, repos, version: str, probes: Probes) -> list:
    out = []
    seen: dict = {}
    for repo in repos:
        for h in bound_hooks(repo, project.root):
            seen.setdefault((str(repo), h["python"]), []).append(h["event"])
    if not seen:
        checked = ", ".join(str(r) for r in repos) or "none"
        return [Check("hooks", "info", f"no repository's Claude Code hooks bound to this project found (checked: "
                                       f"{checked})", "`owiki claude-code --hooks --into <repo> --project <here>`")]
    mine = parse_version(version)
    for (repo, python), events in sorted(seen.items()):
        label = f"{repo}: {len(events)} hook(s) → {python}"
        if not python or not Path(python).exists():
            out.append(Check("hooks", "fail", f"{label} — that interpreter doesn't exist",
                             "`owiki claude-code --hooks --into <repo>` again"))
            continue
        v = probes.version_of(python)
        pv = parse_version(v)
        if pv is None:
            out.append(Check("hooks", "warn", f"{label} — OpenWiki doesn't import there"))
        elif mine and pv != mine:
            out.append(Check("hooks", "warn", f"{label} runs {v}, this one {version}"))
        else:
            out.append(Check("hooks", "ok", f"{label} ({v})"))
    return out


def check_ollama(project, probes: Probes) -> list:
    from .project import DEFAULT_CHAT, DEFAULT_EMBED, DEFAULT_HOST
    host = project.setting("models", "host", DEFAULT_HOST)
    models = [project.setting("models", "embed", DEFAULT_EMBED), project.setting("models", "chat", DEFAULT_CHAT)]
    st = probes.ollama(host, models)
    if not st.get("reachable"):
        return [Check("ollama", "fail", f"{host} not reachable ({st.get('error', '')})", "start Ollama")]
    missing = [m for m, ok in (st.get("models") or {}).items() if not ok]
    if missing:
        return [Check("ollama", "fail", f"{host}: not pulled: {', '.join(missing)}",
                      "; ".join(f"`ollama pull {m}`" for m in missing))]
    return [Check("ollama", "ok", f"{host} · {', '.join(models)} pulled")]


def check_graph(project, probes: Probes) -> list:
    path = project.graph_path
    if not Path(path).exists():
        return [Check("graph", "warn", f"not built yet ({path})", "`owiki build`")]
    graph = probes.open_graph(path)
    if graph is None:
        return [Check("graph", "fail", f"{path} can't be opened for reading",
                      "a writer may hold it (`serve --sync`, a build) — retry; see `hook.log`")]
    try:
        ov = graph.memory_overview() if project.memory_enabled and graph.has_memory() else None
    except Exception:
        ov = None
    finally:
        graph.close()
    if ov is None:
        return [Check("graph", "ok", "readable")]
    return [Check("graph", "ok", f"readable · {ov.get('assertions', 0):,} current fact(s), "
                                 f"{ov.get('sessions', 0):,} session(s), {ov.get('themes', 0)} theme(s)")]


def check_writes(project, probes: Probes) -> list:
    from .graph.journal import journal_path, read_journal, read_staged, staged_path
    recs = read_journal(journal_path(project.graph_path))
    out = []
    if recs:
        oldest = min(float(r.get("t") or probes.now) for r in recs)
        age = probes.now - oldest
        level = "warn" if age > QUEUE_STALE_S else "ok"
        out.append(Check("journal", level, f"{len(recs)} write(s) queued, the oldest {_age(age)} old",
                         "`owiki sleep` folds them" if level == "warn" else ""))
    else:
        out.append(Check("journal", "ok", "nothing queued"))
    staged = read_staged(staged_path(project.graph_path))
    if staged:
        out.append(Check("approvals", "info", f"{len(staged)} agent write(s) awaiting approval",
                         "`owiki memory pending`"))
    return out


def check_schedule(project, probes: Probes) -> list:
    from . import runlog
    from .schedule import TASKS
    out = []
    sched = probes.scheduler(project.name, project.root)
    for task, spec in sorted(TASKS.items()):
        try:
            info = sched.status(task)
        except Exception as exc:
            out.append(Check("schedule", "warn", f"{task}: the scheduler can't tell ({exc})"))
            continue
        if not info.get("installed"):
            out.append(Check("schedule", "warn", f"{task} not scheduled", f"`owiki schedule install --task {task}`"))
            continue
        if not info.get("interpreter_ok"):
            out.append(Check("schedule", "fail", f"{task}: its interpreter no longer exists ({info.get('command')})",
                             "`owiki schedule install` again"))
            continue
        runs = [r for r in runlog.read_runs(project.state_dir, name=" ".join(spec["args"]))
                if r.get("trigger") == "schedule"]
        when = info.get("at") or info.get("interval") or "?"
        if runs:
            last = runs[-1]
            age = probes.now - last["t"]
            if last.get("outcome", "ok") != "ok":
                out.append(Check("schedule", "warn", f"{task} (at {when}): the last run {_age(age)} ago ended "
                                                     f"{last['outcome']}", "see `.openwiki/schedule.log`"))
            elif age > SLEEP_STALE_S:
                out.append(Check("schedule", "warn", f"{task} (at {when}): last ran {_age(age)} ago",
                                 "is the machine on, and the user logged on, at that time?"))
            else:
                out.append(Check("schedule", "ok", f"{task} at {when} · last run {_age(age)} ago: ok"))
        else:
            out.append(Check("schedule", "ok", f"{task} at {when} · not run yet"))
    return out


def check_backups(project, probes: Probes) -> list:
    from .memory_export import backup_time, newest_backup
    if project.memory_backups <= 0:
        return [Check("backups", "info", "turned off ([memory] backups = 0)")]
    newest = newest_backup(project.backups_dir)
    if newest is None:
        return [Check("backups", "warn", "none yet", "the next `owiki sleep` writes one (or `owiki memory export "
                                                     "--full`)")]
    age = probes.now - (backup_time(newest) or 0)
    size = newest.stat().st_size / 1e6
    if age > BACKUP_STALE_S:
        return [Check("backups", "warn", f"the newest is {_age(age)} old ({newest.name}, {size:.1f} MB)",
                      "is `sleep` running? `owiki schedule status`")]
    return [Check("backups", "ok", f"the newest {_age(age)} old ({newest.name}, {size:.1f} MB)")]


def check_runs(project, probes: Probes) -> list:
    from . import runlog
    runs = runlog.read_runs(project.state_dir, since=probes.now - RUN_WINDOW_S)
    if not runs:
        return [Check("runs", "info", "no runs recorded in the last 24 h")]
    failed = [r for r in runs if r.get("outcome", "ok") != "ok"]
    if failed:
        names = sorted({r["name"] for r in failed})
        return [Check("runs", "warn", f"{len(failed)} of {len(runs)} run(s) in the last 24 h failed: "
                                      f"{', '.join(names)} — last: {failed[-1].get('outcome')}",
                      "`owiki status`; the System tab")]
    return [Check("runs", "ok", f"{len(runs)} run(s) in the last 24 h, none failed")]


def check_hook_log(project) -> list:
    from .handoff import hook_log_problems
    problems, _ = hook_log_problems(Path(project.state_dir) / "hook.log", None, limit=3)
    if problems:
        return [Check("hook log", "warn", f"{len(problems)} problem line(s) recently — last: {problems[-1][:140]}",
                      f"{Path(project.state_dir) / 'hook.log'}")]
    return [Check("hook log", "ok", "no problems in its recent lines")]


def check_wiki(project, repos) -> list:
    from .handoff import wiki_freshness
    out = []
    for repo in repos:
        fresh = wiki_freshness(project, repo)
        if not fresh:
            continue
        behind = fresh.get("commits_since") or 0
        level = "warn" if behind > WIKI_BEHIND_WARN else "ok"
        out.append(Check("wiki", level, f"the index is {behind} commit(s) behind {repo}",
                         "`owiki build`" if level == "warn" else ""))
    return out


def run_checks(project, probes: Probes, repos=()) -> list:
    """Every check, in order; a check that raises is reported as a warning, never stops the rest."""
    from . import __version__
    steps = [lambda: check_install(__version__, probes), lambda: check_hooks(project, repos, __version__, probes),
             lambda: check_ollama(project, probes), lambda: check_graph(project, probes)]
    if project.memory_enabled:
        steps += [lambda: check_writes(project, probes), lambda: check_schedule(project, probes),
                  lambda: check_backups(project, probes)]
    steps += [lambda: check_runs(project, probes), lambda: check_hook_log(project), lambda: check_wiki(project, repos)]
    out = []
    for step in steps:
        try:
            out += step()
        except Exception as exc:          # a probe that broke is itself worth a line
            out.append(Check("doctor", "warn", f"a check failed to run: {type(exc).__name__}: {exc}"))
    return out


def candidate_repos(project, cwd=None) -> list:
    """The repositories whose hooks may feed this project: its code sources (directories) and the working
    directory when it is a git repository."""
    seen, out = set(), []
    for src in project.source_paths():
        p = Path(str(src))
        if "://" not in str(src) and p.is_dir():
            key = _norm(p)
            if key not in seen:
                seen.add(key)
                out.append(p)
    if cwd is not None and (Path(cwd) / ".git").exists() and _norm(cwd) not in seen:
        out.append(Path(cwd))
    return out


def render(project, checks: list) -> str:
    lines = [f"OpenWiki doctor — {project.name} ({project.root})"]
    width = max((len(c.area) for c in checks), default=8)
    for c in checks:
        lines.append(f"  {c.level:<5} {c.area:<{width}}  {c.message}")
        if c.hint and c.level in ("warn", "fail", "info"):
            lines.append(f"  {'':<5} {'':<{width}}  → {c.hint}")
    fails = sum(c.level == "fail" for c in checks)
    warns = sum(c.level == "warn" for c in checks)
    lines.append("All good." if not fails and not warns else
                 f"{fails} problem(s) to fix, {warns} warning(s).")
    return "\n".join(lines)


def to_json(project, checks: list) -> dict:
    return {"project": project.name, "root": str(project.root), "checks": [asdict(c) for c in checks],
            "fail": sum(c.level == "fail" for c in checks), "warn": sum(c.level == "warn" for c in checks)}
