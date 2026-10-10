"""Direction K (M8): sessions in the browser — the conversation by day, a stretch of one session's turns for the
viewer, full-text search, and the web endpoints."""

from __future__ import annotations

import json

import pytest

from openwiki import sessions as ss
from openwiki.policy import REDACTED
from test_sessions import TOKEN, _line, _transcript


def test_session_days_group_turns_by_day_and_stretch():
    turns = [ss.Turn("a", 0, "2026-09-01T09:00:00Z", "User", "x"),
             ss.Turn("a", 1, "2026-09-01T23:59:00Z", "Assistant", "y"),
             ss.Turn("a", 2, "2026-09-02T08:00:00Z", "User", "z"),
             ss.Turn("b", 0, "2026-09-02T09:00:00Z", "Ann", "w"),
             ss.Turn("a", 3, "2026-09-02T10:00:00Z", "Assistant", "v"),
             ss.Turn("p", 0, "", "Ann", "undated")]
    d = ss.session_days(turns)
    assert [x["day"] for x in d["days"]] == ["2026-09-02", "2026-09-01"]          # newest first
    day2, day1 = d["days"]
    assert (day2["turns"], day2["user"]) == (3, 1) and (day1["turns"], day1["user"]) == (2, 1)
    assert day2["ranges"] == [{"session": "a", "from": 2, "to": 3, "turns": 2},   # "a" goes on around "b"
                              {"session": "b", "from": 0, "to": 0, "turns": 1}]
    assert day1["ranges"] == [{"session": "a", "from": 0, "to": 1, "turns": 2}]
    assert d["undated"] == [{"session": "p", "turns": 1}]


def test_window_shows_a_stretch_redacted_and_cut():
    turns = [ss.Turn("a", n, f"2026-09-01T09:0{n}:00Z", "User" if n % 2 == 0 else "Assistant", f"turn {n}")
             for n in range(6)]
    long = f"my token is {TOKEN} " + "x" * 5000
    turns[3] = ss.Turn("a", 3, "2026-09-01T09:03:00Z", "Assistant", long)
    index = ss.SessionIndex(turns)
    w = index.window("a", 2, 4)
    assert [t["n"] for t in w["turns"]] == [2, 3, 4] and (w["first"], w["last"]) == (0, 5)
    t3 = w["turns"][1]
    assert TOKEN not in t3["text"] and REDACTED in t3["text"]                    # credentials redacted
    assert t3["cut"] and t3["chars"] == len(long) and len(t3["text"]) < 4100
    full = index.window("a", 3, 3, full=True)["turns"][0]
    assert not full["cut"] and full["text"].endswith("x" * 100)
    assert [t["n"] for t in index.window("a", -5, 99)["turns"]] == [0, 1, 2, 3, 4, 5]   # clamped to the session
    assert index.window("nope", 0, 5) == {"session": "nope", "first": None, "last": None, "turns": []}


def _corpus(tmp_path):
    path = tmp_path / "s1.jsonl"
    path.write_text(_transcript(
        _line("user", "Which database do we use for the graph?", "2026-09-05T10:00:00Z"),
        _line("assistant", [{"type": "text", "text": "We moved the database from Kuzu to Postgres."}],
              "2026-09-05T10:00:05Z"),
        _line("user", "Fine, keep Postgres.", "2026-09-05T10:01:00Z")), encoding="utf-8")
    return ss.SessionCorpus([path])


def test_web_sessions_overview_viewer_and_search(tmp_path):
    from test_memory_browse import _memory_app
    app, store = _memory_app(tmp_path, sessions=_corpus(tmp_path))   # facts said on 2026-09-01 and 2026-09-05
    try:
        o = app.sessions_overview()
        assert o["available"] and o["turns"] == 3 and o["sessions"] == 1
        days = {d["day"]: d for d in o["days"]}
        assert [d["day"] for d in o["days"]] == ["2026-09-05", "2026-09-01"]
        assert (days["2026-09-05"]["turns"], days["2026-09-05"]["user"], days["2026-09-05"]["facts"]) == (3, 2, 1)
        assert days["2026-09-05"]["ranges"] == [{"session": "s1", "from": 0, "to": 2, "turns": 3}]
        assert (days["2026-09-01"]["turns"], days["2026-09-01"]["facts"]) == (0, 2)   # facts, no transcript
        w = app.session_turns("s1", 2, 0)                                           # reversed bounds are fine
        assert [t["speaker"] for t in w["turns"]] == ["User", "Assistant", "User"]
        with pytest.raises(KeyError):
            app.session_turns("nope", 0, 5)
        hits = app.sessions_search("Postgres database")["excerpts"]
        assert hits and hits[0]["session"] == "s1" and any(t["hit"] for t in hits[0]["turns"])
        with pytest.raises(ValueError):
            app.sessions_search("  ")
    finally:
        store.close()


def test_web_sessions_without_transcripts(tmp_path):
    from test_memory_browse import _memory_app
    app, store = _memory_app(tmp_path)
    try:
        assert app.sessions_overview() == {"available": False}
        with pytest.raises(RuntimeError):
            app.session_turns("s1", 0, 5)
        with pytest.raises(RuntimeError):
            app.sessions_search("database")
    finally:
        store.close()


def test_http_session_endpoints(tmp_path):
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    from test_memory_browse import _memory_app
    from openwiki.web.server import make_handler

    app, store = _memory_app(tmp_path, sessions=_corpus(tmp_path))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(path):
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def status(path):
        with pytest.raises(urllib.error.HTTPError) as err:
            get(path)
        return err.value.code
    try:
        assert get("/api/sessions")["turns"] == 3
        assert [t["n"] for t in get("/api/sessions/turns?session=s1&from=1&to=2")["turns"]] == [1, 2]
        assert get("/api/sessions/search?q=Kuzu")["excerpts"][0]["session"] == "s1"
        assert status("/api/sessions/turns?session=nope&from=0&to=1") == 404
        assert status("/api/sessions/turns?session=s1&from=x&to=1") == 400
        assert status("/api/sessions/search?q=") == 400
    finally:
        httpd.shutdown()
        httpd.server_close()
        store.close()
