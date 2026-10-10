"""Memory-tier dynamics — world-model analysis over the Path B "second brain" (P4).

Where `coupling.py`/`gaps.py` analyze the *document* graph (a static mirror), this analyzes
the **remembered tier** — the part of the world model that *learns over time*: facts captured
across sessions, superseded when contradicted (B4), reinforced when re-affirmed (B6
confidence), and consolidated into themes (B5). The metrics are its *dynamics*:

- **revision** — how much remembered knowledge has been overwritten (SUPERSEDES rate): a
  world model that revises its beliefs, not just accretes them.
- **consolidation** — how much of the raw fact stream has been folded into themes (the
  "sleep" pass's coverage) + the theme-size shape.
- **temperature** — hot vs. cold knowledge: per-fact **confidence** (re-affirmation) and the
  decayed **effective weight** (recency), bucketed — what's live vs. fading.
- **breadth** — distinct subjects/predicates + the dominant predicates: the shape of what's
  known.
- **growth** — facts contributed per session: the accrual curve.
- **review** — the current facts of a kind that goes stale on its own (plans, counts, gaps, versions,
  running states — ``graph.memory.volatile_kind``), most likely stale kinds first, each as the line
  ``wiki_remember``'s ``replaces`` matches: a list to check, not a verdict (1 in 4 was stale, v0.109).
- **over time** — facts learned, closed (the world changed), retracted (corrected) and forgotten per day — per
  week over a long span — on the axis of when things were said, with the current facts at the end of each
  (``memory_over_time``, the Dynamik chart, M5); ``period_events`` lists one period's facts.

Read-only. Pure over the data the ``GraphStore`` memory methods return; the decay math is
imported lazily so importing :mod:`openwiki.analysis` never pulls in the graph/Kuzu layer.
"""

from __future__ import annotations

import time

DAY = 86400
MAX_DAILY_DAYS = 120          # memory over time: a longer span is shown per week
PERIOD_LIMIT = 200            # facts listed per event kind for one period
CAUSES = ("closed", "retracted", "forgotten")


def fact_events(f: dict, now: int) -> "tuple | None":
    """When a fact entered the memory and when — and why — it stopped being current, on the axis of when things
    were said: ``(learned, current_from, ended, cause)``. ``learned`` = ``memory_browse.learned_at``;
    ``current_from`` = when it became valid (not before it was learned), ``None`` for a fact not valid yet (planned);
    ``ended`` / ``cause`` = the first of ``valid_to`` (``closed`` — the world changed; only once it has passed),
    ``expired_at`` (``retracted`` — corrected) and ``forgotten_at`` (``forgotten``), or ``None``. Times are clamped
    to ``[learned, now]``: a stated "until 1969" closes a fact on the day it was learned. At ``now`` a fact is current
    exactly when ``temporal.status`` says so."""
    from ..memory_browse import learned_at
    learned = learned_at(f)
    if learned is None:
        return None
    learned = min(int(learned), now)
    ends = [(min(max(int(t), learned), now), cause)
            for t, cause in ((f.get("valid_to"), "closed"), (f.get("expired_at"), "retracted"),
                             (f.get("forgotten_at"), "forgotten"))
            if t is not None and (cause != "closed" or t <= now)]
    ended, cause = min(ends, key=lambda e: e[0]) if ends else (None, None)
    valid = f.get("valid_from")
    current_from = max(learned, int(valid)) if valid is not None else learned
    return learned, (current_from if current_from <= now else None), ended, cause


def _period_start(t: int, week: bool) -> int:
    """The UTC day ``t`` falls on — or the Monday of its week (1970-01-01 was a Thursday)."""
    day = t - t % DAY
    return day - ((day // DAY + 3) % 7) * DAY if week else day


def memory_over_time(facts, now: "int | None" = None, max_days: int = MAX_DAILY_DAYS) -> dict:
    """Facts learned, closed, retracted and forgotten per period, and the current facts at the end of each — per
    day, per week when the memory spans more than ``max_days``. ``{"bucket": "day" | "week", "periods": [{"t",
    "label", "learned", "closed", "retracted", "forgotten", "current"}], "totals": {...}}``, oldest first; each fact
    counts once as learned and at most once as ending (``fact_events``); the last period's ``current`` is today's
    count of current facts."""
    from ..graph.temporal import format_date
    now = int(now if now is not None else time.time())
    events = [e for e in (fact_events(f, now) for f in facts) if e is not None]
    empty = {"learned": 0, **{c: 0 for c in CAUSES}, "current": 0}
    if not events:
        return {"bucket": "day", "periods": [], "totals": dict(empty)}
    first = min(e[0] for e in events)
    week = (now - first) / DAY > max_days
    step = (7 if week else 1) * DAY
    start = _period_start(first, week)
    periods = [dict(empty, t=t, label=format_date(t))
               for t in range(start, _period_start(now, week) + 1, step)]
    delta = [0] * (len(periods) + 1)
    for learned, current_from, ended, cause in events:
        periods[(_period_start(learned, week) - start) // step]["learned"] += 1
        if ended is not None:
            periods[(_period_start(ended, week) - start) // step][cause] += 1
        if current_from is not None and (ended is None or ended > current_from):
            delta[(_period_start(current_from, week) - start) // step] += 1     # current from that period's end …
            if ended is not None:
                delta[(_period_start(ended, week) - start) // step] -= 1        # … not at the end of the one it ended in
    running = 0
    for p, d in zip(periods, delta):
        running += d
        p["current"] = running
    totals = {"learned": len(events), **{c: sum(p[c] for p in periods) for c in CAUSES},
              "current": periods[-1]["current"]}
    return {"bucket": "week" if week else "day", "periods": periods, "totals": totals}


def period_events(facts, start: int, end: int, now: "int | None" = None, limit: int = PERIOD_LIMIT) -> dict:
    """The facts behind one period of ``memory_over_time`` (``start`` ≤ t < ``end``): those learned in it and those
    that stopped being current in it, by cause — each list ordered by time, at most ``limit`` long, with its full
    count in ``counts``."""
    now = int(now if now is not None else time.time())
    out: dict = {"learned": [], **{c: [] for c in CAUSES}}
    for f in facts:
        e = fact_events(f, now)
        if e is None:
            continue
        brief = {k: f.get(k) for k in ("id", "subject", "predicate", "object", "status", "session_id",
                                        "valid_from", "valid_to", "source")}
        if start <= e[0] < end:
            out["learned"].append((e[0], brief))
        if e[2] is not None and start <= e[2] < end:
            out[e[3]].append((e[2], brief))
    counts = {k: len(v) for k, v in out.items()}
    return {"start": start, "end": end, "counts": counts,
            **{k: [b for _, b in sorted(v, key=lambda x: x[0])][:limit] for k, v in out.items()}}


def _theme_size_stats(sizes: list) -> dict:
    if not sizes:
        return {"min": 0, "max": 0, "mean": 0.0}
    return {"min": int(min(sizes)), "max": int(max(sizes)),
            "mean": round(sum(sizes) / len(sizes), 2)}


def analyze_memory(graph, now: "int | None" = None, half_life: "float | None" = None,
                   top_predicates: int = 8) -> dict:
    """Compute the memory-tier dynamics over a ``GraphStore`` (read-only). ``available: false``
    (with a ``reason``) when the graph has no remembered tier."""
    if not graph.has_memory():
        return {"available": False, "reason": "empty"}

    # decay math is pure but lives under graph/; import lazily to keep this package light
    from ..graph.decay import DEFAULT_HALF_LIFE_DAYS, confidence_weight, effective_weight
    now = int(now if now is not None else time.time())
    hl = float(half_life if half_life is not None else DEFAULT_HALF_LIFE_DAYS)

    overview = graph.memory_overview()
    facts = graph.list_assertions(limit=1_000_000, include_superseded=True)
    themes = graph.memory_concepts()
    assignment = graph.concept_assignment()          # {assertion_id: concept_id} (current only)

    # B7: "current" = valid now + believed (a planned, future-dated fact is not current yet)
    current = [f for f in facts
               if (f["status"] == "current" if "status" in f else not f["superseded"])]
    n_current = len(current)
    total = len(facts)

    # -- belief revision --------------------------------------------------
    # B7 splits it: a *closed* fact = the world changed (valid_to set); a *retracted* one = we
    # were wrong (expired_at — a correction). Both count as superseded.
    superseded = overview.get("superseded", total - n_current)
    retracted = int(overview.get("retracted", 0))
    revision = {
        "superseded": int(superseded),
        "revision_rate": round(superseded / total, 3) if total else 0.0,
        "world_changes": int(superseded) - retracted,
        "corrections": retracted,
        "planned": int(overview.get("planned", 0)),
        "forgotten": int(overview.get("forgotten", 0)),     # archived by the sleep pass
    }

    # -- consolidation ----------------------------------------------------
    summarized = {t["id"] for t in themes}         # a pending theme (--budget ran out) doesn't count
    consolidated_ids = {aid for aid, cid in assignment.items() if cid in summarized}
    consolidated = sum(1 for f in current if f["id"] in consolidated_ids)
    sizes = [t.get("size", 0) for t in themes]
    consolidation = {
        "themes": len(themes),
        "consolidated_facts": consolidated,
        "coverage": round(consolidated / n_current, 3) if n_current else 0.0,
        "avg_theme_size": round(consolidated / len(themes), 2) if themes else 0.0,
        "theme_sizes": _theme_size_stats(sizes),
    }

    # -- temperature (hot vs. cold) --------------------------------------
    hot = warm = cold = reaffirmed = 0
    eff_sum = conf_sum = 0.0
    for f in current:
        conf = float(f.get("confidence", 1.0) or 1.0)
        seen = f.get("last_seen") or f.get("created_at") or 0
        eff = effective_weight(confidence_weight(conf), seen, now, hl)
        eff_sum += eff
        conf_sum += conf
        if conf > 1.0:
            reaffirmed += 1
        if eff >= 0.7:
            hot += 1
        elif eff >= 0.3:
            warm += 1
        else:
            cold += 1
    temperature = {
        "mean_confidence": round(conf_sum / n_current, 2) if n_current else 0.0,
        "reaffirmed_fraction": round(reaffirmed / n_current, 3) if n_current else 0.0,
        "mean_effective_weight": round(eff_sum / n_current, 3) if n_current else 0.0,
        "hot": hot, "warm": warm, "cold": cold,
        "half_life_days": hl,
    }

    # -- breadth ----------------------------------------------------------
    subjects, predicates = set(), {}
    for f in current:
        subjects.add((f["subject"] or "").strip().lower())
        p = (f["predicate"] or "").strip().lower()
        predicates[p] = predicates.get(p, 0) + 1
    top = sorted(predicates.items(), key=lambda kv: kv[1], reverse=True)[:top_predicates]
    breadth = {
        "distinct_subjects": len(subjects),
        "distinct_predicates": len(predicates),
        "top_predicates": [{"predicate": p, "count": n} for p, n in top],
    }

    # -- growth (facts per session, oldest session first) -----------------
    # A session's date is the one its id carries (a backfilled day, an agent session), else when its first fact
    # was recorded — recording order alone put a re-captured old day after the days it precedes.
    from ..graph.temporal import session_date
    per_session: dict = {}
    first_seen: dict = {}
    for f in facts:
        sid = f.get("session_id") or "?"
        per_session[sid] = per_session.get(sid, 0) + 1
        first_seen[sid] = min(first_seen.get(sid, f["created_at"]), f["created_at"])
    dated = {sid: session_date(sid) or first_seen.get(sid, 0) for sid in per_session}
    growth = [{"session_id": sid, "facts": per_session[sid], "date": dated[sid]}
              for sid in sorted(per_session, key=lambda s: (dated[s], s))]

    # -- review: current facts of a kind that goes stale (plans, counts, gaps, versions, running states) — the
    # list to check against the project and correct with wiki_remember; each line is in the form `replaces` matches
    from ..graph.memory import VOLATILE_KINDS, fact_line, volatile_kind
    rank = {k: i for i, k in enumerate(VOLATILE_KINDS)}
    to_check = sorted(({"kind": k, "line": fact_line(f), "id": f["id"], "valid_from": f.get("valid_from")}
                       for f in current if (k := volatile_kind(f)) is not None),
                      key=lambda r: (rank[r["kind"]], r["valid_from"] or 0))
    review = {"count": len(to_check),
              "share": round(len(to_check) / n_current, 3) if n_current else 0.0,
              "by_kind": {k: n for k in VOLATILE_KINDS if (n := sum(1 for r in to_check if r["kind"] == k))},
              "facts": to_check}

    return {
        "available": True,
        "counts": {
            "sessions": overview.get("sessions", 0),
            "current": n_current,
            "superseded": int(superseded),
            "themes": len(themes),
        },
        "revision": revision,
        "consolidation": consolidation,
        "temperature": temperature,
        "breadth": breadth,
        "growth": growth,
        "review": review,
        "over_time": memory_over_time(facts, now),
    }
