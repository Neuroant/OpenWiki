"""The web UI's project switcher (v0.120): projects found next to the known ones, the AppHolder's switch and list,
the HTTP routes (each request answered by one project, named in a header), and the CLI's switch, which builds the
next project's app from that project's manifest."""

from __future__ import annotations

import copy
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import unquote

import pytest

from openwiki.project import MANIFEST, Project, find_projects


def _manifest(root, name, extra=""):
    root.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST).write_text(f'[project]\nname = "{name}"\n{extra}', encoding="utf-8")
    return root


def test_find_projects_walks_two_levels_and_skips_noise(tmp_path):
    a = _manifest(tmp_path / "a", "a")
    b = _manifest(tmp_path / "group" / "b", "b")
    _manifest(tmp_path / "a" / "inner", "inner")                 # inside a project: that project's own
    deep = _manifest(tmp_path / "x" / "y" / "deep", "deep")      # three levels down
    for noise in ("node_modules", ".hidden", "output"):
        _manifest(tmp_path / noise / "p", noise)
    assert find_projects([tmp_path]) == [a.resolve(), b.resolve()]
    assert deep.resolve() in find_projects([tmp_path], depth=3)
    assert find_projects([tmp_path / "missing", None]) == []
    assert find_projects([tmp_path], limit=1) == []               # bounded: only the root looked at


class _App:
    """A stand-in for WikiWebApp: a project and a wiki title."""

    def __init__(self, name, root=None):
        self.project = type("P", (), {"name": name, "root": root})() if name else None
        self.title = name

    def manifest(self):
        return {"title": self.title, "pages": []}


def test_holder_switches_by_folder_or_name_and_remembers_under_a_free_name(tmp_path, monkeypatch):
    from openwiki.userconfig import Registry
    from openwiki.web.server import AppHolder
    monkeypatch.setenv("OPENWIKI_HOME", str(tmp_path / "home"))
    a = _manifest(tmp_path / "projects" / "a", "alpha")
    b = _manifest(tmp_path / "projects" / "b", "alpha")              # the same name, another folder
    built = []
    holder = AppHolder(_App("alpha", a), factory=lambda root, old: built.append((root, old.title)) or _App("next", root))
    Registry.load().add("alpha", a)
    assert holder.switch(root=b, remember=True) == {"name": "alpha", "root": str(b.resolve()), "registered": "alpha-2"}
    assert built == [(b.resolve(), "alpha")] and holder.app.title == "next"
    assert Registry.load().projects()["alpha-2"] == b.resolve().as_posix()      # "alpha" was taken
    assert holder.switch(name="alpha")["root"] == str(a.resolve())               # by its registered name
    with pytest.raises(FileNotFoundError):
        holder.switch(root=tmp_path / "nothing here")
    with pytest.raises(FileNotFoundError):
        holder.switch(name="nope")
    with pytest.raises(RuntimeError):                                            # serve --sync: no factory
        AppHolder(_App("x")).switch(root=a)


def test_holder_lists_the_current_registered_and_found_projects(tmp_path, monkeypatch):
    from openwiki.userconfig import Registry
    from openwiki.web.server import AppHolder
    monkeypatch.setenv("OPENWIKI_HOME", str(tmp_path / "home"))
    cur = _manifest(tmp_path / "projects" / "cur", "current one", "\n[memory]\nenabled = true\n")
    _manifest(tmp_path / "projects" / "group" / "sib", "sibling")
    far = _manifest(tmp_path / "elsewhere" / "far", "far away")
    (tmp_path / "projects" / "broken").mkdir()
    (tmp_path / "projects" / "broken" / MANIFEST).write_text("not = [toml", encoding="utf-8")
    p = Project.load(cur)
    for f in (p.wiki_dir / "wiki.json", p.index_dir / "index.json"):           # built
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}", encoding="utf-8")
    Registry.load().add("far", far)
    d = AppHolder(_App("current one", p.root), factory=lambda r, o: o).projects()
    first = d["projects"][0]
    assert first["name"] == "current one" and first["current"] and first["built"] and first["memory"]
    by = {e["name"]: e for e in d["projects"]}
    assert set(by) == {"current one", "sibling", "far away", "broken"}
    assert by["far away"]["registered"] == "far" and by["sibling"]["registered"] is None
    assert not by["sibling"]["built"] and not by["sibling"]["current"]
    assert "error" in by["broken"] and d["switchable"]


def test_http_requests_follow_the_switch(tmp_path, monkeypatch):
    from openwiki.web.server import AppHolder, make_handler
    monkeypatch.setenv("OPENWIKI_HOME", str(tmp_path / "home"))
    a = _manifest(tmp_path / "a", "Gedächtnis A")                   # a non-ASCII name travels in the header
    b = _manifest(tmp_path / "b", "beta")
    holder = AppHolder(_App("Gedächtnis A", a), factory=lambda root, old: _App("beta", root))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(holder))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(path):
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return unquote(r.headers["X-OpenWiki-Project"]), json.loads(r.read().decode("utf-8"))

    def post(body, ctype="application/json"):
        req = urllib.request.Request(base + "/api/projects/switch", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": ctype}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            return err.code, {}
    try:
        assert get("/api/wiki") == ("Gedächtnis A", {"title": "Gedächtnis A", "pages": []})
        assert post({"root": str(b)}, ctype="text/plain")[0] == 415      # a cross-site form can't switch it
        status, body = post({"root": str(b)})
        assert status == 200 and body["switched"]["name"] == "beta"
        assert get("/api/wiki") == ("beta", {"title": "beta", "pages": []})
        assert post({"root": str(tmp_path / "none")})[0] == 400
        holder.factory = None
        assert post({"root": str(a)})[0] == 409 and get("/api/projects")[1]["switchable"] is False
    finally:
        httpd.shutdown()
        httpd.server_close()


def _built_project(root, name, extra=""):
    from openwiki.wiki import Wiki, WikiPage, write_wiki
    _manifest(root, name, extra)
    page = WikiPage(slug="000-a", title="A", level=1, order=0, pdf_page_start=1, pdf_page_end=1, text="x")
    write_wiki(Wiki(title=f"{name} wiki", pages=[page], source="x.pdf", split_level=1), Project.load(root).wiki_dir)
    return root


@pytest.mark.parametrize("flags, beta_model", [([], "model-beta"), (["--model", "mine"], "mine")])
def test_the_cli_switch_builds_the_next_projects_app_from_its_manifest(tmp_path, monkeypatch, flags, beta_model):
    from openwiki import cli
    monkeypatch.setenv("OPENWIKI_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cli, "_transient_fold", lambda *a, **k: None)
    alpha = _built_project(tmp_path / "alpha", "alpha", '\n[models]\nchat = "model-alpha"\n')
    beta = _built_project(tmp_path / "beta", "beta", '\n[models]\nchat = "model-beta"\n')
    args = cli._build_argparser().parse_args(["serve", "--project", str(alpha)] + flags)
    args.project_obj = cli._resolve_project(args.project)
    args.raw_args = copy.copy(args)                                  # as main() keeps them
    cli._apply_project(args, args.project_obj)
    app, _, embedder, _ = cli._serve_app(args)
    assert app.project.name == "alpha" and app.agent.chat.model == ("mine" if flags else "model-alpha")
    current, folds = {"graph": args.graph, "embedder": embedder}, []
    new = cli._project_switcher(args, current, folds)(beta, app)
    for t in folds:
        t.join()
    assert new.project.name == "beta" and new.wiki_dir.resolve() == Project.load(beta).wiki_dir.resolve()
    assert new.agent.chat.model == beta_model                        # beta's manifest — unless given as a flag
    assert current["graph"] == Project.load(beta).graph_path          # what serve folds at shutdown
    assert len(folds) == 1                                           # the old project's queue, in the background
