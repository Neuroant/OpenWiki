"""Append-only usage log for read-path reinforcement (Path B — B1).

A read-only process (`ask`, MCP `wiki_ask`) can't open the Kuzu graph writable to
reinforce usage edges — Kuzu's write lock is exclusive. So it **appends** the
seed→related page pairs it retrieved to a small JSONL sidecar next to the graph,
and the next *writable* process (`serve`/`chat` startup, or `openwiki decay`) folds
them in (`GraphStore.fold_usage`) and clears the log. Reads teach the graph; the
lesson lands when a writer next runs — no lock contention on the read path.

Pure + dependency-free (no Kuzu), mirroring `decay.py`/`memory.py`; the Kuzu side
(reinforce / fold) lives in `store.py`.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


def usage_log_path(db_path) -> Path:
    """The usage-log sidecar for a graph DB file. Named off the DB file so it travels
    with the graph and survives a rebuild (`GraphBuilder._remove_existing` leaves it)."""
    p = Path(db_path)
    return p.with_name(p.name + ".usage.jsonl")


def append_usage(path, pairs, now: Optional[int] = None) -> int:
    """Append one record of ``(from_slug, to_slug)`` usage pairs; returns the number
    of pairs written (0 if none, so an empty read is skipped). One JSON line per call
    keeps concurrent appends from interleaving mid-record."""
    clean = [[str(a), str(b)] for a, b in pairs if a and b and a != b]
    if not clean:
        return 0
    now = int(now if now is not None else time.time())
    line = json.dumps({"t": now, "pairs": clean}, ensure_ascii=False)
    with Path(path).open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return len(clean)


def read_usage(path) -> list:
    """Read all pending records: ``[{"t": int, "pairs": [[a, b], ...]}, ...]``.
    Lenient — skips blank/corrupt lines; ``[]`` if the log is absent."""
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
        if isinstance(obj, dict) and isinstance(obj.get("pairs"), list):
            records.append(obj)
    return records


def clear_usage(path) -> None:
    """Drop the usage log (after folding it into the graph)."""
    Path(path).unlink(missing_ok=True)
