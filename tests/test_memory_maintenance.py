"""Direction K (M7): the Memory tab's maintenance panel — the review list of facts that go stale, the forgetting
candidates and the approval queue together, with close / confirm / forget through the journal."""

from __future__ import annotations

import json

import pytest

from openwiki.graph.journal import append_ids, queued_ids, read_journal


def test_id_ops_queue_and_read_back(tmp_path):
    j = tmp_path / "graph.journal.jsonl"
    assert append_ids(j, "confirm", ["a", " ", "b"], now=5) == 2
    assert append_ids(j, "forget", ["c"], reason="reviewed") == 1
    assert append_ids(j, "forget", []) == 0
    with pytest.raises(ValueError):
        append_ids(j, "delete", ["a"])
    recs = read_journal(j)
    assert [r["op"] for r in recs] == ["confirm", "forget"] and recs[0] == {"op": "confirm", "t": 5, "ids": ["a", "b"]}
    assert recs[1]["reason"] == "reviewed"
    assert queued_ids(j) == {"close": [], "confirm": ["a", "b"], "forget": ["c"]}


def test_review_queue_orders_by_kind_then_least_recently_confirmed():
    from openwiki.memory_browse import review_queue
    rows = [{"id": "new", "subject": "the wiki", "predicate": "has", "object": "51 pages", "status": "current",
             "last_seen": 2000},
            {"id": "old", "subject": "the suite", "predicate": "has", "object": "12 tests", "status": "current",
             "last_seen": 1000},
            {"id": "gone", "subject": "the suite", "predicate": "has", "object": "9 tests", "status": "past",
             "last_seen": 500},
            {"id": "plain", "subject": "the database", "predicate": "is", "object": "Kuzu", "status": "current"}]
    q = review_queue(rows)
    assert [f["id"] for f in q["facts"]] == ["old", "new"] and q["count"] == 2 and q["by_kind"] == {"count": 2}
    assert [f["id"] for f in review_queue(rows, offset=1, limit=1)["facts"]] == ["new"]


def _app(tmp_path):
    from test_memory import _MemEmbedder
    from test_memory_browse import _memory_app
    from openwiki.graph.memory import MemoryFact

    app, store = _memory_app(tmp_path)
    emb = _MemEmbedder()
    store.remember("s-a", [MemoryFact("the wiki", "has", "51 pages")], emb, now=1000)
    store.remember("s-b", [MemoryFact("the test suite", "has", "12 tests"),
                           MemoryFact("OpenWiki", "current release is", "v0.1.0"),
                           MemoryFact("v0.74.0", "was pushed and tagged", "yes")], emb, now=2000)
    return app, store, emb


def test_maintenance_panel_closes_confirms_and_forgets_through_the_journal(tmp_path):
    app, store, emb = _app(tmp_path)
    folds = []
    app.on_approved = lambda: folds.append(1)
    try:
        m = app.memory_maintenance()
        assert m["writable"] and m["staged"] == []
        review = {f["object"]: f for f in m["review"]["facts"]}
        assert review["v0.1.0"]["kind"] == "version"
        counts = [f["object"] for f in m["review"]["facts"] if f["kind"] == "count"]
        assert counts == ["51 pages", "12 tests"]                              # least recently said first
        forget = {f["object"]: f for f in m["forget"]}
        assert forget["yes"]["reason"] == "ephemeral" and forget["yes"]["status"] == "current"
        release, pages, pushed = review["v0.1.0"]["id"], review["51 pages"]["id"], forget["yes"]["id"]

        for action, fid in (("close", release), ("confirm", pages), ("forget", pushed)):
            assert app.memory_maintain(action, [fid]) == {"action": action, "queued": [fid], "folding": True}
        assert len(folds) == 3                                                 # each starts a fold
        assert app.memory_maintenance()["queued"] == {"close": [release], "confirm": [pages], "forget": [pushed]}

        before = {r["id"]: r for r in store.list_assertions()}
        res = store.fold_journal(emb)
        assert (res["retired"], res["confirmed"], res["forgotten"]) == (1, 1, 1)
        after = {r["id"]: r for r in store.list_assertions()}
        assert after[release]["status"] == "past"                              # no longer true: closed, kept
        assert after[pushed]["status"] == "forgotten" and after[pushed]["forgotten"] == "reviewed"
        assert after[pages]["confidence"] > before[pages]["confidence"]        # re-affirmed …
        assert after[pages]["last_seen"] > before[pages]["last_seen"]
        m = app.memory_maintenance()
        assert m["queued"] == {"close": [], "confirm": [], "forget": []}
        assert [f["object"] for f in m["review"]["facts"] if f["kind"] == "count"] == ["12 tests", "51 pages"]
        assert "yes" not in {f["object"] for f in m["forget"]}                 # … and moved to the end
    finally:
        store.close()


def test_maintenance_refuses_bad_input_dry_runs_and_wiki_mode(tmp_path):
    from openwiki.project import Project
    app, store, _ = _app(tmp_path)
    try:
        with pytest.raises(ValueError):
            app.memory_maintain("delete", ["x"])
        with pytest.raises(ValueError):
            app.memory_maintain("close", [])
        app.dry_run = True
        with pytest.raises(RuntimeError):
            app.memory_maintain("close", ["x"])
        assert app.memory_maintenance()["writable"] is False
        app.dry_run = False
        root = tmp_path / "wiki-mode"
        root.mkdir()
        (root / "openwiki.toml").write_text('[project]\nname = "w"\n', encoding="utf-8")
        app.project = Project.load(root)
        with pytest.raises(RuntimeError):
            app.memory_maintain("close", ["x"])
        assert read_journal(app._graph_db_path().with_name(app._graph_db_path().name + ".journal.jsonl")) == []
    finally:
        store.close()


def test_http_maintenance_endpoints(tmp_path):
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    from openwiki.web.server import make_handler

    app, store, _ = _app(tmp_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def post(body):
        req = urllib.request.Request(base + "/api/memory/maintain", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    try:
        with urllib.request.urlopen(base + "/api/memory/maintenance?limit=1", timeout=5) as r:
            m = json.loads(r.read().decode("utf-8"))
        assert m["review"]["limit"] == 1 and len(m["review"]["facts"]) == 1
        assert m["review"]["count"] == 4                  # 2 counts + 2 versions ("v0.74.0 was pushed …" too)
        fid = m["review"]["facts"][0]["id"]
        assert post({"action": "confirm", "ids": [fid]})["queued"] == [fid]
        with pytest.raises(urllib.error.HTTPError) as err:
            post({"action": "delete", "ids": [fid]})
        assert err.value.code == 400
        app.dry_run = True
        with pytest.raises(urllib.error.HTTPError) as err:
            post({"action": "close", "ids": [fid]})
        assert err.value.code == 409
    finally:
        httpd.shutdown()
        httpd.server_close()
        store.close()
