"""A read-only graph that is open only while it is used — so long-running readers don't lock out writers.

Kuzu is reader-XOR-writer across processes: while any process holds the graph open, no other process can open
it writable. The MCP server, ``serve`` and ``chat`` used to hold a read-only connection for their whole life, so
every write during a coding session — the hook capture of a compaction, the agent's ``wiki_remember``, a
nightly ``sleep`` — could only queue to the journal and landed when the session ended (``path-b-memory.md``
§13.15). :class:`LazyGraph` has the same interface as a read-only ``GraphStore`` but opens one per call (≈ 70 ms
on the dogfooding graph) and closes it again; calls that overlap — threads of the web server — share one
connection, closed when the last finishes. When a writer holds the graph, opening is retried until ``wait``
seconds have passed — writers plan read-only and hold the write lock only to apply (``GraphStore.remember``'s
``dry_run``), so that is seconds.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .store import GraphStore


class LazyGraph:
    """A read-only :class:`GraphStore`, opened per use. ``log_usage`` is passed on to every connection; ``wait``
    bounds how long a call waits for a writer to finish."""

    writable = False

    def __init__(self, db_path, wait: float = 15.0, log_usage: bool = False) -> None:
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"No graph at {self.db_path}. Build it with `openwiki graph-build`.")
        self.wait = float(wait)
        self.log_usage = log_usage
        self._lock = threading.RLock()
        self._store = None
        self._users = 0

    def _open(self) -> GraphStore:
        deadline = time.monotonic() + self.wait
        delay = 0.1
        while True:
            try:
                return GraphStore(self.db_path)
            except Exception:                   # held by a writer — it holds the lock for seconds
                if time.monotonic() >= deadline:
                    raise
                time.sleep(delay)
                delay = min(delay * 1.6, 1.0)

    @contextmanager
    def session(self):
        """One open connection for a stretch of calls (closed after the last overlapping user leaves)."""
        with self._lock:
            if self._store is None:
                self._store = self._open()
            self._store.log_usage = self.log_usage
            self._users += 1
            store = self._store
        try:
            yield store
        finally:
            with self._lock:
                self._users -= 1
                if self._users == 0 and self._store is not None:
                    try:
                        self._store.close()
                    finally:
                        self._store = None

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        if not callable(getattr(GraphStore, name, None)):
            raise AttributeError(f"LazyGraph has no attribute {name!r}")

        def call(*args, **kwargs):
            with self.session() as store:
                return getattr(store, name)(*args, **kwargs)
        call.__name__ = name
        return call

    def close(self) -> None:
        """Nothing to close between calls (each call closes its connection)."""
