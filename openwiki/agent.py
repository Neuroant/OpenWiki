"""Retrieval-augmented generation over the wiki.

Retrieves the most relevant chunks from a :class:`SemanticIndex`, feeds them to a
chat model as numbered excerpts, and returns an answer with ``[n]`` citations
that map back to wiki pages. Grounding is enforced by the system prompt (answer
only from the excerpts); provenance is preserved so every claim is traceable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .llm import ChatModel, Message
from .search import SemanticIndex

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_CITATION = re.compile(r"\[(\d+)\]")

SYSTEM_PROMPT = (
    "You are a precise assistant that answers questions using ONLY the "
    "documentation excerpts provided by the user.\n"
    "Rules:\n"
    "- Answer in the same language as the question.\n"
    "- Use only the excerpts. If they do not contain the answer, say so plainly "
    "and do not invent details.\n"
    "- Cite the excerpts you rely on with bracketed numbers like [1], [2].\n"
    "- Be concise, and keep names, buttons, and parameter values verbatim."
)


@dataclass
class Source:
    marker: int  # 1-based citation number shown to the model
    page_slug: str
    page_title: str
    pdf_page_start: int
    pdf_page_end: int
    chunk_id: str
    score: float
    text: str
    kind: str = "seed"  # "seed" (semantic hit) or "related" (graph-expanded)


@dataclass
class RAGAnswer:
    question: str
    answer: str
    sources: list[Source]
    model: str
    messages: list[Message] = field(default_factory=list)

    def cited_markers(self) -> set[int]:
        """Citation numbers the model actually referenced in its answer."""
        return {int(m) for m in _CITATION.findall(self.answer)}


def build_messages(question: str, sources: list[Source]) -> list[Message]:
    def fmt(s: Source) -> str:
        tag = " · related via graph" if s.kind == "related" else ""
        return (f"[{s.marker}] ({s.page_title}, PDF p.{s.pdf_page_start}–{s.pdf_page_end}{tag})\n"
                f"{s.text}")

    excerpts = "\n\n".join(fmt(s) for s in sources)
    user = f"Excerpts:\n{excerpts}\n\nQuestion: {question}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


# Edges worth expanding along: cross-refs, semantic neighbors, shared concepts,
# typed entity relations (Direction B), and reinforced (usage-memory) connections.
_EXPAND_RELS = ("references", "referenced_by", "similar", "shared_entity", "relation", "reinforced")


class RAGAgent:
    def __init__(self, index: SemanticIndex, chat: ChatModel, top_k: int = 5,
                 graph=None, expand_k: int = 3, rerank: bool = False,
                 rerank_pool: int = 20, hybrid: bool = False) -> None:
        self.index = index
        self.chat = chat
        self.top_k = top_k
        self.graph = graph        # optional GraphStore -> graph-augmented retrieval
        self.expand_k = expand_k  # how many related pages to add
        self.rerank = rerank      # LLM re-rank a wider seed pool down to top_k
        self.rerank_pool = rerank_pool
        self.hybrid = hybrid      # fuse BM25 + dense for the seed search (search_hybrid)

    @staticmethod
    def _source(result, marker: int, kind: str) -> Source:
        return Source(
            marker=marker, page_slug=result.page_slug, page_title=result.page_title,
            pdf_page_start=result.pdf_page_start, pdf_page_end=result.pdf_page_end,
            chunk_id=result.chunk_id, score=result.score, text=result.text, kind=kind,
        )

    def retrieve(self, question: str, top_k: int | None = None) -> list[Source]:
        k = top_k or self.top_k
        seeds = self._seed(question, k)
        sources = [self._source(r, i + 1, "seed") for i, r in enumerate(seeds)]
        if self.graph is None or self.expand_k <= 0 or not sources:
            return sources
        sources += self._expand(question, sources)
        return sources

    def _base_search(self, question: str, k: int):
        """Seed search — hybrid (BM25 + dense) if enabled, else pure dense cosine."""
        if self.hybrid:
            return self.index.search_hybrid(question, k=k)
        return self.index.search(question, k=k)

    def _seed(self, question: str, k: int):
        """The top-``k`` seeds — optionally after an LLM re-rank of a wider pool (fetch
        ``rerank_pool``, re-rank by relevance, keep ``k``). Both hybrid + re-rank are off
        by default; they compose (re-rank orders the hybrid pool)."""
        if not self.rerank:
            return self._base_search(question, k)
        pool = self._base_search(question, max(self.rerank_pool, k))
        if len(pool) <= k:
            return pool
        from .rerank import rerank_order
        order = rerank_order(question, [r.text for r in pool], self.chat)
        return [pool[i] for i in order][:k]

    def _expand(self, question: str, seeds: list[Source]) -> list[Source]:
        """Pull in graph-connected pages (references/similar), re-ranked by query."""
        seed_slugs = []
        for s in seeds:
            if s.page_slug not in seed_slugs:
                seed_slugs.append(s.page_slug)

        candidates = set()
        for slug in seed_slugs:
            try:
                neighborhood = self.graph.neighborhood(slug)
            except KeyError:
                continue
            for node in neighborhood["nodes"]:
                if node["rel"] in _EXPAND_RELS and node["slug"] not in seed_slugs:
                    candidates.add(node["slug"])
        if not candidates:
            return []

        related = self.index.best_chunk_per_page(question, candidates)[: self.expand_k]
        # Hebbian usage memory (B1): record the seed→related pairs we actually pulled in.
        # On a writable graph (serve/chat) this reinforces immediately; on a read-only
        # `ask`/MCP with logging enabled it appends to the usage log for a later fold-in
        # (`GraphStore.fold_usage`). Best-effort — a memory write never breaks retrieval.
        if related and seed_slugs:
            self.graph.record_usage([(seed_slugs[0], r.page_slug) for r in related])
        base = len(seeds)
        return [self._source(r, base + i + 1, "related") for i, r in enumerate(related)]

    def answer(self, question: str, top_k: int | None = None) -> RAGAnswer:
        sources = self.retrieve(question, top_k)
        if not sources:
            return RAGAnswer(
                question, "Im Index wurden keine passenden Inhalte gefunden.",
                [], self.chat.name,
            )
        messages = build_messages(question, sources)
        raw = self.chat.chat(messages)
        answer = _THINK.sub("", raw).strip()
        return RAGAnswer(
            question=question, answer=answer, sources=sources,
            model=self.chat.name, messages=messages,
        )
