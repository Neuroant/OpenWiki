"""Tests for the retrieval eval core (pure metrics + evaluate driver)."""

from __future__ import annotations

from openwiki.eval import (
    EvalItem, evaluate, hit_at_k, load_eval_set, recall_at_k, reciprocal_rank,
)

RANKED = ["a", "b", "c", "d"]


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
