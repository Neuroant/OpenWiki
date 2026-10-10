"""Browsing the remembered tier — the pure part of the Memory tab's facts browser and fact detail (Direction K,
M2-M3): filter, sort and page the facts, and estimate when a fact was said, to find the session turns it came from.
No Kuzu: the graph store and the web app feed it."""

from __future__ import annotations

from typing import Optional

from .graph.memory import volatile_kind
from .graph.temporal import session_date

STATUSES = ("current", "past", "retracted", "future", "forgotten")
SORTS = ("recent", "valid", "subject", "confidence")
MAX_LIMIT = 500
DAY = 86400

_SORT_KEYS = {
    "recent": lambda f: -(f.get("last_seen") or f.get("created_at") or 0),
    "valid": lambda f: -(f.get("valid_from") if f.get("valid_from") is not None else f.get("created_at") or 0),
    "subject": lambda f: ((f.get("subject") or "").lower(), (f.get("predicate") or "").lower(),
                          (f.get("object") or "").lower()),
    "confidence": lambda f: (-(f.get("confidence") or 0), -(f.get("last_seen") or f.get("created_at") or 0)),
}


def fact_text(f: dict) -> str:
    return f"{f.get('subject') or ''} {f.get('predicate') or ''} {f.get('object') or ''}"


def filter_facts(rows, q: str = "", status: str = "current", source: str = "", session: str = "",
                 kind: str = "", theme: Optional[int] = None, assignment: Optional[dict] = None,
                 sort: str = "recent", offset: int = 0, limit: int = 50) -> dict:
    """The facts browser over ``rows`` (as ``GraphStore.list_assertions`` returns them).

    ``q``: every word must occur in "subject predicate object" (case-insensitive); ``status``: one of
    ``STATUSES``, ``""`` = all; ``source``: ``user`` / ``assistant`` / ``material``; ``session``: a substring of the
    session id; ``kind``: ``volatile`` (any kind ``memory.volatile_kind`` names), ``timeless`` (none) or one kind;
    ``theme``: a theme id, matched through ``assignment`` (fact id → theme id). Each fact gains ``kind`` and
    ``theme``. Sorted by ``sort`` (``SORTS``) and paged: ``{"total", "offset", "limit", "facts"}``."""
    assignment = assignment or {}
    words = (q or "").lower().split()
    out = []
    for row in rows:
        f = dict(row)
        f["kind"] = volatile_kind(f)
        f["theme"] = assignment.get(f.get("id"))
        if status and f.get("status") != status:
            continue
        if source and (f.get("source") or "") != source:
            continue
        if session and session.lower() not in (f.get("session_id") or "").lower():
            continue
        if kind == "volatile" and not f["kind"]:
            continue
        if kind == "timeless" and f["kind"]:
            continue
        if kind not in ("", "volatile", "timeless") and f["kind"] != kind:
            continue
        if theme is not None and f["theme"] != theme:
            continue
        if words:
            text = fact_text(f).lower()
            if not all(w in text for w in words):
                continue
        out.append(f)
    out.sort(key=_SORT_KEYS.get(sort, _SORT_KEYS["recent"]))
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), MAX_LIMIT))
    return {"total": len(out), "offset": offset, "limit": limit, "facts": out[offset:offset + limit]}


def said_at(f: dict) -> Optional[int]:
    """When a fact was most likely said — where to look for the turns it came from. Its ``valid_from`` (a captured
    window's first turn), unless that is a stated date more than a day away from the date the session id carries (a
    backfilled day or an agent session) — then midday of that date; without a valid time, the session date, else
    when it was recorded."""
    valid = f.get("valid_from")
    day = session_date(f.get("session_id"))
    if valid is None:
        return day + DAY // 2 if day is not None else f.get("created_at")
    if day is not None and (valid < day - DAY or valid > day + 2 * DAY):
        return day + DAY // 2
    return valid


def learned_at(f: dict) -> Optional[int]:
    """When the memory learned a fact, on the axis of when things were said (Memory over time, M5): ``said_at``, but
    never after the fact was recorded — an agent write without a valid time defaults to midday of its session's day,
    which can lie a few hours after it was written. A backfilled day keeps its own date (said weeks before recorded)."""
    said, recorded = said_at(f), f.get("created_at")
    if said is None:
        return recorded
    return min(said, recorded) if recorded is not None else said
