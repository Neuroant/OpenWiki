"""Time/usage decay for reinforced graph edges — the memory-weight core.

The deterministic graph (SIMILAR_TO/REFERENCES/…) is a *mirror* of the sources and
carries no memory of use. This module adds the opposite: an edge weight that grows
when a connection is **used** (reinforced) and **fades** with time when it isn't —
the first step toward using the graph as agent memory (Path B in ``docs/roadmap.md``).

Pure math, no Kuzu: exponential decay by a half-life, and a capped reinforcement
bump. :mod:`openwiki.graph.store` stores the weights on ``REINFORCES`` edges and
applies :func:`effective_weight` at query time; :mod:`openwiki.cli` runs the decay
pass (``openwiki decay``). Fully unit-testable with plain numbers.
"""

from __future__ import annotations

import math

DAY_SECONDS = 86_400

# Defaults (overridable per call / via the CLI):
DEFAULT_HALF_LIFE_DAYS = 30.0   # weight halves after this many days unused
DEFAULT_BOOST = 1.0             # weight added per reinforcement
DEFAULT_CAP = 10.0              # ceiling on accumulated weight (bounds runaway)
DEFAULT_FLOOR = 0.1             # below this effective weight an edge is dropped
CONF_LOG_WEIGHT = 0.1           # how gently confidence lifts recall rank (log-scaled tie-breaker)


def effective_weight(weight: float, last_seen: int, now: int,
                     half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> float:
    """The edge's weight decayed for the time since it was last reinforced.

    ``weight`` at ``last_seen`` (epoch seconds) decays by half every
    ``half_life_days`` up to ``now``. Non-positive weight → 0; a non-positive
    half-life disables decay (returns ``weight``); future ``last_seen`` is clamped.
    """
    if weight <= 0:
        return 0.0
    if half_life_days <= 0:
        return float(weight)
    age_days = max(0.0, (now - last_seen) / DAY_SECONDS)
    return float(weight) * 0.5 ** (age_days / half_life_days)


def reinforced_weight(current: float, boost: float = DEFAULT_BOOST,
                      cap: float = DEFAULT_CAP) -> float:
    """A reinforced edge's new stored weight: ``current + boost``, capped at ``cap``."""
    return min(float(cap), max(0.0, float(current)) + float(boost))


def confidence_weight(confidence: float) -> float:
    """B6: map a raw per-fact confidence (≥1, grows ~+1 per re-affirmation, capped) to a
    **gentle** recall multiplier — ``1 + CONF_LOG_WEIGHT·log2(confidence)``. Deliberately small
    (conf 1→1.0, 2→1.10, 3→1.16, 10→1.33): confidence is a **tie-breaker** among similarly-relevant
    facts, not a way to resurface a less-relevant one — relevance (cosine) still dominates. A one-off
    fact keeps its prior weight of 1.0 (so single-stated memory behaves exactly as before)."""
    return 1.0 + CONF_LOG_WEIGHT * math.log2(max(float(confidence), 1.0))
