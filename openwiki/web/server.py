"""A zero-dependency web UI over the wiki and the agent (stdlib ``http.server``).

`WikiWebApp` holds the app state (wiki dir, search index, editing agent) and
exposes plain methods; the request handler is a thin JSON/static wrapper around
them. `ThreadingHTTPServer` keeps the UI responsive while a chat turn waits on
Ollama; a lock serializes the (stateful, single-user) agent.
"""

from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

from ..chat_agent import WikiAgent
from ..search import SemanticIndex
from ..tools import WikiTools, _first_heading

STATIC_DIR = Path(__file__).parent / "static"

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".md": "text/markdown; charset=utf-8",
}


class WikiWebApp:
    def __init__(self, wiki_dir, index: Optional[SemanticIndex] = None,
                 agent: Optional[WikiAgent] = None, tools: Optional[WikiTools] = None,
                 graph=None, dry_run: bool = False, project=None) -> None:
        self.wiki_dir = Path(wiki_dir)
        self.index = index
        self.tools = tools or WikiTools(wiki_dir, index=index, dry_run=dry_run)
        self.agent = agent
        self.graph = graph  # optional GraphStore
        self.project = project  # optional Project (serves its build status in the UI)
        self._lock = threading.Lock()
        self._ans_lock = threading.Lock()          # guards the answer-eval job state
        self._ans_job = {"status": "idle"}

    _MAIN_BOOK = "(Hauptverzeichnis)"

    def manifest(self) -> dict:
        manifest_path = self.wiki_dir / "wiki.json"
        if manifest_path.is_file():
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        else:
            pages = [
                {"slug": f.stem, "title": _first_heading(f), "parent": None, "children": []}
                for f in sorted((self.wiki_dir / "pages").glob("*.md"))
            ]
            data = {"title": self.wiki_dir.name, "pages": pages}
        self._annotate_provenance(data)
        return data

    @staticmethod
    def _book_label(path) -> str:
        """The ``sources/`` subfolder a source file lives in (its 'book'), or '' for a
        top-level source — used to group merged multi-source corpora by origin."""
        m = re.search(r"(?:^|/)sources/([^/]+)/", str(path).replace("\\", "/"))
        return m.group(1) if m else ""

    def _annotate_provenance(self, data: dict) -> None:
        """Tag each page with its ``source`` (top-level-ancestor title, i.e. the source file it
        came from after the multi-source merge) and — when a project maps the per-source
        top-level nodes to their files, in order — a ``book`` (the ``sources/`` subfolder, else
        ``(Hauptverzeichnis)``). Adds ``sources`` (+ ``books``) lists. Best-effort: leaves pages
        untagged on any mismatch (single-source passthrough, count mismatch, no project)."""
        pages = data.get("pages") or []
        by_slug = {p["slug"]: p for p in pages}

        def root_of(page):
            seen = set()
            while page.get("parent") and page["parent"] in by_slug and page["slug"] not in seen:
                seen.add(page["slug"])
                page = by_slug[page["parent"]]
            return page

        roots = [p for p in pages if not p.get("parent")]
        book_of_root = {}
        if self.project is not None:
            try:
                paths = self.project.source_paths()   # doc sources, manifest order
                if len(paths) == len(roots):           # order-aligned with merge output
                    book_of_root = {r["slug"]: (self._book_label(p) or self._MAIN_BOOK)
                                    for r, p in zip(roots, paths)}
            except Exception:
                book_of_root = {}
        sources, books = [], []
        for p in pages:
            r = root_of(p)
            p["source"] = r.get("title", r["slug"])
            if p["source"] not in sources:
                sources.append(p["source"])
            if book_of_root:
                p["book"] = book_of_root.get(r["slug"], self._MAIN_BOOK)
                if p["book"] not in books:
                    books.append(p["book"])
        data["sources"] = sources
        if books:
            data["books"] = books

    def get_page(self, slug: str) -> dict:
        markdown = self.tools.read_page(slug)
        if markdown.startswith("ERROR"):
            raise KeyError(markdown)
        return {"slug": slug, "markdown": markdown}

    # The graph connectivity a page's prose doesn't hyperlink, surfaced as a "See also"
    # panel: outgoing cross-references + backlinks (#1) and semantic neighbours (#2). The
    # structural spine (parent/child/prev/next) is already linked in the page, so it's omitted.
    _RELATED_GROUPS = [
        ("references", "Verweise"), ("referenced_by", "Erwähnt in"),
        ("relation", "Verwandte Themen"), ("similar", "Ähnliche Seiten"),
        ("shared_entity", "Gemeinsame Begriffe"),
    ]

    def related(self, slug: str) -> dict:
        """Related pages for the **Verwandte Seiten** panel under a wiki page — the graph's
        connectivity (references + backlinks + typed relations + similar + shared-entity) as
        clickable links. Read-only; ``available: false`` without a graph or for an unknown page."""
        if self.graph is None:
            return {"available": False, "reason": "no_graph"}
        try:
            nb = self.graph.neighborhood(slug)
        except Exception:                       # KeyError (unknown page) or a graph hiccup
            return {"available": False, "reason": "not_in_graph"}
        by_rel: dict = {}
        for n in nb.get("nodes", []):
            rel = n.get("rel")
            if rel and rel != "center":
                by_rel.setdefault(rel, []).append({"slug": n["slug"], "title": n["title"]})
        # the citation phrases behind each outgoing reference ("Abschnitt 1.6" → page) — linked
        # inline by the client (#1) and shown on the "Verweise" chips
        try:
            citations = self.graph.citations(slug)
        except Exception:
            citations = []
        cited_as: dict = {}
        for c in citations:
            cited_as.setdefault(c["slug"], []).append(c["label"])
        for page in by_rel.get("references", []):
            if cited_as.get(page["slug"]):
                page["cited_as"] = sorted(cited_as[page["slug"]])
        groups = [{"key": k, "label": label, "pages": by_rel[k]}
                  for k, label in self._RELATED_GROUPS if by_rel.get(k)]
        # canonical entities mentioned on the page — for client-side auto-linking (#3)
        entities = []
        try:
            if self.graph.has_entities():
                entities = [{"name": e["name"], "aliases": e.get("aliases", [])}
                            for e in self.graph.entities_for_page(slug)]
        except Exception:
            pass
        return {"available": True, "groups": groups, "entities": entities,
                "citations": citations}

    def search(self, query: str, k: int = 8, hybrid: bool = False) -> dict:
        if self.index is None:
            raise RuntimeError("No search index is loaded. Run `openwiki index` first.")
        results = self.index.search_hybrid(query, k=k) if hybrid else self.index.search(query, k=k)
        return {"hybrid": bool(hybrid), "results": [
            {"score": r.score, "slug": r.page_slug, "title": r.page_title,
             "pdf_page_start": r.pdf_page_start, "pdf_page_end": r.pdf_page_end,
             "text": r.text}
            for r in results
        ]}

    def graph_neighborhood(self, slug: str) -> dict:
        if self.graph is None:
            raise RuntimeError("No graph is loaded. Run `openwiki graph-build` first.")
        return self.graph.neighborhood(slug)

    def graph_explore(self, slug: str) -> dict:
        if self.graph is None:
            raise RuntimeError("No graph is loaded. Run `openwiki graph-build` first.")
        return self.graph.explore(slug)

    def graph_expand(self, node_type: str, node_id: str) -> dict:
        if self.graph is None:
            raise RuntimeError("No graph is loaded. Run `openwiki graph-build` first.")
        return self.graph.expand(node_type, node_id)

    def _source_info(self, source) -> dict:
        """Display info for a declared source (file, directory/repo, or URL)."""
        from ..sources import is_url, source_exists, source_type
        if is_url(source):
            return {"path": str(source), "exists": True, "type": "web"}
        path = Path(str(source))
        try:
            shown = str(path.relative_to(self.project.root))
        except ValueError:
            shown = str(path)
        return {"path": shown, "exists": source_exists(source), "type": source_type(source)}

    def project_info(self) -> dict:
        """Active project's identity, sources, per-stage build status, and the
        registered-project list (for the UI 'Projekt' tab). ``{"project": null}``
        when the server wasn't started inside an OpenWiki project."""
        if self.project is None:
            return {"project": None, "registry": []}
        from ..pipeline import STAGES, BuildState, compute_fingerprints
        from ..userconfig import Registry

        from ..sources import source_stem
        p = self.project
        sources = p.source_paths()
        multi = len(sources) > 1
        stem = source_stem(sources[0]) if sources else ""
        fingerprints = compute_fingerprints(p, sources) if sources else {}
        state = BuildState.load(p)
        ingest_out = (p.parsed_dir / "_corpus.json") if multi else (p.parsed_dir / f"{stem}.json")
        exists = {
            "ingest": bool(sources) and ingest_out.is_file(),
            "wiki": (p.wiki_dir / "wiki.json").is_file(),
            "index": (p.index_dir / "index.json").is_file(),
            "graph": p.graph_path.exists(),
            "memory": p.graph_path.exists(),   # the remembered tier lives in the graph
        }
        stages = []
        for stage in STAGES:
            record = state.get(stage)
            if not exists.get(stage):
                status = "missing"
            elif not sources or state.fingerprint(stage) != fingerprints.get(stage):
                status = "stale"
            else:
                status = "up_to_date"
            stages.append({"name": stage, "status": status,
                           "stats": record.get("stats", {}), "built": record.get("built", ""),
                           "duration_s": record.get("duration_s"), "llm": record.get("llm")})

        registry_obj = Registry.load()
        active = registry_obj.active()
        registry = [{"name": name, "path": path, "active": name == active}
                    for name, path in sorted(registry_obj.projects().items())]
        # Full settings, per section (every parameter used across the pipeline).
        settings = {
            "build": {"split_level": p.setting("build", "split_level", 2),
                      "tables": p.setting("build", "tables", True),
                      "synthesize_outline": p.setting("build", "synthesize_outline", True),
                      "chunk_size": p.setting("build", "chunk_size", 180),
                      "overlap": p.setting("build", "overlap", 30)},
            "models": {"embed": p.setting("models", "embed", "bge-m3"),
                       "chat": p.setting("models", "chat", ""),
                       "host": p.setting("models", "host", "")},
            "graph": {"similar_k": p.setting("graph", "similar_k", 6),
                      "references": p.setting("graph", "references", True),
                      "entities": p.setting("graph", "entities", False),
                      "relations": p.setting("graph", "relations", False),
                      "resolve_entities": p.setting("graph", "resolve_entities", False),
                      "entity_max_chars": p.setting("graph", "entity_max_chars", 8000)},
            "serve": {"port": p.setting("serve", "port", 8000),
                      "bind": p.setting("serve", "bind", "127.0.0.1"),
                      "temperature": p.setting("serve", "temperature", 0.2)},
            "memory": {"enabled": p.memory_enabled},
        }

        # The entity ontology (parsed "Name: description" list), if any.
        ontology = []
        for entry in (p.setting("graph", "entity_types", None) or []):
            name, sep, desc = str(entry).partition(":")
            ontology.append({"name": name.strip(), "description": desc.strip() if sep else ""})

        # Semantic index details (model + embedding dim + chunk count).
        index_info = None
        if self.index is not None:
            try:
                index_info = {"model": self.index.model_name,
                              "dim": int(self.index.embeddings.shape[1]),
                              "chunks": len(self.index.chunks)}
            except Exception:
                index_info = None
        elif (p.index_dir / "index.json").is_file():
            try:
                meta = json.loads((p.index_dir / "index.json").read_text(encoding="utf-8"))
                index_info = {"model": meta.get("model"), "dim": meta.get("dim"),
                              "chunks": meta.get("count")}
            except (ValueError, OSError):
                index_info = None

        # Live knowledge-graph statistics (nodes / edges / entity-type distribution).
        graph_stats = None
        if self.graph is not None:
            try:
                graph_stats = self.graph.stats()
            except Exception:
                graph_stats = None

        return {
            "project": {
                "name": p.name,
                "root": str(p.root),
                "description": p.description,
                "sources": [self._source_info(s) for s in sources],
                "stages": stages,
                "settings": settings,
                "ontology": ontology,
                "index": index_info,
                "graph": graph_stats,
                "communities": self.communities(),
            },
            "registry": registry,
        }

    def communities(self) -> list:
        """The graph's topical communities + LLM summaries (empty without a graph or
        before ``openwiki communities`` has run). For the Projekt tab / ``/api/communities``."""
        if self.graph is None:
            return []
        try:
            return self.graph.communities()
        except Exception:
            return []

    def ask_global(self, question: str) -> dict:
        """Global search: answer a thematic question from the community summaries
        (one chat call). The answer's ``[n]`` markers index the returned communities
        (same size-desc order as the CLI ``ask --global``). Read-only."""
        from ..graph.community import answer_global

        question = (question or "").strip()
        if not question:
            raise RuntimeError("empty question")
        comms = self.communities()
        if not comms:
            raise RuntimeError("No communities in the graph. Run `openwiki communities` first.")
        chat = getattr(self.agent, "chat", None)
        if chat is None:
            raise RuntimeError("No chat model is available.")
        answer = answer_global(chat, question, [(c["label"], c["summary"]) for c in comms])
        cited = sorted({int(m) for m in re.findall(r"\[(\d+)\]", answer)})
        return {
            "question": question, "answer": answer, "cited": cited,
            "communities": [{"marker": i + 1, "label": c["label"], "size": c["size"]}
                            for i, c in enumerate(comms)],
        }

    def _eval_root(self) -> Path:
        return self.project.root if self.project is not None else self.wiki_dir.parent

    def eval_sets(self) -> list:
        """Available eval-set files (``*.jsonl``) in the project root."""
        return sorted(p.name for p in self._eval_root().glob("*.jsonl"))

    def eval_set_path(self, name=None) -> Optional[Path]:
        """Resolve an eval set by bare filename (default ``eval.jsonl``)."""
        root = self._eval_root()
        if name:
            candidate = root / Path(str(name)).name    # strip any directory (no traversal)
            if candidate.suffix == ".jsonl" and candidate.is_file():
                return candidate
        return root / "eval.jsonl"

    def run_eval(self, top_k: int = 5, expand_k: int = 3, eval_set=None) -> dict:
        """Run the retrieval benchmark (RAG vs GraphRAG) over the chosen eval set at
        the given budget, for the Evaluation tab. Read-only."""
        from ..eval import evaluate, load_eval_set, make_retrievers

        top_k = max(1, min(int(top_k), 20))
        expand_k = max(0, min(int(expand_k), 10))
        path = self.eval_set_path(eval_set)
        if self.index is None:
            return {"error": "No search index is loaded.", "exists": bool(path and path.is_file())}
        if path is None or not path.is_file():
            return {"exists": False, "path": str(path) if path else None,
                    "top_k": top_k, "expand_k": expand_k, "reports": []}
        items = load_eval_set(path)
        budget = top_k + expand_k
        rag_fn, graphrag_fn = make_retrievers(self.index, self.graph, top_k, expand_k)

        def report(name: str, retrieve) -> dict:
            rep = evaluate(items, retrieve, budget)
            return {
                "name": name, "mrr": rep.mrr, "hit_rate": rep.hit_rate, "recall": rep.recall,
                "items": [{"question": r.question, "expected": r.expected,
                           "ranked": r.ranked[:budget], "hit": bool(r.hit), "rr": r.rr}
                          for r in rep.items],
            }

        reports = [report("RAG", rag_fn)]
        if graphrag_fn is not None:
            reports.append(report("GraphRAG", graphrag_fn))
        return {"exists": True, "path": str(path), "eval_set": path.name, "count": len(items),
                "top_k": top_k, "expand_k": expand_k, "budget": budget, "reports": reports}

    def compare(self, question: str, top_k: int = 5, expand_k: int = 3,
                answers: bool = False) -> dict:
        """Side-by-side RAG vs GraphRAG for one question (Evaluation tab, live A/B).
        Always returns the retrieved pages per side; with ``answers`` it also
        generates both answers (2 chat-model calls — slow). Read-only."""
        from ..agent import RAGAgent

        question = (question or "").strip()
        if not question:
            raise RuntimeError("empty question")
        if self.index is None:
            raise RuntimeError("No search index is loaded.")
        top_k = max(1, min(int(top_k), 20))
        expand_k = max(0, min(int(expand_k), 10))
        chat = getattr(self.agent, "chat", None)
        want_answers = bool(answers) and chat is not None

        def side(graph) -> dict:
            agent = RAGAgent(self.index, chat, top_k=top_k, graph=graph, expand_k=expand_k)
            if want_answers:
                result = agent.answer(question)
                sources, answer, cited = result.sources, result.answer, sorted(result.cited_markers())
            else:
                sources, answer, cited = agent.retrieve(question), None, []
            return {
                "answer": answer, "cited": cited,
                "sources": [{"marker": s.marker, "slug": s.page_slug, "title": s.page_title,
                             "kind": s.kind, "score": round(float(s.score), 3)} for s in sources],
            }

        return {
            "question": question, "top_k": top_k, "expand_k": expand_k,
            "answers": want_answers, "graph_available": self.graph is not None,
            "answers_available": chat is not None,
            "rag": side(None),
            "graphrag": side(self.graph) if self.graph is not None else None,
        }

    def health_stats(self) -> dict:
        """Knowledge-base quality signals for the Evaluation tab's health panel."""
        if self.graph is None:
            return {"graph": False}
        try:
            return {"graph": True, **self.graph.health()}
        except Exception as exc:  # never crash the tab on a graph hiccup
            return {"graph": True, "error": str(exc)}

    def answer_eval_status(self) -> dict:
        with self._ans_lock:
            return dict(self._ans_job)

    def start_answer_eval(self, top_k: int = 5, expand_k: int = 3,
                          judge: bool = False, limit=None, eval_set=None) -> dict:
        """Kick off (in a background thread) an answer-quality run over ``eval.jsonl``
        — RAG vs GraphRAG answers scored by citation grounding + optional LLM judge.
        Slow, so it runs async; poll ``answer_eval_status()``. Read-only."""
        from ..eval import load_eval_set
        from ..llm import OllamaChat

        with self._ans_lock:
            if self._ans_job.get("status") == "running":
                return dict(self._ans_job)
            if self.index is None or self.graph is None:
                return {"status": "error", "error": "Answer eval needs both an index and a graph."}
            chat = getattr(self.agent, "chat", None)
            if chat is None:
                return {"status": "error", "error": "No chat model configured."}
            path = self.eval_set_path(eval_set)
            if path is None or not path.is_file():
                return {"status": "error", "error": f"No eval set at {path}."}
            items = load_eval_set(path)
            if limit:
                items = items[: max(1, int(limit))]
            if not items:
                return {"status": "error", "error": "Eval set is empty."}
            top_k = max(1, min(int(top_k), 20))
            expand_k = max(1, min(int(expand_k), 10))
            judge_chat = (OllamaChat(model=chat.model, host=chat.host, temperature=0.0)
                          if judge and isinstance(chat, OllamaChat) else None)
            set_name = path.name
            self._ans_job = {"status": "running", "done": 0, "total": len(items), "eval_set": set_name,
                             "judged": bool(judge_chat), "top_k": top_k, "expand_k": expand_k}

        def worker():
            from ..eval import run_answer_eval
            try:
                def progress(done, total):
                    with self._ans_lock:
                        if self._ans_job.get("status") == "running":
                            self._ans_job["done"] = done
                result = run_answer_eval(items, self.index, self.graph, chat,
                                         top_k, expand_k, judge=judge_chat, on_progress=progress)
                with self._ans_lock:
                    self._ans_job = {"status": "done", "done": len(items), "total": len(items),
                                     "eval_set": set_name, "top_k": top_k, "expand_k": expand_k,
                                     "result": result}
            except Exception as exc:  # never let the worker thread crash silently
                with self._ans_lock:
                    self._ans_job = {"status": "error", "error": str(exc)}

        threading.Thread(target=worker, daemon=True).start()
        with self._ans_lock:
            return dict(self._ans_job)

    def metrics(self, limit: int = 50) -> dict:
        """Runtime observability snapshot (recent LLM/embed/HTTP events + aggregates)
        for the System tab / ``/api/metrics``."""
        from .. import metrics as m
        return m.COLLECTOR.snapshot(limit=limit)

    # -- world-model analysis (Analyse tab) ----------------------------------

    def analyze(self, k: int = 8, method: str = "auto", max_edges: int = 400) -> dict:
        """Graph↔semantic coupling metrics + a 2-D semantic map for the Analyse tab
        (``/api/analyze``). Read-only + offline (stored embeddings). ``available: false``
        (with a ``reason``) when the index or graph is missing."""
        if self.index is None:
            return {"available": False, "reason": "no_index"}
        if self.graph is None:
            return {"available": False, "reason": "no_graph"}
        from ..analysis.coupling import analyze_coupling, page_vectors
        from ..analysis.projection import project_2d

        coupling = analyze_coupling(self.index, self.graph, k=k)
        slugs, vecs = page_vectors(self.index)
        coords, used = project_2d(vecs, method=method)
        titles = {p["slug"]: p.get("title", p["slug"]) for p in self.manifest().get("pages", [])}
        community_of = {s: cid for cid, members in self.graph.community_members().items()
                        for s in members}
        points = [{
            "slug": s, "title": titles.get(s, s),
            "x": round(float(coords[i][0]), 4), "y": round(float(coords[i][1]), 4),
            "community": community_of.get(s),
        } for i, s in enumerate(slugs)]
        present = set(slugs)
        edges = {}
        for kind, pairs in self.graph.coupling_edges().items():
            kept = [[a, b] for a, b in pairs if a in present and b in present]
            edges[kind] = kept[:max_edges]           # cap per kind so the overlay stays light
        communities = [{"id": c["id"], "label": c["label"]} for c in self.communities()]
        return {"available": True, "coupling": coupling,
                "projection": {"method": used, "points": points},
                "edges": edges, "communities": communities}

    def analyze_gaps(self, top: int = 15) -> dict:
        """P3 gap-mining for the Analyse tab (`/api/analyze/gaps`): missing cross-refs,
        near-duplicates, isolated pages, entity-merge candidates. Read-only + offline;
        ``available: false`` (with a reason) when the index or graph is missing."""
        if self.index is None:
            return {"available": False, "reason": "no_index"}
        if self.graph is None:
            return {"available": False, "reason": "no_graph"}
        from ..analysis.gaps import analyze_gaps
        return {"available": True, **analyze_gaps(self.index, self.graph, top=top)}

    def analyze_memory(self) -> dict:
        """P4 memory-tier dynamics for the Analyse tab (`/api/analyze/memory`): revision,
        consolidation, temperature, breadth, growth. Read-only; ``available: false`` when
        there is no graph or no remembered tier."""
        if self.graph is None:
            return {"available": False, "reason": "no_graph"}
        from ..analysis.memory import analyze_memory
        return analyze_memory(self.graph)

    def ask_stream(self, question: str, use_graph: bool = True, hybrid: bool = False,
                   rerank: bool = False, k: int = 5, expand_k: int = 3):
        """Streaming Ask (RAG) for `/api/ask/stream` — a generator of SSE event dicts:
        ``{type:"sources",…}`` then ``{type:"delta",text}``… then ``{type:"done",…}``.
        Fail-soft (yields ``{type:"error"}`` instead of raising). The graph lock is held only
        around retrieval (the first generator step); the token stream runs lock-free so other
        readers aren't blocked during the (long) generation."""
        from ..agent import RAGAgent
        from .. import metrics as m

        question = (question or "").strip()
        if not question:
            yield {"type": "error", "error": "empty question"}; return
        if self.index is None:
            yield {"type": "error", "error": "No search index is loaded."}; return
        chat = getattr(self.agent, "chat", None)
        if chat is None:
            yield {"type": "error", "error": "No chat model is available."}; return
        k = max(1, min(int(k), 20))
        expand_k = max(0, min(int(expand_k), 10))
        graph = self.graph if (use_graph and self.graph is not None) else None
        opts = {"graph": graph is not None, "hybrid": bool(hybrid),
                "rerank": bool(rerank), "k": k, "expand_k": expand_k}
        start = m.COLLECTOR.seq
        agent = RAGAgent(self.index, chat, top_k=k, graph=graph, expand_k=expand_k,
                         hybrid=bool(hybrid), rerank=bool(rerank))
        try:
            gen = agent.stream(question)
            with self._lock:                # retrieval touches the graph → hold the lock here
                ev = next(gen)              # ("sources", [...])
            while True:
                if ev[0] == "sources":
                    yield {"type": "sources", "options": opts,
                           "sources": [{"marker": s.marker, "slug": s.page_slug, "title": s.page_title,
                                        "kind": s.kind, "score": round(float(s.score), 3)} for s in ev[1]]}
                elif ev[0] == "delta":
                    yield {"type": "delta", "text": ev[1]}
                elif ev[0] == "done":
                    yield {"type": "done", "answer": ev[1], "cited": ev[2],
                           "stats": _turn_stats(m.COLLECTOR.since(start))}
                try:
                    ev = next(gen)
                except StopIteration:
                    break
        except Exception as exc:            # network/model failure mid-stream
            yield {"type": "error", "error": str(exc)}

    # -- entity/concept browser (Begriffe tab) -------------------------------

    def entities(self, query: str = "", etype: str = "", limit: int = 200) -> dict:
        """Browse canonical entities for the Begriffe tab (`/api/entities`): a filtered,
        mention-ranked list + the available types (for the filter). Read-only; ``available:
        false`` when there is no graph or no entity layer."""
        if self.graph is None:
            return {"available": False, "reason": "no_graph"}
        if not self.graph.has_entities():
            return {"available": False, "reason": "no_entities"}
        types = [t["type"] for t in self.graph.stats().get("entity_types", [])]
        return {"available": True, "types": types,
                "has_relations": self.graph.has_relations(),
                "entities": self.graph.list_entities(query, etype, limit)}

    def entity(self, name: str) -> dict:
        """Full record for one canonical entity (`/api/entity/{name}`): description, aliases,
        the pages that mention it, and its typed relations. Read-only."""
        if self.graph is None or not self.graph.has_entities():
            raise RuntimeError("No entity layer in the graph.")
        detail = self.graph.entity_detail(name)
        if detail is None:
            raise KeyError(f"entity '{name}' not found")
        return detail

    # -- memory tier (Path B / Second Brain) ---------------------------------

    def _memory_embedder(self):
        return self.index.embedder if self.index is not None else None

    def memory_info(self) -> dict:
        """Overview for the Memory (Gedächtnis) tab: identity + counts + themes + a
        browsable assertion list. ``available: false`` (with a ``reason``) when there is
        no graph, no remembered content, or the project is in Wiki mode. Read-only."""
        mode = bool(self.project is not None and self.project.memory_enabled)
        if self.graph is None:
            return {"available": False, "reason": "no_graph", "mode": mode}
        try:
            if not self.graph.has_memory():
                return {"available": False, "reason": "empty", "mode": mode}
        except Exception:
            return {"available": False, "reason": "no_graph", "mode": mode}
        identity = self.project.identity if self.project is not None else ""
        return {
            "available": True,
            "mode": mode,
            "identity": identity,
            "has_embedder": self._memory_embedder() is not None,
            "stats": self.graph.memory_overview(),
            "themes": self.graph.memory_concepts(),
            "assertions": self.graph.list_assertions(limit=200),
        }

    def memory_recall(self, query: str, k: int = 8, include_superseded: bool = False,
                      as_of=None, known_at=None) -> dict:
        """The remembered facts most relevant to a query (decay-weighted, B6 activation tier),
        optionally at a point in time (B7 ``as_of`` valid time / ``known_at`` transaction time —
        ISO dates or epochs). Needs a graph with memory + a search index. Read-only."""
        embedder = self._memory_embedder()
        if self.graph is None or embedder is None:
            raise RuntimeError("Recall needs a graph with memory and a search index.")
        query = (query or "").strip()
        if not query:
            raise RuntimeError("empty query")
        k = max(1, min(int(k), 30))
        from ..graph.temporal import parse_date
        facts = self.graph.recall(query, embedder, k=k, include_superseded=bool(include_superseded),
                                  as_of=parse_date(as_of), known_at=parse_date(known_at))
        return {"query": query, "k": k, "facts": facts}

    def memory_timeline(self, query: str, groups: int = 3) -> dict:
        """B7: the full history (every validity interval, when recorded, by which session) of
        the subject+predicate pairs best matching ``query``. Read-only."""
        embedder = self._memory_embedder()
        if self.graph is None or embedder is None:
            raise RuntimeError("Timeline needs a graph with memory and a search index.")
        query = (query or "").strip()
        if not query:
            raise RuntimeError("empty query")
        return {"query": query,
                "groups": self.graph.timeline(query, embedder, groups=max(1, min(int(groups), 10)))}

    def memory_context(self, query: str, as_of=None) -> dict:
        """The assembled three-tier session context for a query (identity + activation +
        attractors, B6), budgeted by the project's ``context_budget``; ``as_of`` (B7, ISO date)
        assembles the memory as it was true then. Read-only."""
        embedder = self._memory_embedder()
        if self.graph is None or embedder is None:
            raise RuntimeError("Context needs a graph with memory and a search index.")
        query = (query or "").strip()
        if not query:
            raise RuntimeError("empty query")
        identity = self.project.identity if self.project is not None else ""
        budget = self.project.context_budget if self.project is not None else None
        from ..graph.temporal import parse_date
        probes = None
        chat = getattr(self.agent, "chat", None)
        if chat is not None and self.project is not None and self.project.memory_probes:
            from ..graph.memory import constraint_probes
            probes = constraint_probes(chat, query)          # P1 cue-trigger; fail-soft → []
        context = self.graph.context_for(query, embedder, identity=identity, max_chars=budget,
                                         as_of=parse_date(as_of), probes=probes)
        return {"query": query, "context": context, "identity": identity, "budget": budget}

    def chat(self, message: str) -> dict:
        if self.agent is None:
            raise RuntimeError("Chat is unavailable (no agent configured).")
        from .. import metrics as m
        start = m.COLLECTOR.seq
        with self._lock:
            turn = self.agent.send(message)
        return {
            "reply": turn.reply,
            "tool_calls": [
                {"name": c.name, "arguments": c.arguments, "result": c.result}
                for c in turn.tool_calls
            ],
            "stats": _turn_stats(m.COLLECTOR.since(start)),
        }

    def ask(self, question: str, use_graph: bool = True, hybrid: bool = False,
            rerank: bool = False, k: int = 5, expand_k: int = 3) -> dict:
        """RAG question-answering for the chat pane's **Ask** mode: retrieve → cited answer,
        with the measured retrieval options chosen *per request* (GraphRAG / hybrid / re-rank /
        ``k``). Builds a one-shot `RAGAgent` (like `compare`), read-only (never edits), and
        returns the answer + its cited sources (seed vs graph-expanded) + per-turn stats. The
        browser twin of the CLI ``ask``; the **Global** mode routes to `ask_global` instead."""
        from ..agent import RAGAgent
        from .. import metrics as m

        question = (question or "").strip()
        if not question:
            raise RuntimeError("empty question")
        if self.index is None:
            raise RuntimeError("No search index is loaded.")
        chat = getattr(self.agent, "chat", None)
        if chat is None:
            raise RuntimeError("No chat model is available.")
        k = max(1, min(int(k), 20))
        expand_k = max(0, min(int(expand_k), 10))
        graph = self.graph if (use_graph and self.graph is not None) else None
        start = m.COLLECTOR.seq
        with self._lock:
            agent = RAGAgent(self.index, chat, top_k=k, graph=graph, expand_k=expand_k,
                             hybrid=bool(hybrid), rerank=bool(rerank))
            result = agent.answer(question)
        return {
            "question": question,
            "answer": result.answer,
            "cited": sorted(result.cited_markers()),
            "sources": [{"marker": s.marker, "slug": s.page_slug, "title": s.page_title,
                         "kind": s.kind, "score": round(float(s.score), 3)}
                        for s in result.sources],
            "options": {"graph": graph is not None, "hybrid": bool(hybrid),
                        "rerank": bool(rerank), "k": k, "expand_k": expand_k},
            "graph_available": self.graph is not None,
            "stats": _turn_stats(m.COLLECTOR.since(start)),
        }


def _turn_stats(events) -> Optional[dict]:
    """Aggregate the chat-model calls made during one agent turn (a tool loop can make
    several) into a compact per-turn stat line. ``None`` when nothing was recorded (e.g.
    a fake model in tests, or a non-Ollama backend)."""
    chat_evs = [e for e in events if e.kind == "chat"]
    if not chat_evs:
        return None
    dur = sum(e.duration_ms for e in chat_evs)
    eval_tok = sum(e.eval_tokens or 0 for e in chat_evs)
    prompt_tok = sum(e.prompt_tokens or 0 for e in chat_evs)
    return {
        "calls": len(chat_evs),
        "duration_ms": round(dur, 1),
        "eval_tokens": eval_tok,
        "prompt_tokens": prompt_tok,
        "tokens_per_sec": round(eval_tok / (dur / 1000.0), 1) if dur > 0 and eval_tok else None,
    }


def make_handler(app: WikiWebApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "OpenWiki"

        def log_message(self, *args):  # keep the console quiet
            pass

        # -- responders --

        def _json(self, obj, status=200):
            self._status = status
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _bytes(self, data, content_type, status=200):
            self._status = status
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")  # always serve fresh static assets
            self.end_headers()
            self.wfile.write(data)

        def _body_json(self) -> dict:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _static(self, rel: str):
            target = (STATIC_DIR / rel).resolve()
            root = STATIC_DIR.resolve()
            if target != root and root not in target.parents:
                return self._json({"error": "forbidden"}, 403)
            if not target.is_file():
                return self._json({"error": "not found"}, 404)
            self._bytes(target.read_bytes(), _CONTENT_TYPES.get(target.suffix, "application/octet-stream"))

        # -- routes --

        def do_GET(self):
            t0 = time.perf_counter()
            self._status = 200
            path = urlparse(self.path).path
            try:
                if path == "/":
                    return self._static("index.html")
                if path.startswith("/static/"):
                    return self._static(path[len("/static/"):])
                if path == "/api/wiki":
                    return self._json(app.manifest())
                if path == "/api/project":
                    return self._json(app.project_info())
                if path == "/api/eval-sets":
                    return self._json({"sets": app.eval_sets(), "default": "eval.jsonl"})
                if path == "/api/eval":
                    query = parse_qs(urlparse(self.path).query)
                    top_k = int(query.get("top_k", ["5"])[0])
                    expand_k = int(query.get("expand_k", ["3"])[0])
                    eval_set = query.get("eval_set", [None])[0]
                    return self._json(app.run_eval(top_k, expand_k, eval_set))
                if path == "/api/health":
                    return self._json(app.health_stats())
                if path == "/api/metrics":
                    query = parse_qs(urlparse(self.path).query)
                    limit = int(query.get("limit", ["50"])[0])
                    return self._json(app.metrics(limit))
                if path == "/api/memory":
                    return self._json(app.memory_info())
                if path == "/api/communities":
                    return self._json({"communities": app.communities()})
                if path == "/api/analyze":
                    query = parse_qs(urlparse(self.path).query)
                    k = int(query.get("k", ["8"])[0])
                    method = query.get("method", ["auto"])[0]
                    return self._json(app.analyze(k=k, method=method))
                if path == "/api/analyze/gaps":
                    query = parse_qs(urlparse(self.path).query)
                    return self._json(app.analyze_gaps(top=int(query.get("top", ["15"])[0])))
                if path == "/api/analyze/memory":
                    return self._json(app.analyze_memory())
                if path == "/api/entities":
                    query = parse_qs(urlparse(self.path).query)
                    return self._json(app.entities(
                        query.get("q", [""])[0], query.get("type", [""])[0],
                        int(query.get("limit", ["200"])[0])))
                if path.startswith("/api/entity/"):
                    name = unquote(path[len("/api/entity/"):])
                    try:
                        return self._json(app.entity(name))
                    except KeyError as exc:
                        return self._json({"error": str(exc)}, 404)
                    except RuntimeError as exc:
                        return self._json({"error": str(exc)}, 503)
                if path == "/api/answer-eval":
                    return self._json(app.answer_eval_status())
                if path.startswith("/api/pages/"):
                    slug = unquote(path[len("/api/pages/"):])
                    try:
                        return self._json(app.get_page(slug))
                    except KeyError as exc:
                        return self._json({"error": str(exc)}, 404)
                if path.startswith("/api/related/"):
                    return self._json(app.related(unquote(path[len("/api/related/"):])))
                if path.startswith("/api/graph/"):
                    slug = unquote(path[len("/api/graph/"):])
                    try:
                        return self._json(app.graph_explore(slug))
                    except KeyError as exc:
                        return self._json({"error": str(exc)}, 404)
                    except RuntimeError as exc:  # no graph loaded
                        return self._json({"error": str(exc)}, 503)
                return self._json({"error": "not found"}, 404)
            except Exception as exc:  # never let the handler thread crash
                return self._json({"error": str(exc)}, 500)
            finally:
                _observe_request("GET", path, self._status, (time.perf_counter() - t0) * 1000.0)

        def _stream_ask(self, t0):
            """Server-Sent-Events stream for POST /api/ask/stream (bypasses the JSON path):
            emit `data: {event}` lines as the RAG answer generates. Fail-soft on a dropped
            client (BrokenPipe)."""
            path = "/api/ask/stream"
            try:
                data = self._body_json()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")   # disable proxy buffering
                self.end_headers()
                for event in app.ask_stream(
                    (data.get("question") or "").strip(),
                    bool(data.get("graph", True)), bool(data.get("hybrid", False)),
                    bool(data.get("rerank", False)), int(data.get("k", 5)),
                    int(data.get("expand_k", 3))):
                    self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass                                          # client navigated away
            except Exception as exc:                          # pragma: no cover
                try:
                    self.wfile.write(("data: " + json.dumps({"type": "error", "error": str(exc)}) + "\n\n").encode("utf-8"))
                except Exception:
                    pass
            finally:
                _observe_request("POST", path, self._status, (time.perf_counter() - t0) * 1000.0)

        def do_POST(self):
            t0 = time.perf_counter()
            self._status = 200
            path = urlparse(self.path).path
            if path == "/api/ask/stream":
                return self._stream_ask(t0)
            try:
                data = self._body_json()
                if path == "/api/search":
                    query = (data.get("query") or "").strip()
                    return self._json(app.search(query, int(data.get("k", 8)), bool(data.get("hybrid", False)))
                                      if query else {"results": []})
                if path == "/api/chat":
                    message = (data.get("message") or "").strip()
                    if not message:
                        return self._json({"error": "empty message"}, 400)
                    return self._json(app.chat(message))
                if path == "/api/ask":
                    question = (data.get("question") or "").strip()
                    if not question:
                        return self._json({"error": "empty question"}, 400)
                    return self._json(app.ask(
                        question,
                        use_graph=bool(data.get("graph", True)),
                        hybrid=bool(data.get("hybrid", False)),
                        rerank=bool(data.get("rerank", False)),
                        k=int(data.get("k", 5)),
                        expand_k=int(data.get("expand_k", 3))))
                if path == "/api/compare":
                    question = (data.get("question") or "").strip()
                    if not question:
                        return self._json({"error": "empty question"}, 400)
                    return self._json(app.compare(
                        question, int(data.get("top_k", 5)), int(data.get("expand_k", 3)),
                        bool(data.get("answers", False))))
                if path == "/api/global":
                    question = (data.get("question") or "").strip()
                    if not question:
                        return self._json({"error": "empty question"}, 400)
                    return self._json(app.ask_global(question))
                if path == "/api/recall":
                    query = (data.get("query") or "").strip()
                    if not query:
                        return self._json({"error": "empty query"}, 400)
                    return self._json(app.memory_recall(
                        query, int(data.get("k", 8)), bool(data.get("include_superseded", False)),
                        as_of=data.get("as_of"), known_at=data.get("known_at")))
                if path == "/api/context":
                    query = (data.get("query") or "").strip()
                    if not query:
                        return self._json({"error": "empty query"}, 400)
                    return self._json(app.memory_context(query, as_of=data.get("as_of")))
                if path == "/api/timeline":
                    query = (data.get("query") or "").strip()
                    if not query:
                        return self._json({"error": "empty query"}, 400)
                    return self._json(app.memory_timeline(query, int(data.get("groups", 3))))
                if path == "/api/answer-eval":
                    return self._json(app.start_answer_eval(
                        int(data.get("top_k", 5)), int(data.get("expand_k", 3)),
                        bool(data.get("judge", False)), data.get("limit"), data.get("eval_set")))
                if path == "/api/graph/expand":
                    node_id = (data.get("id") or "").strip()
                    if not node_id:
                        return self._json({"error": "missing id"}, 400)
                    try:
                        return self._json(app.graph_expand(data.get("type", "page"), node_id))
                    except KeyError as exc:
                        return self._json({"error": str(exc)}, 404)
                return self._json({"error": "not found"}, 404)
            except RuntimeError as exc:  # service not configured (no index/agent)
                return self._json({"error": str(exc)}, 503)
            except Exception as exc:
                return self._json({"error": str(exc)}, 500)
            finally:
                _observe_request("POST", path, self._status, (time.perf_counter() - t0) * 1000.0)

    return Handler


def _observe_request(method: str, path: str, status: int, ms: float) -> None:
    """Record an API request as an ``http`` metrics event. Skips static assets and the
    metrics-poll itself so the System tab doesn't drown in its own traffic. Best-effort."""
    if not path.startswith("/api/") or path == "/api/metrics":
        return
    try:
        from .. import metrics as m
        m.COLLECTOR.record("http", f"{method} {path}", duration_ms=ms, status=status)
    except Exception:  # pragma: no cover
        pass


def serve(app: WikiWebApp, host: str = "127.0.0.1", port: int = 8000) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    url = f"http://{host}:{port}"
    print(f"OpenWiki web UI running at {url}   (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        httpd.server_close()
