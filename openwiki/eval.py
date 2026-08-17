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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence


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
