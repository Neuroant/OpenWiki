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
    excerpts = "\n\n".join(
        f"[{s.marker}] ({s.page_title}, PDF p.{s.pdf_page_start}–{s.pdf_page_end})\n{s.text}"
        for s in sources
    )
    user = f"Excerpts:\n{excerpts}\n\nQuestion: {question}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


class RAGAgent:
    def __init__(self, index: SemanticIndex, chat: ChatModel, top_k: int = 5) -> None:
        self.index = index
        self.chat = chat
        self.top_k = top_k

    def retrieve(self, question: str, top_k: int | None = None) -> list[Source]:
        results = self.index.search(question, k=top_k or self.top_k)
        return [
            Source(
                marker=i + 1,
                page_slug=r.page_slug,
                page_title=r.page_title,
                pdf_page_start=r.pdf_page_start,
                pdf_page_end=r.pdf_page_end,
                chunk_id=r.chunk_id,
                score=r.score,
                text=r.text,
            )
            for i, r in enumerate(results)
        ]

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
