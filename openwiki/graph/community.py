"""Consolidation layer: group the page graph into topical communities + summaries.

This is the "sleep pass" of the second-brain design (Path A) — a re-runnable
consolidation over an *already built* graph. It borrows Microsoft GraphRAG's two
useful ideas, **community detection** + **LLM community summaries**, but native,
dependency-free, and against a *local* chat model — so it fits the project's
stdlib/local-first ethos and stays cheap (a handful of communities, not one call
per page). The summaries then power **global search** (``ask --global``): thematic
"what are the main themes / how does X relate across the whole corpus" questions
that chunk-level RAG can't answer.

Pure module: like ``references``/``entities`` it imports **no Kuzu**. Community
detection is a compact, deterministic single-level Louvain (weighted modularity);
the summary + global-answer helpers take an injected ``chat`` model, so they are
unit-testable offline with a fake.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

# Weights when folding the typed page graph into one undirected weighted graph for
# community detection. SIMILAR_TO contributes its cosine score directly.
REFERENCE_WEIGHT = 1.0
SHARED_ENTITY_WEIGHT = 0.5

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def detect_communities(edges, nodes: Optional[Iterable] = None,
                       max_passes: int = 30) -> dict:
    """Partition nodes into communities by weighted modularity (single-level Louvain).

    ``edges`` is an iterable of ``(a, b, weight)`` undirected pairs (weight > 0);
    ``nodes`` optionally seeds isolated vertices. Returns ``{node: community_id}``
    with contiguous ids ordered by community size (largest first). Deterministic:
    nodes are processed in sorted order and ties break to the lowest community id.
    """
    adj: dict = {}

    def ensure(n):
        if n not in adj:
            adj[n] = {}

    for n in (nodes or []):
        ensure(n)
    for a, b, w in edges:
        if a == b or w <= 0:
            continue
        ensure(a)
        ensure(b)
        adj[a][b] = adj[a].get(b, 0.0) + w
        adj[b][a] = adj[b].get(a, 0.0) + w

    if len(adj) < 2:
        return {n: 0 for n in adj}

    k = {n: sum(nb.values()) for n, nb in adj.items()}   # weighted degree
    m2 = sum(k.values())                                  # = 2m
    nodes_sorted = sorted(adj)
    if m2 == 0:                                           # no edges → all singletons
        return {n: i for i, n in enumerate(nodes_sorted)}

    comm = {n: i for i, n in enumerate(nodes_sorted)}
    sigma_tot = {i: k[n] for n, i in comm.items()}       # summed degree per community

    for _ in range(max_passes):
        moved = False
        for n in nodes_sorted:
            ci = comm[n]
            ki = k[n]
            wcomm: dict = {}                              # weight from n into each community
            for nb, w in adj[n].items():
                c = comm[nb]
                wcomm[c] = wcomm.get(c, 0.0) + w
            sigma_tot[ci] -= ki                           # pull n out of its community
            best_c = ci
            best_gain = wcomm.get(ci, 0.0) - sigma_tot[ci] * ki / m2
            for c in sorted(wcomm):                       # deterministic scan
                gain = wcomm[c] - sigma_tot[c] * ki / m2
                if gain > best_gain + 1e-12 or (gain > best_gain - 1e-12 and c < best_c):
                    best_c, best_gain = c, gain
            sigma_tot[best_c] += ki
            if best_c != ci:
                comm[n] = best_c
                moved = True
        if not moved:
            break

    # relabel: largest community first; deterministic tie-break by smallest member
    members: dict = {}
    for n, c in comm.items():
        members.setdefault(c, []).append(n)
    order = sorted(members, key=lambda c: (-len(members[c]), min(members[c])))
    remap = {c: i for i, c in enumerate(order)}
    return {n: remap[c] for n, c in comm.items()}


# -- LLM summaries + global answer (chat model injected — offline-testable) -----

SUMMARY_SYSTEM = (
    "You label and summarize a cluster of related documentation pages. Reply in the "
    "language of the pages, in exactly this shape:\n"
    "Thema: <a short 2–5 word title naming the concept the pages share>\n"
    "<a 2–4 sentence summary of that theme and the key concepts covered>\n"
    "The theme must name the shared *concept*, not echo a page title. Ground it "
    "strictly in the given pages; do not invent. No preamble, no bullet list."
)


def build_summary_messages(members) -> list:
    body = "\n\n".join(f"- {title}: {(snippet or '').strip()}" for title, snippet in members)
    user = f"Member pages:\n{body}\n\nGive the theme title and summary."
    return [{"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": user}]


# The model is asked to lead with a "Thema: <title>" line (also accept a few
# synonyms / light markdown around it); everything after is the summary body.
_LABEL_LINE = re.compile(
    r"^[#*_\s]*(?:thema|theme|titel|title|label)\s*[:\-–]\s*(.+?)\s*$", re.IGNORECASE)


def parse_summary(raw: str) -> tuple:
    """Split a summary reply into ``(label, summary)``. If the model omits the
    ``Thema:`` line, ``label`` is ``""`` (the caller supplies a fallback)."""
    text = _THINK.sub("", raw).strip()
    lines = text.splitlines()
    label = ""
    if lines:
        m = _LABEL_LINE.match(lines[0])
        if m:
            label = m.group(1).strip().strip('*_"# ').strip()
            lines = lines[1:]
    summary = "\n".join(lines).strip() or text
    return label, summary


def summarize_community(chat, members, fallback_label: str = "") -> tuple:
    """One LLM call → ``(label, summary)`` for a community. Falls back to
    ``fallback_label`` (e.g. the hub page's title) when the model emits no theme line."""
    label, summary = parse_summary(chat.chat(build_summary_messages(members)))
    return (label or fallback_label, summary)


# -- B5 consolidation: summarize a cluster of *remembered facts* (Path B "sleep") -----

FACT_SUMMARY_SYSTEM = (
    "You label and summarize a cluster of related facts an assistant has remembered across "
    "sessions. Reply in the language of the facts, in exactly this shape:\n"
    "Thema: <a short 2–5 word title naming what the facts are about>\n"
    "<a 2–4 sentence summary of the theme and what is currently known>\n"
    "Name the shared subject/theme, not a single fact. Ground it strictly in the given "
    "facts; do not invent. No preamble, no bullet list."
)


def build_fact_summary_messages(facts) -> list:
    body = "\n".join(f"- {t}" for t in facts)
    user = f"Remembered facts:\n{body}\n\nGive the theme title and summary."
    return [{"role": "system", "content": FACT_SUMMARY_SYSTEM},
            {"role": "user", "content": user}]


def summarize_facts(chat, facts, fallback_label: str = "") -> tuple:
    """One LLM call → ``(label, summary)`` for a cluster of remembered facts (B5). Reuses
    the ``Thema:``-line parsing; falls back to ``fallback_label`` if the model omits it."""
    label, summary = parse_summary(chat.chat(build_fact_summary_messages(facts)))
    return (label or fallback_label, summary)


GLOBAL_SYSTEM = (
    "You answer high-level, thematic questions about a documentation corpus using "
    "ONLY the community summaries provided. Each summary describes one topical "
    "cluster of pages. Synthesize across the relevant clusters; if the summaries do "
    "not cover the question, say so plainly. Answer in the question's language and "
    "cite the clusters you use with bracketed numbers like [1], [2]."
)


def build_global_messages(question: str, communities) -> list:
    blocks = "\n\n".join(f"[{i}] {label}\n{(summary or '').strip()}"
                         for i, (label, summary) in enumerate(communities, 1))
    user = f"Community summaries:\n{blocks}\n\nQuestion: {question}"
    return [{"role": "system", "content": GLOBAL_SYSTEM},
            {"role": "user", "content": user}]


def answer_global(chat, question: str, communities) -> str:
    """Answer a thematic question from the community summaries (global search)."""
    raw = chat.chat(build_global_messages(question, communities))
    return _THINK.sub("", raw).strip()
