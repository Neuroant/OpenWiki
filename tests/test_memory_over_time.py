"""Direction K (M5): memory over time — facts learned, closed, retracted and forgotten per day, the current facts at
the end of each, and the facts behind one day (the Dynamik chart)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from openwiki.analysis.memory import fact_events, memory_over_time, period_events
from openwiki.graph.temporal import parse_date
from openwiki.memory_browse import learned_at

DAY = 86400
T0 = parse_date("2026-09-01")
NOW = T0 + 5 * DAY + 3600


def _f(i, day, status="current", **kw):
    said = T0 + day * DAY + 3600
    return dict({"id": f"f{i}", "subject": f"s{i}", "predicate": "p", "object": f"o{i}", "session_id": "undated",
                 "status": status, "valid_from": said, "valid_to": None, "expired_at": None, "forgotten_at": None,
                 "created_at": said + 3600}, **kw)


FACTS = [
    _f(1, 0),                                                                   # learned day 0, still current
    _f(2, 0, "past", valid_to=T0 + 2 * DAY + 7200),                             # the world changed on day 2
    _f(3, 1, "forgotten", forgotten_at=T0 + 3 * DAY + 60),                      # forgotten on day 3
    _f(4, 1, "past", valid_to=int(parse_date("1969-01-01"))),                   # history: "until 1969"
    _f(5, 4, "future", valid_from=NOW + 10 * DAY, created_at=T0 + 4 * DAY),     # planned: not current yet
    _f(6, 2, "retracted", expired_at=T0 + 4 * DAY),                             # corrected on day 4
]


def test_fact_events_clamp_and_classify():
    assert fact_events(FACTS[0], NOW) == (T0 + 3600, T0 + 3600, None, None)
    assert fact_events(FACTS[1], NOW)[2:] == (T0 + 2 * DAY + 7200, "closed")
    learned, current_from, ended, cause = fact_events(FACTS[3], NOW)
    assert ended == learned == current_from and cause == "closed"            # closed the day it was learned
    assert fact_events(FACTS[4], NOW) == (T0 + 4 * DAY, None, None, None)     # learned when written; not valid yet
    still = dict(FACTS[0], valid_to=NOW + DAY)                                # an end in the future doesn't count
    assert fact_events(still, NOW)[2] is None


def test_memory_over_time_per_day():
    series = memory_over_time(FACTS, NOW)
    assert series["bucket"] == "day" and [p["label"] for p in series["periods"]][0] == "2026-09-01"
    by = {p["label"][-2:]: p for p in series["periods"]}
    assert [by[d]["learned"] for d in ("01", "02", "03", "04", "05", "06")] == [2, 2, 1, 0, 1, 0]
    assert by["02"]["closed"] == 1 and by["03"]["closed"] == 1                # history (day 1), the change (day 2)
    assert by["04"]["forgotten"] == 1 and by["05"]["retracted"] == 1
    assert [by[d]["current"] for d in ("01", "02", "03", "04", "05", "06")] == [2, 3, 3, 2, 1, 1]
    assert series["totals"] == {"learned": 6, "closed": 2, "retracted": 1, "forgotten": 1, "current": 1}
    assert series["totals"]["current"] == sum(1 for f in FACTS if f["status"] == "current")
    assert memory_over_time([], NOW) == {"bucket": "day", "periods": [],
                                         "totals": {"learned": 0, "closed": 0, "retracted": 0, "forgotten": 0,
                                                    "current": 0}}


def test_memory_over_time_goes_weekly_over_a_long_span():
    facts = [_f(n, n * 10) for n in range(25)]                                # 240 days
    series = memory_over_time(facts, T0 + 250 * DAY)
    assert series["bucket"] == "week" and series["totals"]["learned"] == 25
    assert all(datetime.fromtimestamp(p["t"], timezone.utc).weekday() == 0 for p in series["periods"])   # Mondays
    assert series["periods"][-1]["current"] == 25


def test_period_events_list_one_days_facts():
    day2 = period_events(FACTS, T0 + 2 * DAY, T0 + 3 * DAY, NOW)
    assert day2["counts"] == {"learned": 1, "closed": 1, "retracted": 0, "forgotten": 0}
    assert [f["id"] for f in day2["learned"]] == ["f6"] and [f["id"] for f in day2["closed"]] == ["f2"]
    capped = period_events(FACTS, T0, T0 + DAY, NOW, limit=1)
    assert capped["counts"]["learned"] == 2 and len(capped["learned"]) == 1


def test_learned_at_is_never_after_the_record():
    day = int(parse_date("2026-10-09"))
    agent = {"valid_from": None, "session_id": "agent-2026-10-09", "created_at": day + 3 * 3600}
    assert learned_at(agent) == day + 3 * 3600                                # midday default lies ahead of it
    backfill = {"valid_from": day - 20 * DAY, "session_id": "claude-2026-09-19", "created_at": day}
    assert learned_at(backfill) == day - 20 * DAY                             # a backfilled day keeps its date


def test_web_memory_over_time_and_period(tmp_path):
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    from test_memory_browse import _memory_app
    from openwiki.web.server import make_handler

    app, store = _memory_app(tmp_path)        # "is Kuzu" (2026-09-01) closed by "is Postgres" (2026-09-05)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        series = app.analyze_memory()["over_time"]
        assert series["totals"]["learned"] == 3 and series["totals"]["closed"] == 1
        assert series["totals"]["current"] == 2
        day = int(parse_date("2026-09-05"))
        with urllib.request.urlopen(f"{base}/api/analyze/memory/period?start={day}&bucket=day", timeout=5) as r:
            period = json.loads(r.read().decode("utf-8"))
        assert [f["object"] for f in period["learned"]] == ["Postgres"]
        assert [f["object"] for f in period["closed"]] == ["Kuzu"] and period["bucket"] == "day"
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(f"{base}/api/analyze/memory/period?start=yesterday", timeout=5)
        assert err.value.code == 400
    finally:
        httpd.shutdown()
        httpd.server_close()
        store.close()
