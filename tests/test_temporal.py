"""Tests for B7 — bi-temporal assertions (valid time + transaction time).

Pure tests (date parsing, the valid-time merge rule, legacy derivation, capture/journal
formats) run without Kuzu; the remember/recall/timeline scenarios — out-of-order backfill,
corrections, point-in-time, planned facts, multi-valued predicates, the in-place migration
of a pre-B7 graph, rebuild preservation and journal time fidelity — are gated on Kuzu.
Every scenario pins ``now`` so nothing depends on the wall clock.
"""

from __future__ import annotations

import numpy as np
import pytest

from openwiki.graph import journal
from openwiki.graph.memory import (
    MemoryFact, assemble_context, build_capture_messages, format_memory, parse_facts,
)
from openwiki.graph.temporal import (
    believed_at, coerce_cardinality, derive_legacy_intervals, format_interval, parse_date,
    plan_merge, session_date, status, valid_at,
)

T = parse_date


# -- pure: dates ---------------------------------------------------------------

def test_parse_date_formats_and_rejects_noise():
    assert T("2025-09-01") == 1756684800
    assert T("2025-09") == T("2025-09-01") and T("2025") == T("2025-01-01")
    assert T("2025-09-01T12:00:00Z") == T("2025-09-01") + 12 * 3600
    assert T(1234) == 1234
    for bad in (None, "", "yesterday", "2025-13-01", "2025-02-30", "0042-01-01", True):
        assert T(bad) is None


def test_session_date_from_ids():
    assert session_date("2025-09-08") == T("2025-09-08")
    assert session_date("standup-2025-09-08-b") == T("2025-09-08")
    assert session_date("1362f083-aab4-46e7-abb9-6519f8c42714") is None     # host UUID
    assert session_date("s1") is None


def test_format_interval_and_cardinality():
    assert format_interval(T("2025-09-01"), None) == "since 2025-09-01"
    assert format_interval(T("2025-08-01"), T("2025-09-01")) == "2025-08-01 → 2025-09-01"
    assert format_interval(None, None) == ""
    assert coerce_cardinality("Many") == "many" and coerce_cardinality("several") == "many"
    assert coerce_cardinality(None) == "one" and coerce_cardinality("single") == "one"


def test_status_and_views():
    rec = {"id": "a", "valid_from": 100, "valid_to": 200, "expired_at": None, "created_at": 150}
    assert status(rec, 50) == "future" and status(rec, 150) == "current" and status(rec, 250) == "past"
    assert status(dict(rec, expired_at=300), 150) == "retracted"
    assert valid_at(rec, 100) and not valid_at(rec, 200)
    assert believed_at(rec) and believed_at(rec, 150) and not believed_at(rec, 120)
    assert not believed_at(dict(rec, expired_at=300), 300)


# -- pure: the merge rule -------------------------------------------------------

def _r(rid, okey, vf, vt=None, exp=None, card="one"):
    return {"id": rid, "okey": okey, "valid_from": vf, "valid_to": vt, "expired_at": exp,
            "created_at": vf, "cardinality": card}


def test_plan_merge_reaffirm_and_world_change():
    recs = [_r("a", "9000", 100)]
    assert plan_merge(recs, "9000", 150)["action"] == "reaffirm"
    p = plan_merge(recs, "9100", 150)                     # the world changed at 150
    assert p["action"] == "add" and p["close"] == [("a", 150)] and p["expire"] == []
    assert p["valid_from"] == 150 and p["valid_to"] is None


def test_plan_merge_backfill_lands_in_history():
    """The B7 fix: an OLDER fact processed after a newer one must not overwrite it."""
    recs = [_r("sep", "9000", 300)]
    p = plan_merge(recs, "8137", 100)                     # an August fact, learned later
    assert p["action"] == "add" and p["close"] == [] and p["expire"] == []
    assert (p["valid_from"], p["valid_to"]) == (100, 300)  # bounded by the later record
    assert p["superseded_by"] == "sep"


def test_plan_merge_same_instant_and_explicit_correction_retract():
    recs = [_r("a", "9000", 100, vt=400)]
    p = plan_merge(recs, "9001", 100)                     # same instant → can't both be true
    assert p["expire"] == ["a"] and p["close"] == [] and p["valid_to"] == 400
    c = plan_merge(recs, "9001", 250, correct=True)       # "it was never 9000"
    assert c["expire"] == ["a"] and (c["valid_from"], c["valid_to"]) == (100, 400)


def test_plan_merge_many_coexist_and_one_leaves_many_alone():
    recs = [_r("k", "kuzu", 100, card="many")]
    p = plan_merge(recs, "ollama", 150, cardinality="many")
    assert p["action"] == "add" and not p["close"] and not p["expire"]
    q = plan_merge(recs, "postgres", 150, cardinality="one")   # a "one" fact spares "many" records
    assert not q["close"] and not q["expire"]


def test_plan_merge_extends_back_and_skips_retracted():
    recs = [_r("a", "8137", 300)]
    p = plan_merge(recs, "8137", 100)                     # earlier evidence for the same fact
    assert p["action"] == "extend" and p["target"] == "a" and p["valid_from"] == 100
    gone = [_r("x", "9000", 100, exp=200)]                # retracted records never conflict
    assert plan_merge(gone, "9001", 150)["close"] == []


def test_plan_merge_revisit_between_intervals_extends_the_next():
    recs = [_r("a", "8137", 100, vt=200), _r("b", "9000", 200, vt=300), _r("c", "8137", 300)]
    p = plan_merge(recs, "8137", 250)                     # 8137 again from 250 → merge with c
    assert p["action"] == "extend" and p["target"] == "c" and p["close"] == [("b", 250)]


def test_derive_legacy_intervals_matches_b4():
    recs = [{"id": "a", "created_at": 100, "valid_from": None},
            {"id": "b", "created_at": 200, "valid_from": None},
            {"id": "c", "created_at": 200, "valid_from": None},
            {"id": "d", "created_at": 150, "valid_from": None}]
    for r in recs:
        r.update(valid_to=None, expired_at=None)
    derive_legacy_intervals(recs, [("b", "a"), ("c", "b")])
    by = {r["id"]: r for r in recs}
    assert (by["a"]["valid_from"], by["a"]["valid_to"]) == (100, 200)   # closed by b
    assert by["b"]["expired_at"] == 200                                  # same instant as c
    assert status(by["c"], 999) == "current" and status(by["d"], 999) == "current"


# -- pure: capture + journal + formatting ----------------------------------------

def test_parse_facts_reads_valid_from_and_cardinality():
    raw = ('[{"subject":"the server","predicate":"listens on","object":"port 9000",'
           '"valid_from":"2025-09-01","cardinality":"one"},'
           ' {"subject":"the project","predicate":"uses","object":"Ollama","cardinality":"many"},'
           ' {"subject":"x","predicate":"y","object":"z","valid_from":"last week"}]')
    a, b, c = parse_facts(raw)
    assert a.valid_from == T("2025-09-01") and a.cardinality == "one"
    assert b.valid_from is None and b.cardinality == "many"
    assert c.valid_from is None                            # unparseable → dropped, never guessed
    assert MemoryFact("s", "p", "o") == MemoryFact("s", "p", "o", None, "one")   # back-compat


def test_capture_messages_carry_session_date():
    msgs = build_capture_messages("hi", session_date=T("2025-09-08"))
    assert "Session date: 2025-09-08" in msgs[1]["content"]
    assert "valid_from" in msgs[0]["content"] and "JSON array" in msgs[0]["content"]
    assert "Session date" not in build_capture_messages("hi")[1]["content"]


def test_journal_remember_record_carries_b7_fields(tmp_path):
    jp = tmp_path / "g.journal.jsonl"
    facts = [MemoryFact("a", "b", "c"),
             MemoryFact("s", "p", "o", valid_from=T("2025-08-01"), cardinality="many")]
    assert journal.append_remember(jp, "s1", facts, now=1000, session_date=T("2025-08-01"),
                                   correct=True) == 2
    (rec,) = journal.read_journal(jp)
    assert rec["t"] == 1000 and rec["session_date"] == T("2025-08-01") and rec["correct"] is True
    assert rec["facts"] == [["a", "b", "c"], ["s", "p", "o", T("2025-08-01"), "many"]]


def test_format_memory_and_context_show_validity():
    facts = [{"subject": "the server", "predicate": "listens on", "object": "port 9000",
              "session_id": "s1", "valid_from": T("2025-09-01"), "valid_to": None}]
    assert "(since 2025-09-01; s1)" in format_memory(facts)
    assert "(since 2025-09-01; s1)" in assemble_context("", facts, [])
    old = [dict(facts[0], valid_to=T("2025-10-01"), status="past", in_view=False)]
    assert "[superseded]" in format_memory(old) and "2025-09-01 → 2025-10-01" in format_memory(old)
    as_of = [dict(old[0], in_view=True)]                  # the right answer for an as-of query
    assert "[superseded]" not in format_memory(as_of)


# -- Kuzu-gated: the scenarios ----------------------------------------------------

class _TEmbedder:
    VOCAB = ["server", "port", "project", "uses", "database", "python", "language"]
    name = "fake:temporal"

    def _vec(self, text):
        low = text.lower()
        v = np.array([float(low.count(w)) for w in self.VOCAB], dtype=np.float32)
        return v if v.any() else v + 1e-6

    def embed_documents(self, texts):
        return np.vstack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


def _store(tmp_path):
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphBuilder, GraphStore
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki, WikiPage

    pages = [WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=1,
                      pdf_page_end=1, text="server port project database")]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=1)
    index = SemanticIndex.build(wiki, _TEmbedder(), size_words=50, overlap_words=10)
    GraphBuilder(tmp_path / "graph").build(wiki, index)
    return GraphStore(tmp_path / "graph", writable=True)


def _port(obj, **kw):
    return MemoryFact("the server", "listens on", obj, **kw)


def _objs(hits):
    return [h["object"] for h in hits]


def test_backfill_out_of_order_keeps_the_present(tmp_path):
    """The headline bug: September's session (9000) remembered first, August's (8137)
    backfilled after — pre-B7 the stale 8137 became current. Now it lands in history."""
    store, emb = _store(tmp_path), _TEmbedder()
    try:
        store.remember("2025-09-16", [_port("port 9000")], emb, now=T("2025-09-20"))
        r = store.remember("2025-08-01", [_port("port 8137")], emb, now=T("2025-09-21"))
        assert r["added"] == 1 and r["historical"] == 1 and r["superseded"] == 0
        now = T("2025-09-22")
        assert _objs(store.recall("server port", emb, now=now)) == ["port 9000"]
        assert _objs(store.recall("server port", emb, now=now, as_of=T("2025-08-15"))) == ["port 8137"]
        (g,) = store.timeline("server port", emb, now=now)
        assert [x["object"] for x in g["records"]] == ["port 8137", "port 9000"]
        assert [x["status"] for x in g["records"]] == ["past", "current"]
        assert g["records"][0]["valid_to"] == T("2025-09-16")     # ends where 9000 begins
    finally:
        store.close()


def test_world_change_closes_the_interval(tmp_path):
    store, emb = _store(tmp_path), _TEmbedder()
    try:
        store.remember("2025-08-01", [_port("port 8137")], emb, now=T("2025-08-01"))
        r = store.remember("2025-09-01", [_port("port 9000")], emb, now=T("2025-09-01"))
        assert r["superseded"] == 1 and r["retracted"] == 0
        now = T("2025-09-10")
        assert _objs(store.recall("server port", emb, now=now)) == ["port 9000"]
        assert _objs(store.recall("server port", emb, now=now, as_of=T("2025-08-20"))) == ["port 8137"]
        hist = store.recall("server port", emb, now=now, include_superseded=True)
        old = next(h for h in hist if h["object"] == "port 8137")
        assert old["status"] == "past" and old["superseded"] and not old["in_view"]
    finally:
        store.close()


def test_correction_retracts_and_known_at_sees_the_old_belief(tmp_path):
    store, emb = _store(tmp_path), _TEmbedder()
    try:
        store.remember("s1", [_port("port 9000")], emb, now=T("2025-09-01"))
        r = store.remember("fix", [_port("port 9001")], emb, now=T("2025-09-18"), correct=True)
        assert r["retracted"] == 1
        now = T("2025-09-20")
        assert _objs(store.recall("server port", emb, now=now)) == ["port 9001"]
        # valid time: 9001 inherits 9000's whole interval — it was never 9000
        assert _objs(store.recall("server port", emb, now=now, as_of=T("2025-09-10"))) == ["port 9001"]
        # transaction time: what we believed on Sep 17, before the correction
        assert _objs(store.recall("server port", emb, now=now, known_at=T("2025-09-17"))) == ["port 9000"]
        (g,) = store.timeline("server port", emb, now=now)
        assert {x["object"]: x["status"] for x in g["records"]} == {"port 9000": "retracted",
                                                                     "port 9001": "current"}
    finally:
        store.close()


def test_known_at_sees_an_interval_open_before_its_end_was_learned(tmp_path):
    store, emb = _store(tmp_path), _TEmbedder()
    try:
        store.remember("s1", [_port("port 9000")], emb, now=T("2025-09-01"))
        store.remember("2025-10-01", [_port("port 9100")], emb, now=T("2025-10-02"))
        now, oct15 = T("2025-10-20"), T("2025-10-15")
        assert _objs(store.recall("server port", emb, now=now, as_of=oct15)) == ["port 9100"]
        # on Sep 20 we did not yet know 9000 would end on Oct 1
        assert _objs(store.recall("server port", emb, now=now, as_of=oct15,
                                  known_at=T("2025-09-20"))) == ["port 9000"]
    finally:
        store.close()


def test_planned_fact_becomes_current_on_its_date(tmp_path):
    store, emb = _store(tmp_path), _TEmbedder()
    try:
        store.remember("s1", [_port("port 9000")], emb, now=T("2025-09-01"))
        store.remember("s2", [_port("port 9100", valid_from=T("2025-10-01"))], emb, now=T("2025-09-20"))
        before = T("2025-09-25")
        assert _objs(store.recall("server port", emb, now=before)) == ["port 9000"]
        planned = [h for h in store.recall("server port", emb, now=before, include_superseded=True)
                   if h["object"] == "port 9100"]
        assert planned and planned[0]["status"] == "future"
        assert _objs(store.recall("server port", emb, now=T("2025-10-05"))) == ["port 9100"]
    finally:
        store.close()


def test_multi_valued_facts_coexist(tmp_path):
    store, emb = _store(tmp_path), _TEmbedder()
    try:
        store.remember("s1", [MemoryFact("the project", "uses", "Kuzu", cardinality="many"),
                              MemoryFact("the project", "uses", "Ollama", cardinality="many")],
                       emb, now=T("2025-09-01"))
        r = store.remember("s2", [MemoryFact("the project", "uses", "NumPy", cardinality="many")],
                           emb, now=T("2025-09-02"))
        assert r["superseded"] == 0
        got = _objs(store.recall("project uses", emb, k=5, now=T("2025-09-03")))
        assert sorted(got) == ["Kuzu", "NumPy", "Ollama"]
    finally:
        store.close()


def test_stated_valid_from_beats_the_session_date_and_extends_back(tmp_path):
    store, emb = _store(tmp_path), _TEmbedder()
    try:
        store.remember("2025-09-16", [_port("port 9000", valid_from=T("2025-09-01"))], emb,
                       now=T("2025-09-16"))
        (row,) = store.list_assertions()
        assert row["valid_from"] == T("2025-09-01")                 # stated date, not the session's
        r = store.remember("2025-07-01", [_port("port 9000")], emb, now=T("2025-09-17"))
        assert r["duplicates"] == 1 and r["added"] == 0             # earlier evidence, same fact
        (row,) = store.list_assertions()
        assert row["valid_from"] == T("2025-07-01") and row["confidence"] > 1.0
    finally:
        store.close()


def test_context_for_renders_validity_and_as_of(tmp_path):
    store, emb = _store(tmp_path), _TEmbedder()
    try:
        store.remember("2025-08-01", [_port("port 8137")], emb, now=T("2025-08-01"))
        store.remember("2025-09-01", [_port("port 9000")], emb, now=T("2025-09-01"))
        ctx = store.context_for("server port", emb)
        assert "port 9000" in ctx and "since 2025-09-01" in ctx and "8137" not in ctx
        then = store.context_for("server port", emb, as_of=T("2025-08-15"))
        assert "port 8137" in then and "2025-08-01 → 2025-09-01" in then
    finally:
        store.close()


def test_overview_counts_split_revision(tmp_path):
    store, emb = _store(tmp_path), _TEmbedder()
    try:
        store.remember("s1", [_port("port 8137")], emb, now=T("2025-08-01"))
        store.remember("s2", [_port("port 9000")], emb, now=T("2025-09-01"))            # world change
        store.remember("s3", [_port("port 9001")], emb, now=T("2025-09-02"), correct=True)  # correction
        ov = store.memory_overview()
        assert ov["assertions"] == 1 and ov["superseded"] == 2 and ov["retracted"] == 1
        from openwiki.analysis.memory import analyze_memory
        rev = analyze_memory(store)["revision"]
        assert rev["world_changes"] == 1 and rev["corrections"] == 1
    finally:
        store.close()


def test_rebuild_preserves_bitemporal_fields(tmp_path):
    store, emb = _store(tmp_path), _TEmbedder()
    try:
        store.remember("2025-09-16", [_port("port 9000")], emb, now=T("2025-09-20"))
        store.remember("2025-08-01", [_port("port 8137")], emb, now=T("2025-09-21"))
        store.remember("fix", [MemoryFact("the database", "is", "Kuzu")], emb, now=T("2025-09-01"))
        store.remember("fix2", [MemoryFact("the database", "is", "DuckDB")], emb,
                       now=T("2025-09-02"), correct=True)
    finally:
        store.close()
    store = _store(tmp_path)                                  # rebuild the doc tier over it
    try:
        now = T("2025-09-22")
        assert _objs(store.recall("server port", emb, k=1, now=now)) == ["port 9000"]
        assert _objs(store.recall("server port", emb, k=1, now=now,
                                  as_of=T("2025-08-15"))) == ["port 8137"]
        assert store.memory_overview()["retracted"] == 1         # expired_at survived
    finally:
        store.close()


def test_fold_journal_keeps_the_queued_record_time(tmp_path):
    store, emb = _store(tmp_path), _TEmbedder()
    try:
        journal.append_remember(store._journal_path, "q",
                                [_port("port 9000", valid_from=T("2025-08-01"))], now=T("2025-09-01"))
        store.fold_journal(emb, now=T("2025-09-30"))
        (row,) = store.list_assertions()
        assert row["created_at"] == T("2025-09-01")              # queued time, not fold time
        assert row["valid_from"] == T("2025-08-01")
    finally:
        store.close()


def test_pre_b7_graph_is_read_and_migrated_in_place(tmp_path):
    """A graph from before B7 (no validity columns): readers derive the intervals from the
    B4 SUPERSEDES edges; the first writable remember ALTERs + backfills — no rebuild."""
    kuzu = pytest.importorskip("kuzu")
    from openwiki.graph import GraphStore

    path = tmp_path / "legacy"
    db = kuzu.Database(str(path))
    conn = kuzu.Connection(db)
    conn.execute("CREATE NODE TABLE Session(id STRING, created_at INT64, PRIMARY KEY(id));")
    conn.execute("CREATE NODE TABLE Assertion(id STRING, subject STRING, predicate STRING, "
                 "object STRING, session_id STRING, created_at INT64, confidence DOUBLE, "
                 "last_seen INT64, emb FLOAT[7], PRIMARY KEY(id));")
    conn.execute("CREATE REL TABLE ASSERTS(FROM Session TO Assertion);")
    conn.execute("CREATE REL TABLE SUPERSEDES(FROM Assertion TO Assertion);")
    emb = _TEmbedder()
    legacy = [("a", "the database", "is", "Kuzu", 100), ("b", "the database", "is", "Postgres", 200),
              ("c", "the database", "is", "SQLite", 200), ("d", "the language", "is", "Python", 150)]
    for aid, s, p, o, t in legacy:
        vec = emb.embed_documents([f"{s} {p} {o}"])[0]
        vec = (vec / np.linalg.norm(vec)).tolist()
        conn.execute("CREATE (:Assertion {id:$id, subject:$s, predicate:$p, object:$o, "
                     "session_id:'old', created_at:$t, confidence:1.0, last_seen:$t, emb:$e});",
                     parameters={"id": aid, "s": s, "p": p, "o": o, "t": t, "e": vec})
    for n, o in (("b", "a"), ("c", "b")):
        conn.execute("MATCH (n:Assertion {id:$n}),(o:Assertion {id:$o}) CREATE (n)-[:SUPERSEDES]->(o);",
                     parameters={"n": n, "o": o})
    conn.close()
    db.close()

    ro = GraphStore(path)                                     # read-only: derived, not written
    try:
        st = {r["id"]: r["status"] for r in ro.list_assertions()}
        assert st == {"a": "past", "b": "retracted", "c": "current", "d": "current"}
        assert sorted(_objs(ro.recall("database language", emb, k=5, now=1000))) == ["Python", "SQLite"]
    finally:
        ro.close()

    rw = GraphStore(path, writable=True)
    try:
        rw.remember("new", [MemoryFact("the language", "is", "Python")], emb, now=1000)   # triggers migration
        rows = {r[0]: r[1:] for r in rw._rows(
            "MATCH (a:Assertion) RETURN a.id, a.valid_from, a.valid_to, a.expired_at;")}
        assert rows["a"] == [100, 200, None] and rows["b"] == [200, None, 200]
        assert rows["c"] == [200, None, None] and rows["d"][:2] == [150, None]
        assert sorted(_objs(rw.recall("database language", emb, k=5, now=1000))) == ["Python", "SQLite"]
    finally:
        rw.close()


# -- surfaces: CLI helpers + MCP wiki_memory(as_of) -------------------------------

def test_cli_date_arg_and_timeline_format():
    import argparse

    from openwiki.cli import _date_arg, _format_timeline
    assert _date_arg("2025-09-01") == T("2025-09-01")
    with pytest.raises(argparse.ArgumentTypeError):
        _date_arg("soon")
    out = _format_timeline([{"subject": "the server", "predicate": "listens on", "cos": 0.9,
                             "records": [
        {"object": "port 8137", "valid_from": T("2025-08-01"), "valid_to": T("2025-09-16"),
         "created_at": T("2025-09-21"), "expired_at": None, "session_id": "aug", "status": "past"},
        {"object": "port 9000", "valid_from": T("2025-09-16"), "valid_to": None,
         "created_at": T("2025-09-20"), "expired_at": None, "session_id": "sep", "status": "current"}]}])
    lines = out.splitlines()
    assert lines[1].startswith("  ○ 2025-08-01 → 2025-09-16") and "recorded 2025-09-21; aug" in lines[1]
    assert lines[2].startswith("  ● since 2025-09-16") and "port 9000" in lines[2]


def test_mcp_wiki_memory_accepts_as_of(tmp_path):
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphBuilder, GraphStore
    from openwiki.mcp_server import build_server
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki, WikiPage, write_wiki

    pages = [WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=1,
                      pdf_page_end=1, text="server port project database")]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=1)
    write_wiki(wiki, tmp_path / "wiki")
    emb = _TEmbedder()
    index = SemanticIndex.build(wiki, emb, size_words=50, overlap_words=10)
    GraphBuilder(tmp_path / "graph").build(wiki, index)
    store = GraphStore(tmp_path / "graph", writable=True)
    try:
        store.remember("2025-08-01", [_port("port 8137")], emb, now=T("2025-08-01"))
        store.remember("2025-09-01", [_port("port 9000")], emb, now=T("2025-09-01"))
        server = build_server(tmp_path / "wiki", index=index, graph=store)
        spec = next(t for t in server.tools if t["name"] == "wiki_memory")
        assert "as_of" in spec["inputSchema"]["properties"]

        def call(args):
            res = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": "wiki_memory", "arguments": args}})
            return res["result"]["content"][0]["text"]
        assert "port 9000" in call({"query": "server port"})
        then = call({"query": "server port", "as_of": "2025-08-15"})
        assert "port 8137" in then and "port 9000" not in then
    finally:
        store.close()
