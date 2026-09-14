"""Tests for LLM re-ranking (retrieval quality). Pure + fake-injected — no Ollama."""

from __future__ import annotations

from openwiki.rerank import build_rerank_messages, parse_order, rerank_order


class _Result:
    def __init__(self, slug, i):
        self.page_slug, self.page_title = slug, slug.upper()
        self.pdf_page_start = self.pdf_page_end = i
        self.chunk_id, self.score, self.text = f"c{i}", 1.0 / i, f"text about {slug}"


class _Index:
    def __init__(self, slugs):
        self.slugs = slugs

    def search(self, query, k):
        return [_Result(s, i + 1) for i, s in enumerate(self.slugs[:k])]

    def best_chunk_per_page(self, query, slugs):
        return [_Result(s, i + 1) for i, s in enumerate(slugs)]


class _Chat:
    name = "fake"

    def __init__(self, out):
        self.out, self.calls = out, 0

    def chat(self, messages):
        self.calls += 1
        return self.out


class _Boom:
    name = "boom"

    def chat(self, messages):
        raise RuntimeError("model down")


# -- parse_order: always a full, valid permutation -----------------------------

def test_parse_order_full_ranking():
    assert parse_order("[3, 1, 2]", 3) == [2, 0, 1]          # 1-based → 0-based


def test_parse_order_partial_appends_missing():
    assert parse_order("2 then 1", 3) == [1, 0, 2]           # index 2 omitted → appended, stable


def test_parse_order_dedups_and_drops_out_of_range():
    assert parse_order("[5, 2, 2, 9, 1]", 3) == [1, 0, 2]    # keep 2,1 (0-based), append 0


def test_parse_order_strips_think_and_prefers_bracket():
    assert parse_order("<think>9,9</think> ranking: [1,2,3]", 3) == [0, 1, 2]


def test_parse_order_identity_on_garbage():
    assert parse_order("no numbers here", 3) == [0, 1, 2]
    assert parse_order("", 2) == [0, 1]


# -- rerank_order: one chat call, robust fallbacks -----------------------------

def test_rerank_order_reorders_via_chat():
    c = _Chat("[3,1,2]")
    assert rerank_order("q", ["a", "b", "c"], c) == [2, 0, 1] and c.calls == 1


def test_rerank_order_skips_call_when_trivial():
    assert rerank_order("q", ["only"], _Chat("[1]")) == [0]       # <2 items → no call
    empty = _Chat("[2,1]")
    assert rerank_order("", ["a", "b"], empty) == [0, 1] and empty.calls == 0   # empty query


def test_rerank_order_identity_on_model_error():
    assert rerank_order("q", ["a", "b"], _Boom()) == [0, 1]       # error → identity, never raises


def test_build_messages_numbers_and_truncates():
    msgs = build_rerank_messages("why", ["alpha " * 200, "beta"], snippet_chars=50)
    body = msgs[1]["content"]
    assert "[1]" in body and "[2]" in body and "…" in body       # long passage truncated


# -- eval integration: reranker over candidate slugs ---------------------------

def test_make_reranker_reorders_slugs():
    from openwiki.eval import make_reranker
    fn = make_reranker(_Index(["a", "b", "c", "d"]), _Chat("[3,1,2]"))
    assert fn("q", ["a", "b", "c"]) == ["c", "a", "b"]
    assert fn("q", ["solo"]) == ["solo"]                          # <2 → unchanged


def test_reranking_retriever_pools_then_trims():
    from openwiki.eval import reranking_retriever
    index = _Index(["a", "b", "c", "d", "e"])
    retrieve = reranking_retriever(index, _Chat("[4,3,2,1]"), budget=2, pool=4)
    assert retrieve("q") == ["d", "c"]      # pool a,b,c,d → reversed → top-2


# -- agent integration: rerank a wider seed pool -------------------------------

def test_ragagent_rerank_seeds():
    from openwiki.agent import RAGAgent
    agent = RAGAgent(_Index(["a", "b", "c", "d", "e"]), _Chat("[4,3,2,1]"),
                     top_k=2, rerank=True, rerank_pool=4)
    sources = agent.retrieve("q")
    assert [s.page_slug for s in sources] == ["d", "c"]           # reranked pool, top-2
    assert [s.marker for s in sources] == [1, 2]
