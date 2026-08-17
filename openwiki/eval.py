"""Retrieval evaluation for the wiki.

Measures whether the retriever surfaces the *right* page(s) for a set of
ground-truth questions, and lets you compare plain semantic retrieval (**RAG**)
with graph-augmented retrieval (**GraphRAG**) on the same questions.

The core is pure and backend-agnostic: :func:`evaluate` takes a ``retrieve``
callable (``question -> ranked page slugs``) and an eval set, and returns
standard ranking metrics (MRR, hit@k, recall@k). The CLI (``openwiki eval``)
plugs in the real semantic + graph retrievers; tests plug in fakes. No Ollama or
Kuzu imports here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .agent import _EXPAND_RELS  # canonical expansion edges (agent imports no Kuzu)


@dataclass
class EvalItem:
    question: str
    expected: list[str]          # ground-truth relevant page slugs (any-of)


def load_eval_set(path) -> list[EvalItem]:
    """Read an eval set from JSONL: one ``{"question": ..., "pages": [...]}`` per
    line (``expected`` is accepted as an alias; ``#`` lines and blanks skipped)."""
    items: list[EvalItem] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        expected = obj.get("pages", obj.get("expected", []))
        if isinstance(expected, str):
            expected = [expected]
        items.append(EvalItem(question=obj["question"], expected=list(expected)))
    return items


# -- ranking metrics (all operate on a ranked list of page slugs) --------------

def reciprocal_rank(ranked: Sequence[str], expected: Iterable[str]) -> float:
    exp = set(expected)
    for i, slug in enumerate(ranked):
        if slug in exp:
            return 1.0 / (i + 1)
    return 0.0


def hit_at_k(ranked: Sequence[str], expected: Iterable[str], k: int) -> float:
    exp = set(expected)
    return 1.0 if any(s in exp for s in ranked[:k]) else 0.0


def recall_at_k(ranked: Sequence[str], expected: Iterable[str], k: int) -> float:
    exp = set(expected)
    if not exp:
        return 0.0
    return len(exp & set(ranked[:k])) / len(exp)


def _mean(xs: Iterable[float]) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


@dataclass
class ItemResult:
    question: str
    expected: list[str]
    ranked: list[str]
    rr: float
    hit: float
    recall: float


@dataclass
class EvalReport:
    k: int
    items: list[ItemResult] = field(default_factory=list)

    @property
    def mrr(self) -> float:
        return _mean(r.rr for r in self.items)

    @property
    def hit_rate(self) -> float:
        return _mean(r.hit for r in self.items)

    @property
    def recall(self) -> float:
        return _mean(r.recall for r in self.items)

    @property
    def misses(self) -> list[ItemResult]:
        """Questions where no expected page appeared in the top-k."""
        return [r for r in self.items if r.hit == 0.0]


def evaluate(items: Sequence[EvalItem], retrieve: Callable[[str], list[str]],
             k: int) -> EvalReport:
    """Run ``retrieve`` over every item and score its ranked page slugs at ``k``."""
    report = EvalReport(k=k)
    for item in items:
        ranked = list(retrieve(item.question))
        report.items.append(ItemResult(
            question=item.question, expected=item.expected, ranked=ranked,
            rr=reciprocal_rank(ranked, item.expected),
            hit=hit_at_k(ranked, item.expected, k),
            recall=recall_at_k(ranked, item.expected, k),
        ))
    return report


# -- retrievers (dependency-injected: take an index/graph, import no Kuzu/Ollama) --

def semantic_pages(index, question: str, n: int) -> list[str]:
    """The top ``n`` distinct page slugs for a query, by semantic rank."""
    ranked: list[str] = []
    for result in index.search(question, k=max(n * 6, 30)):
        if result.page_slug not in ranked:
            ranked.append(result.page_slug)
            if len(ranked) >= n:
                break
    return ranked


def graph_expand(index, graph, seeds: list[str], question: str, expand_k: int) -> list[str]:
    """Pages reachable from ``seeds`` along expansion edges, re-ranked by the query."""
    candidates: list[str] = []
    for slug in seeds:
        try:
            neighborhood = graph.neighborhood(slug)
        except KeyError:
            continue
        for node in neighborhood["nodes"]:
            if (node["rel"] in _EXPAND_RELS and node["slug"] not in seeds
                    and node["slug"] not in candidates):
                candidates.append(node["slug"])
    if not candidates:
        return []
    return [r.page_slug for r in index.best_chunk_per_page(question, candidates)][:expand_k]


def make_retrievers(index, graph, top_k: int, expand_k: int):
    """Return ``(rag, graphrag)`` retrieve callables over the same ``top_k+expand_k``
    budget: RAG = top semantic pages; GraphRAG = ``top_k`` seeds + ``expand_k``
    graph-expanded. ``graphrag`` is ``None`` when no graph is available."""
    budget = top_k + expand_k

    def rag(question: str) -> list[str]:
        return semantic_pages(index, question, budget)

    graphrag = None
    if graph is not None and expand_k > 0:
        def graphrag(question: str) -> list[str]:      # noqa: E731 (named for the report)
            seeds = semantic_pages(index, question, top_k)
            return seeds + graph_expand(index, graph, seeds, question, expand_k)

    return rag, graphrag


# -- answer-quality evaluation -------------------------------------------------

def cited_page_slugs(answer) -> set:
    """The wiki pages an answer actually cited — its ``[n]`` markers resolved to the
    ``Source`` pages they refer to (a :class:`~openwiki.agent.RAGAnswer`)."""
    markers = answer.cited_markers()
    return {s.page_slug for s in answer.sources if s.marker in markers}


def grounding(answer, expected: Iterable[str]) -> dict:
    """Objective answer grounding vs the ground-truth pages: did the answer *cite*
    an expected page, and what fraction of them?"""
    cited = cited_page_slugs(answer)
    exp = set(expected)
    hit = bool(cited & exp)
    recall = len(cited & exp) / len(exp) if exp else 0.0
    return {"cited": sorted(cited), "cite_hit": hit, "expected_recall": recall}


_JUDGE_SYSTEM = (
    "You are an impartial judge comparing two answers, A and B, to the same question "
    "about a documentation wiki. Choose the answer that is more accurate, specific, "
    "and complete. Ignore length and formatting. Reply with exactly one token — "
    "`A`, `B`, or `tie` — and nothing else."
)
_VERDICT = re.compile(r"\b(a|b|tie)\b", re.IGNORECASE)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def judge_pairwise(chat, question: str, answer_a: str, answer_b: str) -> str:
    """Ask a judge model which of two answers is better → ``"a"`` / ``"b"`` / ``"tie"``.
    Callers should balance which system is A vs B across questions to cancel position bias."""
    user = (f"Question: {question}\n\n"
            f"Answer A:\n{answer_a or '(no answer)'}\n\n"
            f"Answer B:\n{answer_b or '(no answer)'}\n\n"
            "Which answer is better — A, B, or tie?")
    reply = _THINK.sub("", chat.chat(
        [{"role": "system", "content": _JUDGE_SYSTEM}, {"role": "user", "content": user}]))
    match = _VERDICT.search(reply)
    return match.group(1).lower() if match else "tie"
