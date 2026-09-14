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
        self._bm25 = None             # lazy lexical index (built on first hybrid search)

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
        return [self._result(int(i), float(scores[i])) for i in top]

    def _lexical(self):
        """The lazily-built BM25 index over the chunk texts (cheap; cached)."""
        if self._bm25 is None:
            from .lexical import BM25
            self._bm25 = BM25.build([c.text for c in self.chunks])
        return self._bm25

    def search_hybrid(self, query: str, k: int = 5, rrf_k: int = 60) -> list[SearchResult]:
        """Hybrid retrieval: fuse the **dense** cosine ranking with a **BM25 lexical**
        ranking over the same chunks via reciprocal rank fusion (Direction A). Dense
        catches paraphrase/semantics; BM25 catches exact rare terms (identifiers,
        acronyms, German compounds) the embedder blurs. ``.score`` is the RRF score."""
        if not self.chunks:
            return []
        from .lexical import reciprocal_rank_fusion
        q = self.embedder.embed_query(query).astype(np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        dense = self.embeddings @ q
        lexical = self._lexical().scores(query)
        dense_order = list(np.argsort(-dense))
        lexical_order = list(np.argsort(-lexical))
        fused = reciprocal_rank_fusion([dense_order, lexical_order], k=rrf_k)
        # RRF score for the chosen chunks (recomputed for the SearchResult .score).
        rank_of = {}
        for order in (dense_order, lexical_order):
            for rank, i in enumerate(order, 1):
                rank_of[i] = rank_of.get(i, 0.0) + 1.0 / (rrf_k + rank)
        k = min(k, len(fused))
        return [self._result(int(i), float(rank_of.get(int(i), 0.0))) for i in fused[:k]]

    def best_chunk_per_page(self, query: str, page_slugs) -> list[SearchResult]:
        """Best-matching chunk within each of ``page_slugs`` for the query.

        Used for graph-augmented retrieval: the graph proposes related pages, and
        this re-ranks each by the query so the added context stays relevant.
        Returned highest-score first.
        """
        wanted = set(page_slugs)
        if not wanted or not self.chunks:
            return []
        q = self.embedder.embed_query(query).astype(np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        scores = self.embeddings @ q
        best: dict[str, int] = {}  # page_slug -> chunk index of its best chunk
        for i, chunk in enumerate(self.chunks):
            if chunk.page_slug in wanted:
                if chunk.page_slug not in best or scores[i] > scores[best[chunk.page_slug]]:
                    best[chunk.page_slug] = i
        results = [self._result(i, float(scores[i])) for i in best.values()]
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def _result(self, i: int, score: float) -> SearchResult:
        chunk = self.chunks[i]
        return SearchResult(
            score=score,
            page_slug=chunk.page_slug,
            page_title=chunk.page_title,
            pdf_page_start=chunk.pdf_page_start,
            pdf_page_end=chunk.pdf_page_end,
            chunk_id=chunk.id,
            text=chunk.text,
        )
