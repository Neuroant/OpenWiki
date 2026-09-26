"""Bi-temporal assertions (Path B+ / B7) — the pure valid-time core.

A remembered fact has **two independent time axes** (the Snodgrass / Graphiti model):

* **valid time** — ``valid_from`` / ``valid_to``: when the fact was true *in the world*
  (``valid_to`` ``None`` = still true). The fact's *semantic* content.
* **transaction time** — ``created_at`` / ``expired_at``: when OpenWiki *recorded* the
  record and when it stopped *believing* it (``expired_at`` ``None`` = still believed).
  The fact's *episodic* trace (which session taught it, when).

Before B7 one ``created_at`` stood in for both, and supersession followed *processing
order* — so backfilling an older transcript after a newer one made the stale fact
current. Here facts are ordered by **valid time**: :func:`plan_merge` decides how a new
fact slots into the history of its ``(subject, predicate)`` — re-affirm, extend back,
close the rival's interval (the world changed), or retract it (same instant / an explicit
correction: we were wrong). Nothing is deleted; every past belief stays reconstructable.

Pure + dependency-free (no Kuzu), mirroring :mod:`~openwiki.graph.decay`; the Kuzu I/O
lives in :class:`~openwiki.graph.store.GraphStore`. Record dicts carry ``id``, ``okey``
(normalized object), ``valid_from``, ``valid_to``, ``expired_at``, ``created_at``,
``cardinality``. Times are epoch seconds (UTC).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable, Optional

_DATE = re.compile(r"^\s*(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?"
                   r"(?:[T ](\d{1,2}):(\d{2})(?::(\d{2}))?)?\s*(?:Z|[+-]00:?00)?\s*$")
_SESSION_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_YEAR_MIN, _YEAR_MAX = 1900, 2200     # outside this a "date" is noise, not a fact's time

MANY = "many"
ONE = "one"
MAX_RIVAL_CHECKS = 6          # coexistence checks per new fact against overlapping rivals
_MANY_WORDS = {"many", "multi", "multiple", "several", "set", "list"}


def _epoch(y: int, mo: int = 1, d: int = 1, h: int = 0, mi: int = 0, s: int = 0) -> Optional[int]:
    if not (_YEAR_MIN <= y <= _YEAR_MAX):
        return None
    try:
        return int(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp())
    except ValueError:              # 2026-02-30, month 13, …
        return None


def parse_date(value) -> Optional[int]:
    """An ISO-ish date → epoch seconds (UTC), or ``None``. Accepts ``YYYY``, ``YYYY-MM``,
    ``YYYY-MM-DD`` (optionally ``THH:MM[:SS][Z]``) or an int/float epoch. Lenient by design:
    anything unparseable (or implausible — outside 1900–2200) is dropped, never guessed."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = _DATE.match(str(value))
    if not m:
        return None
    y, mo, d, h, mi, s = (int(g) if g else None for g in m.groups())
    return _epoch(y, mo or 1, d or 1, h or 0, mi or 0, s or 0)


def session_date(session_id) -> Optional[int]:
    """The date embedded in a session id (``2026-09-08``, ``standup-2026-09-08``) → epoch,
    else ``None`` (e.g. a host's UUID session id). Lets a dated backfill order itself."""
    m = _SESSION_DATE.search(str(session_id or ""))
    return _epoch(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def format_date(epoch) -> str:
    """Epoch seconds → ``YYYY-MM-DD`` (UTC); ``""`` for ``None``."""
    if epoch is None:
        return ""
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%d")


def format_interval(valid_from, valid_to) -> str:
    """A fact's validity for display: ``since 2026-09-01`` / ``2026-08-01 → 2026-09-01``."""
    if valid_from is None:
        return ""
    if valid_to is None:
        return f"since {format_date(valid_from)}"
    return f"{format_date(valid_from)} → {format_date(valid_to)}"


def coerce_cardinality(value) -> str:
    """Capture's cardinality hint → ``"many"`` (several objects can hold at once, e.g. the
    tools a project uses) or ``"one"`` (a functional value: a port, a version). Missing or
    unknown → ``"one"`` — the pre-B7 behavior (a new value replaces the old)."""
    return MANY if str(value or "").strip().lower() in _MANY_WORDS else ONE


# -- validity predicates -------------------------------------------------------

def valid_at(rec: dict, t: int, valid_to=...) -> bool:
    """Was the fact true in the world at ``t``? (``valid_from <= t < valid_to``)."""
    vf = rec.get("valid_from")
    vt = rec.get("valid_to") if valid_to is ... else valid_to
    return vf is not None and vf <= t and (vt is None or t < vt)


def believed_at(rec: dict, known_at: Optional[int] = None) -> bool:
    """Did OpenWiki hold this record at transaction time ``known_at``? ``None`` = now
    (i.e. simply: not retracted, not forgotten). A **forgotten** record (the sleep pass —
    ``forgotten_at``) was held until it was forgotten: archived, not disbelieved."""
    exp, gone = rec.get("expired_at"), rec.get("forgotten_at")
    if known_at is None:
        return exp is None and gone is None
    created = rec.get("created_at") or 0
    return (created <= known_at and (exp is None or known_at < exp)
            and (gone is None or known_at < gone))


def status(rec: dict, now: int) -> str:
    """``current`` | ``past`` (interval closed — the world moved on) | ``future`` (a planned
    fact, not yet valid) | ``retracted`` (we stopped believing it — a correction) |
    ``forgotten`` (the sleep pass archived it: not worth keeping, e.g. a one-off "was pushed" event)."""
    if rec.get("forgotten_at") is not None:
        return "forgotten"
    if rec.get("expired_at") is not None:
        return "retracted"
    vf, vt = rec.get("valid_from"), rec.get("valid_to")
    if vf is not None and vf > now:
        return "future"
    if vt is not None and vt <= now:
        return "past"
    return "current"


def derive_legacy_intervals(recs: Iterable[dict], supersedes: Iterable[tuple]) -> None:
    """Migrate pre-B7 records (``valid_from is None``) **in place** from the B4 model:
    ``valid_from = created_at``; for each ``(new)-[:SUPERSEDES]->(old)`` the old record's
    interval closes at the new one's ``created_at`` — or, when both were recorded in the
    same instant (an empty interval), the old one counts as retracted. Afterwards the set of
    current facts is exactly what B4's "no incoming SUPERSEDES" gave."""
    by_id = {r["id"]: r for r in recs}
    legacy = {rid for rid, r in by_id.items() if r.get("valid_from") is None}
    if not legacy:
        return
    for rid in legacy:
        by_id[rid]["valid_from"] = int(by_id[rid].get("created_at") or 0)
    for new_id, old_id in supersedes:
        if old_id not in legacy or new_id not in by_id:
            continue
        old = by_id[old_id]
        t = int(by_id[new_id].get("created_at") or 0)
        if t > old["valid_from"]:
            if old.get("valid_to") is None or t < old["valid_to"]:
                old["valid_to"] = t
        elif old.get("expired_at") is None:
            old["expired_at"] = t


def close_times(recs: Iterable[dict], supersedes: Iterable[tuple]) -> dict:
    """``{id: transaction time its valid_to was set}`` — derived from the provenance edges
    (a record's interval is closed by the record that SUPERSEDES it). Lets ``known_at``
    queries see an interval as still *open* before OpenWiki learned it had ended."""
    created = {r["id"]: int(r.get("created_at") or 0) for r in recs}
    out: dict = {}
    for new_id, old_id in supersedes:
        if new_id in created and old_id in created:
            t = max(created[new_id], created[old_id])
            out[old_id] = min(out.get(old_id, t), t)
    return out


def valid_to_known_at(rec: dict, known_at: Optional[int], closed: dict):
    """The ``valid_to`` OpenWiki believed at ``known_at`` (``None`` = now → as stored)."""
    vt = rec.get("valid_to")
    if known_at is None or vt is None:
        return vt
    t = closed.get(rec["id"])
    return None if (t is not None and t > known_at) else vt


# -- the merge rule --------------------------------------------------------------

def plan_merge(records: Iterable[dict], okey: str, valid_from: int,
               cardinality: str = ONE, correct: bool = False, coexists=None,
               batch=frozenset()) -> dict:
    """How a new fact ``(…, object→okey)`` valid from ``valid_from`` slots into the history
    of its ``(subject, predicate)`` — ``records`` are that group's existing records. Ordered
    by **valid time**, not processing order (the B7 fix for out-of-order backfills).

    Returns ``{"action", "target", "valid_from", "valid_to", "close", "expire",
    "superseded_by"}``:

    * ``reaffirm`` — a believed record with the same object already holds at ``valid_from``
      (``target``): reinforce it (the B6 confidence path).
    * ``extend`` — the same object holds from a *later* start (``target``): move its
      ``valid_from`` back — earlier evidence for the same fact.
    * ``add`` — create a new record over ``[valid_from, valid_to)``.

    For a **functional** fact (``cardinality="one"``) every believed rival (different object,
    not itself multi-valued) holding at ``valid_from`` is invalidated: its interval is
    **closed** at ``valid_from`` (``close`` = ``[(id, t)]`` — the world changed), or, when it
    started at the very same instant or ``correct`` is set, it is **retracted** (``expire`` —
    we were wrong; with ``correct`` the new record inherits the rival's whole interval). The
    new record ends where the next later record begins (a backfill lands *in* history, not
    on top of it; ``superseded_by`` names that later record). A ``"many"`` fact coexists with
    other objects and only ever re-affirms / extends / adds.

    ``coexists(record) -> bool`` (optional — an LLM "can both be true at once?" check) then
    **decides rivalry by itself**: every believed record with a different object is a candidate
    rival — the capture model's cardinality tags (and ``"many"`` marks) are ignored, because
    they are noisy and a single wrong one would exempt a record from supersession for good
    (measured in the dogfooding memory). It is consulted lazily, only for the rivals that matter
    (those holding at ``valid_from`` — at most ``MAX_RIVAL_CHECKS``, most recent first — + the
    next later one), never with ``correct`` (an explicit correction wins — then the tags
    decide); vetoed ids are returned as ``coexist``. Without ``coexists`` the tags decide.

    ``batch`` = ids created earlier in the *same* capture: a same-instant rival from the batch is
    **closed** (a zero-length interval — an ordered change within one session, "0.43 → 0.44"),
    not retracted; transcript order is time order, and a correction needs ``correct``."""
    V = int(valid_from)
    believed = [r for r in records if r.get("expired_at") is None]
    covering = [r for r in believed if valid_at(r, V)]
    for r in covering:
        if r.get("okey") == okey:
            return {"action": "reaffirm", "target": r["id"], "valid_from": r.get("valid_from"),
                    "valid_to": r.get("valid_to"), "close": [], "expire": [], "superseded_by": None,
                    "coexist": []}

    functional = coerce_cardinality(cardinality) != MANY
    coexist: list = []

    judged = coexists is not None and not correct   # the LLM check decides, not the tags

    def tag_rival(r):
        return functional and r.get("okey") != okey and r.get("cardinality") != MANY

    def rival(r):                                  # a candidate the coexistence check doesn't veto
        if judged:
            if r.get("okey") == okey:
                return False
            if coexists(r):
                coexist.append(r["id"])
                return False
            return True
        return tag_rival(r)

    candidates = [r for r in covering if r.get("okey") != okey]
    if judged:                                     # bound the calls for big multi-valued groups
        candidates = sorted(candidates, key=lambda r: -(r.get("valid_from") or 0))[:MAX_RIVAL_CHECKS]
    rivals = [r for r in candidates if rival(r)]
    nxt = None                                     # the next later record that bounds the new one
    for r in sorted((r for r in believed if (r.get("valid_from") or 0) > V),
                    key=lambda r: r["valid_from"]):
        if r.get("okey") == okey or rival(r):
            nxt = r
            break

    new_vf = V
    if correct and rivals:
        new_vf = min(r["valid_from"] for r in rivals)
    ends = [r["valid_to"] for r in rivals if r.get("valid_to") is not None]
    if nxt is not None:
        ends.append(nxt["valid_from"])
    new_vt = min(ends) if ends else None

    close, expire = [], []
    for r in rivals:
        if correct or (r["valid_from"] >= V and r["id"] not in batch):   # same instant / correction
            expire.append(r["id"])
        else:                                      # the world changed at V
            close.append((r["id"], V))

    if nxt is not None and nxt.get("okey") == okey and new_vt == nxt["valid_from"]:
        # the same value already holds right after → extend it back instead of a new record
        return {"action": "extend", "target": nxt["id"], "valid_from": new_vf,
                "valid_to": nxt.get("valid_to"), "close": close, "expire": expire,
                "superseded_by": None, "coexist": coexist}
    superseded_by = (nxt["id"] if nxt is not None and nxt.get("okey") != okey
                     and new_vt == nxt["valid_from"] else None)
    return {"action": "add", "target": None, "valid_from": new_vf, "valid_to": new_vt,
            "close": close, "expire": expire, "superseded_by": superseded_by, "coexist": coexist}
