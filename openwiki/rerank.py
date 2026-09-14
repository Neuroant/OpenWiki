"""LLM re-ranking of retrieval candidates (retrieval quality — roadmap Direction A).

A second, precision-oriented pass over a **wider** candidate pool: one chat call asks the
local model to order the passages by relevance to the query, and we keep the top few. Cheap
(a single call), on-ethos (reuses the existing `ChatModel`/Ollama — no cross-encoder
dependency), and **measurable** (`owiki eval --rerank` scores it against plain semantic RAG
over the same final budget). The open question — like GraphRAG — is whether it actually
beats spending that budget on more semantic hits; the eval harness answers it.

Pure + chat-injected + fake-testable: the parsing is deterministic and the model is a
protocol. **Robust by construction** — strip ``<think>``, parse the first bracketed ordering,
append any omitted passages in their original order, and fall back to the input order on any
parse failure or model error, so a bad rank never drops, duplicates, or reorders-to-garbage
a candidate (at worst it's a no-op).
"""

from __future__ import annotations

import re

from .llm import ChatModel, Message

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_INTS = re.compile(r"\d+")

RERANK_SYSTEM = (
    "You re-rank documentation passages by how well each one helps answer a question.\n"
    "Output ONLY a JSON array of the passage numbers, most relevant first — e.g. [3, 1, 2].\n"
    "Include every passage number exactly once. No prose, no explanation."
)


def build_rerank_messages(query: str, texts, snippet_chars: int = 500) -> list[Message]:
    """The ranking prompt: the query + the candidate passages, numbered 1..N (each
    whitespace-collapsed and truncated to ``snippet_chars`` to bound tokens)."""
    lines = []
    for i, text in enumerate(texts, 1):
        snippet = " ".join(str(text).split())
        if len(snippet) > snippet_chars:
            snippet = snippet[:snippet_chars] + "…"
        lines.append(f"[{i}] {snippet}")
    user = f"Question: {query}\n\nPassages:\n" + "\n".join(lines) + "\n\nRanking:"
    return [{"role": "system", "content": RERANK_SYSTEM}, {"role": "user", "content": user}]


def parse_order(raw: str, n: int) -> list[int]:
    """Parse a model's 1-based ranking into a **0-based permutation of ``range(n)``**.

    Prefers the first bracketed list (``[3, 1, 2]``); else reads any integers in the text.
    Dedups, drops out-of-range numbers, then appends any omitted indices in their original
    order — so the result is always a full, valid permutation (identity on total failure)."""
    raw = _THINK.sub("", raw or "")
    match = re.search(r"\[([^\]]*)\]", raw)
    span = match.group(1) if match else raw
    seen: set[int] = set()
    order: list[int] = []
    for token in _INTS.findall(span):
        idx = int(token) - 1
        if 0 <= idx < n and idx not in seen:
            seen.add(idx)
            order.append(idx)
    for i in range(n):            # append any passage the model omitted, stable
        if i not in seen:
            order.append(i)
    return order


def rerank_order(query: str, texts, chat: ChatModel, snippet_chars: int = 500) -> list[int]:
    """Return a permutation of ``range(len(texts))`` — the passages ordered most→least
    relevant to ``query`` via one chat call. Fewer than 2 passages, an empty query, or any
    model error → identity order (and no call is made)."""
    n = len(texts)
    if n < 2 or not (query or "").strip():
        return list(range(n))
    try:
        raw = chat.chat(build_rerank_messages(query, texts, snippet_chars))
    except Exception:             # a re-rank failure must never break retrieval
        return list(range(n))
    return parse_order(raw, n)
