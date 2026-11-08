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

import dataclasses
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
        accepted = read_accepted(project.state_dir)
        active = active_bindings(load_world(project), accepted) if accepted else []
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
                      "changed_at": r.changed_at, "commit": r.commit, "ms": r.ms, "detail": r.detail,
                      "source": r.source}
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


OVERLAY_BUDGET_MS = 200        # the inject hook reads due sensors for at most this long per prompt (§6.3)


def _text(f: dict) -> str:
    return f"{f.get('subject')} | {f.get('predicate')} | {f.get('object')}"


def _ago(seconds: int) -> str:
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{round(seconds / 60)} min ago"
    return f"{round(seconds / 3600)} h ago" if seconds < 129600 else f"{round(seconds / 86400)} days ago"


class Overlay:
    """C1.6 — the read-time overlay (``docs/path-c-world-model.md`` §6.5). Readers can't write (ADR-38), and an
    observation the inject hook makes must not wait for a fold: ``GraphStore.context_for(…, overlay=)`` hands the
    recalled facts to this callable, which — for the accepted bindings that govern one of them — reads the fast
    sensors whose cached reading is older than ``every`` (within ``budget_ms`` in all), takes the cached reading for
    the rest, and replaces each recalled fact that claims another value by the observation: "OpenWiki current release
    is v0.124.0 (observed just now; git tags)". A recalled observation that is out of date is replaced the same way;
    an agreeing belief stays. A replacement gets an id of its own (``obs:<binding>:<value>``), so the hook's "each
    fact once per stretch" never hides a correction. A fresh value the cache didn't hold is queued as an ``observe``
    op for the next writer. Fail-soft: any error returns the facts unchanged. ``report`` = what it did."""

    def __init__(self, world, accepted: dict, state_dir, journal=None, host=None,
                 budget_ms: int = OVERLAY_BUDGET_MS, now: Optional[int] = None, reader=None) -> None:
        from . import sensors
        self.world = world
        self.active = active_bindings(world, accepted)
        self.authority = Authority(self.active)
        self.state_dir = state_dir
        self.journal = journal
        self.host = host
        self.budget = budget_ms / 1000.0
        self.now = now
        self.reader = reader or sensors.read
        self.report = {"read": [], "replaced": [], "queued": 0, "ms": 0}

    def __call__(self, hits: list) -> list:
        t0 = time.perf_counter()
        try:
            return self._apply(list(hits))
        except Exception:
            return hits
        finally:
            self.report["ms"] = round((time.perf_counter() - t0) * 1000)

    def _read(self, b: Binding, light: bool = False):
        env = self.world.environment(b.environment)
        kw = {"light": True} if light else {}
        return self.reader(b.id, b.type, b.params, root=env.root if env else None, host=self.host, **kw)

    def _owner(self, h: dict) -> Optional[str]:
        sid = str(h.get("session_id") or "")
        if h.get("source") == "observed" and sid.startswith("sensor:") and sid[7:] in self.authority.bindings:
            return sid[7:]
        return None

    def _values(self, wanted, now: int) -> dict:
        """``{binding id: (value, source, read_at)}`` — read now where due and fast, within the budget; else cached."""
        cache = read_cache(self.state_dir)
        out, fresh = {}, {}
        start = time.perf_counter()
        for bid in wanted:
            b = self.authority.bindings[bid]
            entry = cache.get(bid) or {}
            due_now = not entry or (b.every > 0 and now - int(entry.get("time") or 0) >= b.every)
            if due_now and not b.slow and time.perf_counter() - start < self.budget:
                r = self._read(b, light=True)
                self.report["read"].append(bid)
                if r.quality == "complete":
                    fresh[bid] = r
                    out[bid] = (r.value, r.source, r.time)
                    continue
            if entry.get("quality") == "complete":
                out[bid] = (entry.get("value"), entry.get("source") or "", int(entry.get("time") or now))
        if fresh:
            changed = []
            for bid, r in list(fresh.items()):
                entry = cache.get(bid) or {}
                if entry.get("rendered") == render(r.value):       # unchanged: the cached provenance still holds
                    fresh[bid] = dataclasses.replace(r, changed_at=entry.get("changed_at"),
                                                     commit=entry.get("commit") or "")
                    continue
                full = self._read(self.authority.bindings[bid])   # a change: when and in which commit, for the writer
                if full.quality == "complete" and render(full.value) == render(r.value):
                    fresh[bid] = r = full
                changed.append(observation(self.authority.bindings[bid], r))
            update_cache(self.state_dir, fresh)
            if changed and self.journal is not None:
                from ..graph.journal import append_observe
                self.report["queued"] = append_observe(self.journal, changed, now=now)
        return out

    def _apply(self, hits: list) -> list:
        if not self.active or not hits:
            return hits
        wanted = {}
        for h in hits:
            owner = self._owner(h)
            for bid in ([owner] if owner else self.authority.governing(_text(h))):
                wanted[bid] = True
        if not wanted:
            return hits
        now = int(time.time() if self.now is None else self.now)
        values = self._values(list(wanted), now)
        out, shown = [], set()
        for h in hits:
            owner = self._owner(h)
            bid = owner or next((b for b in self.authority.governing(_text(h)) if b in values), None)
            if bid is None or bid not in values:
                out.append(h)
                continue
            value, source, read_at = values[bid]
            b = self.authority.bindings[bid]
            subject, predicate, obj = b.fact(value)
            stale = (owner is not None and _normalize_text(h.get("object")) != _normalize_text(obj)) or \
                (owner is None and self.authority.verdict(bid, h, value) == "differs")
            if not stale:
                if owner is not None:
                    shown.add(bid)
                out.append(h)
                continue
            if bid in shown:                               # the observation is in already: the belief goes
                self.report["replaced"].append({"binding": bid, "was": _text(h), "now": None})
                continue
            shown.add(bid)
            fact = dict(h, id=f"obs:{bid}:{_normalize_text(obj)}", subject=subject, predicate=predicate, object=obj,
                        source="observed", session_id=f"sensor:{bid}", valid_to=None,
                        overlay=f"{_ago(now - int(read_at))}; {source}" if source else _ago(now - int(read_at)))
            self.report["replaced"].append({"binding": bid, "was": _text(h), "now": _text(fact)})
            out.append(fact)
        return out


def _normalize_text(value) -> str:
    return " ".join(str(value or "").lower().split())


def world_state(project, graph=None) -> Optional[dict]:
    """C1.13 — what the session handoff shows of the world model: each binding's latest reading (the cache), whether
    a person accepted it, and how many current facts differ from what the sensors read (when ``graph`` is given).
    ``None`` when the world model is off. Never raises."""
    try:
        from .bindings import compare, load_world
        if project is None or not project.world_enabled:
            return None
        world = load_world(project)
        accepted = read_accepted(project.state_dir)
        cache = read_cache(project.state_dir)
        rows = []
        for b in world.bindings:
            entry = cache.get(b.id) or {}
            rows.append({"id": b.id, "value": entry.get("rendered"), "read_at": entry.get("time"),
                         "quality": entry.get("quality"), "acceptance": acceptance(b, accepted)})
        differs = None
        if graph is not None:
            facts = [f for f in graph.list_assertions(limit=10 ** 9) if f.get("status") == "current"
                     and f.get("source") != "observed"]
            differs = 0
            for b in world.bindings:
                entry = cache.get(b.id) or {}
                if entry.get("quality") != "complete" or not b.governs:
                    continue
                differs += sum(1 for f in facts if b.governs_text(_text(f))
                               and compare(b, f, entry.get("value")) == "differs")
        return {"bindings": rows, "differs": differs, "problems": list(world.problems)}
    except Exception:
        return None


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
