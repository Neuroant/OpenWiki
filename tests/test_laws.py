"""Path C, C2.3 — laws: the whitelisted expression language, the planted-violation rule, co-change laws."""
from __future__ import annotations

import pytest

from openwiki.world.laws import (HOLDS, OK, UNKNOWN, VIOLATED, CheckError, Law, check_cochange, check_laws, duration,
                                 evaluate, law_health, load_laws, parse_check, version)


@pytest.mark.parametrize("bad", [
    "os.system('x')", "__import__('os')", "eval('1')", "open('f')", "x[0]", "lambda: 1", "[y for y in x]",
    "(x := 1)", "f'{x}'", "b'x' == x", "ver(x, strict=True)", "implies(x)", "age('x') > 1", "x if y else z",
    "{'a': 1}", "{1, 2}", "x == " + "1 + " * 200 + "1", "", "x ==",
])
def test_the_whitelist_refuses_everything_else(bad):
    with pytest.raises(CheckError):
        parse_check(bad)


def test_parse_check_names_the_variables_but_not_the_functions():
    tree, names = parse_check("ver(version) == ver(release) or age(version) < days(1)")
    assert names == ("version", "release")
    assert parse_check("implies(x > 0, y in ['a', 'b'])")[1] == ("x", "y")


def test_evaluate():
    def run(check, values, ages=None):
        return evaluate(parse_check(check)[0], values, ages)[0]
    assert run("version == code_version", {"version": "0.2.0", "code-version": "0.2.0"}) == HOLDS   # - as _
    assert run("version == code_version", {"version": "0.2.0", "code_version": "0.1.0"}) == VIOLATED
    assert run("ver(release) == ver(version)", {"release": "v0.125.0", "version": "0.125"}) == HOLDS
    assert run("ver(release) < ver(version)", {"release": "v0.99.0", "version": "0.125.0"}) == HOLDS  # numeric
    assert run("tests >= 800 and tests < 2000", {"tests": 867}) == HOLDS
    assert run("implies(dirty > 0, branch != 'main')", {"dirty": 0, "branch": "main"}) == HOLDS
    assert run("implies(dirty > 0, branch != 'main')", {"dirty": 3, "branch": "main"}) == VIOLATED
    assert run("stages == ['ingest', 'wiki']", {"stages": ("ingest", "wiki")}) == HOLDS          # tuple = list
    assert run("age(version) < hours(36)", {}, {"version": 3600}) == HOLDS
    assert run("not (len(name) > 3) or lower(name) == 'kuzu'", {"name": "KUZU"}) == HOLDS
    assert run("max(a, b) == 3 and abs(c) == 2 and num(d) == 1.5", {"a": 1, "b": 3, "c": -2, "d": "1.5"}) == HOLDS
    assert run("x == 1", {}) == UNKNOWN                                        # no reading: never a violation
    assert run("age(x) < 10", {"x": 1}) == UNKNOWN                             # no change time
    assert run("x < 1", {"x": "text"}) == UNKNOWN                              # a type mismatch
    assert run("x + 1", {"x": 1}) == UNKNOWN                                   # not true or false
    assert run("x and y", {"x": True, "y": "yes"}) == UNKNOWN
    assert run("x / 0 == 1", {"x": 1}) == UNKNOWN
    assert version("v1.2") == (1, 2, 0) and duration("2d") == 172800 and duration(30) == 30


def test_a_law_counts_only_after_failing_on_its_planted_violation():
    def law(check, violation=None, example=None):
        tree, names = parse_check(check)
        return Law("l", check=check, tree=tree, names=names, violation=violation or {}, example=example or {})
    good = law("version == code_version", {"version": "0.2.0", "code_version": "0.1.0"},
               {"version": "0.2.0", "code_version": "0.2.0"})
    assert law_health(good) == OK
    assert "no planted violation" in law_health(law("version == code_version"))
    assert "can't fail" in law_health(law("version == version", {"version": "1"}))          # a check that can't fail
    assert "can't fail" in law_health(law("version == code_version", {"version": "1"}))      # unknown isn't a failure
    assert "example gives violated" in law_health(law("x < 1", {"x": 2}, {"x": 5}))
    aged = law("ver(version) == ver(release) or age(version) < days(1)",
               {"version": {"value": "0.126.0", "age": "2d"}, "release": "v0.125.0"},
               {"version": {"value": "0.126.0", "age": "2h"}, "release": "v0.125.0"})
    assert law_health(aged) == OK                                            # a fresh bump may wait for its tag


def test_load_laws_from_the_manifest():
    raw = [
        {"id": "version-in-code", "kind": "equality", "check": "version == code_version",
         "violation": {"version": "0.2.0", "code_version": "0.1.0"}, "text": "pyproject = __version__"},
        {"id": "cli-docs", "kind": "co-change", "when": "openwiki/cli.py", "then": ["CLAUDE.md"], "window": 3,
         "violation": {"commits": [["x.py"], ["README.md"], ["openwiki/cli.py"]]},
         "example": {"commits": [["CLAUDE.md"], ["openwiki/cli.py"]]}},
        {"id": "typo", "check": "version == cod_version", "violation": {"version": "1"}},
        {"id": "weird", "kind": "magic", "check": "x"},
        {"id": "version-in-code", "check": "version == code_version"},
        {"id": "half", "kind": "co-change", "when": "a.py"},
        {"id": "untested", "check": "tests > 0"},
        {"text": "no id"},
    ]
    laws, problems = load_laws(raw, {"version", "code-version", "tests"}, ["openwiki"])
    by = {law.id: law for law in laws}
    assert set(by) == {"version-in-code", "cli-docs", "typo", "weird", "half", "untested"}
    assert by["version-in-code"].health == OK and by["version-in-code"].row()["check"] == "version == code_version"
    assert by["cli-docs"].health == OK and by["cli-docs"].environment == "openwiki"
    assert by["cli-docs"].described == "openwiki/cli.py changed ⇒ CLAUDE.md changed within 3 commits"
    text = "\n".join(problems)
    for expected in ("law typo: check reads unknown variable(s) cod_version", "law weird: unknown kind 'magic'",
                     "law version-in-code: declared twice", "law half: a co-change law needs when and then",
                     "law untested: no planted violation", "law #8: no id"):
        assert expected in text, expected
    results = check_laws(laws, {"version": "0.3.0", "code_version": "0.3.0"},
                         commits={"openwiki": [(30, ["CLAUDE.md"]), (20, ["openwiki/cli.py"])]})
    assert results["version-in-code"] == (HOLDS, "") and results["cli-docs"][0] == HOLDS
    assert results["untested"][0] == UNKNOWN and "not trusted" in results["untested"][1]   # never evaluated
    assert check_laws([by["cli-docs"]], {})["cli-docs"] == (UNKNOWN, "no git history")


def test_cochange_windows_pending_and_since():
    when, then = ("openwiki/cli.py",), ("CLAUDE.md", "docs/*.md")
    assert check_cochange([(9, ["docs/roadmap.md"]), (8, ["openwiki/cli.py"])], when, then, 2)[0] == HOLDS
    assert check_cochange([(9, ["a.py"]), (8, ["openwiki/cli.py"])], when, then, 2)[0] == VIOLATED
    result, detail = check_cochange([(8, ["openwiki/cli.py"])], when, then, 2)     # its window isn't complete yet
    assert result == HOLDS and "1 pending" in detail
    assert check_cochange([(9, ["a.py"]), (8, ["openwiki/cli.py"])], when, then, 2, since=9)[1] == \
        "no change to check yet"                                                    # watches from its first check
    assert check_cochange([["openwiki\\cli.py"], ["x"]], when, then, 1)[0] == VIOLATED   # Windows separators


def test_cli_world_laws_and_the_observe_pass(tmp_path, monkeypatch, capsys):
    import json
    import shutil
    pytest.importorskip("kuzu")
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    from test_observe import _Emb, _git
    from openwiki import cli
    from openwiki.graph import GraphStore
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
    sensors = (
        '[[world.sensors]]\nid = "version"\ntype = "toml-key"\npath = "pyproject.toml"\nkey = "project.version"\n'
        'fact = { subject = "demo", predicate = "has version", object = "{value}" }\n\n'
        '[[world.sensors]]\nid = "code-version"\ntype = "py-constant"\npath = "pkg/__init__.py"\nname = "__version__"\n'
        'fact = { subject = "demo", predicate = "code version is", object = "{value}" }\n\n')
    laws = ('[[world.laws]]\nid = "version-in-code"\ncheck = "version == code_version"\n'
            'text = "The version in pyproject.toml equals __version__."\n'
            'violation = { version = "1.3.0", code_version = "1.2.0" }\n\n'
            '[[world.laws]]\nid = "few-uncommitted"\nkind = "comparison"\ncheck = "git_dirty <= 5"\n'
            'violation = { git_dirty = 9 }\n')

    def manifest(extra: str) -> None:
        (root / "openwiki.toml").write_text(
            f'[project]\nname = "p"\n\n[[sources]]\ntype = "code"\npath = {json.dumps(str(repo))}\n\n'
            '[memory]\nenabled = true\n\n' + sensors + extra, encoding="utf-8")
    manifest(laws)
    cli._build_stub_graph(root / "output", _Emb())
    monkeypatch.setenv("OPENWIKI_HOME", str(tmp_path / "home"))

    def run(*argv):
        code = cli.main(["world", *argv, "--project", str(root)])
        return code, capsys.readouterr().out
    code, out = run("laws")
    assert code == 0 and "✓ version-in-code       holds     version == code_version" in out
    assert "✓ few-uncommitted" in out                                   # a built-in variable (C2.14)
    (repo / "pkg" / "__init__.py").write_text('__version__ = "1.3.0"\n', encoding="utf-8")
    code, out = run("laws")
    assert "✗ version-in-code       violated" in out
    code, out = run("observe")                                          # nothing accepted — the laws still report
    assert code == 0 and "laws: 1 hold, 1 violated (version-in-code)" in out
    g = GraphStore(root / "output" / "graph")
    try:
        by = {law["id"]: law for law in g.laws()}
        assert by["version-in-code"]["last_result"] == "violated" and by["few-uncommitted"]["last_result"] == "holds"
        assert by["version-in-code"]["variables"] == ["sensor:code-version", "sensor:version"]
        assert {v["id"] for v in g.variables()} == {"sensor:version", "sensor:code-version"}
        assert not g.has_memory()                                       # a law writes no fact
    finally:
        g.close()
    code, out = run("observe")
    assert "Nothing changed — nothing written." in out                  # write on change
    assert "[proposed — `owiki world accept version-in-code`]" in run("laws")[1]   # nobody accepted it yet
    code, out = run("accept", "version-in-code")
    assert code == 0 and "Accepted law version-in-code: violated now (version == code_version)" in out
    manifest(laws.split("[[world.laws]]\nid = \"few-uncommitted\"")[0])   # a person drops a law
    run("observe")
    g = GraphStore(root / "output" / "graph")
    try:
        assert {law["id"]: law["status"] for law in g.laws()} == {"version-in-code": "accepted",
                                                                   "few-uncommitted": "retired"}
    finally:
        g.close()
    code, out = run("revoke", "version-in-code")
    assert "Revoked: law version-in-code" in out and "[proposed" in run("laws")[1]
    manifest(laws.replace('violation = { git_dirty = 9 }', 'violation = { git_dirty = 1 }'))
    code, out = run("laws")
    assert code == 1 and "not trusted: its planted violation gives holds" in out   # a check that can't fail
    code, out = run("accept", "few-uncommitted")
    assert code == 1 and "Not accepted: law few-uncommitted — its planted violation gives holds" in out


def test_builtin_variables_come_from_the_handoffs_probes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from openwiki import handoff
    from openwiki.world.builtin import BUILTIN, read_builtin
    monkeypatch.setattr(handoff, "git_state", lambda repo: {"branch": "main", "head": "abc", "dirty": ["x"],
                                                            "dirty_count": 3, "ahead": 1, "behind": 0})
    monkeypatch.setattr(handoff, "ollama_state", lambda host, models: {"reachable": True, "host": host,
                                                                       "models": {"m1": True, "m2": False}})
    monkeypatch.setattr(handoff, "wiki_freshness", lambda project, repo: {"built": 1, "commits_since": 4})
    monkeypatch.setattr(handoff, "capture_workers", lambda state_dir: [{"session": "s"}])
    project = SimpleNamespace(state_dir=tmp_path)
    seen = read_builtin(project, tmp_path, "http://h", ["m1", "m2"])
    assert set(seen) == set(BUILTIN)
    assert {n: r["value"] for n, r in seen.items()} == {
        "git_branch": "main", "git_head": "abc", "git_dirty": 3, "git_ahead": 1, "git_behind": 0,
        "ollama_reachable": True, "ollama_models": ["m1"], "wiki_commits_behind": 4, "capture_workers": 1}
    assert seen["wiki_commits_behind"]["raw"] == {"built": 1, "commits_since": 4}
    assert read_builtin(project, None, names=["git_dirty"])["git_dirty"]["quality"] == "failed"   # no repository

    def broken(*a, **k):
        raise OSError("boom")
    monkeypatch.setattr(handoff, "capture_workers", broken)
    assert read_builtin(project, names=["capture_workers"])["capture_workers"]["quality"] == "failed"  # never raises


def test_the_handoff_reads_the_world_model(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from openwiki import handoff
    from openwiki.world import builtin
    calls = []

    def fake(project, repo_root=None, host=None, models=(), names=None, now=None):
        calls.append(sorted(names))
        return {"ollama_reachable": {"raw": {"reachable": True, "host": host, "models": {"chat": True}}},
                "wiki_commits_behind": {"raw": {"built": 5, "commits_since": 2}},
                "capture_workers": {"raw": None}}
    monkeypatch.setattr(builtin, "read_builtin", fake)
    project = SimpleNamespace(state_dir=tmp_path, graph_path=tmp_path / "graph", world_enabled=False)
    env = handoff.HandoffEnv(repo=tmp_path, project=project, graph=None, host="http://h", chat_model="chat",
                             embed_model="embed")
    got = handoff.environment(env, repo_root=tmp_path)
    assert calls == [["capture_workers", "ollama_reachable", "wiki_commits_behind"]]
    assert got["ollama"]["models"] == {"chat": True} and got["wiki"]["commits_since"] == 2 and got["workers"] == []
    lines = "\n".join(handoff._env_lines({"ollama": got["ollama"], "graph": "readable", "world": {
        "bindings": [], "laws": [{"id": "version-in-code", "last_result": "violated", "detail": "1.3.0 vs 1.2.0"},
                                 {"id": "few-uncommitted", "last_result": "holds"}]}}))
    assert "Laws: 1 of 2 hold; violated: version-in-code (1.3.0 vs 1.2.0) — `owiki world laws`" in lines


def test_a_cochange_law_watches_from_its_acceptance(tmp_path):
    from types import SimpleNamespace
    from openwiki import cli
    from openwiki.world.observe import accept_laws, read_accepted
    (law,), _ = load_laws([{"id": "cli-docs", "kind": "co-change", "when": "cli.py", "then": "CLAUDE.md",
                            "violation": {"commits": [["x"], ["cli.py"]]}}], set(), ["repo"])
    world = SimpleNamespace(laws=[law], bindings=[])
    inputs = {"values": {}, "ages": {}, "commits": {"repo": [(200, ["x"]), (100, ["cli.py"])]}}
    (row,) = cli._law_rows(world, inputs, {})
    assert (row["status"], row["last_result"]) == ("proposed", VIOLATED)       # the dry run: over the history
    accept_laws(tmp_path, [law], now=150)
    (row,) = cli._law_rows(world, inputs, read_accepted(tmp_path))
    assert (row["status"], row["last_result"], row["detail"]) == ("accepted", HOLDS, "no change to check yet")
