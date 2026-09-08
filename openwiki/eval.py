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


_CITE = re.compile(r"\[(\d+)\]")


def community_grounding(cited, gt) -> dict:
    """Score a global answer's community citations against the ground-truth communities
    (those containing an expected page). ``cite_hit`` = cited any relevant theme;
    ``recall`` = fraction of relevant themes cited; ``precision`` = fraction of cited
    themes that are relevant (penalizes citing everything)."""
    cited, gt = set(cited), set(gt)
    return {
        "cite_hit": bool(cited & gt),
        "recall": len(cited & gt) / len(gt) if gt else 0.0,
        "precision": len(cited & gt) / len(cited) if cited else 0.0,
    }


def run_global_eval(items, communities, member_by_marker, chat, index=None,
                    judge=None, on_progress=None) -> dict:
    """Evaluate **global search** on a thematic question set. ``communities`` is the
    ``[(label, summary), …]`` list (marker = 1-based index); ``member_by_marker`` maps
    each marker to its member page slugs. For each item the ground-truth communities are
    those containing an expected page; we generate a global answer and score its
    ``[n]`` community citations (grounding). With ``judge`` (+ ``index``) we also generate
    a plain-RAG answer and get a position-balanced verdict — does the community layer beat
    local RAG on thematic questions? Backend-agnostic (chat/index injected)."""
    from .agent import RAGAgent
    from .graph.community import answer_global

    items = list(items)
    acc = {"cite_hit": 0.0, "recall": 0.0, "precision": 0.0}
    tally = {"Global": 0, "RAG": 0, "tie": 0}
    rag_agent = RAGAgent(index, chat, graph=None) if (judge is not None and index is not None) else None
    for i, item in enumerate(items):
        expected = set(item.expected)
        gt = {m for m, pages in member_by_marker.items() if expected & set(pages)}
        global_answer = answer_global(chat, item.question, communities)
        cited = {int(m) for m in _CITE.findall(global_answer)}
        g = community_grounding(cited, gt)
        for key in acc:
            acc[key] += g[key]
        if rag_agent is not None:
            rag_answer = rag_agent.answer(item.question).answer
            if i % 2 == 0:      # alternate A/B to cancel position bias
                verdict = judge_pairwise(judge, item.question, global_answer, rag_answer)
                winner = {"a": "Global", "b": "RAG", "tie": "tie"}[verdict]
            else:
                verdict = judge_pairwise(judge, item.question, rag_answer, global_answer)
                winner = {"a": "RAG", "b": "Global", "tie": "tie"}[verdict]
            tally[winner] += 1
        if on_progress:
            on_progress(i + 1, len(items))
    div = len(items) or 1
    return {
        "questions": len(items),
        "judged": judge is not None and index is not None,
        "grounding": {key: acc[key] / div for key in acc},
        "tally": tally,
    }


def run_answer_eval(items, index, graph, chat, top_k: int = 5, expand_k: int = 3,
                    judge=None, on_progress=None) -> dict:
    """Generate a RAG and a GraphRAG answer per item and score answer quality:
    citation **grounding** vs the ground-truth pages and, if ``judge`` is given, a
    position-balanced LLM pairwise verdict. Backend-agnostic (``index``/``graph``/
    ``chat`` injected); returns aggregate metrics. Shared by the CLI and the web job."""
    from .agent import RAGAgent

    items = list(items)
    rag_agent = RAGAgent(index, chat, top_k=top_k, graph=None)
    graph_agent = RAGAgent(index, chat, top_k=top_k, graph=graph, expand_k=expand_k)
    acc = {"RAG": {"hit": 0.0, "recall": 0.0}, "GraphRAG": {"hit": 0.0, "recall": 0.0}}
    tally = {"RAG": 0, "GraphRAG": 0, "tie": 0}
    for i, item in enumerate(items):
        answers = {"RAG": rag_agent.answer(item.question),
                   "GraphRAG": graph_agent.answer(item.question)}
        for name, ans in answers.items():
            g = grounding(ans, item.expected)
            acc[name]["hit"] += 1.0 if g["cite_hit"] else 0.0
            acc[name]["recall"] += g["expected_recall"]
        if judge is not None:
            if i % 2 == 0:   # alternate A/B assignment to cancel position bias
                verdict = judge_pairwise(judge, item.question, answers["RAG"].answer, answers["GraphRAG"].answer)
                winner = {"a": "RAG", "b": "GraphRAG", "tie": "tie"}[verdict]
            else:
                verdict = judge_pairwise(judge, item.question, answers["GraphRAG"].answer, answers["RAG"].answer)
                winner = {"a": "GraphRAG", "b": "RAG", "tie": "tie"}[verdict]
            tally[winner] += 1
        if on_progress:
            on_progress(i + 1, len(items))
    div = len(items) or 1
    return {
        "questions": len(items),
        "judged": judge is not None,
        "grounding": {name: {"cite_hit": acc[name]["hit"] / div,
                             "expected_recall": acc[name]["recall"] / div}
                      for name in ("RAG", "GraphRAG")},
        "tally": tally,
    }


# -- cross-session memory evaluation (Path B headline metric) ------------------
#
# The honest test of the remembered tier (docs/path-b-memory.md §7): does memory make
# the agent better in the *next* session? A scenario establishes facts in one or more
# earlier sessions, then asks a question that depends on them. We answer that probe under
# three conditions and compare — **cold** (no memory; should fail), **raw-log** (the raw
# transcripts pasted in), and **assembled** (decay-weighted `recall` → concentrated facts).
# Assembled should beat cold (it remembers) *and* raw-log (concentrated, not noisy).

@dataclass
class CrossSessionItem:
    name: str
    setup: list[str]        # earlier-session transcripts, chronological
    question: str           # asked in a later session
    expected: list[str]     # a correct answer contains all of these (case-insensitive)


def load_cross_session_set(path) -> list[CrossSessionItem]:
    """Read a cross-session scenario set from JSONL — one scenario per line:
    ``{"name", "setup": [transcript, …] | transcript, "question", "expected": [str,…] | str}``
    (``answer`` is accepted as an alias for ``expected``; ``#`` lines and blanks skipped)."""
    items: list[CrossSessionItem] = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        setup = obj.get("setup", [])
        if isinstance(setup, str):
            setup = [setup]
        expected = obj.get("expected", obj.get("answer", []))
        if isinstance(expected, str):
            expected = [expected]
        items.append(CrossSessionItem(
            name=str(obj.get("name") or f"scenario-{n}"),
            setup=[str(s) for s in setup],
            question=obj["question"],
            expected=[str(e) for e in expected],
        ))
    return items


def task_success(answer: str, expected: Iterable[str]) -> bool:
    """Objective cross-session task success: does the answer contain every expected
    substring (case-insensitive)? ``expected`` is usually a single key fact token."""
    low = (answer or "").lower()
    exp = [str(e).lower() for e in expected]
    return bool(exp) and all(e in low for e in exp)


_PROBE_SYSTEM = (
    "You are an assistant continuing your work with a user across multiple sessions. "
    "Answer the user's question using the remembered context from earlier sessions below. "
    "If that context does not contain the answer, say you don't know — do not guess. "
    "Answer in one short, specific sentence."
)


def build_probe_messages(question: str, context: str) -> list:
    """Messages for a probe question under a given memory ``context`` (``""`` → cold)."""
    if context.strip():
        user = f"{context}\n\nQuestion: {question}"
    else:
        user = f"(No remembered context is available.)\n\nQuestion: {question}"
    return [{"role": "system", "content": _PROBE_SYSTEM},
            {"role": "user", "content": user}]


def run_cross_session_eval(items, graph, embedder, chat, judge=None, recall_k: int = 10,
                           on_progress=None) -> dict:
    """The Path B headline metric — cross-session task success (path-b-memory.md §7).

    For each scenario: wipe memory, **remember** its setup sessions (capture → merge into
    ``graph``), then answer the probe under three conditions — **cold**, **raw-log**, and
    **assembled** (decay-weighted ``recall``). Scores objective ``task_success`` per
    condition; with ``judge`` adds a position-balanced *assembled vs raw-log* verdict — the
    honest test of whether concentrated memory beats replaying the log. ``graph`` must be a
    **writable** throwaway (scenarios are isolated via ``forget_all``). Backend-agnostic —
    ``graph``/``embedder``/``chat`` injected; capture/format reused from the memory tier."""
    from .graph.memory import capture_session, format_memory

    items = list(items)
    conditions = ("cold", "raw-log", "assembled")
    success = {c: 0.0 for c in conditions}
    tally = {"assembled": 0, "raw-log": 0, "tie": 0}
    details = []
    for i, item in enumerate(items):
        graph.forget_all()                                  # isolate this scenario
        for j, transcript in enumerate(item.setup):
            facts = capture_session(chat, transcript)
            graph.remember(f"{item.name}-s{j + 1}", facts, embedder)
        recalled = graph.recall(item.question, embedder, k=recall_k)
        contexts = {
            "cold": "",
            "raw-log": "Earlier sessions (raw transcript):\n" + "\n\n".join(item.setup),
            "assembled": format_memory(recalled),
        }
        answers = {c: _THINK.sub("", chat.chat(build_probe_messages(item.question, ctx))).strip()
                   for c, ctx in contexts.items()}
        for c in conditions:
            success[c] += 1.0 if task_success(answers[c], item.expected) else 0.0
        if judge is not None:
            if i % 2 == 0:      # alternate A/B to cancel position bias
                verdict = judge_pairwise(judge, item.question, answers["assembled"], answers["raw-log"])
                winner = {"a": "assembled", "b": "raw-log", "tie": "tie"}[verdict]
            else:
                verdict = judge_pairwise(judge, item.question, answers["raw-log"], answers["assembled"])
                winner = {"a": "raw-log", "b": "assembled", "tie": "tie"}[verdict]
            tally[winner] += 1
        details.append({
            "name": item.name, "question": item.question, "expected": item.expected,
            "recalled": len(recalled), "answers": answers,
            "success": {c: task_success(answers[c], item.expected) for c in conditions},
        })
        if on_progress:
            on_progress(i + 1, len(items))
    div = len(items) or 1
    return {
        "scenarios": len(items),
        "judged": judge is not None,
        "success": {c: success[c] / div for c in conditions},
        "tally": tally,
        "details": details,
    }
