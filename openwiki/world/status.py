"""``owiki world status`` — the world model's state for a person (Path C, C1.7): each binding's reading, the fact it
would write, and the current remembered facts it governs, split into those that agree with the reading, differ from
it and can't be compared. The differing ones are the dry run of §6.2: what the binding would close once a person has
accepted it (v0.124). Read-only and pure — readings and facts are passed in."""

from __future__ import annotations

from typing import Optional

from ..graph.temporal import format_date
from .bindings import World, compare
from .sensors import render

VERDICTS = ("agrees", "differs", "unclear", "unknown")
MARKS = {"agrees": "=", "differs": "≠", "unclear": "?", "unknown": "·"}


def _fact_row(fact: dict, verdict: str) -> dict:
    return {"id": fact.get("id"), "subject": fact.get("subject"), "predicate": fact.get("predicate"),
            "object": fact.get("object"), "valid_from": fact.get("valid_from"), "source": fact.get("source"),
            "session_id": fact.get("session_id"), "verdict": verdict}


def world_report(world: World, readings: dict, facts: Optional[list], only=None,
                 accepted: Optional[dict] = None) -> dict:
    """The status as data. ``readings`` = :class:`~openwiki.world.sensors.Reading` by binding id; ``facts`` = the
    current remembered facts (dicts with id, subject, predicate, object, valid_from, source, session_id), or ``None``
    when the memory couldn't be read; ``only`` = the binding ids to report (default: all); ``accepted`` = the
    acceptance records (``world.observe.read_accepted``) — whether each binding may write."""
    from .observe import acceptance
    sensors = []
    totals = {"governed": 0, **{v: 0 for v in VERDICTS}}
    for b in world.bindings:
        if only is not None and b.id not in only:
            continue
        reading = readings.get(b.id)
        ok = reading is not None and reading.ok
        governed = None
        if facts is not None and b.governs:
            rows = []
            for f in facts:
                if b.governs_text(f"{f.get('subject')} | {f.get('predicate')} | {f.get('object')}"):
                    rows.append(_fact_row(f, compare(b, f, reading.value) if ok else "unknown"))
            rows.sort(key=lambda r: (VERDICTS.index(r["verdict"]), -(r["valid_from"] or 0)))
            governed = {"facts": rows, **{v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}}
            totals["governed"] += len(rows)
            for v in VERDICTS:
                totals[v] += governed[v]
        fact = " | ".join(b.fact(reading.value)) if ok else None
        rec = (accepted or {}).get(b.id) or {}
        sensors.append({"id": b.id, "environment": b.environment, "type": b.type, "target": b.target(),
                        "every": b.every, "default": b.default, "slow": b.slow,
                        "acceptance": acceptance(b, accepted or {}), "accepted_at": rec.get("accepted_at"),
                        "reading": reading.to_dict() if reading is not None else None, "fact": fact,
                        # the observation's own wording must be governed, or v0.124 couldn't keep it current
                        "self_governed": b.governs_text(fact) if fact and b.governs else None,
                        "governed": governed})
    return {"enabled": world.enabled, "memory": world.memory, "problems": list(world.problems),
            "environments": [{"name": e.name, "kind": e.kind, "where": e.describe(), "subject": e.subject}
                             for e in world.environments],
            "sensors": sensors, "facts_read": facts is not None, "totals": totals}


def _every(seconds: int) -> str:
    if not seconds:
        return "slow passes"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % size == 0:
            return f"every {seconds // size}{unit}"
    return f"every {seconds}s"


def _line(row: dict) -> str:
    when = format_date(row["valid_from"]) if row.get("valid_from") else ""
    tail = ", ".join(x for x in (f"since {when}" if when else "", row.get("session_id") or "") if x)
    return f"{row['subject']} | {row['predicate']} | {row['object']}" + (f"   ({tail})" if tail else "")


def format_report(report: dict, project: str = "", show_all: bool = False) -> str:
    """The status as text — per environment its sensors (reading, fact, governed facts); differing facts listed,
    the others counted (``show_all`` lists them too)."""
    sensors = report["sensors"]
    state = "on" if report["enabled"] else "off — v0.123 only reads, so this is what it would observe"
    head = f"World model{' — ' + project if project else ''}   [world] {state}"
    lines = [head, f"{len(report['environments'])} environment(s), {len(sensors)} sensor(s)"]
    if report["problems"]:
        lines += ["", "Configuration problems:"] + [f"  ✗ {p}" for p in report["problems"]]
    w = max([14] + [len(s["id"]) + 2 for s in sensors])
    for env in report["environments"]:
        mine = [s for s in sensors if s["environment"] == env["name"]]
        lines += ["", f"{env['name']} — {env['kind']} {env['where']}"]
        if env["kind"] == "twin":
            lines.append("  (a twin's variables are read with the twin type — C2)")
        for s in mine:
            r = s["reading"] or {}
            tag = " ".join(x for x in ("default" if s["default"] else "", "slow" if s["slow"] else "") if x)
            if r.get("quality") == "failed":
                value = f"✗ {r.get('detail') or 'failed'}"
            else:
                value = render(r.get("value"))
                if r.get("quality") == "partial":
                    value += f"  (partial: {r.get('detail')})"
                elif r.get("detail"):
                    value += f"  ({r.get('detail')})"
            changed = f"changed {format_date(r['changed_at'])}" if r.get("changed_at") else ""
            ms = r.get("ms") or 0
            took = f"{ms / 1000:.1f} s" if ms >= 1000 else f"{ms} ms"
            lines.append(f"  {s['id']:<{w}}{s['type']:<15}{s['target']}")
            lines.append(f"  {'':<{w}}{value}" + "".join(f"   {x}" for x in (changed, took, _every(s["every"]), tag)
                                                         if x))
            if s["fact"]:
                lines.append(f"  {'':<{w}}→ {s['fact']}")
            if s.get("self_governed") is False:
                lines.append(f"  {'':<{w}}⚠ governs doesn't match this sensor's own fact — widen the pattern")
            state = s.get("acceptance")
            if state == "accepted":
                lines.append(f"  {'':<{w}}accepted {format_date(s.get('accepted_at'))} — observing writes it and "
                             f"closes the facts that differ")
            elif state == "changed":
                lines.append(f"  {'':<{w}}⚠ changed since it was accepted ({format_date(s.get('accepted_at'))}) — "
                             f"`owiki world accept {s['id']}` again")
            elif state == "not accepted" and s.get("governed") is not None:
                lines.append(f"  {'':<{w}}not accepted — after checking the facts below: `owiki world accept "
                             f"{s['id']}`")
            gov = s["governed"]
            if gov is None:
                continue
            n = len(gov["facts"])
            counts = " · ".join(f"{gov[v]} {v}" for v in VERDICTS if gov[v])
            lines.append(f"  {'':<{w}}governs {n} current fact(s)" + (f": {counts}" if n else ""))
            for row in gov["facts"]:
                if show_all or row["verdict"] == "differs":
                    lines.append(f"  {'':<{w + 2}}{MARKS[row['verdict']]} {_line(row)}")
    t = report["totals"]
    lines.append("")
    if not report["facts_read"]:
        lines.append("Memory not read (no graph, or Wiki mode) — governed facts not shown.")
    elif t["differs"]:
        lines.append(f"{t['differs']} current fact(s) differ from what the sensors read — the dry run: once a binding "
                     f"is accepted, `owiki world observe` closes them, kept as history.")
    else:
        lines.append("No current fact differs from what the sensors read.")
    return "\n".join(lines)
