"""Tests for lexical (BM25) retrieval + RRF + hybrid search. Pure + a fake embedder."""

from __future__ import annotations

import numpy as np

from openwiki.lexical import BM25, reciprocal_rank_fusion, tokenize


# -- tokenize ------------------------------------------------------------------

def test_tokenize_keeps_umlauts_splits_punctuation():
    assert tokenize("Lautstärke RPPR USB-Ethernet") == ["lautstärke", "rppr", "usb", "ethernet"]
    assert tokenize("") == [] and tokenize(None) == []


# -- BM25 ----------------------------------------------------------------------

def test_bm25_ranks_exact_term_doc_top():
    docs = ["der arpeggiator erzeugt muster", "rppr realtime pattern play record",
            "audio aufnahme im sequencer"]
    bm = BM25.build(docs)
    scores = bm.scores("was ist rppr")
    assert int(scores.argmax()) == 1               # only doc 1 has "rppr"
    assert scores[0] == 0 and scores[2] == 0


def test_bm25_unknown_term_scores_zero():
    bm = BM25.build(["alpha beta", "gamma delta"])
    assert bm.scores("xyzzy").sum() == 0.0


def test_bm25_rare_term_outweighs_common():
    # "common" appears everywhere (low idf); "rare" in one doc (high idf)
    docs = ["common rare", "common word", "common thing", "common stuff"]
    bm = BM25.build(docs)
    scored = bm.scores("common rare")
    assert int(scored.argmax()) == 0               # the doc with the rare term wins


# -- reciprocal rank fusion ----------------------------------------------------

def test_rrf_blends_two_rankings():
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["c", "b", "a"]])
    assert set(fused) == {"a", "b", "c"}           # all present, valid order


def test_rrf_weights_bias_toward_a_ranking():
    assert reciprocal_rank_fusion([["a", "b"], ["b", "a"]], weights=[3.0, 1.0])[0] == "a"


def test_rrf_rewards_agreement():
    # 'x' is top of both lists → must win over items each list ranks once
    assert reciprocal_rank_fusion([["x", "a", "b"], ["x", "b", "a"]])[0] == "x"


# -- hybrid search over a real SemanticIndex (fake embedder) -------------------

class _FakeEmbedder:
    """Bag-of-words over a fixed vocab — deliberately blind to the rare term, so dense
    retrieval is uninformative for it and only BM25 can find the right page."""
    VOCAB = ["alpha", "beta"]
    name = "fake:bow"

    def _vec(self, text):
        low = text.lower()
        v = np.array([float(low.count(w)) for w in self.VOCAB], dtype=np.float32)
        return v if v.any() else v          # all-zero when no vocab term is present

    def embed_documents(self, texts):
        return np.vstack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


def _index():
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki, WikiPage
    pages = [
        WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=1, pdf_page_end=1,
                 text="alpha filler content one"),
        WikiPage(slug="001-b", title="B", level=1, order=1, pdf_page_start=2, pdf_page_end=2,
                 text="beta zzqrare identifier passage"),      # the only page with the rare term
        WikiPage(slug="002-c", title="C", level=1, order=2, pdf_page_start=3, pdf_page_end=3,
                 text="alpha beta two content"),
    ]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=1)
    return SemanticIndex.build(wiki, _FakeEmbedder(), size_words=50, overlap_words=10)


def test_hybrid_surfaces_exact_term_dense_misses():
    index = _index()
    # "zzqrare" is out of the embedder's vocab → dense is blind and leaves 001-b out of its top-1;
    # BM25 knows the exact term, and RRF surfaces 001-b into the hybrid top-k.
    dense_top1 = [r.page_slug for r in index.search("zzqrare", k=1)]
    hybrid_topk = [r.page_slug for r in index.search_hybrid("zzqrare", k=2)]
    assert "001-b" not in dense_top1                 # dense (term-blind) misses it
    assert "001-b" in hybrid_topk                    # hybrid rescues it
    assert all(r.score > 0 for r in index.search_hybrid("zzqrare", k=2))   # RRF scores set


def test_hybrid_pages_still_works_for_dense_terms():
    from openwiki.eval import hybrid_pages
    pages = hybrid_pages(_index(), "alpha", 2)
    assert "000-a" in pages and len(pages) <= 2      # a dense-strong term is unharmed
