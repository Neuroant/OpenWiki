"""Lexical retrieval (BM25) + rank fusion — the lexical half of hybrid search.

Dense embeddings (bge-m3) blur *exact* terms the way a paraphrase can't help: rare
identifiers, acronyms (``RPPR``, ``USB``), and long German compounds. A classic **BM25**
signal over the same chunk texts catches those by literal term overlap, and **reciprocal
rank fusion** blends the two rankings without needing to reconcile their score scales.
Whether the blend actually beats dense-alone on this corpus is an empirical question the
eval harness answers (`owiki eval --hybrid`).

Pure + stdlib (+ NumPy, already a core dep): an inverted-index BM25 built from the chunk
texts at load, and a scale-free RRF. No new dependency, no model — the on-ethos lexical
counterpart to the embedder.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Iterable, Optional, Sequence

import numpy as np

_TOKEN = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens (Unicode ``\\w+``, so German umlauts/ß are kept). No stemming
    — the point is *exact*-term recall; stemming would blur it."""
    return _TOKEN.findall((text or "").lower())


class BM25:
    """Okapi BM25 over a fixed corpus, backed by an inverted index (postings) so a query
    only touches the documents that actually contain its terms."""

    def __init__(self, corpus_tokens: Sequence[Sequence[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.n = len(corpus_tokens)
        self.doc_len = np.array([len(d) for d in corpus_tokens], dtype=np.float32)
        self.avgdl = float(self.doc_len.mean()) if self.n else 0.0
        postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        df: Counter = Counter()
        for i, toks in enumerate(corpus_tokens):
            for term, freq in Counter(toks).items():
                postings[term].append((i, freq))
                df[term] += 1
        self.postings = postings
        # BM25 idf (the "+0.5" smoothed form; ``log(1+…)`` keeps it non-negative).
        self.idf = {t: math.log(1 + (self.n - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    @classmethod
    def build(cls, texts: Iterable[str], **kwargs) -> "BM25":
        return cls([tokenize(t) for t in texts], **kwargs)

    def scores(self, query: str) -> np.ndarray:
        """BM25 score of ``query`` against every document (shape ``(n,)``, zeros if empty)."""
        scores = np.zeros(self.n, dtype=np.float32)
        if not self.n or self.avgdl == 0:
            return scores
        for term in set(tokenize(query)):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i, freq in self.postings[term]:
                denom = freq + self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avgdl)
                scores[i] += idf * (freq * (self.k1 + 1)) / denom
        return scores


def reciprocal_rank_fusion(rankings: Sequence[Sequence], k: int = 60,
                           weights: Optional[Sequence[float]] = None) -> list:
    """Fuse several ranked lists (each best-first) into one order by **reciprocal rank
    fusion**: an item's score is ``Σ weight / (k + rank)`` across the lists it appears in
    (rank 1-based). Scale-free — no score normalization needed — and robust to one ranker
    being noisy. Returns the fused items, best first."""
    if weights is None:
        weights = [1.0] * len(rankings)
    fused: dict = defaultdict(float)
    for ranking, weight in zip(rankings, weights):
        for rank, item in enumerate(ranking, 1):
            fused[item] += weight / (k + rank)
    return sorted(fused, key=lambda item: -fused[item])
