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

Read-only. Pure over the data the ``GraphStore`` memory methods return; the decay math is
imported lazily so importing :mod:`openwiki.analysis` never pulls in the graph/Kuzu layer.
"""

from __future__ import annotations

import time


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

    current = [f for f in facts if not f["superseded"]]
    n_current = len(current)
    total = len(facts)

    # -- belief revision --------------------------------------------------
    superseded = overview.get("superseded", total - n_current)
    revision = {
        "superseded": int(superseded),
        "revision_rate": round(superseded / total, 3) if total else 0.0,
    }

    # -- consolidation ----------------------------------------------------
    consolidated_ids = set(assignment)
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
    per_session: dict = {}
    first_seen: dict = {}
    for f in facts:
        sid = f.get("session_id") or "?"
        per_session[sid] = per_session.get(sid, 0) + 1
        first_seen[sid] = min(first_seen.get(sid, f["created_at"]), f["created_at"])
    growth = [{"session_id": sid, "facts": per_session[sid]}
              for sid in sorted(per_session, key=lambda s: (first_seen.get(s, 0), s))]

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
    }
