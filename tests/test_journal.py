"""Pure tests for the deferred-write journal (Path B — B1 concurrency). No Kuzu."""

from __future__ import annotations

from openwiki.graph import journal


def test_append_and_read_roundtrip(tmp_path):
    jp = tmp_path / "g.journal.jsonl"
    assert journal.read_journal(jp) == [] and journal.pending_journal(jp) == 0
    assert journal.append_remember(jp, "s1", [("the db", "is", "kuzu"), ("a", "b", "c")]) == 2
    assert journal.append_reindex(jp, "000-a", "page text") == 1
    recs = journal.read_journal(jp)
    assert [r["op"] for r in recs] == ["remember", "reindex"]
    assert recs[0]["session"] == "s1"
    assert recs[0]["facts"] == [["the db", "is", "kuzu"], ["a", "b", "c"]]
    assert recs[1]["slug"] == "000-a" and recs[1]["text"] == "page text"
    assert journal.pending_journal(jp) == 2


def test_append_skips_blank_and_empty(tmp_path):
    jp = tmp_path / "g.journal.jsonl"
    assert journal.append_remember(jp, "s", [("", "p", "o"), ("s", "", "o")]) == 0  # all blank
    assert journal.append_reindex(jp, "   ", "x") == 0                              # empty slug
    assert journal.pending_journal(jp) == 0


def test_memoryfact_like_objects(tmp_path):
    from openwiki.graph.memory import MemoryFact

    jp = tmp_path / "g.journal.jsonl"
    journal.append_remember(jp, "s", [MemoryFact("x", "y", "z")])
    assert journal.read_journal(jp)[0]["facts"] == [["x", "y", "z"]]


def test_read_is_lenient_then_clear(tmp_path):
    jp = tmp_path / "g.journal.jsonl"
    journal.append_reindex(jp, "p", "t")
    jp.write_text(jp.read_text(encoding="utf-8") + "\n{ broken json\n"
                  + '{"op":"unknown","x":1}\n', encoding="utf-8")   # junk + unknown op
    assert journal.pending_journal(jp) == 1                          # only the valid reindex
    journal.clear_journal(jp)
    assert journal.pending_journal(jp) == 0
    journal.clear_journal(jp)                                        # idempotent (no error)


def test_journal_path_naming(tmp_path):
    assert journal.journal_path(tmp_path / "graph").name == "graph.journal.jsonl"
