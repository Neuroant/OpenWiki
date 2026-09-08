"""Tests for the append-only usage log (Path B — B1 read-path reinforcement).

Pure + dependency-free; the Kuzu fold-in round-trips live in ``test_decay.py``.
"""

from __future__ import annotations

from openwiki.graph.usage import append_usage, clear_usage, read_usage, usage_log_path


def test_usage_log_path_is_a_sidecar():
    assert usage_log_path("out/graph").name == "graph.usage.jsonl"
    assert usage_log_path("out/graph").parent.name == "out"


def test_append_read_roundtrip(tmp_path):
    path = tmp_path / "g.usage.jsonl"
    assert append_usage(path, [("a", "b"), ("a", "c")], now=100) == 2
    assert append_usage(path, [("b", "c")], now=200) == 1        # a second appended record
    records = read_usage(path)
    assert [r["t"] for r in records] == [100, 200]
    assert records[0]["pairs"] == [["a", "b"], ["a", "c"]]
    assert records[1]["pairs"] == [["b", "c"]]


def test_append_skips_empty_self_and_blank(tmp_path):
    path = tmp_path / "g.usage.jsonl"
    assert append_usage(path, []) == 0
    assert append_usage(path, [("x", "x"), ("", "y"), ("z", "")]) == 0   # self + blanks dropped
    assert not path.exists()                                            # nothing written at all
    assert read_usage(path) == []


def test_read_tolerates_garbage(tmp_path):
    path = tmp_path / "g.usage.jsonl"
    path.write_text('{"t":1,"pairs":[["a","b"]]}\n'
                    'not json\n'
                    '\n'
                    '{"no_pairs":1}\n', encoding="utf-8")
    recs = read_usage(path)
    assert len(recs) == 1 and recs[0]["pairs"] == [["a", "b"]]


def test_clear_usage_is_idempotent(tmp_path):
    path = tmp_path / "g.usage.jsonl"
    append_usage(path, [("a", "b")])
    clear_usage(path)
    assert not path.exists()
    clear_usage(path)         # missing_ok — no error on a second clear
