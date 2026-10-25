"""The saved run log (track O, O.3): one JSON line per hook, worker, scheduled run and CLI write pass in
``<project>/.openwiki/runs.jsonl`` — its start, duration, outcome, model calls and tokens.

``metrics.COLLECTOR`` keeps these numbers only for the life of its process, so a hook, a detached capture worker or a
scheduled ``sleep`` lost them when it exited (only build stages saved theirs, in ``state.json``). The run log keeps
them: ``status``, ``doctor``, ``schedule status`` and the web UI's System tab read it, and Path C measures the inject
hook's latency from it (C1.10).

Pure stdlib, no Kuzu. Best-effort throughout — a log that can't be written never breaks the run it records. Lines are
appended with one ``write`` each, so processes writing at the same time don't interleave a line in practice; a reader
skips any line that doesn't parse. Past ``MAX_BYTES`` the file is rotated to ``runs.1.jsonl`` (two files are kept).
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

RUNS_FILE = "runs.jsonl"
ROTATED_FILE = "runs.1.jsonl"
MAX_BYTES = 2_000_000        # ~10,000 runs per file; the two files hold weeks of hooks
OUTCOME_CHARS = 300

_STACK: list = []            # the runs in progress in this process (innermost last), for ``note``


def runs_path(state_dir) -> Path:
    """The run log of a project's state directory (``<project>/.openwiki``)."""
    return Path(state_dir) / RUNS_FILE


def llm_spend(events) -> dict:
    """The model calls in a slice of ``metrics`` events: chat + embed calls, their tokens and their time —
    ``{}`` when there were none."""
    calls = prompt = evalt = 0
    chat_ms = embed_ms = 0.0
    for e in events:
        if e.kind not in ("chat", "embed"):
            continue
        calls += 1
        prompt += e.prompt_tokens or 0
        evalt += e.eval_tokens or 0
        if e.kind == "chat":
            chat_ms += e.duration_ms or 0.0
        else:
            embed_ms += e.duration_ms or 0.0
    if not calls:
        return {}
    return {"calls": calls, "prompt_tokens": prompt, "eval_tokens": evalt,
            "chat_ms": round(chat_ms, 1), "embed_ms": round(embed_ms, 1)}


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


class Run:
    """One run being recorded: started on creation, written by :meth:`finish`."""

    def __init__(self, name: str, state_dir, trigger: Optional[str] = None) -> None:
        from . import metrics
        self.name = name
        self.path = runs_path(state_dir)
        self.trigger = trigger
        self.start = time.time()
        self._t0 = time.perf_counter()
        self._seq0 = metrics.COLLECTOR.seq
        self.outcome = "ok"
        self.notes: dict = {}
        self.entry: Optional[dict] = None

    def note(self, **kv) -> None:
        """Add facts about the run (``chars=…``, ``skipped=…``); ``None`` values are left out."""
        self.notes.update({k: v for k, v in kv.items() if v is not None})

    def exit(self, code) -> None:
        """Set the outcome from a command's exit code (0 or ``None`` = ok)."""
        self.outcome = "ok" if code in (0, None) else f"exit {code}"

    def finish(self) -> Optional[dict]:
        """Write the run's line (once) and return it; ``None`` if it couldn't be written."""
        if self.entry is not None:
            return self.entry
        from . import metrics
        entry = {"name": self.name, "start": _iso(self.start), "t": round(self.start, 3),
                 "duration_s": round(time.perf_counter() - self._t0, 3), "outcome": self.outcome[:OUTCOME_CHARS],
                 "pid": os.getpid()}
        if self.trigger:
            entry["trigger"] = self.trigger
        spend = llm_spend(metrics.COLLECTOR.since(self._seq0))
        if spend:
            entry["llm"] = spend
        if self.notes:
            entry["notes"] = self.notes
        self.entry = entry
        return entry if append_run(self.path, entry) else None


@contextmanager
def run(name: str, state_dir, trigger: Optional[str] = None) -> Iterator[Run]:
    """Record the enclosed block as one run. An exception is recorded as the outcome (``error: Type: message``,
    ``interrupted``) and raised on; the line is written either way."""
    r = Run(name, state_dir, trigger)
    _STACK.append(r)
    try:
        yield r
    except KeyboardInterrupt:
        r.outcome = "interrupted"
        raise
    except SystemExit as exc:
        r.exit(exc.code)
        raise
    except BaseException as exc:
        r.outcome = f"error: {type(exc).__name__}: {exc}"
        raise
    finally:
        _STACK.pop()
        r.finish()


def note(**kv) -> None:
    """Add facts to the innermost run in progress (a no-op outside one)."""
    if _STACK:
        _STACK[-1].note(**kv)


def append_run(path, entry: dict) -> bool:
    """Append one run's line, rotating the file past ``MAX_BYTES``. ``False`` on any I/O error."""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.stat().st_size > MAX_BYTES:
                os.replace(path, path.with_name(ROTATED_FILE))
        except OSError:          # no file yet, or another process has it open (Windows) — rotate next time
            pass
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        return True
    except (OSError, TypeError, ValueError):
        return False


def read_runs(state_dir, since: Optional[float] = None, name: Optional[str] = None) -> list:
    """The recorded runs, oldest first — both files; ``since`` an epoch time, ``name`` an exact run name or a
    prefix ending in a space (``"hook "``)."""
    out = []
    for fname in (ROTATED_FILE, RUNS_FILE):
        try:
            text = (Path(state_dir) / fname).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or not isinstance(rec.get("t"), (int, float)):
                continue
            if since is not None and rec["t"] < since:
                continue
            if name is not None and not (rec.get("name") == name
                                         or (name.endswith(" ") and str(rec.get("name", "")).startswith(name))):
                continue
            out.append(rec)
    out.sort(key=lambda r: r["t"])
    return out


def _pct(sorted_vals: list, p: int) -> float:
    if not sorted_vals:
        return 0.0
    i = min(len(sorted_vals) - 1, int(round((p / 100.0) * (len(sorted_vals) - 1))))
    return sorted_vals[i]


def summarize(runs: list) -> list:
    """Per run name: how often, how many failed, the median and p95 duration, the model calls and tokens, and the
    last run — newest last run first."""
    groups: dict = {}
    for r in runs:
        groups.setdefault(str(r.get("name", "?")), []).append(r)
    out = []
    for name, rs in groups.items():
        ds = sorted(float(r.get("duration_s") or 0.0) for r in rs)
        last = max(rs, key=lambda r: r["t"])
        llm = [r.get("llm") or {} for r in rs]
        out.append({
            "name": name, "count": len(rs),
            "failures": sum(1 for r in rs if r.get("outcome", "ok") != "ok"),
            "p50_s": round(_pct(ds, 50), 3), "p95_s": round(_pct(ds, 95), 3),
            "calls": sum(x.get("calls", 0) for x in llm),
            "prompt_tokens": sum(x.get("prompt_tokens", 0) for x in llm),
            "eval_tokens": sum(x.get("eval_tokens", 0) for x in llm),
            "last": {"start": last.get("start"), "t": last["t"], "duration_s": last.get("duration_s"),
                     "outcome": last.get("outcome", "ok")},
        })
    out.sort(key=lambda s: s["last"]["t"], reverse=True)
    return out


def last_run(state_dir, name: str) -> Optional[dict]:
    """The most recent run of ``name`` (exact), or ``None``."""
    runs = read_runs(state_dir, name=name)
    return runs[-1] if runs else None


def format_age(seconds: float) -> str:
    """``42 s``, ``7 min``, ``3 h``, ``2 d`` — how long ago, roughly."""
    s = max(0.0, float(seconds))
    if s < 90:
        return f"{int(s)} s"
    if s < 90 * 60:
        return f"{int(round(s / 60))} min"
    if s < 36 * 3600:
        return f"{int(round(s / 3600))} h"
    return f"{int(round(s / 86400))} d"


def format_duration(seconds: float) -> str:
    """``120 ms``, ``4.2 s``, ``3.5 min``."""
    s = float(seconds or 0.0)
    if s < 1:
        return f"{int(round(s * 1000))} ms"
    if s < 120:
        return f"{s:.1f} s"
    return f"{s / 60:.1f} min"
