"""Pluggable text-embedding backends.

Ships an Ollama backend (local, already running for this project — no API key,
no PyTorch). The :class:`Embedder` protocol keeps the index code independent of
the backend, so a sentence-transformers or hosted-API embedder could be added
later without touching :mod:`openwiki.search`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Protocol, Sequence, runtime_checkable

import numpy as np


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
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Could not reach Ollama at {self.host} (is it running?): {exc}"
            ) from exc

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
