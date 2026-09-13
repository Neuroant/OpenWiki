"""Write-ahead journal for deferred memory ops (Path B — B1 concurrency).

Kuzu 0.11 is **reader-XOR-writer**: a writable connection is exclusive (it blocks all
readers, *and* readers block a writer — empirically verified). So while a read-only
``serve`` (or any reader) holds the graph, a would-be *writer* — ``remember``, a host
``capture`` hook, or a chat-agent edit's graph re-sync — can't open it writable. Rather
than fail or drop the write, it **appends** the op to a small JSONL sidecar next to the
graph, and the next *writable* pass (``serve``/``chat`` start-or-shutdown fold,
``openwiki decay``, or the next ``remember``) drains it (``GraphStore.fold_journal``).
Writes are never lost; they land when a writer next runs.

This complements :mod:`openwiki.graph.usage` (the reinforce-pair slice of the same
write-ahead idea); together they are OpenWiki's lock-free deferred-write log. Records
are **self-contained** (a ``remember`` carries its triples, a ``reindex`` its page text),
so a fold needs only an embedder — no wiki directory or session lookup. Pure +
dependency-free (no Kuzu), mirroring ``usage.py``/``decay.py``; the Kuzu side (fold)
lives in ``store.py``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


def journal_path(db_path) -> Path:
    """The op-journal sidecar for a graph DB file. Named off the DB file so it travels
    with the graph and survives a rebuild (``GraphBuilder._remove_existing`` leaves it)."""
    p = Path(db_path)
    return p.with_name(p.name + ".journal.jsonl")


def _now(now: Optional[int]) -> int:
    return int(now if now is not None else time.time())


def _append(path, obj: dict) -> None:
    """Write one JSON record per line so concurrent appends never interleave mid-record."""
    line = json.dumps(obj, ensure_ascii=False)
    with Path(path).open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def append_remember(path, session_id, facts, now: Optional[int] = None) -> int:
    """Queue a ``remember`` op — (subject, predicate, object) triples for one session.
    ``facts`` may be ``MemoryFact``-likes (``.subject``/``.predicate``/``.object``) or
    ``(s, p, o)`` tuples. Returns the number of triples written (0 → nothing queued)."""
    triples = []
    for f in facts:
        if hasattr(f, "subject"):
            s, p, o = f.subject, f.predicate, f.object
        else:
            s, p, o = f
        s, p, o = str(s).strip(), str(p).strip(), str(o).strip()
        if s and p and o:
            triples.append([s, p, o])
    if not triples:
        return 0
    _append(path, {"op": "remember", "t": _now(now), "session": str(session_id), "facts": triples})
    return len(triples)


def append_reindex(path, slug, text, now: Optional[int] = None) -> int:
    """Queue a ``reindex`` op — re-sync one page into the graph. The record carries the
    page text so the fold is self-contained. Returns 1 (queued) or 0 (empty slug)."""
    slug = str(slug).strip()
    if not slug:
        return 0
    _append(path, {"op": "reindex", "t": _now(now), "slug": slug, "text": str(text or "")})
    return 1


def read_journal(path) -> list:
    """Read all pending op records. Lenient — skips blank/corrupt lines and unknown ops;
    ``[]`` if the journal is absent."""
    p = Path(path)
    if not p.is_file():
        return []
    records = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("op") in ("remember", "reindex"):
            records.append(obj)
    return records


def clear_journal(path) -> None:
    """Drop the journal (after folding it into the graph)."""
    Path(path).unlink(missing_ok=True)


def pending_journal(path) -> int:
    """How many op records are queued (0 if none)."""
    return len(read_journal(path))
