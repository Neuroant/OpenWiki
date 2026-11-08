"""The authority rule (Path C, v0.124 — C1.3 – C1.5, C1.8, C1.9, C1.12): observations written by `GraphStore.observe`
(rules 1–4), rule 5 in `remember`, the `observe` journal op, acceptance and the world cache, the review list without
governed facts, session kinds, the scheduled task, and `owiki world accept / observe / revoke` end to end — offline,
on throwaway Kuzu graphs with a bag-of-words embedder."""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess

import numpy as np
import pytest

from openwiki.graph.memory import MemoryFact, fact_line, session_kind
from openwiki.graph.temporal import parse_date, status
from openwiki.world.bindings import Binding, World
from openwiki.world.observe import (Authority, accept, acceptance, active_bindings, binding_fingerprint, due,
                                    observation, read_accepted, read_cache, revoke, update_cache)
from openwiki.world.sensors import Reading

T = parse_date


class _Emb:
    VOCAB = ["tests", "suite", "release", "version", "collects", "openwiki", "server", "serving"]
    name = "fake:observe"

    def _vec(self, text):
        low = text.lower()
        v = np.array([float(low.count(w)) for w in self.VOCAB], dtype=np.float32)
        return v if v.any() else v + 1e-6

    def embed_documents(self, texts):
        return np.vstack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


def _graph(tmp_path, name="graph"):
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphBuilder, GraphStore
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki, WikiPage
    pages = [WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=1, pdf_page_end=1,
                      text="tests suite release version")]
    wiki = Wiki(title="T", pages=pages, source="x.md", split_level=1)
    GraphBuilder(tmp_path / name).build(wiki, SemanticIndex.build(wiki, _Emb(), size_words=50, overlap_words=10))
    return GraphStore(tmp_path / name, writable=True)


TESTS = Binding("tests", "repo", "pytest-collect", subject="OpenWiki", predicate="test suite collects",
                object="{value} tests", governs=r"\b\d{2,}\s+(?:\w+\s+){0,2}tests?\b|test suite \| (?:has|collects)",
                excludes=r"\bnew tests\b", belief=r"(?P<value>\d[\d,]+)", every=0)


def _obs(value, valid_from=None, read_at=None, binding=TESTS):
    return observation(binding, Reading(binding.id, value, read_at, changed_at=valid_from))


def _state(g, now):
    return {(r["subject"], r["object"]): status(r, now) for r in g._load_assertions()}


def _old_beliefs(g):
    g.remember("s1", [MemoryFact("test suite", "has passing tests", "823"), MemoryFact("Path A", "has", "10 new tests"),
                      MemoryFact("CI", "runs", "848 tests")], _Emb(), session_date=T("2025-01-10"), now=T("2025-01-10"))


def test_observe_writes_observations_by_rules_one_to_four(tmp_path):
    g = _graph(tmp_path)
    try:
        _old_beliefs(g)
        g.remember("s2", [MemoryFact("suite", "has", "800 tests")], _Emb(), session_date=T("2025-02-05"),
                   now=T("2025-02-05"))
        auth = Authority([TESTS])
        at = T("2025-02-10")
        res = g.observe([_obs(848, valid_from=T("2025-02-01"), read_at=at)], _Emb(), auth, now=at)
        (e,) = res["observations"]
        assert e["action"] == "added" and e["replaced"] == [] and res["added"] == 1
        assert sorted(f["object"] for f in e["closed"]) == ["800 tests", "823"]                 # rule 3
        assert [f["object"] for f in e["discrepancies"]] == ["800 tests"]          # believed after the change
        assert [f["object"] for f in e["agreed"]] == ["848 tests"]                               # rule 4
        st = _state(g, at)
        assert st[("test suite", "823")] == "past" and st[("suite", "800 tests")] == "past"
        assert st[("CI", "848 tests")] == "current" and st[("Path A", "10 new tests")] == "current"   # excluded
        obs = g.observations()["tests"]
        assert (obs["object"], obs["source"], obs["session_id"], obs["valid_from"]) == (
            "848 tests", "observed", "sensor:tests", T("2025-02-01"))
        assert (obs["id"], next(r["id"] for r in g._load_assertions() if r["object"] == "823")) in \
            g._supersedes_edges()
        again = g.observe([_obs(848, valid_from=T("2025-02-01"), read_at=T("2025-02-20"))], _Emb(), auth,
                          now=T("2025-02-20"))                                                     # rule 1
        assert (again["unchanged"], again["added"], again["closed"]) == (1, 0, 0)
        assert g.observations()["tests"]["last_seen"] == T("2025-02-20")
        moved = g.observe([_obs(849, valid_from=T("2025-03-01"), read_at=T("2025-03-02"))], _Emb(), auth,
                          now=T("2025-03-02"))                                                     # rule 2
        (e,) = moved["observations"]
        assert e["action"] == "added" and e["replaced"] == ["848 tests"]
        assert [f["subject"] for f in e["closed"]] == ["CI"]           # agreed with 848, not with 849
        st = _state(g, T("2025-03-02"))
        assert st[("OpenWiki", "848 tests")] == "past" and st[("OpenWiki", "849 tests")] == "current"
        before = len(g._load_assertions())
        plan = g.observe([_obs(900, read_at=T("2025-03-05"))], None, auth, now=T("2025-03-05"), dry_run=True)
        assert plan["added"] == 1 and len(g._load_assertions()) == before                    # a dry run writes nothing
    finally:
        g.close()


def test_without_authority_only_the_observation_is_written(tmp_path):
    g = _graph(tmp_path)
    try:
        _old_beliefs(g)
        res = g.observe([_obs(848, valid_from=T("2025-02-01"), read_at=T("2025-02-10"))], _Emb(), None,
                        now=T("2025-02-10"))
        assert res["added"] == 1 and res["closed"] == 0
        assert _state(g, T("2025-02-10"))[("test suite", "823")] == "current"
    finally:
        g.close()


def test_rule_five_a_belief_said_while_an_observation_held(tmp_path):
    g = _graph(tmp_path)
    try:
        auth = Authority([TESTS])
        g.observe([_obs(848, valid_from=T("2025-02-01"), read_at=T("2025-02-01"))], _Emb(), auth, now=T("2025-02-01"))
        g.observe([_obs(848, valid_from=T("2025-02-01"), read_at=T("2025-03-01"))], _Emb(), auth, now=T("2025-03-01"))
        said_then = g.remember("s-old", [MemoryFact("test suite", "has passing tests", "823")], _Emb(),
                               session_date=T("2025-02-15"), now=T("2025-03-05"), authority=auth)
        assert (said_then["contradicted"], said_then["added"]) == (1, 0)
        rec = next(r for r in g._load_assertions() if r["object"] == "823")
        assert rec["valid_from"] == rec["valid_to"] == T("2025-02-15")          # a zero-length interval: never current
        obs_id = g.observations()["tests"]["id"]
        assert (obs_id, rec["id"]) in g._supersedes_edges()
        agrees = g.remember("s-ok", [MemoryFact("CI", "runs", "848 tests")], _Emb(), session_date=T("2025-02-20"),
                            now=T("2025-03-05"), authority=auth)
        assert (agrees["contradicted"], agrees["added"]) == (0, 1)
        later = g.remember("s-new", [MemoryFact("suite", "has", "850 tests")], _Emb(), session_date=T("2025-03-10"),
                           now=T("2025-03-10"), authority=auth)       # after the last confirmation: may be news
        assert (later["contradicted"], later["added"]) == (0, 1)
        assert _state(g, T("2025-03-10"))[("suite", "850 tests")] == "current"
        g.remember("s-x", [MemoryFact("x", "is", "y", source="observed")], _Emb())       # only a sensor observes
        assert next(r for r in g._load_assertions() if r["object"] == "y")["source"] is None
        g.remember("s-same", [MemoryFact("OpenWiki", "test suite collects", "900 tests")], _Emb(),
                   session_date=T("2025-04-01"), now=T("2025-04-01"))        # no belief joins the sensor's group
        assert g.observations()["tests"]["object"] == "848 tests"
    finally:
        g.close()


def test_recall_shows_an_agreeing_belief_once_through_its_observation(tmp_path):
    g = _graph(tmp_path)
    try:
        g.remember("s1", [MemoryFact("OpenWiki", "test suite collects", "848 tests")], _Emb(),
                   session_date=T("2025-01-10"), now=T("2025-01-10"))
        before = g.recall("how many tests does the suite collect", _Emb(), k=5)
        assert [h["session_id"] for h in before] == ["s1"]
        g.observe([_obs(848, valid_from=T("2025-02-01"), read_at=T("2025-02-10"))], _Emb(), Authority([TESTS]),
                  now=T("2025-02-10"))
        hits = g.recall("how many tests does the suite collect", _Emb(), k=5)
        assert [h["session_id"] for h in hits] == ["sensor:tests"]          # the belief agrees: shown once
    finally:
        g.close()


def test_the_observe_op_through_the_journal(tmp_path):
    from openwiki.graph.journal import append_observe, read_journal
    g = _graph(tmp_path)
    try:
        _old_beliefs(g)
        path = g._journal_path
        assert append_observe(path, [_obs(848, valid_from=T("2025-02-01"), read_at=T("2025-02-10"))],
                              now=T("2025-02-10")) == 1
        assert append_observe(path, [{"no": "binding"}]) == 0
        assert [r["op"] for r in read_journal(path)] == ["observe"]
        res = g.fold_journal(_Emb(), authority=Authority([TESTS]))
        assert res["observed"] == 1 and read_journal(path) == []
        assert g.observations()["tests"]["object"] == "848 tests"
        assert next(r for r in g._load_assertions() if r["object"] == "823")["valid_to"] == T("2025-02-01")
    finally:
        g.close()


def test_acceptance_is_pinned_to_the_binding(tmp_path):
    accept(tmp_path, [TESTS], {"tests": {"governed": 3, "differs": 1}}, now=100)
    rec = read_accepted(tmp_path)["tests"]
    assert (rec["fingerprint"], rec["accepted_at"], rec["differs"]) == (binding_fingerprint(TESTS), 100, 1)
    assert acceptance(TESTS, read_accepted(tmp_path)) == "accepted"
    wider = dataclasses.replace(TESTS, governs=TESTS.governs + "|test count")
    assert acceptance(wider, read_accepted(tmp_path)) == "changed"          # a changed pattern needs a new look
    assert active_bindings(World(bindings=[wider]), read_accepted(tmp_path)) == []
    assert active_bindings(World(bindings=[TESTS]), read_accepted(tmp_path)) == [TESTS]
    assert acceptance(dataclasses.replace(TESTS, id="other"), read_accepted(tmp_path)) == "not accepted"
    assert revoke(tmp_path, ["tests", "nope"]) == ["tests"] and read_accepted(tmp_path) == {}


def test_the_world_cache_and_when_a_sensor_is_due(tmp_path):
    fast = dataclasses.replace(TESTS, id="tabs", type="count", every=60)
    assert due(fast, {}, 1000) is True
    update_cache(tmp_path, {"tabs": Reading("tabs", (1, 2), 1000, commit="abc1234 x")})
    cache = read_cache(tmp_path)
    assert cache["tabs"]["value"] == [1, 2] and cache["tabs"]["commit"] == "abc1234 x"
    assert due(fast, cache, 1030) is False and due(fast, cache, 1060) is True
    assert due(TESTS, cache, 10 ** 9) is False and due(TESTS, cache, 10 ** 9, slow=True) is True   # every 0


def test_authority_judges_beliefs_against_observations():
    auth = Authority([TESTS])
    assert auth.governing("suite | has | 823 tests") == ["tests"] and auth.governing("Path A | has | 10 new tests") == []
    held = {"object": "848 tests"}                                          # an observation record: its claim
    assert auth.verdict("tests", {"subject": "s", "predicate": "has", "object": "823 tests"}, held) == "differs"
    assert auth.verdict("tests", {"subject": "CI", "predicate": "runs", "object": "848 tests"}, held) == "agrees"
    assert auth.verdict("tests", {"subject": "CI", "predicate": "runs", "object": "848"}, 848) == "agrees"


def test_observations_and_governed_facts_leave_the_review_list():
    from openwiki.memory_browse import review_queue
    rows = [{"id": "a", "status": "current", "subject": "OpenWiki", "predicate": "current release is",
             "object": "v0.120.0", "source": None, "last_seen": 1},
            {"id": "b", "status": "current", "subject": "OpenWiki", "predicate": "latest release tag is",
             "object": "v0.123.0", "source": "observed", "last_seen": 2},
            {"id": "c", "status": "current", "subject": "the parser", "predicate": "has version", "object": "v2.1",
             "source": None, "last_seen": 3}]
    listed = {f["id"] for f in review_queue(rows)["facts"]}
    assert listed == {"a", "c"}                                             # an observation is never listed
    governed = lambda f: f["subject"] == "OpenWiki"                         # noqa: E731
    assert {f["id"] for f in review_queue(rows, governed=governed)["facts"]} == {"c"}


def test_session_kinds_and_observed_lines():
    from openwiki.graph.store import _line_key
    assert [session_kind(s) for s in ("sensor:tests", "agent-2026-11-08", "twin:live", "claude-2026-11-01", "",
                                      "conv-26-s1")] == ["sensor", "agent", "twin", "conversation", "conversation",
                                                         "conversation"]
    line = fact_line({"subject": "OpenWiki", "predicate": "test suite collects", "object": "848 tests",
                      "valid_from": T("2025-02-01"), "session_id": "sensor:tests", "source": "observed"})
    assert line.endswith("; sensor:tests; observed)")
    assert _line_key(line) == "openwiki test suite collects 848 tests"      # wiki_remember's replaces still match
    from openwiki.graph.memory import coerce_source
    assert coerce_source("observed") is None                                # capture can't claim a sensor's authority


def test_observations_are_not_forgotten(tmp_path):
    serving = Binding("serving", "repo", "http-json", subject="server", predicate="is serving", object="{value}",
                      governs=r"^server \| is serving")
    g = _graph(tmp_path)
    try:
        g.remember("s1", [MemoryFact("server", "is serving", "v0.78.0")], _Emb())
        g.observe([_obs("v0.80.0", read_at=T("2025-01-01"), binding=serving)], _Emb(), None, now=T("2025-01-01"))
        flagged = {c["object"] for c in g.forget_candidates()}
        assert "v0.78.0" in flagged and "v0.80.0" not in flagged            # a sensor keeps its observation
    finally:
        g.close()


def test_world_observe_is_a_scheduled_task():
    from openwiki.schedule import TASKS, render_task_xml, task_arguments
    spec = TASKS["world-observe"]
    assert spec["every_minutes"] == 15 and spec["args"] == ["world", "observe", "--due", "--fast"]
    xml = render_task_xml("pythonw.exe", task_arguments("C:/proj", "world-observe"), "C:/proj", spec["at"], "observe",
                          spec["every_minutes"], spec["limit"])
    assert "<Interval>PT15M</Interval>" in xml and "world observe --due --fast" in xml and "PT10M" in xml


# -- the read-time overlay (C1.6) --------------------------------------------------------------------------

def _overlay(tmp_path, binding=TESTS, value=860, accepted=True, budget_ms=200, calls=None, quality="complete",
             now=1_000):
    from openwiki.world.bindings import Environment
    from openwiki.world.observe import Overlay

    def reader(bid, type_, params, root=None, host=None, light=False):
        if calls is not None:
            calls.append(bid + (" light" if light else ""))
        return Reading(bid, value, now, quality=quality, source="pytest --collect-only",
                       changed_at=None if light else 900, commit="" if light else "abc1234 Add tests")
    world = World(bindings=[binding], environments=[Environment("repo", root=tmp_path)])
    if accepted:
        accept(tmp_path, [binding])
    return Overlay(world, read_accepted(tmp_path), tmp_path, journal=tmp_path / "journal.jsonl",
                   budget_ms=budget_ms, now=now, reader=reader)


def _hit(subject, predicate, obj, session="s1", source=None, hid="h1"):
    return {"id": hid, "subject": subject, "predicate": predicate, "object": obj, "session_id": session,
            "source": source, "valid_from": 1, "valid_to": None}


def test_overlay_replaces_what_a_fresh_reading_contradicts(tmp_path):
    from openwiki.graph.journal import read_journal
    fast = dataclasses.replace(TESTS, type="count", every=60)                 # a fast sensor, so the hook may read it
    calls = []
    ov = _overlay(tmp_path, binding=fast, calls=calls)
    hits = [_hit("test suite", "has passing tests", "848", hid="a"), _hit("CI", "runs", "860 tests", hid="b"),
            _hit("the parser", "is", "fast", hid="c")]
    out = ov(hits)
    assert calls == ["tests light", "tests"]                    # no cache yet: read now — light, then in full
    assert [h["id"] for h in out] == ["obs:tests:860 tests", "b", "c"]
    assert out[0]["object"] == "860 tests" and out[0]["overlay"] == "just now; pytest --collect-only"
    assert fact_line(out[0]) == "- OpenWiki test suite collects 860 tests  (observed just now; pytest --collect-only)"
    assert ov.report["replaced"][0]["was"] == "test suite | has passing tests | 848" and ov.report["queued"] == 1
    assert [r["op"] for r in read_journal(tmp_path / "journal.jsonl")] == ["observe"]   # for the next writer
    assert read_cache(tmp_path)["tests"]["rendered"] == "860"   # for the change: when and in which commit
    assert read_cache(tmp_path)["tests"]["commit"] == "abc1234 Add tests"
    again = _overlay(tmp_path, binding=fast, calls=calls)       # within `every`: the cached reading, no read
    calls.clear()
    assert again(hits)[0]["object"] == "860 tests" and calls == [] and again.report["queued"] == 0
    later = _overlay(tmp_path, binding=fast, calls=calls, now=1_060)
    assert later(hits)[0]["object"] == "860 tests" and calls == ["tests light"]   # due, unchanged: light only
    assert later.report["queued"] == 0 and read_cache(tmp_path)["tests"]["commit"] == "abc1234 Add tests"
    old_obs = _hit("OpenWiki", "test suite collects", "848 tests", session="sensor:tests", source="observed", hid="o")
    out = again([old_obs, _hit("test suite", "has passing tests", "848", hid="a")])
    assert [h["object"] for h in out] == ["860 tests"]          # an outdated observation too; the belief then goes


def test_overlay_leaves_alone_what_it_must_not_touch(tmp_path):
    belief = [_hit("test suite", "has passing tests", "848")]
    assert _overlay(tmp_path / "a", accepted=False).active == []                  # not accepted: no overlay
    (tmp_path / "b").mkdir()
    calls = []
    slow = _overlay(tmp_path / "b", calls=calls)              # pytest-collect is slow: never read by the hook
    assert slow(belief) == belief and calls == []
    (tmp_path / "c").mkdir()
    broken = _overlay(tmp_path / "c", binding=dataclasses.replace(TESTS, type="count", every=60), quality="failed")
    assert broken(belief) == belief                            # a failed reading changes nothing
    (tmp_path / "d").mkdir()
    ov = _overlay(tmp_path / "d", binding=dataclasses.replace(TESTS, type="count", every=60))
    ov.reader = lambda *a, **k: 1 / 0
    assert ov(belief) == belief                                # fail-soft


def test_context_shows_the_correction_even_once_per_stretch(tmp_path):
    g = _graph(tmp_path)
    try:
        g.remember("s1", [MemoryFact("test suite", "has passing tests", "848")], _Emb(),
                   session_date=T("2025-01-10"), now=T("2025-01-10"))
        query = "how many tests does the test suite have"
        plain = g.context_for(query, _Emb(), k=5)
        assert "has passing tests 848" in plain
        belief_id = next(r["id"] for r in g._load_assertions() if r["object"] == "848")
        (tmp_path / "w").mkdir()
        ov = _overlay(tmp_path / "w", binding=dataclasses.replace(TESTS, type="count", every=60))
        report = {}
        text = g.context_for(query, _Emb(), k=5, exclude={"facts": [belief_id]}, overlay=ov, report=report)
        assert "OpenWiki test suite collects 860 tests  (observed just now" in text and "848" not in text
        assert report["overlay"]["replaced"] and "obs:tests:860 tests" in report["facts"]
    finally:
        g.close()


def test_the_handoff_shows_the_observed_state():
    from openwiki.handoff import _env_lines
    from openwiki.world.observe import world_state
    env = {"ollama": {"reachable": True, "host": "h", "models": {}}, "graph": "readable", "world": {
        "bindings": [{"id": "tests", "value": "860", "read_at": 1_700_000_000, "acceptance": "accepted"},
                     {"id": "tabs", "value": None, "read_at": None, "acceptance": "not accepted"}], "differs": 2}}
    lines = "\n".join(_env_lines(env))
    assert "- Observed state (1 of 2 sensor(s) accepted): tests 860 (" in lines
    assert "2 remembered fact(s) differ from what the sensors read — `owiki world observe` closes them" in lines
    assert world_state(None) is None


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_cli_world_accept_observe_revoke(tmp_path, monkeypatch, capsys):
    pytest.importorskip("kuzu")
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    from openwiki import cli
    from openwiki.graph import GraphStore
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (("init", "-q"), ("config", "user.email", "t@example.com"), ("config", "user.name", "T")):
        _git(repo, *args)
    (repo / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "1.2.0"\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "release 1.2.0")
    _git(repo, "tag", "v1.2.0")
    root = tmp_path / "proj"
    root.mkdir()
    (root / "openwiki.toml").write_text(
        f'[project]\nname = "p"\n\n[[sources]]\ntype = "code"\npath = {json.dumps(str(repo))}\n\n'
        '[memory]\nenabled = true\n\n[world]\nenabled = true\n', encoding="utf-8")
    cli._build_stub_graph(root / "output", _Emb())
    (root / "output" / "index").mkdir()
    (root / "output" / "index" / "index.json").write_text("{}", encoding="utf-8")

    class _Index:
        embedder = _Emb()
    monkeypatch.setattr(cli.SemanticIndex, "load", staticmethod(lambda path: _Index()))
    monkeypatch.setenv("OPENWIKI_HOME", str(tmp_path / "home"))
    g = GraphStore(root / "output" / "graph", writable=True)
    g.remember("2025-01-01", [MemoryFact("repo", "current release is", "v1.1.0")], _Emb(),
               session_date=T("2025-01-01"), now=T("2025-01-01"))
    g.close()

    def run(*argv):
        code = cli.main(["world", *argv, "--project", str(root)])
        return code, capsys.readouterr().out
    code, out = run("observe")
    assert code == 0 and "no accepted binding" in out
    code, out = run("status")
    assert "not accepted" in out and "≠ repo | current release is | v1.1.0" in out
    code, out = run("accept", "release")
    assert code == 0 and "Accepted release: governs 1 current fact(s), 1 differing" in out
    assert run("accept")[0] == 2 and run("accept", "nope")[0] == 2
    code, out = run("observe", "--dry-run")
    assert "Dry run — nothing written." in out and "v1.2.0 (first observation)" in out
    code, out = run("observe")
    assert code == 0 and "release         v1.2.0 (first observation)   changed in" in out
    assert "≠ closed repo | current release is | v1.1.0" in out and "Written: 1 new observation(s)" in out
    code, out = run("observe")
    assert "v1.2.0   unchanged" in out and "Nothing changed — nothing written." in out
    code, out = run("status")
    assert "accepted 20" in out and "1 agrees" in out
    g = GraphStore(root / "output" / "graph")
    try:
        assert g.observations()["release"]["object"] == "v1.2.0"
    finally:
        g.close()
    code, out = run("revoke", "release")
    assert "Revoked: release" in out and "no accepted binding" in run("observe")[1]
    runs = [json.loads(line)["name"] for line in (root / ".openwiki" / "runs.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert "world observe" in runs and "world accept" in runs                 # logged like the other write passes
