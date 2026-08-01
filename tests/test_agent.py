"""Tests for the RAG agent.

Uses a deterministic ``FakeEmbedder`` (retrieval) and ``FakeChat`` (generation)
so the whole pipeline is exercised offline. A real Ollama chat test runs only if
a server is reachable.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import numpy as np
import pytest

from openwiki.agent import RAGAgent, build_messages
from openwiki.search import SemanticIndex
from openwiki.wiki import Wiki, WikiPage


class FakeEmbedder:
    VOCAB = ["lautstarke", "midi", "effekt"]
    name = "fake:bow"

    def _vec(self, text: str) -> np.ndarray:
        low = text.lower()
        v = np.array([float(low.count(w)) for w in self.VOCAB], dtype=np.float32)
        return v if v.any() else v + 1e-6

    def embed_documents(self, texts):
        return np.vstack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


class FakeChat:
    """Records the prompt it received and returns a canned, citation-bearing answer."""

    name = "fake:chat"

    def __init__(self, reply="<think>reasoning</think>Regeln Sie die Lautstärke am MASTER VOLUME [1]."):
        self.reply = reply
        self.last_messages = None

    def chat(self, messages):
        self.last_messages = list(messages)
        return self.reply


def _index() -> SemanticIndex:
    wiki = Wiki(
        title="Manual",
        pages=[
            WikiPage(slug="000-vol", title="Lautstärke", level=1, order=0,
                     pdf_page_start=1, pdf_page_end=1,
                     text="Die lautstarke regeln Sie mit dem MASTER VOLUME. lautstarke lautstarke."),
            WikiPage(slug="001-midi", title="MIDI", level=1, order=1,
                     pdf_page_start=2, pdf_page_end=2,
                     text="midi kanaele und midi verbindungen. midi midi."),
        ],
    )
    return SemanticIndex.build(wiki, FakeEmbedder(), size_words=50, overlap_words=10)


@pytest.fixture
def agent() -> RAGAgent:
    return RAGAgent(_index(), FakeChat(), top_k=2)


def test_retrieve_orders_by_relevance(agent):
    sources = agent.retrieve("wie regele ich die lautstarke")
    assert sources[0].page_slug == "000-vol"
    assert sources[0].marker == 1


def test_build_messages_contains_context_and_question():
    from openwiki.agent import Source

    src = Source(marker=1, page_slug="000-vol", page_title="Lautstärke",
                 pdf_page_start=1, pdf_page_end=1, chunk_id="000-vol#0",
                 score=0.9, text="MASTER VOLUME regelt die Lautstärke.")
    messages = build_messages("Wie laut?", [src])
    assert messages[0]["role"] == "system"
    user = messages[1]["content"]
    assert "[1]" in user and "MASTER VOLUME" in user and "Wie laut?" in user


def test_answer_strips_think_and_keeps_sources(agent):
    result = agent.answer("wie stelle ich die lautstarke ein")
    assert "<think>" not in result.answer
    assert result.answer.startswith("Regeln Sie die Lautstärke")
    assert result.model == "fake:chat"
    assert len(result.sources) == 2
    # the prompt the model saw carried the retrieved context
    assert "MASTER VOLUME" in agent.chat.last_messages[1]["content"]


def test_cited_markers_parses_answer(agent):
    result = agent.answer("lautstarke")
    assert result.cited_markers() == {1}


def test_answer_without_results_is_graceful():
    empty = SemanticIndex(FakeEmbedder(), [], np.zeros((0, 3), dtype=np.float32), "fake:bow")
    result = RAGAgent(empty, FakeChat()).answer("irgendwas")
    assert result.sources == []
    assert "keine" in result.answer.lower()


# -- real backend smoke test -----------------------------------------------

def _ollama_up(host="http://localhost:11434") -> bool:
    try:
        urllib.request.urlopen(f"{host}/api/version", timeout=2)
        return True
    except (urllib.error.URLError, OSError):
        return False


@pytest.mark.skipif(not _ollama_up(), reason="Ollama server not reachable")
def test_ollama_chat_smoke():
    from openwiki.llm import OllamaChat

    chat = OllamaChat(model="qwen3:30b-a3b-instruct-2507-q4_K_M")
    try:
        reply = chat.chat([{"role": "user", "content": "Antworte mit genau dem Wort: OK"}])
    except RuntimeError as exc:  # model not pulled
        pytest.skip(str(exc))
    assert isinstance(reply, str) and reply.strip()
