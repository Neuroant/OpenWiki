"""Direction K (M2-M3): the Memory tab's facts browser and fact detail — the pure helpers, the store's
fact_detail and the web app's endpoints."""

from __future__ import annotations

import json

import pytest

from openwiki.graph.temporal import parse_date
from openwiki.memory_browse import filter_facts, said_at

DAY = 86400


def _row(i, subject, predicate, obj, status="current", source="assistant", session="s1", t=1_000_000):
    return {"id": f"f{i}", "subject": subject, "predicate": predicate, "object": obj, "status": status,
            "source": source, "session_id": session, "created_at": t, "last_seen": t, "valid_from": t,
            "valid_to": None, "confidence": 1.0}


ROWS = [
    _row(1, "OpenWiki", "current release is", "v0.113.1", t=5_000),
    _row(2, "the database", "is", "Kuzu", status="past", t=1_000),
    _row(3, "the database", "is", "LadybugDB", source="user", session="agent-2026-10-09", t=4_000),
    _row(4, "pasted email", "asks for", "a refund", source="material", t=3_000),
    _row(5, "the web UI", "has", "10 tabs", t=2_000),
]


def test_filter_facts_by_status_words_source_session_and_kind():
    assert filter_facts(ROWS)["total"] == 4                                    # default: current only
    assert filter_facts(ROWS, status="")["total"] == 5                         # all states
    assert [f["id"] for f in filter_facts(ROWS, status="past")["facts"]] == ["f2"]
    assert [f["id"] for f in filter_facts(ROWS, q="DATABASE lady")["facts"]] == ["f3"]   # every word, any case
    assert [f["id"] for f in filter_facts(ROWS, source="material")["facts"]] == ["f4"]
    assert [f["id"] for f in filter_facts(ROWS, session="agent")["facts"]] == ["f3"]
    vol = filter_facts(ROWS, kind="volatile")["facts"]                         # a version and a count go stale
    assert {f["id"] for f in vol} == {"f1", "f5"} and {f["kind"] for f in vol} == {"version", "count"}
    assert {f["id"] for f in filter_facts(ROWS, kind="timeless")["facts"]} == {"f3", "f4"}
    assert [f["id"] for f in filter_facts(ROWS, kind="version")["facts"]] == ["f1"]


def test_filter_facts_by_theme_sort_and_page():
    page = filter_facts(ROWS, status="", theme=7, assignment={"f2": 7, "f3": 7, "f5": 8})
    assert [f["id"] for f in page["facts"]] == ["f3", "f2"]                    # most recently said first
    assert all(f["theme"] == 7 for f in page["facts"])
    by_subject = filter_facts(ROWS, status="", sort="subject")["facts"]
    assert [f["subject"] for f in by_subject][:2] == ["OpenWiki", "pasted email"]
    first = filter_facts(ROWS, status="", limit=2)
    second = filter_facts(ROWS, status="", limit=2, offset=2)
    assert first["total"] == second["total"] == 5 and len(first["facts"]) == len(second["facts"]) == 2
    assert not {f["id"] for f in first["facts"]} & {f["id"] for f in second["facts"]}
    assert filter_facts(ROWS, limit=10_000)["limit"] == 500                    # bounded


def test_said_at_prefers_the_capture_window_over_a_stated_date():
    day = int(parse_date("2026-09-05"))
    window = {"valid_from": day + 9 * 3600, "session_id": "claude-2026-09-05", "created_at": day + 30 * DAY}
    assert said_at(window) == day + 9 * 3600                                   # the window's first turn
    stated = dict(window, valid_from=int(parse_date("1969-01-01")))            # "Kauffman, 1969"
    assert said_at(stated) == day + DAY // 2                                   # the session's own day
    undated = {"valid_from": None, "session_id": "1362f083-aab4", "created_at": 1234}
    assert said_at(undated) == 1234
    assert said_at({"valid_from": None, "session_id": "agent-2026-10-09", "created_at": 1}) \
        == int(parse_date("2026-10-09")) + DAY // 2


# -- the store and the web app (Kuzu) ------------------------------------------------------------------------------

def _memory_app(tmp_path, sessions=None):
    pytest.importorskip("kuzu")
    from test_memory import _MemEmbedder
    from openwiki.graph import GraphBuilder, GraphStore
    from openwiki.graph.memory import MemoryFact
    from openwiki.search import SemanticIndex
    from openwiki.web.server import WikiWebApp
    from openwiki.wiki import Wiki, WikiPage, write_wiki

    wiki_dir = tmp_path / "wiki"
    pages = [WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=1, pdf_page_end=1,
                      text="python project database kuzu")]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=1)
    write_wiki(wiki, wiki_dir)
    emb = _MemEmbedder()
    index = SemanticIndex.build(wiki, emb, size_words=50, overlap_words=10)
    GraphBuilder(tmp_path / "graph").build(wiki, index)
    store = GraphStore(tmp_path / "graph", writable=True)
    store.remember("2026-09-01", [MemoryFact("the database", "is", "Kuzu"),
                                  MemoryFact("the project", "uses", "Python")], emb)
    store.remember("2026-09-05", [MemoryFact("the database", "is", "Postgres")], emb)   # closes "is Kuzu"
    return WikiWebApp(wiki_dir, index=index, graph=store, sessions=sessions), store


def test_fact_detail_has_history_supersession_and_unknown_ids(tmp_path):
    app, store = _memory_app(tmp_path)
    try:
        rows = {(r["object"], r["status"]): r for r in store.list_assertions()}
        old, new = rows[("Kuzu", "past")], rows[("Postgres", "current")]
        d = store.fact_detail(old["id"])
        assert [r["object"] for r in d["group"]] == ["Kuzu", "Postgres"]      # the attribute's history, in order
        assert d["fact"]["status"] == "past" and d["fact"]["kind"] is None
        assert [r["id"] for r in d["superseded_by"]] == [new["id"]]
        assert [r["id"] for r in store.fact_detail(new["id"])["supersedes"]] == [old["id"]]
        assert store.fact_detail("no-such-fact") is None
    finally:
        store.close()


def test_web_facts_browser_and_fact_detail(tmp_path):
    from openwiki.sessions import SessionCorpus
    transcript = tmp_path / "session.txt"
    transcript.write_text("User: which database do we use?\n\nAssistant: we moved the database from Kuzu to "
                          "Postgres last week.\n\nUser: fine.", encoding="utf-8")
    app, store = _memory_app(tmp_path, sessions=SessionCorpus([transcript]))
    try:
        cur = app.memory_facts()
        assert cur["total"] == 2 and {f["object"] for f in cur["facts"]} == {"Postgres", "Python"}
        allf = app.memory_facts(status="", q="database")
        assert {f["object"] for f in allf["facts"]} == {"Kuzu", "Postgres"}
        fid = next(f["id"] for f in allf["facts"] if f["object"] == "Postgres")
        d = app.memory_fact(fid)
        assert d["fact"]["object"] == "Postgres" and len(d["group"]) == 2
        said = d["said"]
        assert said["available"] and said["excerpts"]                         # found in the transcript
        assert not said["in_window"]                                          # an undated text: not in a window
        assert any("Postgres" in t["text"] for t in said["excerpts"][0]["turns"])
        with pytest.raises(KeyError):
            app.memory_fact("no-such-fact")
    finally:
        store.close()


def test_web_fact_detail_without_session_search(tmp_path):
    app, store = _memory_app(tmp_path)
    try:
        fid = app.memory_facts()["facts"][0]["id"]
        assert app.memory_fact(fid)["said"] == {"at": app.memory_fact(fid)["said"]["at"], "available": False,
                                                "in_window": False, "excerpts": []}
    finally:
        store.close()


def test_http_facts_browser_keeps_a_blank_status(tmp_path):
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    from openwiki.web.server import make_handler

    app, store = _memory_app(tmp_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(path):
        with urllib.request.urlopen(base + path, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    try:
        assert get("/api/memory/facts?q=database")["total"] == 1                # default: current only
        allf = get("/api/memory/facts?q=database&status=&sort=subject")       # "alle": the blank must survive
        assert allf["total"] == 2 and {f["status"] for f in allf["facts"]} == {"current", "past"}
        fid = allf["facts"][0]["id"]
        assert get(f"/api/memory/fact/{fid}")["fact"]["id"] == fid
        with pytest.raises(urllib.error.HTTPError) as err:
            get("/api/memory/fact/no-such-fact")
        assert err.value.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
        store.close()


def test_web_memory_endpoints_without_graph(tmp_path):
    from openwiki.web.server import WikiWebApp
    (tmp_path / "wiki" / "pages").mkdir(parents=True)
    app = WikiWebApp(tmp_path / "wiki")
    with pytest.raises(RuntimeError):
        app.memory_facts()
    with pytest.raises(RuntimeError):
        app.memory_fact("x")
    assert json.dumps(app.memory_info())                                      # unchanged: no graph → unavailable
