"""Direction K (M4): recall explained — the parts of each hit's score, what the recall aids swapped in and out,
and the web UI's hook preview, which must show exactly what the inject hook adds to a prompt."""

from __future__ import annotations

from openwiki.graph.memory import MemoryFact
from openwiki.graph.temporal import parse_date

DAY = 86400


def test_the_parts_multiply_to_the_score(tmp_path):
    from test_hybrid_recall import _Emb, _store
    store, emb = _store(tmp_path), _Emb()
    try:
        store.remember("s1", [MemoryFact("Ann", "adopted a cat named", "Tom"),
                              MemoryFact("OpenWiki", "current release is", "v0.1.0"),
                              MemoryFact("the email", "says Ann adopted", "a pet", source="material")], emb, now=1000)
        report: dict = {}
        hits = store.recall("pet adopt", emb, k=3, now=1000 + 90 * DAY, report=report)
        for h in hits:
            p = h["parts"]
            assert abs(p["cos"] * p["confidence"] * p["recency"] * p["material"] - p["score"]) < 2e-3
            assert p["score"] == h["score"] and not p["swapped_in"]
        by = {h["object"]: h["parts"] for h in hits}
        assert by["v0.1.0"]["kind"] == "version" and by["v0.1.0"]["recency"] < 1.0     # goes stale: recency counts
        assert by["v0.1.0"]["age_days"] == 90.0
        assert by["Tom"]["kind"] is None and by["Tom"]["recency"] == 1.0                 # timeless: it doesn't
        assert by["a pet"]["material"] == 0.75 and by["Tom"]["material"] == 1.0          # a claim from material
        assert [h["parts"]["rank"] for h in hits] == [1, 2, 3]
        assert report["candidates"] == 3 and report["displaced"] == [] and report["window"] is None
        assert report["weights"]["recency_floor"] == 0.9 and report["weights"]["material"] == 0.75
        plain = store.recall("pet adopt", emb, k=3, now=1000 + 90 * DAY)
        assert "parts" not in plain[0]                                                   # only when asked
        assert [h["id"] for h in plain] == [h["id"] for h in hits]                       # the same ranking
    finally:
        store.close()


def test_the_report_names_what_the_aids_swapped_in_and_out(tmp_path):
    from test_hybrid_recall import _Emb, _store
    store, emb = _store(tmp_path), _Emb()
    try:                                      # four pet facts outrank a keyword-only match (dense rank 5)
        store.remember("s1", [MemoryFact(f"owner {n}", "adopted", "a pet") for n in range(4)]
                       + [MemoryFact("Bob", "owns", "a zebra")], emb, now=1000)
        report: dict = {}
        hits = store.recall("pet zebra", emb, k=3, now=2000, lexical=5.0, report=report)
        zebra = next(h for h in hits if h["object"] == "a zebra")
        p = zebra["parts"]
        assert p["rank"] == 5 and p["swapped_in"] and p["lexical_boost"] == 5.0
        assert abs(p["selection"] - (p["score"] + p["lexical_boost"])) < 2e-3
        assert [d["object"] for d in report["displaced"]] == ["a pet"]                   # pushed out by the zebra
        assert report["displaced"][0]["parts"]["rank"] == 3 and not report["displaced"][0]["parts"]["swapped_in"]
        assert report["pool"] == 5 and report["candidates"] == 5 and report["weights"]["lexical"] == 5.0
    finally:
        store.close()


def test_the_report_shows_the_questions_time_window(tmp_path):
    from test_time_window import _Emb, _store
    store, emb = _store(tmp_path), _Emb()
    now = parse_date("2023-12-01")
    try:
        for n, (place, date) in enumerate([("the park", "2023-03-10"), ("the beach", "2023-07-14")]):
            store.remember(f"s{n}", [MemoryFact(f"Ann ({place})", "walked the dog at", place,
                                                valid_from=parse_date(date))], emb, session_date=parse_date(date),
                           now=now)
        report: dict = {}
        hits = store.recall("Where did Ann walk the dog in July 2023?", emb, k=1, now=now, temporal=0.1,
                            report=report)
        assert hits[0]["object"] == "the beach" and hits[0]["parts"]["window_boost"] == 0.1
        assert report["window"] == {"start": parse_date("2023-07-01"), "end": parse_date("2023-08-01"),
                                    "tolerance_days": 7.0}
    finally:
        store.close()


def test_context_for_reports_every_recalled_fact_and_what_the_budget_cut(tmp_path):
    from test_hybrid_recall import _Emb, _store
    store, emb = _store(tmp_path), _Emb()
    try:
        store.remember("s1", [MemoryFact(f"owner number {n}", "adopted a pet called", f"pet name {n}")
                              for n in range(6)], emb, now=1000)
        report: dict = {}
        store.context_for("which pet did they adopt?", emb, k=6, max_chars=260, report=report)
        recalled = [f["id"] for f in report["recalled"]]
        assert len(recalled) == 6 and all("parts" in f for f in report["recalled"])
        assert 0 < len(report["facts"]) < 6 and set(report["facts"]) <= set(recalled)  # the budget cut the rest
        assert report["recall"]["k"] == 6
    finally:
        store.close()


def _project_app(tmp_path, monkeypatch):
    from test_hybrid_recall import _Emb, _store
    from openwiki import cli
    from openwiki.project import Project
    from openwiki.search import SemanticIndex
    from openwiki.web.server import WikiWebApp
    from openwiki.wiki import Wiki, WikiPage, write_wiki

    store, emb = _store(tmp_path), _Emb()
    store.remember("s1", [MemoryFact("Ann", "adopted a cat named", "Tom"), MemoryFact("Ann", "likes", "hiking"),
                          MemoryFact("OpenWiki", "current release is", "v0.1.0")], emb, now=1000)
    root = tmp_path / "brain"
    root.mkdir()
    (root / "openwiki.toml").write_text('[project]\nname = "brain"\n\n[memory]\nenabled = true\n', encoding="utf-8")
    project = Project.load(root)
    project.graph_path.parent.mkdir(parents=True, exist_ok=True)
    project.graph_path.write_text("", encoding="utf-8")       # the hook only reads a graph that exists

    class _Reader:                                              # the hook closes its reader; the test keeps it
        def __getattr__(self, name):
            return getattr(store, name)

        def close(self):
            pass
    monkeypatch.setattr(cli, "_hook_embedder", lambda p: emb)
    monkeypatch.setattr(cli, "_open_reader", lambda *a, **kw: _Reader())
    wiki = Wiki(title="T", pages=[WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=1,
                                           pdf_page_end=1, text="pets adopt")], source="x.pdf", split_level=1)
    write_wiki(wiki, tmp_path / "wiki")
    index = SemanticIndex.build(wiki, emb, size_words=50, overlap_words=10)
    return WikiWebApp(tmp_path / "wiki", index=index, graph=store, project=project), project, store


def test_the_hook_preview_shows_exactly_what_the_hook_injects(tmp_path, monkeypatch, capsys):
    from openwiki import cli
    app, project, store = _project_app(tmp_path, monkeypatch)
    try:
        prompt = "What is the name of the cat Ann adopted?"
        capsys.readouterr()
        cli._hook_inject(project, {"prompt": prompt})
        printed = capsys.readouterr().out
        d = app.memory_context(prompt)
        assert d["hook"] and d["chore"] is None and printed == d["header"] + d["context"] + "\n"
        assert d["recalled"] and all("parts" in f and "shown" in f for f in d["recalled"])
        assert {f["object"] for f in d["recalled"] if f["shown"]} <= {"Tom", "hiking", "v0.1.0"}
        assert d["k"] == project.context_k and d["budget"] == project.context_budget
        assert app.memory_context("push and tag v1.2.3")["chore"] == "git"           # the hook skips it
        assert app.memory_context("/clear")["chore"] == "command"
        dated = app.memory_context(prompt, as_of="1970-01-02")                      # a date: not the hook's view
        assert not dated["hook"] and dated["header"] == "" and dated["chore"] is None
        recall = app.memory_recall(prompt, k=2)
        assert recall["explain"]["k"] == 2 and all("parts" in f for f in recall["facts"])
    finally:
        store.close()
