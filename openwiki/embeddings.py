"""Pluggable text-embedding backends.

Ships an Ollama backend (local, already running for this project — no API key,
no PyTorch). The :class:`Embedder` protocol keeps the index code independent of
the backend, so a sentence-transformers or hosted-API embedder could be added
later without touching :mod:`openwiki.search`.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

import numpy as np

from .metrics import COLLECTOR, parse_ollama_stats


@runtime_checkable
class Embedder(Protocol):
    @property
    def name(self) -> str: ...

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class OllamaEmbedder:
    """Embed text via a local Ollama server's ``/api/embed`` endpoint.

    ``bge-m3`` (the default) needs no query/passage prefixes; that's why the
    document and query paths are symmetric here.
    """

    def __init__(
        self,
        model: str = "bge-m3",
        host: str = "http://localhost:11434",
        batch_size: int = 32,
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.batch_size = batch_size
        self.timeout = timeout

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    def _embed(self, inputs: list[str]) -> np.ndarray:
        payload = json.dumps({"model": self.model, "input": inputs}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.host}/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Could not reach Ollama at {self.host} (is it running?): {exc}"
            ) from exc
        try:  # best-effort telemetry — an embed call has no eval tokens, just prompt + latency
            stats = parse_ollama_stats(data)
            COLLECTOR.record("embed", f"ollama:{self.model}",
                             duration_ms=(time.perf_counter() - t0) * 1000.0,
                             prompt_tokens=stats.get("prompt_tokens"),
                             count=len(inputs))
        except Exception:  # pragma: no cover
            pass

        vectors = data.get("embeddings")
        if not vectors:
            raise RuntimeError(
                f"Ollama returned no embeddings for model '{self.model}'. "
                f"Is it pulled? Try `ollama pull {self.model}`."
            )
        return np.asarray(vectors, dtype=np.float32)

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        texts = list(texts)
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        batches = [
            self._embed(texts[i : i + self.batch_size])
            for i in range(0, len(texts), self.batch_size)
        ]
        return np.vstack(batches)

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([text])[0]


def get_embedder(model: str = "bge-m3", host: str = "http://localhost:11434") -> Embedder:
    """Factory for the configured embedding backend (currently Ollama)."""
    return OllamaEmbedder(model=model, host=host)


class CachingEmbedder:
    """Wraps an embedder and remembers every vector it produced, so embeddings can be computed **in one
    batch** ahead of the calls that need them (``warm``) — the store's own ``embed_documents`` /
    ``embed_query`` calls are then served without touching the model server. Used by the LoCoMo harness
    (no GPU model swaps) and by two-phase memory writes (the write pass re-embeds nothing)."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.name = getattr(inner, "name", "embedder")
        self._docs: dict = {}
        self._queries: dict = {}

    def warm(self, docs=(), queries=()) -> None:
        import numpy as np
        todo = [t for t in dict.fromkeys(docs) if t not in self._docs]
        if todo:
            for t, v in zip(todo, np.asarray(self.inner.embed_documents(todo))):
                self._docs[t] = v
        for q in dict.fromkeys(queries):
            if q not in self._queries:
                self._queries[q] = np.asarray(self.inner.embed_query(q))

    def embed_documents(self, texts):
        import numpy as np
        self.warm(docs=texts)
        return np.vstack([self._docs[t] for t in texts]) if texts else np.zeros((0, 0), dtype=np.float32)

    def embed_query(self, text):
        self.warm(queries=[text])
        return self._queries[text]

    def save(self, path) -> None:
        """Write every vector held to ``path`` (a ``.npz``), so a later run can ``load`` them and ask the model
        only for texts it never embedded — the same text gets the same vector across runs (the LoCoMo guard)."""
        import os

        import numpy as np

        def stack(held: dict) -> np.ndarray:
            return (np.vstack([np.asarray(v, dtype=np.float32) for v in held.values()]) if held
                    else np.zeros((0, 0), dtype=np.float32))

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        meta = json.dumps({"name": self.name, "docs": list(self._docs), "queries": list(self._queries)},
                          ensure_ascii=False)
        tmp = p.with_name(p.stem + ".partial.npz")
        np.savez(tmp, meta=np.array([meta]), docs=stack(self._docs), queries=stack(self._queries))
        os.replace(tmp, p)

    def load(self, path) -> int:
        """Take the vectors a ``save`` wrote for this embedder (none when ``path`` is absent, unreadable or another
        model's); returns how many."""
        import numpy as np

        p = Path(path)
        if not p.is_file():
            return 0
        try:
            with np.load(p, allow_pickle=False) as z:
                meta = json.loads(str(z["meta"][0]))
                docs, queries = z["docs"], z["queries"]
        except (OSError, ValueError, KeyError, IndexError):
            return 0
        if meta.get("name") != self.name:
            return 0
        for t, v in zip(meta.get("docs") or [], docs):
            self._docs.setdefault(t, v)
        for t, v in zip(meta.get("queries") or [], queries):
            self._queries.setdefault(t, v)
        return len(meta.get("docs") or []) + len(meta.get("queries") or [])
