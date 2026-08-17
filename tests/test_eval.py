"""Tests for the retrieval eval core (pure metrics + evaluate driver)."""

from __future__ import annotations

from openwiki.agent import RAGAnswer, Source
from openwiki.eval import (
    EvalItem, cited_page_slugs, evaluate, grounding, hit_at_k, judge_pairwise,
    load_eval_set, recall_at_k, reciprocal_rank,
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
