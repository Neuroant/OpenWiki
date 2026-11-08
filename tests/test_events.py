"""Path C, v0.127 — events (C2.6), decisions with their preconditions (C2.5), law proposals and their review (C2.4)."""
from __future__ import annotations

import json
import shutil

import pytest

from openwiki.graph.memory import MemoryFact
from openwiki.world.laws import (OK, Law, cochange_candidates, extract_laws, law_from_row, law_health,
                                 precondition_law, rule_sentences, variable_terms)
from openwiki.world.observe import Authority

from test_observe import TESTS, T, _Emb, _git, _graph, _obs


# -- the store's events ------------------------------------------------------------------------------------------

def test_events_one_open_per_key_resolved_by_id_or_key(tmp_path):
    g = _graph(tmp_path)
    try:
        g.upsert_variables([{"id": "sensor:tests", "label": "the test count"}], now=1)
        ev = {"kind": "violation", "key": "law:a", "detail": "a broke", "concerns": ["sensor:tests", "sensor:nope"],
              "law": "missing"}
        assert g.raise_events([ev], now=10)["raised"] == 1
        assert g.raise_events([ev], now=11) == {"raised": 0, "open": 1, "ids": []}       # one open per key
        (e,) = g.events(open_only=True)
        assert (e["kind"], e["key"], e["at"], e["concerns"], e["law"]) == ("violation", "law:a", 10,
                                                                          ["sensor:tests"], None)   # edges that exist
        assert g.resolve_events(keys=["law:a"], resolution="holds again", now=12) == 1
        assert g.events(open_only=True) == [] and g.events()[0]["resolution"] == "holds again"
        assert g.raise_events([ev], now=13)["raised"] == 1                               # a new episode
        eid = g.events(open_only=True)[0]["id"]
        assert g.raise_events([dict(ev, key="law:b")], dry_run=True)["raised"] == 1 and len(g.events()) == 2
        assert g.resolve_events([eid], resolution="checked", now=14) == 1 and g.events(open_only=True) == []
    finally:
        g.close()


def test_the_authority_rule_raises_discrepancies_and_contested_values(tmp_path):
    g = _graph(tmp_path)
    try:
        auth = Authority([TESTS])
        g.remember("late", [MemoryFact("test suite", "has passing tests", "823")], _Emb(), session_date=T("2025-02-08"),
                   now=T("2025-02-12"))                      # recorded after the change the sensor will report
        res = g.observe([_obs(849, valid_from=T("2025-02-10"), read_at=T("2025-02-13"))], _Emb(), auth,
                        now=T("2025-02-13"))
        assert res["discrepancies"] == 1 and res["events"]["raised"] == 1
        (ev,) = g.events(open_only=True)
        assert ev["kind"] == "discrepancy" and "823" in ev["detail"] and len(ev["involves"]) == 2
        flip = T("2025-02-13") + 10 * 3600
        g.observe([_obs(850, valid_from=flip, read_at=flip)], _Emb(), auth, now=flip)
        back = flip + 5 * 3600                                   # 849 → 850 → 849 within a day
        g.observe([_obs(849, valid_from=back, read_at=back)], _Emb(), auth, now=back)
        kinds = [e["kind"] for e in g.events(open_only=True)]
        assert kinds.count("contested") == 1
        assert "849 tests → 850 tests → 849 tests" in next(e for e in g.events() if e["kind"] == "contested")["detail"]
    finally:
        g.close()


def test_a_contradicting_late_belief_is_a_discrepancy_event(tmp_path):
    g = _graph(tmp_path)
    try:
        auth = Authority([TESTS])
        g.observe([_obs(848, valid_from=T("2025-02-01"), read_at=T("2025-03-01"))], _Emb(), auth, now=T("2025-03-01"))
        res = g.remember("s-old", [MemoryFact("test suite", "has passing tests", "823")], _Emb(),
                         session_date=T("2025-02-15"), now=T("2025-03-05"), authority=auth)
        assert res["contradicted"] == 1                                          # rule 5, decision 13
        (ev,) = g.events(open_only=True)
        assert ev["kind"] == "discrepancy" and "while tests read 848 tests" in ev["detail"]
    finally:
        g.close()


def test_the_journal_carries_reviews_resolutions_and_preconditions(tmp_path):
    from openwiki.graph.journal import append_ids
    g = _graph(tmp_path)
    try:
        g.upsert_variables([{"id": "sensor:gpu-total", "label": "GPU memory"}], now=1)
        g.queue_remember("agent-x", [MemoryFact("embedding", "runs", "in one phase first", source="user")],
                         preconditions={"gpu-total": 12288})
        proposal = Law(id="cochange:a=>b", kind="co-change", when=("a",), then=("b",), window=1, origin="co-change",
                       status="proposed", violation={"commits": [["a"]]})
        proposal.health = law_health(proposal)
        g.upsert_laws([dict(proposal.row(), last_result="unknown", detail="", variables=[])], retire_missing=False)
        g.raise_events([{"kind": "drift", "key": "k1", "detail": "d"}], now=5)
        append_ids(g._journal_path, "resolve-event", [g.events()[0]["id"]], reason="seen")
        append_ids(g._journal_path, "accept-law", ["cochange:a=>b"])
        res = g.fold_journal(_Emb())
        assert (res["decided"], res["resolved"], res["reviewed"]) == (1, 1, 1)
        laws = {law["id"]: law for law in g.laws()}
        decision = next(law for law in laws.values() if law["origin"] == "decision")
        assert (decision["kind"], decision["check"], decision["status"]) == ("precondition", "gpu_total == 12288",
                                                                             "accepted")
        assert decision["variables"] == ["sensor:gpu-total"]
        assert g._rows("MATCH (l:Law)-[:FROM_FACT]->(a:Assertion) RETURN a.object;") == [["in one phase first"]]
        assert laws["cochange:a=>b"]["status"] == "accepted"
        assert json.loads(laws["cochange:a=>b"]["spec"])["accepted_at"]
        assert g.events()[0]["resolution"] == "seen"
        assert g.review_laws(["cochange:a=>b"], "rejected") == 1 and g.review_laws(["nope"], "accepted") == 0
    finally:
        g.close()


def test_a_graph_from_v0126_gets_the_new_columns(tmp_path):
    kuzu = pytest.importorskip("kuzu")
    from openwiki.graph import GraphStore, schema
    db = kuzu.Database(str(tmp_path / "old"))
    conn = kuzu.Connection(db)
    for table in schema.tables(group=schema.DOCUMENT):
        conn.execute(table.create(4))
    conn.execute("CREATE NODE TABLE Law(id STRING, text STRING, kind STRING, check STRING, origin STRING, "
                 "status STRING, health STRING, last_result STRING, last_checked INT64, detail STRING, "
                 "created_at INT64, updated_at INT64, PRIMARY KEY(id));")             # v0.126's Law: no spec
    conn.execute("CREATE (:Law {id: 'old', text: 't', status: 'accepted'});")
    conn.close()
    db.close()
    g = GraphStore(tmp_path / "old")
    try:
        assert g.laws()[0]["id"] == "old" and g.laws()[0]["spec"] is None              # read-only: read as it is
    finally:
        g.close()
    g = GraphStore(tmp_path / "old", writable=True)
    try:
        g._ensure_memory_schema(4)
        g._ensure_world_schema()
        assert "spec" in schema.present_columns(g._rows)("Law") and "key" in schema.present_columns(g._rows)("Event")
    finally:
        g.close()


# -- laws held in the graph, decisions, proposals ----------------------------------------------------------------

def test_decisions_proposals_and_candidates():
    law = precondition_law("embed in one phase first", {"gpu-total": 12288, "models": ["a"]}, ["f1"])
    assert law.id.startswith("decision:") and law.check == "gpu_total == 12288 and models == ['a']"
    assert law.health == OK and law.violation["gpu_total"] == 12289
    back = law_from_row(dict(law.row()))
    assert (back.kind, back.names, back.health, back.origin) == ("precondition", ("gpu_total", "models"), OK,
                                                                 "decision")
    assert precondition_law("x", {}) is None
    history = [(i, ["cli.py", "docs.md"] if i % 2 else ["other.py"]) for i in range(30)]
    found = {(c.when, c.then) for c in cochange_candidates(history, min_changes=8, window=1)}
    assert found == {(("cli.py",), ("docs.md",)), (("docs.md",), ("cli.py",))}      # other.py: no partner
    assert cochange_candidates([(i, ["a", "b"]) for i in range(10)], window=1) == []  # b everywhere: no lift


def test_document_proposals_are_screened():
    variables = [{"name": "version", "label": "OpenWiki version file says", "subject": "OpenWiki",
                  "paths": ["pyproject.toml"], "value": "0.127.0"},
                 {"name": "code_version", "label": "OpenWiki code version is", "subject": "OpenWiki",
                  "paths": ["openwiki/__init__.py"], "value": "0.127.0"}]
    text = ("An introduction. The version in pyproject.toml must equal __version__ in openwiki/__init__.py. "
            "Nothing else here must change.")
    sentences = rule_sentences(text, list(variable_terms(variables)))
    assert sentences == ["The version in pyproject.toml must equal __version__ in openwiki/__init__.py."]

    class _Chat:
        def chat(self, messages):
            return ('<think>…</think>[{"text": "The version in pyproject.toml equals __version__ in __init__.py", '
                    '"kind": "equality", '
                    '"check": "version == code_version", "violation": {"version": "1", "code_version": "2"}}, '
                    '{"text": "unsafe", "check": "__import__(\'os\')", "violation": {}}, '
                    '{"text": "can\'t fail", "check": "version == version", "violation": {"version": "1"}}, '
                    '{"text": "unknown name", "check": "wrong == 1", "violation": {"wrong": 2}}, '
                    '{"text": "B6 (v0.52)", "check": "version == \'v0.52\' and code_version == \'v0.52\'", '
                    '"violation": {"version": "v0.51", "code_version": "v0.52"}}, '
                    '{"text": "the version differs from the code version", "check": "version != code_version", '
                    '"violation": {"version": "1", "code_version": "1"}}, '
                    '{"text": "the version is at most 3", "check": "version <= 3", "violation": {"version": 4}}, '
                    '{"text": "recall ranks stale facts low", "check": "version != code_version or version == \'1\'", '
                    '"violation": {"version": "2", "code_version": "2"}}]')
    dropped = {}
    found = extract_laws(_Chat(), "pyproject-toml", sentences, variables, {"version", "code-version"}, report=dropped)
    assert [(x.check, x.origin, x.status) for x in found] == [("version == code_version", "document", "proposed")]
    assert dropped == {"invalid": 3, "pinned": 1, "violated_now": 1,    # a restated reading; contradicted now;
                       "unknown_now": 1, "unrelated": 1}                 # '0.127.0' <= 3; a text about recall

    class _Broken:
        def chat(self, messages):
            raise TimeoutError("cold model")
    assert extract_laws(_Broken(), "p", sentences, variables, {"version"}) == []         # fail-soft


def test_a_malformed_candidate_loses_only_itself():
    from openwiki.world.laws import _json_array
    reply = ('<think>[x]</think>Here: [{"a": 1}, {"stages": ("ingest", "wiki"), "t": "a } in a string"}, '
             '{"c": {"d": "say \\"}\\""}}] done')
    assert _json_array(reply) == [{"a": 1}, {"c": {"d": 'say "}"'}}]
    assert _json_array('[{"a": 1}]') == [{"a": 1}] and _json_array("no array") == []


def test_a_check_that_pins_constants_is_no_law():
    from openwiki.world.laws import parse_check, pins_constants
    pinned = ["version == 'v0.52'", "'1' == version", "len(stages) == 5 and version == '1'", "tabs == 10 or tabs == 11"]
    laws = ["ver(version) == ver(release)", "tabs >= 10", "version == code_version", "version != '0.1'",
            "version == '1' and tabs > 3", "implies(tabs > 3, version == code_version)"]
    assert [pins_constants(parse_check(c)[0]) for c in pinned] == [True] * 4
    assert [pins_constants(parse_check(c)[0]) for c in laws] == [False] * 6


def test_constant_tests_and_the_same_law_twice():
    from openwiki.world.laws import check_key, parse_check, tests_constants
    tree = lambda c: parse_check(c)[0]                                          # noqa: E731
    assert tests_constants(tree("not ('memory' in stages) or (probes == '3' and 'session' in ['session'])"))
    assert not tests_constants(tree("version == code_version or len(stages) >= 5"))
    assert check_key(tree("version == code_version")) == check_key(tree("code_version == version"))
    assert check_key(tree("a > 1 and b != c")) == check_key(tree("c != b and a > 1"))
    assert check_key(tree("a < b")) != check_key(tree("b < a"))

    class _Chat:
        def chat(self, messages):
            return ('[{"text": "the version equals the code version", "check": "code_version == version", '
                    '"violation": {"version": "1", "code_version": "2"}}]')
    variables = [{"name": "version", "value": "1.0"}, {"name": "code_version", "value": "1.0"}]
    dropped, seen = {}, {check_key(tree("version == code_version"))}
    assert extract_laws(_Chat(), "p", ["s"], variables, {"version", "code-version"}, report=dropped, seen=seen) == []
    assert dropped == {"duplicate": 1}
    seen = set()
    first = extract_laws(_Chat(), "p", ["s"], variables, {"version", "code-version"}, seen=seen)
    assert len(first) == 1 and extract_laws(_Chat(), "q", ["s"], variables, {"version", "code-version"},
                                            seen=seen) == []          # found again on another page: once


def test_cochange_candidates_follow_the_co_change_rules():
    from openwiki.graph import cochange
    from openwiki.world import laws
    assert (laws.SWEEP_FILES, laws.UBIQUITOUS_SHARE, laws.UBIQUITOUS_MIN) == (
        cochange.MAX_COMMIT_FILES, cochange.UBIQUITOUS_SHARE, cochange.UBIQUITOUS_MIN)
    # a release bundle: every feature commit bumps the version files (60 % of the commits) — they follow cli.py as
    # surely as each other, but only the version pair is a law (both ubiquitous)
    history = ([(i, ["cli.py", "pyproject.toml", "version.py"]) for i in range(12)]
               + [(i, ["pyproject.toml", "version.py", "notes.md"]) for i in range(6)]
               + [(i, ["docs.md"]) for i in range(12)])
    pairs = {(c.when[0], c.then[0]) for c in cochange_candidates(history, window=1)}
    assert pairs == {("pyproject.toml", "version.py"), ("version.py", "pyproject.toml")}
    sweep = [(i, ["a.py", "b.py"] + [f"f{j}" for j in range(40)]) for i in range(10)] + [(i, ["c"]) for i in range(10)]
    assert cochange_candidates(sweep, window=1) == []                                   # a sweep is no coupling
    found = cochange_candidates(history, window=1, limit=1, skip={"cochange:pyproject.toml=>version.py"})
    assert [c.id for c in found] == ["cochange:version.py=>pyproject.toml"]             # skipped before the limit
    assert cochange_candidates(history, window=1, skip={(("version.py",), ("pyproject.toml",)),
                                                        "cochange:pyproject.toml=>version.py"}) == []


# -- the CLI: violations, exogenous changes, resolution; proposals and their review -----------------------------

def test_an_undated_change_is_aged_from_when_it_was_first_read(tmp_path):
    from types import SimpleNamespace
    from openwiki.cli import _law_inputs
    from openwiki.world.observe import read_cache, update_cache
    from openwiki.world.sensors import Reading

    def r(value, t, changed_at=None):
        return Reading("version", value=value, quality="complete", time=t, changed_at=changed_at,
                       source="pyproject.toml", commit="uncommitted" if changed_at is None else "abc")
    update_cache(tmp_path, {"version": r("1.2.0", 100, changed_at=50)})
    assert read_cache(tmp_path)["version"]["changed_at"] == 50                    # the source's own time
    update_cache(tmp_path, {"version": r("1.3.0", 200)})                           # an uncommitted change
    update_cache(tmp_path, {"version": r("1.3.0", 300)})
    assert read_cache(tmp_path)["version"]["changed_at"] == 200                   # first read with this value
    world = SimpleNamespace(laws=[], environments=[], bindings=[])
    project = SimpleNamespace(setting=lambda *a: None)
    cache = {"version": {"value": "1.2.0", "rendered": "1.2.0", "quality": "complete", "changed_at": 50}}
    inputs = _law_inputs(project, world, {"version": r("1.3.0", 400)}, cache, None, 1000)
    assert inputs["ages"]["version"] == 600 and inputs["commits_of"]["sensor:version"] == "uncommitted"
    inputs = _law_inputs(project, world, {"version": r("1.2.0", 400)}, cache, None, 1000)
    assert inputs["ages"]["version"] == 950                                       # unchanged: the cached time


def test_law_results_become_events():
    from openwiki.cli import _law_events
    rows = [{"id": "a", "status": "accepted", "health": "ok", "last_result": "violated", "kind": "equality",
             "variables": ["sensor:x"], "detail": "x is 2"},
            {"id": "decision:1", "status": "accepted", "health": "ok", "last_result": "violated",
             "kind": "precondition", "variables": ["sensor:gpu-total"], "check": "gpu_total == 12288"},
            {"id": "c", "status": "accepted", "health": "ok", "last_result": "violated", "kind": "precondition",
             "variables": ["sensor:y"]},
            {"id": "d", "status": "accepted", "health": "ok", "last_result": "holds"},
            {"id": "e", "status": "proposed", "health": "ok", "last_result": "violated"},
            {"id": "f", "status": "accepted", "health": "vacuous", "last_result": "violated"}]
    raise_, resolve = _law_events(rows, {"commits_of": {"sensor:y": "uncommitted", "sensor:x": "abc123"}})
    assert [(e["kind"], e["key"]) for e in raise_] == [("violation", "law:a"), ("drift", "law:decision:1"),
                                                       ("exogenous", "law:c")]
    assert raise_[1]["detail"] == "decision:1: gpu_total == 12288" and resolve == ["law:d"]


def _repo_project(tmp_path, monkeypatch, law: bool = True):
    from openwiki import cli
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    for args in (("init", "-q"), ("config", "user.email", "t@example.com"), ("config", "user.name", "T")):
        _git(repo, *args)
    (repo / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "1.2.0"\n', encoding="utf-8")
    (repo / "pkg" / "__init__.py").write_text('__version__ = "1.2.0"\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "release 1.2.0")
    root = tmp_path / "proj"
    root.mkdir()
    (root / "openwiki.toml").write_text(
        f'[project]\nname = "p"\n\n[[sources]]\ntype = "code"\npath = {json.dumps(str(repo))}\n\n'
        '[memory]\nenabled = true\n\n'
        '[[world.sensors]]\nid = "version"\ntype = "toml-key"\npath = "pyproject.toml"\nkey = "project.version"\n'
        'fact = { subject = "demo", predicate = "has version", object = "{value}" }\n\n'
        '[[world.sensors]]\nid = "code-version"\ntype = "py-constant"\npath = "pkg/__init__.py"\nname = "__version__"\n'
        'fact = { subject = "demo", predicate = "code version is", object = "{value}" }\n\n'
        + ('[[world.laws]]\nid = "version-in-code"\ncheck = "version == code_version"\n'
           'violation = { version = "1.3.0", code_version = "1.2.0" }\n' if law else ""), encoding="utf-8")
    cli._build_stub_graph(root / "output", _Emb())
    monkeypatch.setenv("OPENWIKI_HOME", str(tmp_path / "home"))
    return cli, repo, root


def test_cli_violations_exogenous_changes_and_resolution(tmp_path, monkeypatch, capsys):
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphStore
    cli, repo, root = _repo_project(tmp_path, monkeypatch)

    def run(*argv):
        code = cli.main(["world", *argv, "--project", str(root)])
        return code, capsys.readouterr().out

    def events():
        g = GraphStore(root / "output" / "graph")
        try:
            return g.events()
        finally:
            g.close()
    run("observe")
    assert events() == []                                         # proposed: checked, raises nothing
    (repo / "pkg" / "__init__.py").write_text('__version__ = "1.3.0"\n', encoding="utf-8")
    run("observe")
    assert events() == []
    assert run("accept", "version-in-code")[0] == 0
    code, out = run("observe")
    assert "events: 1 raised" in out
    (ev,) = events()
    assert ev["kind"] == "exogenous" and ev["law"] == "version-in-code"   # the change is in no commit
    assert ev["concerns"] == ["sensor:code-version", "sensor:version"]
    run("observe")
    assert len(events()) == 1                                     # one open event per law
    code, out = run("events")
    assert code == 0 and "exogenous" in out and ev["id"] in out
    (repo / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "1.3.0"\n', encoding="utf-8")
    _git(repo, "commit", "-qam", "release 1.3.0")
    code, out = run("observe")
    assert "1 resolved" in out and events()[0]["resolution"] == "holds again"   # the world resolved it
    (repo / "pkg" / "__init__.py").write_text('__version__ = "1.4.0"\n', encoding="utf-8")
    _git(repo, "commit", "-qam", "bump the code only")
    run("observe")
    opened = [e for e in events() if e.get("resolved_at") is None]
    assert [e["kind"] for e in opened] == ["violation"]             # committed: an ordinary violation
    code, out = run("resolve", opened[0]["id"], "--note", "seen, fixing it")
    assert code == 0 and "1 written" in out
    assert [e for e in events() if e.get("resolved_at") is None] == []
    assert "none open" in run("events")[1] and "seen, fixing it" in run("events", "--all")[1]


def test_cli_a_decisions_precondition_drifts(tmp_path, monkeypatch, capsys):
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphStore
    cli, repo, root = _repo_project(tmp_path, monkeypatch, law=False)
    graph_path = root / "output" / "graph"
    g = GraphStore(graph_path)
    try:                                                          # wiki_remember(depends_on=["version"]), queued
        g.queue_remember("agent-2025-01-01", [MemoryFact("the release notes", "are written", "per minor version",
                                                         source="user")], preconditions={"version": "1.2.0"})
    finally:
        g.close()
    g = GraphStore(graph_path, writable=True)
    try:
        assert g.fold_journal(_Emb())["decided"] == 1
    finally:
        g.close()

    def run(*argv):
        code = cli.main(["world", *argv, "--project", str(root)])
        return code, capsys.readouterr().out
    code, out = run("observe")
    assert code == 0 and "laws: 1 hold" in out and "events" not in out    # accepted by construction, holding
    for path, text in (("pyproject.toml", '[project]\nname = "demo"\nversion = "1.3.0"\n'),
                       ("pkg/__init__.py", '__version__ = "1.3.0"\n')):
        (repo / path).write_text(text, encoding="utf-8")
    _git(repo, "commit", "-qam", "release 1.3.0")
    code, out = run("observe")
    assert "1 violated" in out and "events: 1 raised" in out
    g = GraphStore(graph_path)
    try:
        (ev,) = g.events(open_only=True)
        law = next(x for x in g.laws() if x["origin"] == "decision")
    finally:
        g.close()
    assert ev["kind"] == "drift" and ev["law"] == law["id"] and "version == '1.2.0'" in ev["detail"]
    assert "drift" in run("events")[1]


def test_cli_propose_accept_and_reject_cochange_laws(tmp_path, monkeypatch, capsys):
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphStore
    cli, repo, root = _repo_project(tmp_path, monkeypatch, law=False)
    for i in range(20):
        (repo / "c.py").write_text(f"x = {i}\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"c {i}")
    for i in range(10):                                           # a.py and b.md always change together
        (repo / "a.py").write_text(f"x = {i}\n", encoding="utf-8")
        (repo / "b.md").write_text(f"doc {i}\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"a {i}")

    def run(*argv):
        code = cli.main(["world", *argv, "--project", str(root)])
        return code, capsys.readouterr().out
    code, out = run("propose", "--dry-run")
    assert code == 0 and "proposed cochange:a.py=>b.md" in out and "nothing written" in out
    code, out = run("propose")
    assert "Proposed 2 law(s)" in out
    assert "[proposed — `owiki world accept cochange:a.py=>b.md`]" in run("laws")[1]   # listed with the manifest's
    g = GraphStore(root / "output" / "graph")
    try:
        assert {law["id"]: law["status"] for law in g.laws()} == {"cochange:a.py=>b.md": "proposed",
                                                                   "cochange:b.md=>a.py": "proposed"}
    finally:
        g.close()
    assert "Accepted: law cochange:a.py=>b.md" in run("accept", "cochange:a.py=>b.md")[1]
    assert "Rejected: law cochange:b.md=>a.py" in run("reject", "cochange:b.md=>a.py")[1]
    assert run("reject", "nope")[0] == 2
    assert "No new law to propose." in run("propose")[1]   # a rejected one stays rejected
    g = GraphStore(root / "output" / "graph")
    try:
        assert {law["id"]: law["status"] for law in g.laws()} == {"cochange:a.py=>b.md": "accepted",
                                                                   "cochange:b.md=>a.py": "rejected"}
    finally:
        g.close()
    code, out = run("observe")
    assert code == 0 and "laws: 1 hold" in out                    # the accepted one watches from its acceptance


# -- the agent's decisions and the Pflege panel ------------------------------------------------------------------

def test_wiki_remember_records_a_decisions_preconditions(tmp_path):
    from openwiki.graph.journal import read_journal
    from openwiki.mcp_server import _remember
    g = _graph(tmp_path)
    try:
        def values(names):
            return {"gpu-total": 12288}, [n for n in names if n != "gpu-total"]
        out = _remember(g, None, {"facts": [{"subject": "embedding", "predicate": "runs",
                                             "object": "in one phase first"}], "source": "user",
                                  "depends_on": ["gpu-total", "nope"]}, world_values=values)
        assert "made while gpu-total = 12288" in out and "no reading of nope" in out
        assert read_journal(g._journal_path)[-1]["preconditions"] == {"gpu-total": 12288}
        out = _remember(g, None, {"facts": [{"subject": "x", "predicate": "is", "object": "y"}],
                                  "depends_on": ["gpu-total"]})
        assert "depends_on ignored" in out and "preconditions" not in read_journal(g._journal_path)[-1]
    finally:
        g.close()


def test_a_staged_decision_keeps_its_preconditions_through_approval(tmp_path):
    from openwiki.graph.journal import approve_staged, read_staged
    from openwiki.mcp_server import _remember
    g = _graph(tmp_path)
    try:
        g.upsert_variables([{"id": "sensor:gpu-total", "label": "GPU memory"}], now=1)
        out = _remember(g, None, {"facts": [{"subject": "embedding", "predicate": "runs",
                                             "object": "in one phase first"}], "source": "user",
                                  "depends_on": ["gpu-total"]}, stage=True,
                        world_values=lambda names: ({"gpu-total": 12288}, []))
        assert "Staged 1 fact(s)" in out and "made while gpu-total = 12288" in out
        (rec,) = read_staged(g._staged_path)
        assert rec["preconditions"] == {"gpu-total": 12288}
        approve_staged(g._staged_path, g._journal_path, [rec["id"]])
        assert g.fold_journal(_Emb())["decided"] == 1
        (law,) = [x for x in g.laws() if x["origin"] == "decision"]
        assert law["check"] == "gpu_total == 12288" and law["status"] == "accepted"
    finally:
        g.close()


def test_the_pflege_panel_lists_events_and_laws_to_review(tmp_path):
    pytest.importorskip("kuzu")
    from test_memory_maintenance import _app
    app, store, emb = _app(tmp_path)
    try:
        proposal = Law(id="cochange:a=>b", kind="co-change", when=("a",), then=("b",), window=1,
                       origin="co-change", status="proposed", violation={"commits": [["a"]]})
        proposal.health = law_health(proposal)
        store.upsert_laws([dict(proposal.row(), last_result="holds", detail="", variables=[])], retire_missing=False)
        store.raise_events([{"kind": "violation", "key": "law:x", "detail": "x broke"}], now=50)
        m = app.memory_maintenance()
        assert [e["detail"] for e in m["events"]] == ["x broke"]
        assert [(law["id"], law["origin"]) for law in m["laws"]] == [("cochange:a=>b", "co-change")]
        eid = m["events"][0]["id"]
        assert app.memory_maintain("resolve-event", [eid])["queued"] == [eid]
        assert app.memory_maintain("reject-law", ["cochange:a=>b"])["queued"] == ["cochange:a=>b"]
        res = store.fold_journal(emb)
        assert res["resolved"] == 1 and res["reviewed"] == 1
        m = app.memory_maintenance()
        assert m["events"] == [] and m["laws"] == []
    finally:
        store.close()
