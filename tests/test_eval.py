"""Tests for the retrieval eval core (pure metrics + evaluate driver)."""

from __future__ import annotations

from openwiki.agent import RAGAnswer, Source
from openwiki.eval import (
    EvalItem, cited_page_slugs, community_grounding, evaluate, grounding, hit_at_k,
    judge_pairwise, load_eval_set, recall_at_k, reciprocal_rank, run_global_eval,
)

RANKED = ["a", "b", "c", "d"]


def _src(marker, slug):
    return Source(marker=marker, page_slug=slug, page_title=slug.upper(),
                  pdf_page_start=marker, pdf_page_end=marker, chunk_id=f"c{marker}",
                  score=1.0 / marker, text="")


def test_answer_grounding_uses_citations():
    ans = RAGAnswer(question="q", answer="Because of X [1] and Y.",
                    sources=[_src(1, "a"), _src(2, "b")], model="m")
    assert cited_page_slugs(ans) == {"a"}                     # only [1] is cited
    g = grounding(ans, ["a", "c"])
    assert g["cite_hit"] is True and g["expected_recall"] == 0.5
    assert grounding(ans, ["c"])["cite_hit"] is False         # cited page isn't expected


class _Judge:
    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages):
        return self.reply


def test_judge_pairwise_parses_verdict():
    assert judge_pairwise(_Judge("A"), "q", "x", "y") == "a"
    assert judge_pairwise(_Judge("The better one is B."), "q", "x", "y") == "b"
    assert judge_pairwise(_Judge("<think>weighing…</think>tie"), "q", "x", "y") == "tie"
    assert judge_pairwise(_Judge("(no clear token)"), "q", "x", "y") == "tie"   # fallback


class _FakeResult:
    def __init__(self, slug, marker):
        self.page_slug, self.page_title = slug, slug.upper()
        self.pdf_page_start = self.pdf_page_end = marker
        self.chunk_id, self.score, self.text = f"c{marker}", 1.0 / marker, f"text {slug}"


class _FakeIndex:
    def __init__(self, slugs):
        self.slugs = slugs

    def search(self, query, k):
        return [_FakeResult(s, i + 1) for i, s in enumerate(self.slugs[:k])]

    def best_chunk_per_page(self, query, slugs):
        return [_FakeResult(s, i + 1) for i, s in enumerate(slugs)]


class _FakeChat:
    name = "fake"

    def chat(self, messages):
        return "The answer relies on [1]."          # always cites the top source


def test_run_answer_eval_aggregates(monkeypatch):
    from openwiki import eval as ev
    items = [ev.EvalItem("q1", ["a"]), ev.EvalItem("q2", ["b"])]
    index = _FakeIndex(["a", "b", "c"])
    # graph=None → RAG and GraphRAG both retrieve seeds; the top source is 'a'
    result = ev.run_answer_eval(items, index, graph=None, chat=_FakeChat(),
                                top_k=3, expand_k=0, judge=_Judge("tie"))
    assert result["questions"] == 2 and result["judged"] is True
    # both answers cite 'a': q1 (expected 'a') hits, q2 (expected 'b') doesn't → 50%
    assert result["grounding"]["RAG"]["cite_hit"] == 0.5
    assert result["tally"]["tie"] == 2


# -- global (thematic) eval ----------------------------------------------------

class _GlobalChat:
    name = "fake:global"

    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages):
        return self.reply


def test_community_grounding_precision_and_recall():
    assert community_grounding({1, 2}, {2, 3}) == {"cite_hit": True, "recall": 0.5, "precision": 0.5}
    assert community_grounding({1}, set())["cite_hit"] is False          # no relevant themes exist
    assert community_grounding(set(), {1})["precision"] == 0.0           # cited nothing


def test_run_global_eval_grounding_only():
    items = [EvalItem("q1", ["a1"]), EvalItem("q2", ["b1", "c1"])]
    communities = [("A", "sa"), ("B", "sb"), ("C", "sc")]                # markers 1,2,3
    members = {1: {"a1", "a2"}, 2: {"b1"}, 3: {"c1"}}
    chat = _GlobalChat("Antwort [1] [2].")                              # cites themes 1 & 2 every time
    r = run_global_eval(items, communities, members, chat)
    assert r["questions"] == 2 and r["judged"] is False
    assert r["grounding"]["cite_hit"] == 1.0                            # each hits a relevant theme
    assert r["grounding"]["recall"] == 0.75                             # q1 1/1, q2 1/2
    assert r["grounding"]["precision"] == 0.5                           # 1 of 2 cited themes relevant


def test_run_global_eval_judges_global_vs_rag():
    items = [EvalItem("q1", ["a"]), EvalItem("q2", ["b"])]
    communities = [("A", "sa"), ("B", "sb")]
    members = {1: {"a"}, 2: {"b"}}
    index = _FakeIndex(["a", "b", "c"])
    r = run_global_eval(items, communities, members, _FakeChat(), index=index, judge=_Judge("tie"))
    assert r["judged"] is True and r["tally"]["tie"] == 2


def test_reciprocal_rank():
    assert reciprocal_rank(RANKED, ["c"]) == 1 / 3       # first hit at position 3
    assert reciprocal_rank(RANKED, ["a", "d"]) == 1.0    # earliest hit wins
    assert reciprocal_rank(RANKED, ["x"]) == 0.0


def test_hit_and_recall_at_k():
    assert hit_at_k(RANKED, ["c"], 3) == 1.0
    assert hit_at_k(RANKED, ["c"], 2) == 0.0             # c is at rank 3, outside top-2
    assert recall_at_k(RANKED, ["a", "d"], 3) == 0.5     # only a is in top-3
    assert recall_at_k(RANKED, ["a", "b"], 3) == 1.0
    assert recall_at_k(RANKED, [], 3) == 0.0


def test_evaluate_aggregates_and_lists_misses():
    items = [EvalItem("q1", ["a"]), EvalItem("q2", ["z"])]
    ranked = {"q1": ["a", "b"], "q2": ["b", "c"]}
    report = evaluate(items, lambda q: ranked[q], k=2)
    assert report.hit_rate == 0.5                        # q1 hits, q2 misses
    assert abs(report.mrr - 0.5) < 1e-9                  # rr 1.0 and 0.0
    assert [m.question for m in report.misses] == ["q2"]


def test_load_eval_set(tmp_path):
    path = tmp_path / "eval.jsonl"
    path.write_text(
        '# a comment line\n'
        '{"question": "q1", "pages": ["a", "b"]}\n'
        '\n'
        '{"question": "q2", "expected": "c"}\n',   # alias + scalar coerced to list
        encoding="utf-8",
    )
    items = load_eval_set(path)
    assert [i.question for i in items] == ["q1", "q2"]
    assert items[0].expected == ["a", "b"]
    assert items[1].expected == ["c"]
