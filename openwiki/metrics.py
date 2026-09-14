"""Lightweight in-process observability — a bounded ring buffer of recent runtime
events (LLM calls, embeddings, HTTP requests) with timing + token counts.

The LLM/embedding backends already receive rich telemetry from Ollama on every
call (latency + prompt/eval token counters) but historically **discarded** it. This
module captures it: `parse_ollama_stats` extracts the counters (nanosecond durations
→ ms, tokens/sec), and a module-level `COLLECTOR` keeps the last N events so the CLI
(`ask` footer) and the web UI (`/api/metrics`, the System tab) can surface *how slow*
and *how many tokens* — the first observability the project has.

Pure + dependency-free (stdlib `deque` + a lock); always-on but **bounded** (maxlen),
so it costs nothing to leave running. Recording is best-effort — a metrics failure
must never break the call it is measuring.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class Event:
    """One recorded runtime event. Durations are milliseconds; token counts are
    ``None`` when the backend didn't report them (e.g. a fake model in tests)."""
    seq: int
    t: float                              # epoch seconds
    kind: str                             # "chat" | "embed" | "http"
    name: str = ""                        # model name or "METHOD /route"
    duration_ms: float = 0.0
    prompt_tokens: Optional[int] = None
    eval_tokens: Optional[int] = None
    tokens_per_sec: Optional[float] = None
    extra: dict = field(default_factory=dict)


class MetricsCollector:
    """A thread-safe, bounded ring buffer of `Event`s with cheap aggregates."""

    def __init__(self, maxlen: int = 256) -> None:
        self._events: deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._seq = 0

    @property
    def seq(self) -> int:
        """The current sequence counter — pass to :meth:`since` to fetch events
        recorded after this point (used to scope one chat turn's calls)."""
        with self._lock:
            return self._seq

    def record(self, kind: str, name: str = "", duration_ms: float = 0.0,
               prompt_tokens: Optional[int] = None, eval_tokens: Optional[int] = None,
               tokens_per_sec: Optional[float] = None, **extra) -> Event:
        with self._lock:
            self._seq += 1
            ev = Event(self._seq, time.time(), kind, name, round(float(duration_ms), 1),
                       prompt_tokens, eval_tokens, tokens_per_sec, dict(extra))
            self._events.append(ev)
            return ev

    def since(self, seq: int) -> list:
        """Events recorded after sequence ``seq`` (in order)."""
        with self._lock:
            return [e for e in self._events if e.seq > seq]

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def snapshot(self, limit: int = 50) -> dict:
        """A JSON-ready view: the newest ``limit`` events + per-kind aggregates."""
        with self._lock:
            evs = list(self._events)
        newest = [asdict(e) for e in evs[-limit:][::-1]]
        return {"events": newest, "summary": _summarize(evs), "total_events": len(evs)}


def _pct(sorted_vals: list, p: int) -> float:
    if not sorted_vals:
        return 0.0
    i = min(len(sorted_vals) - 1, int(round((p / 100.0) * (len(sorted_vals) - 1))))
    return round(sorted_vals[i], 1)


def _summarize(events: list) -> dict:
    """Per-kind counts, p50/p95 + total latency, and token totals."""
    buckets: dict = {}
    for e in events:
        b = buckets.setdefault(e.kind, {"count": 0, "durations": [],
                                        "prompt_tokens": 0, "eval_tokens": 0})
        b["count"] += 1
        b["durations"].append(e.duration_ms)
        if e.prompt_tokens:
            b["prompt_tokens"] += e.prompt_tokens
        if e.eval_tokens:
            b["eval_tokens"] += e.eval_tokens
    out = {}
    for kind, b in buckets.items():
        ds = sorted(b["durations"])
        out[kind] = {
            "count": b["count"],
            "p50_ms": _pct(ds, 50),
            "p95_ms": _pct(ds, 95),
            "total_ms": round(sum(ds), 1),
            "prompt_tokens": b["prompt_tokens"],
            "eval_tokens": b["eval_tokens"],
        }
    return out


def parse_ollama_stats(data: dict) -> dict:
    """Extract Ollama's telemetry from a ``/api/chat`` or ``/api/embed`` response into a
    tidy dict — nanosecond durations → milliseconds, plus tokens/sec. Returns ``{}`` when
    the fields are absent (older Ollama, or a response without counters)."""
    if not isinstance(data, dict):
        return {}
    out: dict = {}
    pe, ec, ed = data.get("prompt_eval_count"), data.get("eval_count"), data.get("eval_duration")
    if isinstance(pe, int):
        out["prompt_tokens"] = pe
    if isinstance(ec, int):
        out["eval_tokens"] = ec
    if isinstance(ec, int) and isinstance(ed, (int, float)) and ed > 0:
        out["tokens_per_sec"] = round(ec / (ed / 1e9), 1)
    for label, key in (("total_ms", "total_duration"), ("load_ms", "load_duration"),
                       ("prompt_ms", "prompt_eval_duration"), ("eval_ms", "eval_duration")):
        v = data.get(key)
        if isinstance(v, (int, float)):
            out[label] = round(v / 1e6, 1)
    return out


# Module-level default collector — the LLM/embedding backends record here, and the
# web app + CLI read it. Bounded, so it is safe to leave on for the process lifetime.
# 1024 events comfortably spans a full build's per-stage LLM spend (e.g. one entity
# call per page) so the build-observability stage deltas aren't evicted mid-stage.
COLLECTOR = MetricsCollector(maxlen=1024)
