"""Tests for chunking and the semantic search index.

A deterministic bag-of-words ``FakeEmbedder`` stands in for Ollama so these
tests are fully offline. A separate smoke test exercises the real Ollama backend
only if a server is reachable.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import numpy as np
import pytest

from openwiki.chunking import chunk_text, chunk_wiki, normalize_text
from openwiki.search import SemanticIndex
from openwiki.wiki import Wiki, WikiPage


class FakeEmbedder:
    """Bag-of-words vectors over a fixed vocabulary — deterministic, offline."""

    VOCAB = ["lautstarke", "display", "midi", "sample", "effekt"]
    name = "fake:bow"

    def _vec(self, text: str) -> np.ndarray:
        low = text.lower()
        v = np.array([float(low.count(w)) for w in self.VOCAB], dtype=np.float32)
        return v if v.any() else v + 1e-6

    def embed_documents(self, texts):
        return np.vstack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


def _wiki() -> Wiki:
    return Wiki(
        title="Manual",
        pages=[
            WikiPage(slug="000-vol", title="Lautstärke", level=1, order=0,
                     pdf_page_start=1, pdf_page_end=1,
                     text="Die lautstarke regeln Sie mit dem MASTER VOLUME. lautstarke lautstarke."),
            WikiPage(slug="001-midi", title="MIDI", level=1, order=1,
                     pdf_page_start=2, pdf_page_end=2,
                     text="midi kanaele und midi verbindungen. midi midi."),
            WikiPage(slug="002-fx", title="Effekte", level=1, order=2,
                     pdf_page_start=3, pdf_page_end=3,
                     text="effekt routing und effekt presets. effekt effekt."),
        ],
    )


# -- chunking ---------------------------------------------------------------

def test_chunk_text_windows_and_overlap():
    chunks = chunk_text("one two three four five", size_words=2, overlap_words=1)
    assert chunks == ["one two", "two three", "three four", "four five"]


def test_chunk_text_empty():
    assert chunk_text("   ") == []


def test_normalize_collapses_pdf_linebreaks():
    assert normalize_text("a\nb\n\nc\nd") == "a b\nc d"


def test_chunk_wiki_tags_provenance():
    chunks = chunk_wiki(_wiki(), size_words=50, overlap_words=10)
    assert {c.page_slug for c in chunks} == {"000-vol", "001-midi", "002-fx"}
    first = chunks[0]
    assert first.id == "000-vol#0"
    assert first.page_title == "Lautstärke"


# -- index / search ---------------------------------------------------------

@pytest.fixture
def index() -> SemanticIndex:
    return SemanticIndex.build(_wiki(), FakeEmbedder(), size_words=50, overlap_words=10)


def test_search_ranks_relevant_page_first(index):
    results = index.search("wie regele ich die lautstarke", k=3)
    assert results[0].page_slug == "000-vol"
    assert results[0].score > results[-1].score


def test_search_finds_midi(index):
    assert index.search("midi verbindung", k=1)[0].page_slug == "001-midi"


def test_embeddings_are_normalized(index):
    norms = np.linalg.norm(index.embeddings, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


def test_best_chunk_per_page(index):
    results = index.best_chunk_per_page("effekt presets", ["001-midi", "002-fx"])
    assert {r.page_slug for r in results} == {"001-midi", "002-fx"}  # one per page
    assert results[0].page_slug == "002-fx"                          # best match first


def test_best_chunk_per_page_empty(index):
    assert index.best_chunk_per_page("anything", []) == []


def test_save_and_load_round_trip(tmp_path, index):
    index.save(tmp_path)
    assert (tmp_path / "embeddings.npy").is_file()
    assert (tmp_path / "index.json").is_file()

    loaded = SemanticIndex.load(tmp_path, embedder=FakeEmbedder())
    assert loaded.model_name == "fake:bow"
    assert len(loaded.chunks) == len(index.chunks)
    assert loaded.search("effekt presets", k=1)[0].page_slug == "002-fx"


# -- real backend smoke test -----------------------------------------------

def _ollama_up(host="http://localhost:11434") -> bool:
    try:
        urllib.request.urlopen(f"{host}/api/version", timeout=2)
        return True
    except (urllib.error.URLError, OSError):
        return False


@pytest.mark.skipif(not _ollama_up(), reason="Ollama server not reachable")
def test_ollama_embedder_smoke():
    from openwiki.embeddings import OllamaEmbedder

    try:
        vecs = OllamaEmbedder(model="bge-m3").embed_documents(["Hallo", "Welt"])
    except RuntimeError as exc:  # model not pulled yet
        pytest.skip(str(exc))
    assert vecs.shape[0] == 2 and vecs.shape[1] > 0
