"""Tests for the web app (offline: fake embedder + scripted agent + live socket)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import numpy as np
import pytest

from openwiki.chat_agent import WikiAgent
from openwiki.search import SemanticIndex
from openwiki.tools import WikiTools
from openwiki.web.server import WikiWebApp, make_handler
from openwiki.wiki import Wiki, WikiPage, write_wiki


class FakeEmbedder:
    VOCAB = ["lautstarke", "effekt", "midi"]
    name = "fake:bow"

    def _vec(self, text):
        low = text.lower()
        v = np.array([float(low.count(w)) for w in self.VOCAB], dtype=np.float32)
        return v if v.any() else v + 1e-6

    def embed_documents(self, texts):
        return np.vstack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


class ScriptedChat:
    name = "scripted"

    def __init__(self, script):
        self.script = list(script)

    def chat_raw(self, messages, tools=None):
        return self.script.pop(0)


def _edit_call():
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {
        "name": "edit_page",
        "arguments": {"slug": "000-a", "old_text": "Alpha lautstarke content.",
                      "new_text": "ALPHA lautstarke content."}}}]}


@pytest.fixture
def app(tmp_path) -> WikiWebApp:
    wiki_dir = tmp_path / "wiki"
    pages = [
        WikiPage(slug="000-a", title="Alpha", level=1, order=0, pdf_page_start=1,
                 pdf_page_end=1, text="Alpha lautstarke content."),
        WikiPage(slug="001-b", title="Beta", level=1, order=1, pdf_page_start=2,
                 pdf_page_end=2, text="Beta effekt content."),
    ]
    wiki = Wiki(title="Testwiki", pages=pages, source="x.pdf", split_level=1)
    write_wiki(wiki, wiki_dir)
    index = SemanticIndex.build(wiki, FakeEmbedder(), size_words=50, overlap_words=10)
    tools = WikiTools(wiki_dir, index=index)
    agent = WikiAgent(ScriptedChat([_edit_call(), {"role": "assistant", "content": "Erledigt."}]), tools)
    return WikiWebApp(wiki_dir, index=index, agent=agent, tools=tools)


# -- app methods ------------------------------------------------------------

def test_manifest(app):
    manifest = app.manifest()
    assert manifest["title"] == "Testwiki"
    assert {p["slug"] for p in manifest["pages"]} == {"000-a", "001-b"}


def test_manifest_provenance(app):
    manifest = app.manifest()
    # both pages are their own top-level source (no parent); no project → no books
    by_slug = {p["slug"]: p for p in manifest["pages"]}
    assert by_slug["000-a"]["source"] == "Alpha"
    assert by_slug["001-b"]["source"] == "Beta"
    assert manifest["sources"] == ["Alpha", "Beta"]
    assert "books" not in manifest              # single-file sources, no project mapping


def test_book_label_extracts_subfolder():
    from openwiki.web.server import WikiWebApp
    assert WikiWebApp._book_label("sources/Lehrbuch der Softwaretechnik/1 - X.pdf") == "Lehrbuch der Softwaretechnik"
    assert WikiWebApp._book_label("G:/p/sources/Lehrbuch/9 - Y.pdf") == "Lehrbuch"
    assert WikiWebApp._book_label("sources/1 - Grundbegriffe.pdf") == ""   # top-level → no book


def test_get_page(app):
    assert "Alpha" in app.get_page("000-a")["markdown"]


def test_get_missing_page(app):
    with pytest.raises(KeyError):
        app.get_page("404-x")


class _RelGraph:
    """Fake graph exposing neighborhood() + entities for the related-panel / auto-link tests."""
    def neighborhood(self, slug, similar_k=6):
        if slug == "missing":
            raise KeyError(slug)
        return {"center": slug, "edges": [], "nodes": [
            {"slug": slug, "title": "Self", "rel": "center"},
            {"slug": "p-parent", "title": "Parent", "rel": "parent"},          # structural → omitted
            {"slug": "p-ref", "title": "Ref Target", "rel": "references"},
            {"slug": "p-back", "title": "Backlink", "rel": "referenced_by"},
            {"slug": "p-sim", "title": "Similar", "rel": "similar"},
            {"slug": "p-ent", "title": "Shared", "rel": "shared_entity"},
            {"slug": "p-rel", "title": "Related", "rel": "relation"},
        ]}

    def has_entities(self):
        return True

    def entities_for_page(self, slug):
        return [{"name": "Rekursion", "type": "K", "description": "", "aliases": ["Recursion"]}]


def test_related_groups(app):
    app.graph = _RelGraph()
    out = app.related("p0")
    assert out["available"] is True
    # curated order (structural parent/child/prev/next omitted)
    assert [g["key"] for g in out["groups"]] == [
        "references", "referenced_by", "relation", "similar", "shared_entity"]
    refs = next(g for g in out["groups"] if g["key"] == "references")
    assert refs["label"] == "Verweise" and refs["pages"][0]["slug"] == "p-ref"
    # #3: page entities (for client-side auto-linking) ride along in the same payload
    assert out["entities"] == [{"name": "Rekursion", "aliases": ["Recursion"]}]


def test_related_without_graph(app):
    assert app.related("p0")["available"] is False   # fixture has no graph


def test_related_unknown_page(app):
    app.graph = _RelGraph()
    assert app.related("missing")["available"] is False


def test_search(app):
    results = app.search("lautstarke", k=3)["results"]
    assert results and results[0]["slug"] == "000-a"


def test_search_hybrid(app):
    out = app.search("lautstarke", k=3, hybrid=True)   # BM25 + dense fusion path
    assert out["hybrid"] is True
    assert "000-a" in [r["slug"] for r in out["results"]]


def test_run_eval(app, tmp_path):
    assert app.run_eval()["exists"] is False           # no eval.jsonl yet
    (tmp_path / "eval.jsonl").write_text(
        '{"question": "lautstarke", "pages": ["000-a"]}\n'
        '{"question": "effekt", "pages": ["001-b"]}\n', encoding="utf-8")
    result = app.run_eval(top_k=2, expand_k=1)
    assert result["exists"] and result["count"] == 2
    assert [r["name"] for r in result["reports"]] == ["RAG"]   # no graph → RAG only
    rag = result["reports"][0]
    assert rag["hit_rate"] == 1.0 and rag["mrr"] == 1.0        # each question finds its page
    assert result["budget"] == 3


def test_compare_retrieval_only(app):
    result = app.compare("lautstarke", top_k=2, expand_k=1, answers=False)
    assert result["graph_available"] is False and result["graphrag"] is None
    assert result["answers"] is False
    assert result["rag"]["answer"] is None
    assert "000-a" in [s["slug"] for s in result["rag"]["sources"]]   # semantic hit


def test_health_stats_without_graph(app):
    assert app.health_stats() == {"graph": False}   # app fixture has no graph


def test_communities_without_graph(app):
    assert app.communities() == []                   # no graph → no communities


def test_ask_global_needs_communities(app):
    with pytest.raises(RuntimeError):                 # app fixture has no graph/communities
        app.ask_global("Was sind die Hauptthemen?")


def test_answer_eval_needs_a_graph(app):
    assert app.answer_eval_status() == {"status": "idle"}
    result = app.start_answer_eval()                 # app fixture has no graph
    assert result["status"] == "error" and "graph" in result["error"].lower()


def test_chat_edits_page(app):
    out = app.chat("bitte ändern")
    assert out["reply"] == "Erledigt."
    assert out["tool_calls"][0]["name"] == "edit_page"
    assert "ALPHA lautstarke content." in app.get_page("000-a")["markdown"]
    assert "stats" in out                     # per-turn telemetry key always present
    assert out["stats"] is None               # a fake (non-Ollama) chat records nothing


class _RagChat:
    """A minimal RAG-answering fake: `chat(messages) -> str` (what RAGAgent.answer calls)."""
    name = "ragfake"

    def chat(self, messages):
        return "Die Lautstärke steht auf Seite [1]."


def test_ask_returns_cited_sources(app):
    app.agent.chat = _RagChat()                      # give the agent a RAG-capable chat model
    out = app.ask("lautstarke", use_graph=False, k=2)
    assert out["answer"].startswith("Die Lautstärke")
    assert out["cited"] == [1]                        # the [1] marker is parsed
    assert "000-a" in [s["slug"] for s in out["sources"]]
    assert out["sources"][0]["kind"] == "seed"         # no graph → seed source
    assert out["options"] == {"graph": False, "hybrid": False, "rerank": False,
                              "k": 2, "expand_k": 3}
    assert out["graph_available"] is False and "stats" in out


def test_ask_hybrid_path_runs(app):
    app.agent.chat = _RagChat()
    out = app.ask("lautstarke", use_graph=False, hybrid=True, k=2)
    assert out["options"]["hybrid"] is True           # BM25+dense seed path exercised
    assert out["sources"]                              # still returns sources


def test_ask_needs_a_chat_model(app):
    app.agent = None
    with pytest.raises(RuntimeError):
        app.ask("lautstarke")


def test_parse_stream_line():
    from openwiki.llm import parse_stream_line
    assert parse_stream_line('{"message":{"content":"Hallo"},"done":false}') == ("Hallo", False, {})
    c, done, stats = parse_stream_line(
        b'{"message":{"content":""},"done":true,"eval_count":10,"prompt_eval_count":5}')
    assert c == "" and done is True and stats.get("eval_tokens") == 10 and stats.get("prompt_tokens") == 5
    assert parse_stream_line("") == ("", False, {})
    assert parse_stream_line("not json") == ("", False, {})


class _RagStreamChat:
    """A fake chat model with streaming: chat_stream yields deltas, chat returns the whole."""
    name = "ragstream"

    def chat_stream(self, messages):
        for t in ["Die Lautstärke ", "steht auf ", "Seite [1]."]:
            yield t

    def chat(self, messages):
        return "Die Lautstärke steht auf Seite [1]."


def test_ask_stream_events(app):
    app.agent.chat = _RagStreamChat()
    events = list(app.ask_stream("lautstarke", use_graph=False, k=2))
    assert events[0]["type"] == "sources"
    assert "000-a" in [s["slug"] for s in events[0]["sources"]]
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert "".join(deltas) == "Die Lautstärke steht auf Seite [1]."
    done = events[-1]
    assert done["type"] == "done"
    assert done["answer"] == "Die Lautstärke steht auf Seite [1]." and done["cited"] == [1]


def test_ask_stream_error_without_chat_model(app):
    app.agent = None
    assert list(app.ask_stream("x")) == [{"type": "error", "error": "No chat model is available."}]


def test_analyze_gaps_without_graph(app):
    out = app.analyze_gaps()                          # fixture has no graph
    assert out["available"] is False and out["reason"] == "no_graph"


def test_analyze_memory_without_graph(app):
    out = app.analyze_memory()                        # fixture has no graph
    assert out["available"] is False and out["reason"] == "no_graph"


def test_entities_without_graph(app):
    out = app.entities()                              # fixture has no graph
    assert out["available"] is False and out["reason"] == "no_graph"


def test_entity_detail_without_graph(app):
    with pytest.raises(RuntimeError):
        app.entity("Reverb")


def test_metrics_snapshot_shape(app):
    snap = app.metrics()
    assert set(snap) == {"events", "summary", "total_events"}
    assert isinstance(snap["events"], list) and isinstance(snap["summary"], dict)


def test_memory_info_no_graph(app):
    info = app.memory_info()                  # app fixture has no graph
    assert info["available"] is False and info["reason"] == "no_graph"


def test_memory_recall_guarded_without_graph(app):
    with pytest.raises(RuntimeError):
        app.memory_recall("anything")


def test_search_without_index(tmp_path):
    (tmp_path / "wiki" / "pages").mkdir(parents=True)
    with pytest.raises(RuntimeError):
        WikiWebApp(tmp_path / "wiki").search("x")


def test_graph_neighborhood_without_graph(tmp_path):
    (tmp_path / "wiki" / "pages").mkdir(parents=True)
    with pytest.raises(RuntimeError):  # -> 503 in the HTTP layer
        WikiWebApp(tmp_path / "wiki").graph_neighborhood("000-a")


def test_project_info_none(app):
    assert app.project_info()["project"] is None


def test_project_info_with_project(tmp_path):
    from openwiki.project import MANIFEST, Project, render_manifest
    root = tmp_path / "proj"
    (root / "sources").mkdir(parents=True)
    (root / "sources" / "m.pdf").write_bytes(b"%PDF-1.4 x")
    (root / MANIFEST).write_text(
        render_manifest(name="demo", sources=[{"type": "pdf", "path": "sources/m.pdf"}]),
        encoding="utf-8")
    proj = Project.load(root)
    (tmp_path / "wiki" / "pages").mkdir(parents=True)

    info = WikiWebApp(tmp_path / "wiki", project=proj).project_info()
    assert info["project"]["name"] == "demo"
    assert [s["name"] for s in info["project"]["stages"]] == ["ingest", "wiki", "index", "graph", "memory"]
    assert all(s["status"] == "missing" for s in info["project"]["stages"])  # nothing built
    assert info["project"]["sources"][0]["exists"] is True


# -- HTTP round trip --------------------------------------------------------

@pytest.fixture
def base_url(app):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.read().decode("utf-8")


def test_http_index_html(base_url):
    status, body = _get(base_url + "/")
    assert status == 200 and "OpenWiki" in body


def test_http_api_wiki(base_url):
    status, body = _get(base_url + "/api/wiki")
    assert status == 200 and json.loads(body)["title"] == "Testwiki"


def test_http_api_page(base_url):
    status, body = _get(base_url + "/api/pages/000-a")
    assert status == 200 and "Alpha" in json.loads(body)["markdown"]


def test_http_static_js(base_url):
    status, body = _get(base_url + "/static/app.js")
    assert status == 200 and "loadPage" in body


def test_http_api_metrics(base_url):
    status, body = _get(base_url + "/api/metrics")
    assert status == 200
    data = json.loads(body)
    assert "events" in data and "summary" in data


def test_http_api_memory(base_url):
    status, body = _get(base_url + "/api/memory")
    assert status == 200
    assert json.loads(body)["available"] is False   # base_url app fixture has no graph


def test_http_index_html_has_tabs(base_url):
    status, body = _get(base_url + "/")
    assert status == 200
    for tab in ('data-tab="wiki"', 'data-tab="help"', 'data-tab="tutorial"',
                'data-tab="graph"', 'data-tab="project"', 'data-tab="system"', 'data-tab="memory"'):
        assert tab in body


def test_http_api_project(base_url):
    status, body = _get(base_url + "/api/project")
    assert status == 200 and json.loads(body)["project"] is None  # app fixture has no project


def test_http_api_communities(base_url):
    status, body = _get(base_url + "/api/communities")
    assert status == 200 and json.loads(body)["communities"] == []  # no graph in the fixture


def test_http_api_global_no_communities(base_url):
    req = urllib.request.Request(
        base_url + "/api/global", data=json.dumps({"question": "Themen?"}).encode(),
        headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 503                      # no communities → service unavailable


def test_http_static_help_doc(base_url):
    status, body = _get(base_url + "/static/help.md")
    assert status == 200 and "# Hilfe" in body


def test_http_static_tutorial_doc(base_url):
    status, body = _get(base_url + "/static/tutorial.md")
    assert status == 200 and "run:" in body  # tutorial action links are present


def test_http_404(base_url):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(base_url + "/api/nope")
    assert exc.value.code == 404
