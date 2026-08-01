"""Build and query a semantic search index over the wiki's chunks.

The corpus is small (tens of pages -> a few hundred chunks), so the index is a
plain normalized embedding matrix with brute-force cosine similarity — no vector
database, and easy to read. Cosine reduces to a dot product because both stored
vectors and the query vector are L2-normalized.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from .chunking import Chunk, chunk_wiki
from .embeddings import Embedder, OllamaEmbedder
from .wiki import Wiki


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


@dataclass
class SearchResult:
    score: float
    page_slug: str
    page_title: str
    pdf_page_start: int
    pdf_page_end: int
    chunk_id: str
    text: str


class SemanticIndex:
    def __init__(self, embedder: Embedder, chunks: list[Chunk],
                 embeddings: np.ndarray, model_name: str) -> None:
        self.embedder = embedder
        self.chunks = chunks
        self.embeddings = embeddings  # L2-normalized, shape (n_chunks, dim)
        self.model_name = model_name

    # -- build ----------------------------------------------------------

    @classmethod
    def build(cls, wiki: Wiki, embedder: Embedder, *,
              size_words: int = 180, overlap_words: int = 30) -> "SemanticIndex":
        chunks = chunk_wiki(wiki, size_words, overlap_words)
        if not chunks:
            raise ValueError("No chunks produced from the wiki (empty page text?).")
        vectors = embedder.embed_documents([c.text for c in chunks]).astype(np.float32)
        return cls(embedder, chunks, _normalize_rows(vectors), embedder.name)

    # -- persistence ----------------------------------------------------

    def save(self, out_dir) -> None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        np.save(out_dir / "embeddings.npy", self.embeddings)
        meta = {
            "model": self.model_name,
            "dim": int(self.embeddings.shape[1]) if self.embeddings.size else 0,
            "count": len(self.chunks),
            "chunks": [asdict(c) for c in self.chunks],
        }
        (out_dir / "index.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, index_dir, embedder: Optional[Embedder] = None) -> "SemanticIndex":
        index_dir = Path(index_dir)
        meta = json.loads((index_dir / "index.json").read_text(encoding="utf-8"))
        embeddings = np.load(index_dir / "embeddings.npy")
        chunks = [Chunk(**c) for c in meta["chunks"]]
        if embedder is None:
            model = meta["model"]
            name = model.split(":", 1)[1] if model.startswith("ollama:") else model
            embedder = OllamaEmbedder(model=name)
        return cls(embedder, chunks, embeddings, meta["model"])

    # -- query ----------------------------------------------------------

    def search(self, query: str, k: int = 5) -> list[SearchResult]:
        if not self.chunks:
            return []
        q = self.embedder.embed_query(query).astype(np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        scores = self.embeddings @ q
        k = min(k, len(scores))
        top = np.argsort(-scores)[:k]
        results = []
        for i in top:
            chunk = self.chunks[int(i)]
            results.append(
                SearchResult(
                    score=float(scores[i]),
                    page_slug=chunk.page_slug,
                    page_title=chunk.page_title,
                    pdf_page_start=chunk.pdf_page_start,
                    pdf_page_end=chunk.pdf_page_end,
                    chunk_id=chunk.id,
                    text=chunk.text,
                )
            )
        return results
