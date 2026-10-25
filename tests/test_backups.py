"""Rolling memory backups at ``sleep`` (track O, O.4): before it changes anything, ``sleep`` writes a full export
(``memory export --full``'s content) into ``.openwiki/backups/`` and keeps the newest ``[memory] backups`` (7). The
graph is the memory's only copy, and Kuzu is archived upstream. Pure file tests run anywhere; the sleep round-trip is
Kuzu-gated."""

from __future__ import annotations

import time

import numpy as np
import pytest

from openwiki.graph.memory import MemoryFact
from openwiki.memory_export import (
    TAR_SUFFIX, backup_name, backup_time, list_backups, newest_backup, prune_backups, read_cogx,
    write_backup,
)


def test_backup_names_are_utc_and_round_trip():
    t = 1_792_900_000.0
    name = backup_name(t)
    assert name.startswith("memory-") and name.endswith("Z" + TAR_SUFFIX)
    assert backup_time(name) == t
    assert backup_time("memory.cogx.tar.gz") is None and backup_time("partial-" + name) is None


def test_list_prune_and_newest(tmp_path):
    names = [backup_name(1_792_900_000.0 + 86400 * i) for i in range(5)]
    for n in names:
        (tmp_path / n).write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("not a backup", encoding="utf-8")
    (tmp_path / ("partial-" + names[0])).write_bytes(b"half")
    assert [p.name for p in list_backups(tmp_path)] == names
    assert newest_backup(tmp_path).name == names[-1]
    assert prune_backups(tmp_path, 3) == 2
    assert [p.name for p in list_backups(tmp_path)] == names[2:]
    assert not list(tmp_path.glob("partial-*"))                    # a failed run's leftover goes too
    assert (tmp_path / "notes.txt").exists()                       # other files are never touched
    assert prune_backups(tmp_path, 0) == 2                         # at least one is always kept
    assert newest_backup(tmp_path / "missing") is None and list_backups(tmp_path / "missing") == []


def test_write_backup_leaves_no_partial_file(tmp_path, monkeypatch):
    rec = [{"kind": "fact", "id": "f1", "subject_ref": "a", "predicate": "b", "object_ref": "c"}]
    path = write_backup(rec, tmp_path / "backups", 1_792_900_000.0)
    assert path.exists() and path.name == backup_name(1_792_900_000.0)
    assert not list((tmp_path / "backups").glob("partial-*"))
    from openwiki import memory_export

    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(memory_export, "write_cogx", boom)
    with pytest.raises(OSError):
        write_backup(rec, tmp_path / "backups", 1_792_990_000.0)
    assert [p.name for p in list_backups(tmp_path / "backups")] == [path.name]
    assert not list((tmp_path / "backups").glob("partial-*"))


@pytest.mark.parametrize("line, keep", [("backups = 3\n", 3), ('backups = "lots"\n', 7), ("", 7),
                                        ("backups = 0\n", 0)])
def test_project_setting(tmp_path, line, keep):
    from openwiki.project import Project
    (tmp_path / "openwiki.toml").write_text(f'[project]\nname = "p"\n\n[memory]\n{line}', encoding="utf-8")
    p = Project.load(tmp_path)
    assert p.memory_backups == keep and p.backups_dir == tmp_path / ".openwiki" / "backups"


# -- sleep writes the backup -----------------------------------------------------------------------------------


class _Emb:
    VOCAB = ["server", "port", "graph", "kuzu"]
    name = "fake:backup"

    def _vec(self, text):
        low = text.lower()
        v = np.array([float(low.count(w)) for w in self.VOCAB], dtype=np.float32)
        return v if v.any() else v + 1e-3

    def embed_documents(self, texts):
        return np.vstack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


def _brain(tmp_path, backups=None):
    pytest.importorskip("kuzu")
    from openwiki.graph import GraphBuilder, GraphStore
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki, WikiPage

    root = tmp_path / "brain"
    root.mkdir()
    extra = f"backups = {backups}\n" if backups is not None else ""
    (root / "openwiki.toml").write_text(
        f'[project]\nname = "brain"\n\n[memory]\nenabled = true\n{extra}', encoding="utf-8")
    graph = root / "output" / "graph"
    pages = [WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=1, pdf_page_end=1,
                      text="server port kuzu graph")]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=1)
    GraphBuilder(graph).build(wiki, SemanticIndex.build(wiki, _Emb(), size_words=50, overlap_words=10))
    store = GraphStore(graph, writable=True)
    try:
        store.remember("s1", [MemoryFact("the server", "listens on", "port 8137"),
                              MemoryFact("the graph", "is stored in", "Kuzu"),
                              MemoryFact("v0.74.0", "was pushed and tagged", "yes")], _Emb(), now=1000)
    finally:
        store.close()
    return root


def test_sleep_backs_up_before_it_forgets_and_keeps_the_newest(tmp_path, capsys):
    from openwiki import cli, runlog
    root = _brain(tmp_path, backups=2)
    folder = root / ".openwiki" / "backups"

    def forgotten(recs):
        return [r for r in recs if r["kind"] == "fact"
                and (r.get("metadata") or {}).get("openwiki", {}).get("forgotten_at")]

    assert cli.main(["sleep", "--project", str(root), "--no-consolidate"]) == 0
    # the first backup was written before the pass forgot the one-off event: all three facts, none forgotten
    [first] = list_backups(folder)
    manifest, records = read_cogx(first)
    assert manifest["counts"]["fact"] == 3 and forgotten(records) == []
    for _ in range(2):
        time.sleep(1.1)                                            # backup names have one-second resolution
        assert cli.main(["sleep", "--project", str(root), "--no-consolidate"]) == 0
    out = capsys.readouterr().out
    assert "backup → " in out and "keeping the newest 2" in out and "1 older removed" in out
    files = list_backups(folder)
    assert len(files) == 2 and first not in files
    assert len(forgotten(read_cogx(files[-1])[1])) == 1             # later backups hold the forgotten mark
    runs = runlog.read_runs(root / ".openwiki", name="sleep")
    assert runs[-1]["notes"]["backup"] == files[-1].name


def test_sleep_without_backups(tmp_path, capsys):
    from openwiki import cli
    root = _brain(tmp_path, backups=0)
    assert cli.main(["sleep", "--project", str(root), "--no-consolidate"]) == 0
    (tmp_path / "two").mkdir()
    root2 = _brain(tmp_path / "two")
    assert cli.main(["sleep", "--project", str(root2), "--no-consolidate", "--no-backup"]) == 0
    assert "backup → " not in capsys.readouterr().out
    assert list_backups(root / ".openwiki" / "backups") == [] == list_backups(root2 / ".openwiki" / "backups")


def test_a_backup_restores_into_a_fresh_graph(tmp_path, capsys):
    """The way back: move the graph aside, build a fresh one (`owiki build --only graph --force`), import the backup —
    a full export restores losslessly into an empty memory."""
    from openwiki import cli
    from openwiki.graph import GraphBuilder, GraphStore
    from openwiki.search import SemanticIndex
    from openwiki.wiki import Wiki, WikiPage
    root = _brain(tmp_path)
    assert cli.main(["sleep", "--project", str(root), "--no-consolidate"]) == 0   # backup first, then forgets one
    backup = newest_backup(root / ".openwiki" / "backups")
    graph = root / "output" / "graph"
    aside = tmp_path / "aside"
    aside.mkdir()
    for f in graph.parent.glob("graph*"):                           # the Kuzu file and its sidecars
        f.rename(aside / f.name)
    pages = [WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=1, pdf_page_end=1,
                      text="server port kuzu graph")]
    wiki = Wiki(title="T", pages=pages, source="x.pdf", split_level=1)
    GraphBuilder(graph).build(wiki, SemanticIndex.build(wiki, _Emb(), size_words=50, overlap_words=10))
    assert cli.main(["memory", "import", str(backup), "--project", str(root)]) == 0
    store = GraphStore(graph)
    try:
        ov = store.memory_overview()
        assert ov["assertions"] == 3 and ov["forgotten"] == 0       # the memory as it was before that sleep
        assert store.recall("which port does the server listen on", _Emb(), k=1)[0]["object"] == "port 8137"
    finally:
        store.close()
