"""The observe pass — what the world model writes (Path C, C1.5, with C1.2's acceptance and C1.9's attribution;
``docs/path-c-world-model.md`` §2.3, §6.2 – §6.4).

* **Acceptance.** A binding closes nothing until a person has accepted it: ``owiki world status`` lists the facts it
  governs (the dry run), ``owiki world accept ID`` records the decision in ``.openwiki/world-accepted.json`` with a
  fingerprint of the binding — a changed pattern, fact or sensor needs a new acceptance. An unaccepted binding is
  read, never written.
* **The world cache** (``.openwiki/world-cache.json``): each binding's last reading — whether a sensor is due again
  (``every``) and what it read; the graph is written only when an observation or a governed belief changes (write on
  change, §2.3), and ``sleep`` syncs ``last_seen``.
* **Authority** — the accepted bindings as ``GraphStore.observe`` / ``remember`` ask them: which govern a fact's
  wording, and does a fact agree with what one observed.

Pure apart from those two JSON files; no Kuzu."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

from ..graph.temporal import format_date
from .bindings import Binding, compare, observed_claim
from .sensors import render

ACCEPTED_FILE = "world-accepted.json"
CACHE_FILE = "world-cache.json"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def jsonable(value):
    """A reading's value as JSON keeps it (tuples as lists)."""
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    return value


# -- acceptance (§6.2) -----------------------------------------------------------------------------

def binding_fingerprint(b: Binding) -> str:
    """What a person accepted: the sensor, its settings, the fact it writes and what it governs."""
    spec = [b.environment, b.type, jsonable(b.params), b.subject, b.predicate, b.object, b.governs, b.excludes,
            b.belief]
    return hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
                          ).hexdigest()[:12]


def read_accepted(state_dir) -> dict:
    """``{binding id: {"fingerprint", "accepted_at", "governed", "differs"}}``."""
    data = _read_json(Path(state_dir) / ACCEPTED_FILE)
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def acceptance(binding: Binding, accepted: dict) -> str:
    """``accepted`` / ``changed`` (accepted, then edited — accept it again) / ``not accepted``."""
    rec = accepted.get(binding.id)
    if not rec:
        return "not accepted"
    return "accepted" if rec.get("fingerprint") == binding_fingerprint(binding) else "changed"


def active_bindings(world, accepted: dict) -> list:
    """The bindings that may write: accepted, unchanged since."""
    return [b for b in world.bindings if acceptance(b, accepted) == "accepted"]


def accept(state_dir, bindings, counts: Optional[dict] = None, now: Optional[int] = None) -> list:
    """Record a person's acceptance of ``bindings`` (with the dry run they saw: ``counts[id]`` = ``{"governed",
    "differs"}``) → the accepted ids."""
    now = int(time.time() if now is None else now)
    data = read_accepted(state_dir)
    for b in bindings:
        seen = (counts or {}).get(b.id) or {}
        data[b.id] = {"fingerprint": binding_fingerprint(b), "accepted_at": now,
                      "governed": int(seen.get("governed") or 0), "differs": int(seen.get("differs") or 0)}
    _write_json(Path(state_dir) / ACCEPTED_FILE, data)
    return [b.id for b in bindings]


def revoke(state_dir, ids) -> list:
    data = read_accepted(state_dir)
    gone = [i for i in ids if data.pop(i, None) is not None]
    _write_json(Path(state_dir) / ACCEPTED_FILE, data)
    return gone


def governed_predicate(project):
    """``fact -> bool``: does an accepted binding govern this fact (C1.8 — the review list leaves it to the sensor)?
    ``None`` when the world model is off or nothing is accepted. Never raises."""
    try:
        from .bindings import load_world
        if project is None or not project.world_enabled:
            return None
        active = active_bindings(load_world(project), read_accepted(project.state_dir))
    except Exception:
        return None
    if not active:
        return None
    authority = Authority(active)
    return lambda f: bool(authority.governing(f"{f.get('subject')} | {f.get('predicate')} | {f.get('object')}"))


class Authority:
    """The accepted bindings as the graph store asks them (duck-typed in ``GraphStore``)."""

    def __init__(self, bindings) -> None:
        self.bindings = {b.id: b for b in bindings}

    def governing(self, text: str) -> list:
        """The bindings that govern a fact worded ``text`` (``"subject | predicate | object"``)."""
        return [bid for bid, b in self.bindings.items() if b.governs_text(text)]

    def verdict(self, binding_id: str, fact: dict, observed) -> str:
        """Does ``fact`` agree with what binding ``binding_id`` observed? ``observed`` = the reading's value, or an
        observation record the graph holds (its wording's claim is compared then)."""
        b = self.bindings[binding_id]
        if isinstance(observed, dict) and "object" in observed:
            observed = observed_claim(b, observed["object"])
        return compare(b, fact, observed)


# -- the world cache -----------------------------------------------------------------------------------

def read_cache(state_dir) -> dict:
    return _read_json(Path(state_dir) / CACHE_FILE)


def update_cache(state_dir, readings: dict) -> dict:
    """Keep each binding's latest reading (``readings`` = Reading by id) → the cache."""
    cache = read_cache(state_dir)
    for bid, r in readings.items():
        cache[bid] = {"value": jsonable(r.value), "rendered": render(r.value), "time": r.time, "quality": r.quality,
                      "changed_at": r.changed_at, "commit": r.commit, "ms": r.ms, "detail": r.detail}
    _write_json(Path(state_dir) / CACHE_FILE, cache)
    return cache


def due(binding: Binding, cache: dict, now: int, slow: bool = False) -> bool:
    """Is ``binding``'s reading due? A sensor with ``every = 0`` only on slow passes; else once its period has
    passed since the cached reading."""
    if binding.every <= 0:
        return slow
    last = (cache.get(binding.id) or {}).get("time")
    return last is None or now - int(last) >= binding.every


def observation(binding: Binding, reading) -> dict:
    """A complete reading as the observation ``GraphStore.observe`` writes (self-contained, journal-ready)."""
    subject, predicate, obj = binding.fact(reading.value)
    return {"binding": binding.id, "subject": subject, "predicate": predicate, "object": obj,
            "value": jsonable(reading.value), "valid_from": reading.changed_at, "read_at": reading.time,
            "source_ref": reading.source, "commit": reading.commit}


def changes(planned: dict) -> int:
    """How many writes a planned observe pass needs: new observations and beliefs to close."""
    return int(planned.get("added") or 0) + int(planned.get("closed") or 0)


def _brief(f: dict) -> str:
    when = format_date(f.get("valid_from")) if f.get("valid_from") else ""
    tail = ", ".join(x for x in (f"since {when}" if when else "", f.get("session_id") or "") if x)
    return f"{f.get('subject')} | {f.get('predicate')} | {f.get('object')}" + (f"  ({tail})" if tail else "")


def format_observe(result: dict, observations: list, written: str, project: str = "") -> str:
    """The observe pass as text: per binding what it read, whether it changed, the commit behind a change (C1.9 —
    "outside any commit" when the value isn't committed yet) and the beliefs it closed or confirmed."""
    by = {o["binding"]: o for o in observations}
    lines = [f"World observe{' — ' + project if project else ''}   {len(observations)} sensor(s) with a reading"]
    for e in result.get("observations") or []:
        o = by.get(e["binding"], {})
        if e["action"] == "added":
            was = f" (was {', '.join(e['replaced'])})" if e.get("replaced") else " (first observation)"
            commit = o.get("commit") or ""
            why = ("outside any commit (not committed yet)" if commit == "uncommitted"
                   else f"changed in {commit}" if commit else "")
            lines.append(f"  {e['binding']:<16}{e['object']}{was}" + (f"   {why}" if why else ""))
        else:
            lines.append(f"  {e['binding']:<16}{e['object']}   unchanged")
        for f in e.get("closed") or []:
            flag = "   a discrepancy: believed after the change" if f in (e.get("discrepancies") or []) else ""
            lines.append(f"  {'':<16}≠ closed {_brief(f)}{flag}")
        if e.get("agreed"):
            lines.append(f"  {'':<16}= {len(e['agreed'])} belief(s) agree")
    lines.append(written)
    return "\n".join(lines)
