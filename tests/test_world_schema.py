"""Path C, C2.1 — the table registry (``openwiki/graph/schema.py``): the builder creates every table from it, B0's
snapshot keeps the authoritative world tables across a rebuild, older graphs get them lazily, the portable export
carries them."""
from __future__ import annotations

import pytest

from openwiki.graph import schema


def _pages(*titles):
    from openwiki.wiki import WikiPage
    return [WikiPage(slug=f"{i:03d}-{t.replace('/', '-').replace('.', '-')}", title=t, level=1, order=i,
                     pdf_page_start=i + 1, pdf_page_end=i + 1, text=f"python project {t}")
            for i, t in enumerate(titles)]


def _build(tmp_path, titles=("A", "openwiki/__init__.py")):
    from test_memory import _MemEmbedder
    from openwiki.graph import GraphBuilder
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki
    wiki = Wiki(title="T", pages=_pages(*titles), source="x", split_level=1)
    index = SemanticIndex.build(wiki, _MemEmbedder(), size_words=50, overlap_words=10)
    GraphBuilder(tmp_path / "graph").build(wiki, index)
    return tmp_path / "graph", wiki


WORLD = {
    "Variable": [{"id": "sensor:code-version", "label": "OpenWiki code version is", "subject": "OpenWiki",
                  "predicate": "code version is", "kind": "version", "sensor": "py-constant",
                  "environment": "openwiki", "created_at": 10, "updated_at": 10}],
    "Law": [{"id": "version-in-code", "text": "the version in pyproject.toml equals __version__", "kind": "equality",
             "check": "version == code_version", "origin": "manifest", "status": "accepted", "health": "ok",
             "last_result": "holds", "last_checked": 20, "created_at": 10, "updated_at": 20}],
    "Event": [{"id": "e1", "kind": "violation", "at": 30, "detail": "0.2.0 != 0.1.0"}],
    "DESCRIBED_IN": [{"from": "sensor:code-version", "to": "001-openwiki-__init__-py"}],
    "CONSTRAINS": [{"from": "version-in-code", "to": "sensor:code-version"}],
    "FROM_LAW": [{"from": "e1", "to": "version-in-code"}],
}


def test_the_registry_declares_every_table_the_builder_creates(tmp_path):
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphStore
    gpath, _ = _build(tmp_path)
    store = GraphStore(gpath)
    try:
        for table in schema.TABLES:
            cols = schema.present_columns(store._rows)(table.name)
            assert cols is not None, table.name
            if not table.rel:
                assert cols == set(table.columns), table.name           # the DDL creates what the registry says
        assert {t.name for t in schema.tables(group=schema.WORLD)} == {
            "Variable", "DESCRIBED_IN", "Law", "CONSTRAINS", "FROM_PAGE", "FROM_FACT", "Event", "CONCERNS",
            "FROM_LAW", "INVOLVES"}
        assert all(t.authoritative for t in schema.tables(group=schema.WORLD))
        assert not any(t.authoritative for t in schema.tables(group=schema.DOCUMENT))
        assert {t.name for t in schema.tables(authoritative=True, custom=True)} == {
            "REINFORCES", "Session", "Assertion", "ASSERTS", "SUPERSEDES"}   # B0's own reader, every generation
    finally:
        store.close()


def test_a_rebuild_keeps_the_world_model(tmp_path):
    pytest.importorskip("kuzu")
    from test_memory import _MemEmbedder
    from openwiki.graph import GraphStore
    from openwiki.graph.memory import MemoryFact
    gpath, _ = _build(tmp_path)
    store = GraphStore(gpath, writable=True)
    try:
        store.remember("s1", [MemoryFact("the project", "uses", "python")], _MemEmbedder())
        aid = store._rows("MATCH (a:Assertion) RETURN a.id;")[0][0]
        world = dict(WORLD, INVOLVES=[{"from": "e1", "to": aid}])
        counts = store.restore_world(world)
        assert counts["Variable"] == 1 and counts["DESCRIBED_IN"] == 1 and counts["INVOLVES"] == 1
        assert store.restore_world(world) == counts                        # merged on the key: idempotent
        assert len(store._rows("MATCH (l:Law) RETURN l.id;")) == 1
    finally:
        store.close()
    _build(tmp_path, titles=("A",))                    # rebuild — the code file's page is gone now
    store = GraphStore(gpath)
    try:
        snap = store.world_snapshot()
        assert snap["Variable"][0]["id"] == "sensor:code-version"
        assert snap["Law"][0]["last_result"] == "holds" and snap["Law"][0]["status"] == "accepted"
        assert snap["Event"][0]["detail"] == "0.2.0 != 0.1.0"
        assert snap["CONSTRAINS"] == [{"from": "version-in-code", "to": "sensor:code-version"}]
        assert snap["INVOLVES"] == [{"from": "e1", "to": aid}]             # the assertion survived, so did its edge
        assert "DESCRIBED_IN" not in snap                                  # its page didn't: the edge went with it
        assert store.memory_snapshot()["world"] == snap                    # what memory export --full carries
    finally:
        store.close()


def test_older_graphs_get_the_world_tables_lazily(tmp_path):
    kuzu = pytest.importorskip("kuzu")
    from openwiki.graph import GraphStore
    gpath = tmp_path / "old"
    db = kuzu.Database(str(gpath))
    conn = kuzu.Connection(db)
    for table in schema.tables(group=schema.DOCUMENT):                     # a graph from before v0.126
        conn.execute(table.create(4))
    conn.close()
    db.close()
    store = GraphStore(gpath)
    try:
        assert store.world_snapshot() == {}                                # read-only: absent, nothing to read
    finally:
        store.close()
    store = GraphStore(gpath, writable=True)
    try:
        store._ensure_memory_schema(4)
        store._ensure_world_schema()
        assert schema.present_columns(store._rows)("Law") == set(schema.BY_NAME["Law"].columns)
        assert store.restore_world({"Law": WORLD["Law"]}) == {"Law": 1}
    finally:
        store.close()


def test_snapshot_and_restore_without_kuzu():
    executed = []
    restore = schema.restore(lambda q, p: executed.append((q, p)), dict(WORLD, DESCRIBED_IN=[
        {"from": "sensor:code-version", "to": "gone-page"}]), {"Page": {"other"}})
    assert restore == {"Variable": 1, "Law": 1, "Event": 1, "CONSTRAINS": 1, "FROM_LAW": 1}   # no edge to a missing page
    assert executed[0][0].startswith("MERGE (n:Variable {id: $k}) SET ")
    assert all("MERGE" in q for q, _ in executed)

    def rows(query):
        return [["v1", "label"]] if query.startswith("MATCH (n:Variable)") else []
    present = {"Variable": {"id", "label"}}.get                             # an older Variable table, two columns
    assert schema.snapshot(rows, present) == {"Variable": [{"id": "v1", "label": "label"}]}
    assert schema.restore(lambda q, p: None, {}) == {} and schema.restore(lambda q, p: None, None) == {}


def test_the_world_model_travels_in_cogx(tmp_path):
    from openwiki import memory_export as mx
    world = dict(WORLD, Law=WORLD["Law"] + [{"id": "draft", "text": "a proposed law", "kind": "equality",
                                             "status": "proposed"}],
                 INVOLVES=[{"from": "e1", "to": "a1"}])
    records = mx.world_records(world)
    assert sorted((r["kind"], r["external_id"]) for r in records) == [
        ("entity", "variable:sensor:code-version"), ("memory", "event:e1"), ("memory", "law:draft"),
        ("memory", "law:version-in-code")]                                  # standard kinds (decision 14)
    law = next(r for r in records if r["external_id"] == "law:version-in-code")
    assert law["content"] == "the version in pyproject.toml equals __version__"
    assert law["categories"] == ["law", "equality"]
    assert law["metadata"]["openwiki"]["constrains"] == ["sensor:code-version"]

    def norm(w):
        return {t: sorted(sorted(r.items()) for r in rows) for t, rows in w.items()}
    out = mx.write_cogx(records, tmp_path / "archive")
    manifest, back = mx.read_cogx(out)
    assert manifest["counts"] == {"entity": 1, "memory": 3}
    snap, foreign = mx.from_cogx(back)
    assert foreign == [] and norm(snap["world"]) == norm(world)            # lossless through an archive
    believed = mx.believed_only({"assertions": [], "world": world})["world"]
    assert [law["id"] for law in believed["Law"]] == ["version-in-code"]   # accepted laws only
    assert set(believed) == {"Law", "CONSTRAINS"}                          # no variables, no events
    assert "world" not in mx.believed_only({"assertions": []})


def test_variables_one_per_binding_described_by_their_file(tmp_path):
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphStore
    from openwiki.world.bindings import Binding
    gpath, _ = _build(tmp_path)
    code = Binding("code-version", "openwiki", "py-constant", {"path": "./openwiki/__init__.py", "name": "__version__"},
                   "OpenWiki", "code version is")
    tag = Binding("release", "openwiki", "git", {"read": "latest-tag"}, "OpenWiki", "current release is")
    assert code.variable()["kind"] == "version" and code.variable()["paths"] == ["openwiki/__init__.py"]
    assert tag.variable()["id"] == "sensor:release" and tag.variable()["paths"] == []
    store = GraphStore(gpath, writable=True)
    try:
        variables = [code.variable(), tag.variable()]
        assert store.upsert_variables(variables, now=5, dry_run=True) == {"added": 2, "updated": 0, "unchanged": 0}
        assert store.variables() == []                                        # a dry run writes nothing
        assert store.upsert_variables(variables, now=5) == {"added": 2, "updated": 0, "unchanged": 0}
        rows = {v["id"]: v for v in store.variables()}
        assert rows["sensor:code-version"]["pages"] == ["001-openwiki-__init__-py"]   # DESCRIBED_IN its file's page
        assert rows["sensor:release"]["pages"] == [] and rows["sensor:release"]["sensor"] == "git latest-tag"
        assert store.upsert_variables(variables, now=6) == {"added": 0, "updated": 0, "unchanged": 2}  # on change only
        moved = Binding("release", "openwiki", "git", {"read": "latest-tag"}, "OpenWiki", "latest release is")
        assert store.upsert_variables([moved.variable()], now=7)["updated"] == 1
        row = {v["id"]: v for v in store.variables()}["sensor:release"]
        assert (row["label"], row["created_at"], row["updated_at"]) == ("OpenWiki latest release is", 5, 7)
    finally:
        store.close()
