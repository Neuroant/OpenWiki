"""Replay real prompts at their own time (Path C, phase M, M.1): ``owiki eval --replay TRANSCRIPT``.

For each user prompt of a Claude Code transcript, the memory the inject hook would have given it **as it stood then**:
``context_for`` with ``as_of`` = the prompt's time minus a capture lag (capture runs at compaction and at a session's
end, so the last hours' facts were not in memory yet) and ``now`` = the prompt's time (recency is measured from the
prompt, not from today), with the project's identity, fact count, budget and recall aids — the hook's own settings.
``--view known`` uses transaction time instead (``known_at``): what had been *recorded* by then, faithful for a memory
captured live, wrong for a backfilled one (its facts were all recorded on the backfill's day).

The replay runs on a **snapshot** of the graph — copied while a read-only connection keeps writers out — so a run and
its resumption see one memory, and the live graph is never held for long. Each prompt becomes one JSONL record: the
prompt, the injected text, every recalled fact with its rank, score parts and whether the text holds it, the themes.
Optionally a **blind judge** pass (``--judge``): per prompt, the facts the text holds, shuffled, and one deterministic
call — which of them would help with this request? — written to a separate file. The local judge rewards text that
resembles the request (path-b-memory §13.36): compare its verdicts between variants, don't read them as absolute.

Both passes are resumable and time-budgeted: records already in the output files are kept and skipped. The output is
private (real prompts): by default under the project's ``.openwiki/pathc/``, never in a repository.

This is how the replay judgments of path-b-memory §13.32 and §13.36 were made, from scratch scripts that were never
committed; C0's baseline (C0.5) and every Path C measurement on the live path build on it. No Kuzu import here.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

DEFAULT_LAG_H = 6.0              # capture lag: the memory as it stood this long before the prompt (§13.32, §13.36)
PROMPT_CHARS = 4000              # a record keeps this much of a prompt (pasted material can be long)
JUDGE_PROMPT_CHARS = 3000
YOUNG_S = 7 * 86400              # "younger than a week" in the summary (§13.32's maturation question)
RANK_BUCKETS = ((1, 4), (5, 8), (9, 12), (13, 16), (17, 10_000))

JUDGE_SYSTEM = (
    "You judge which remembered facts would help an AI coding assistant with a user's request. You see the request "
    "and a numbered list of facts from the assistant's memory of earlier work. A fact helps when it bears on this "
    "request — it tells the assistant something it needs, or should take into account, to do this task well at that "
    "time. A fact that is merely on the same topic, restates the obvious, or is out of date does not help. Answer "
    "with the numbers of the helpful facts as a JSON array, e.g. [2, 5], or [] if none helps. No other text.")


@dataclass
class Prompt:
    session: str
    n: int                       # the turn's position in its session (all speakers)
    t: int                       # epoch seconds
    text: str
    first: bool = False          # the session's first user prompt (a bare "ok" there keeps its memory)

    @property
    def key(self) -> str:
        return f"{self.session}#{self.n}"


def _epoch(ts: str) -> Optional[int]:
    try:
        return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())
    except (TypeError, ValueError):
        return None


def iso(t: Optional[float]) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)) if t is not None else ""


def load_prompts(paths: Iterable, since: Optional[int] = None, until: Optional[int] = None) -> list:
    """The user prompts of the transcripts (Claude Code JSONL, as capture sees them — host blocks stripped), with
    their times, oldest first; prompts without a time are left out."""
    from .sessions import load_turns
    out = []
    for path in paths:
        seen = set()
        for turn in load_turns(path):
            if turn.speaker != "User" or not turn.text.strip():
                continue
            t = _epoch(turn.ts)
            if t is None:
                continue
            first = turn.session not in seen
            seen.add(turn.session)
            if (since is not None and t < since) or (until is not None and t > until):
                continue
            out.append(Prompt(turn.session, turn.n, t, turn.text, first))
    out.sort(key=lambda p: (p.t, p.session, p.n))
    return out


def evenly(items: list, limit: Optional[int]) -> list:
    """At most ``limit`` items, evenly spaced over the list (a time-ordered sample that covers the whole range)."""
    if not limit or limit >= len(items):
        return list(items)
    step = len(items) / float(limit)
    return [items[int(i * step)] for i in range(limit)]


def harness_kind(text: str) -> Optional[str]:
    """A user turn the host wrote, not the person — no inject hook ran for it."""
    return "task notification" if text.lstrip().startswith("<task-notification") else None


# -- the snapshot --------------------------------------------------------------------------------------------------


def snapshot_graph(graph_path, dest, open_reader: Callable) -> Path:
    """Copy the graph (Kuzu's file and its ``.wal``) to ``dest`` while a read-only connection holds it — a writer
    can't change it mid-copy. ``dest`` is the new graph's path; returns it."""
    src, dest = Path(graph_path), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    reader = open_reader(src)
    if reader is None:
        raise RuntimeError(f"can't open {src} for reading (a writer holds it?) — retry in a moment")
    try:
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
            for side in src.parent.glob(src.name + ".*"):        # graph.wal and friends
                if side.is_file() and not side.name.endswith((".jsonl", ".lock")):
                    shutil.copy2(side, dest.with_name(dest.name + side.name[len(src.name):]))
    finally:
        reader.close()
    return dest


# -- replaying -----------------------------------------------------------------------------------------------------


@dataclass
class Settings:
    """The inject hook's settings for a project (``cli.inject_context``)."""
    identity: str = ""
    k: int = 16
    budget: int = 3000
    lexical: float = 0.2
    temporal: float = 0.1


def _fact(hit: dict, rank: int, shown: set) -> dict:
    from .graph.memory import fact_line
    try:
        line = fact_line(hit)
    except Exception:
        line = f"- {hit.get('subject')} {hit.get('predicate')} {hit.get('object')}"
    out = {"id": hit.get("id"), "rank": rank, "line": line, "shown": hit.get("id") in shown,
           "score": hit.get("score"), "valid_from": hit.get("valid_from"), "source": hit.get("source")}
    parts = hit.get("parts")
    if isinstance(parts, dict):
        out["parts"] = parts
    return out


def replay_prompt(graph, embedder, prompt: Prompt, settings: Settings, lag_s: float, view: str = "valid",
                  skip: Optional[Callable] = None, probes: Optional[Callable] = None) -> dict:
    """One prompt replayed at its own time → its record."""
    cut = int(prompt.t - lag_s)
    rec = {"session": prompt.session, "n": prompt.n, "t": prompt.t, "time": iso(prompt.t), "view": view,
           "cut": cut, "cut_time": iso(cut), "prompt": prompt.text[:PROMPT_CHARS],
           "prompt_chars": len(prompt.text), "skipped": None}
    why = harness_kind(prompt.text) or (skip(prompt) if skip else None)
    if why:
        rec["skipped"] = why
        return rec
    report: dict = {}
    when = {"as_of": cut} if view == "valid" else {"known_at": cut}
    text = graph.context_for(prompt.text, embedder, identity=settings.identity, k=settings.k,
                             max_chars=settings.budget, lexical=settings.lexical, temporal=settings.temporal,
                             probes=probes(prompt.text) if probes else None, now=prompt.t, report=report, **when)
    shown = set(report.get("facts") or ())
    labels = report.get("theme_labels") or {}
    rec.update({"context": text, "chars": len(text),
                "facts": [_fact(h, i + 1, shown) for i, h in enumerate(report.get("recalled") or [])],
                "themes": [{"id": tid, "label": labels.get(tid)} for tid in report.get("themes") or []],
                "identity": bool(report.get("identity"))})
    return rec


def read_jsonl(path) -> list:
    out = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _append(path, rec: dict) -> None:
    with Path(path).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def run_replay(graph, embedder, prompts: list, settings: Settings, out, lag_s: float, view: str = "valid",
               skip: Optional[Callable] = None, probes: Optional[Callable] = None,
               time_budget: Optional[float] = None, progress: Optional[Callable] = None) -> dict:
    """Replay ``prompts`` into the JSONL ``out``, skipping those already in it; stop cleanly after ``time_budget``
    seconds. → ``{"done", "skipped_existing", "left"}``."""
    have = {f"{r.get('session')}#{r.get('n')}" for r in read_jsonl(out)}
    todo = [p for p in prompts if p.key not in have]
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    start, done = time.monotonic(), 0
    if hasattr(embedder, "warm"):                         # embed the prompts in one go — no model swaps between them
        embedder.warm(queries=[p.text for p in todo if not harness_kind(p.text) and not (skip and skip(p))])
    for p in todo:
        if time_budget is not None and time.monotonic() - start > time_budget:
            break
        _append(out, replay_prompt(graph, embedder, p, settings, lag_s, view, skip, probes))
        done += 1
        if progress:
            progress(done, len(todo))
    return {"done": done, "skipped_existing": len(prompts) - len(todo), "left": len(todo) - done}


# -- the blind judge ---------------------------------------------------------------------------------------------


def judge_order(rec: dict) -> list:
    """The facts the injected text holds, in a shuffled order fixed by the prompt (blind to rank and score)."""
    facts = [f for f in rec.get("facts") or [] if f.get("shown")]
    seed = int(hashlib.sha1(f"{rec.get('session')}#{rec.get('n')}".encode("utf-8")).hexdigest()[:8], 16)
    random.Random(seed).shuffle(facts)
    return facts


def _fact_text(line: str) -> str:
    """A fact as the judge sees it: the statement, without the provenance in brackets."""
    return re.sub(r"\s{2}\(.*\)\s*$", "", str(line or "")).lstrip("- ").strip()


def judge_messages(rec: dict, facts: list) -> list:
    listed = "\n".join(f"{i}. {_fact_text(f['line'])}" for i, f in enumerate(facts, 1))
    user = (f"Request (on {rec.get('time', '')[:10]}):\n{str(rec.get('prompt', ''))[:JUDGE_PROMPT_CHARS]}\n\n"
            f"Facts:\n{listed}")
    return [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}]


def parse_numbers(text: str, n: int) -> list:
    """The fact numbers in a judge's answer (the first JSON array, else bare numbers), deduplicated, in range."""
    text = re.sub(r"<think>.*?</think>", "", str(text or ""), flags=re.S)
    m = re.search(r"\[[^\]]*\]", text)
    raw = re.findall(r"\d+", m.group(0) if m else text)
    out = []
    for x in raw:
        i = int(x)
        if 1 <= i <= n and i not in out:
            out.append(i)
    return out


def run_judge(records: list, chat, out, time_budget: Optional[float] = None,
              progress: Optional[Callable] = None) -> dict:
    """One judge call per replayed prompt with facts in its text; verdicts appended to ``out`` (resumable)."""
    have = {f"{r.get('session')}#{r.get('n')}" for r in read_jsonl(out)}
    todo = [r for r in records if not r.get("skipped") and any(f.get("shown") for f in r.get("facts") or [])
            and f"{r.get('session')}#{r.get('n')}" not in have]
    start, done = time.monotonic(), 0
    for rec in todo:
        if time_budget is not None and time.monotonic() - start > time_budget:
            break
        facts = judge_order(rec)
        try:
            answer = chat.chat(judge_messages(rec, facts))
        except Exception as exc:                          # one failed call costs one prompt, not the run
            answer, error = "", str(exc)[:200]
        else:
            error = None
        picked = parse_numbers(answer, len(facts))
        verdict = {"session": rec.get("session"), "n": rec.get("n"), "order": [f["id"] for f in facts],
                   "helpful": [facts[i - 1]["id"] for i in picked], "model": getattr(chat, "name", "")}
        if error:
            verdict["error"] = error
        _append(out, verdict)
        done += 1
        if progress:
            progress(done, len(todo))
    return {"done": done, "left": len(todo) - done}


# -- the summary ---------------------------------------------------------------------------------------------------


def summarize(records: list, verdicts: Optional[list] = None) -> dict:
    """What the replay found: prompts replayed and skipped (by reason), facts and characters per prompt, the share of
    injected facts younger than a week at the prompt — and, with verdicts, the helpful rate overall and by rank."""
    replayed = [r for r in records if not r.get("skipped")]
    skipped: dict = {}
    for r in records:
        if r.get("skipped"):
            skipped[r["skipped"]] = skipped.get(r["skipped"], 0) + 1
    shown = [f for r in replayed for f in r.get("facts") or [] if f.get("shown")]
    young = sum(1 for r in replayed for f in r.get("facts") or []
                if f.get("shown") and isinstance(f.get("valid_from"), (int, float)) and r["t"] - f["valid_from"] < YOUNG_S)
    out = {"prompts": len(records), "replayed": len(replayed), "skipped": skipped,
           "facts_per_prompt": round(len(shown) / len(replayed), 2) if replayed else 0.0,
           "chars_per_prompt": round(sum(r.get("chars", 0) for r in replayed) / len(replayed)) if replayed else 0,
           "young_share": round(young / len(shown), 3) if shown else 0.0,
           "empty": sum(1 for r in replayed if not r.get("chars"))}
    if verdicts:
        by_key = {f"{v.get('session')}#{v.get('n')}": v for v in verdicts}
        judged = helpful = prompts_helped = 0
        buckets = {f"{lo}-{hi}" if hi < 10_000 else f"{lo}+": [0, 0] for lo, hi in RANK_BUCKETS}
        for r in replayed:
            v = by_key.get(f"{r.get('session')}#{r.get('n')}")
            if v is None or v.get("error"):
                continue
            good = set(v.get("helpful") or ())
            prompts_helped += bool(good)
            for f in r.get("facts") or []:
                if not f.get("shown"):
                    continue
                judged += 1
                hit = f["id"] in good
                helpful += hit
                for lo, hi in RANK_BUCKETS:
                    if lo <= f["rank"] <= hi:
                        b = buckets[f"{lo}-{hi}" if hi < 10_000 else f"{lo}+"]
                        b[0] += 1
                        b[1] += hit
        out["judge"] = {"prompts": sum(1 for r in replayed if f"{r.get('session')}#{r.get('n')}" in by_key),
                        "facts": judged, "helpful_rate": round(helpful / judged, 3) if judged else 0.0,
                        "prompts_helped": prompts_helped,
                        "by_rank": {k: {"facts": n, "helpful_rate": round(h / n, 3) if n else 0.0}
                                    for k, (n, h) in buckets.items() if n}}
    return out


def format_summary(s: dict) -> str:
    skipped = ", ".join(f"{n} {why}" for why, n in sorted(s["skipped"].items())) or "none"
    lines = [f"Prompts: {s['prompts']} — replayed {s['replayed']}, skipped {skipped}",
             f"Injected per prompt: {s['facts_per_prompt']} fact(s), {s['chars_per_prompt']} characters "
             f"({s['empty']} prompt(s) got nothing)",
             f"Injected facts younger than a week at the prompt: {s['young_share']:.1%}"]
    j = s.get("judge")
    if j:
        lines.append(f"Judge: {j['prompts']} prompt(s), {j['facts']} fact(s) — helpful {j['helpful_rate']:.1%}; "
                     f"{j['prompts_helped']} prompt(s) got at least one helpful fact")
        lines.append("  by rank: " + " · ".join(f"{k}: {v['helpful_rate']:.1%} of {v['facts']}"
                                                for k, v in j["by_rank"].items()))
    return "\n".join(lines)


# -- hand labels (M.3) ---------------------------------------------------------------------------------------------
# Label files sit in the project, never in the repository — they quote real facts: `<project>/.openwiki/pathc/`. One
# JSON object per line: `id` (an assertion id), `label`, and from C0.2 on `route` (how the fact could be checked) and
# `binding` (the sensor binding that would govern it); `note`; any other key (`fact`, `set`, `closed_at`, …) is kept.

LABELS = ("stale", "current", "past", "unclear")
ROUTES = ("sensor", "law", "person", "deadline", "none")      # C0.2's rubric (WM §18.1)


def pathc_dir(project) -> Path:
    """Where a project keeps its Path C measurement files (private)."""
    return Path(project.state_dir) / "pathc"


def label_problems(row: dict) -> list:
    """What's wrong with one label record (empty when nothing is)."""
    out = []
    if not isinstance(row, dict):
        return ["not an object"]
    if not row.get("id"):
        out.append("no id")
    if row.get("label") not in LABELS:
        out.append(f"label {row.get('label')!r} is not one of {', '.join(LABELS)}")
    if row.get("route") is not None and row.get("route") not in ROUTES:
        out.append(f"route {row.get('route')!r} is not one of {', '.join(ROUTES)}")
    return out


def read_labels(path, strict: bool = False) -> list:
    """The label records of a file, in order; a line that doesn't parse or fit is skipped (``strict``: raises)."""
    out = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        if strict:
            raise
        return out
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        problems = label_problems(row) if row is not None else ["not JSON"]
        if problems:
            if strict:
                raise ValueError(f"{path}:{i}: {'; '.join(problems)}")
            continue
        out.append({"route": None, "binding": None, "note": "", **row})
    return out


def write_labels(path, rows: Iterable) -> int:
    """Write label records (every one checked first — a bad one is a ``ValueError`` and nothing is written), through a
    temporary file, so a reader never sees half a file. Returns how many."""
    rows = [{"id": r.get("id"), "label": r.get("label"), "route": r.get("route"), "binding": r.get("binding"),
             "note": r.get("note") or "", **{k: v for k, v in r.items()
                                            if k not in ("id", "label", "route", "binding", "note")}}
            for r in rows]
    for i, r in enumerate(rows, 1):
        problems = label_problems(r)
        if problems:
            raise ValueError(f"label {i}: {'; '.join(problems)}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(path)
    return len(rows)
