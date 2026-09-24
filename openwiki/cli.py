"""Command-line interface for OpenWiki.

Subcommands are the unit of capability (``ingest``, ``build-wiki``, ``index``,
``search``, ``ask``, ``chat``, ``graph-build``, ``serve``, ``mcp``) plus the
project commands (``init`` …).

Commands are **project-aware**: when run inside an OpenWiki project (a folder with
an ``openwiki.toml``, discovered from the CWD or via ``--project``), unset paths,
models, host, and split-level are filled from the manifest. Explicit flags always
win; with no project the built-in ``./output`` defaults apply (back-compat).
See ``docs/projects.md``.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

from .agent import RAGAgent
from .analysis import (
    analyze_coupling, analyze_gaps, analyze_memory, diff_fingerprints, is_coupling_fingerprint,
)
from .analysis.compare import notable_differences
from .eval import evaluate, load_eval_set, make_retrievers, run_global_eval
from .chat_agent import WikiAgent, summarize_wiki
from .claude_code_template import scaffold_claude_code
from .opencode_template import scaffold_opencode
from .graph import (
    GraphStore, answer_global, build_graph, capture_session, choose_attribute, detect_communities,
    facts_coexist,
    detect_page_offset, extract_entities, extract_references, extract_references_multi,
    extract_relations, format_memory, resolve_entities, summarize_community, summarize_facts,
)
from .embeddings import OllamaEmbedder
from .graph.temporal import format_date, format_interval, parse_date
from .graph.temporal import session_date as session_date_of
from .llm import OllamaChat
from .mcp_server import build_server
from .merge import combine_documents
from .models import ParsedDocument
from .ontology import format_entity_types, propose_ontology, sample_corpus
from .outline import synthesize_outline
from .sources import is_supported, is_url, parse_source, source_exists, source_stem, source_type
from .pipeline import STAGES, BuildState, compute_fingerprints, stale_stages
from .project import (
    DEFAULT_CHAT, DEFAULT_EMBED, DEFAULT_HOST, MANIFEST, Project, render_manifest,
)
from .search import SemanticIndex
from .tools import WikiTools
from .userconfig import Registry, UserConfig
from .web import WikiWebApp, serve
from .wiki import WikiBuilder, write_wiki


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openwiki",
        description="OpenWiki — tools for building agentic wikis.",
    )
    from . import __version__
    parser.add_argument("--version", action="version", version=f"openwiki {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    # Shared by every command that operates on a project's artifacts.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--project", type=Path, default=None, metavar="DIR",
        help="Project directory (with openwiki.toml). Default: discover from CWD "
             "(or $OPENWIKI_PROJECT).",
    )

    init_p = sub.add_parser("init", help="Scaffold a new OpenWiki project (openwiki.toml + sources/).")
    init_p.add_argument("dir", nargs="?", type=Path, default=Path("."),
                        help="Project directory (default: current directory).")
    init_p.add_argument("--name", default=None, help="Project name (default: directory name).")
    init_p.add_argument("--source", action="append", metavar="SOURCE",
                        help="A source file (pdf/md/txt/html) copied into sources/, a folder, a glob, "
                             "an http(s) URL, or (with --repo) a code-repo directory (repeatable).")
    init_p.add_argument("--repo", action="store_true",
                        help="Treat a directory --source as one code-repository source (in place), "
                             "not a folder to scan for files.")
    init_p.add_argument("--session", action="store_true",
                        help="Register the --source files as session transcripts (Path B memory tier), "
                             "not documents — enables [memory] and captures them on build.")
    init_p.add_argument("--force", action="store_true", help="Overwrite an existing openwiki.toml.")
    init_p.add_argument("--opencode", action="store_true",
                        help="Also scaffold an OpenCode agent config (opencode.json + .opencode/).")

    oc_p = sub.add_parser("opencode", parents=[common],
                          help="Scaffold an OpenCode agent config (opencode.json + .opencode/) into the project.")
    oc_p.add_argument("--force", action="store_true", help="Overwrite existing OpenCode files.")
    oc_p.add_argument("--model", default=None, help="Chat model for the agent (default: the project's models.chat).")
    oc_p.add_argument("--host", default=None, help="Ollama host URL (default: the project's models.host).")

    cc_p = sub.add_parser("claude-code", parents=[common],
                          help="Scaffold a Claude Code config (.mcp.json + .claude/) wiring the project's MCP server.")
    cc_p.add_argument("--force", action="store_true", help="Overwrite existing Claude Code files.")
    cc_p.add_argument("--into", type=Path, default=None, metavar="DIR",
                      help="With --hooks: install ONLY the memory hooks — bound to this project — into "
                           "DIR/.claude/settings.local.json (machine-local), e.g. the repo you work in "
                           "with Claude Code; no MCP/command files are written there.")
    cc_p.add_argument("--hooks", action="store_true",
                      help="Also wire Path B memory hooks into .claude/settings.json — auto-inject "
                           "recalled memory on each prompt + capture the session on end/compaction "
                           "(Second Brain mode; runs `owiki hook` per prompt).")

    build_p = sub.add_parser("build", parents=[common],
                             help="Run the pipeline (ingest → wiki → index → graph) from the manifest.")
    build_p.add_argument("--only", default=None, metavar="STAGES",
                         help="Comma-separated stages to run (ingest,wiki,index,graph,memory).")
    build_p.add_argument("--force", action="store_true", help="Rebuild even stages that are up to date.")
    build_p.add_argument("-v", "--verbose", action="store_true", help="Verbose progress logging.")

    sub.add_parser("status", parents=[common],
                   help="Show the project's sources, settings, and per-stage build state.")

    ont_p = sub.add_parser("ontology", parents=[common],
                           help="Propose a domain entity ontology ([graph] entity_types) from the corpus.")
    ont_p.add_argument("--write", action="store_true",
                       help="Write the proposal into openwiki.toml [graph] entity_types.")
    ont_p.add_argument("--types", type=int, default=7, help="How many types to propose (default: 7).")
    ont_p.add_argument("--model", default=None, help="Ollama chat model (default: manifest models.chat).")
    ont_p.add_argument("--host", default=None, help="Ollama host URL.")

    project_p = sub.add_parser("project", help="Manage the project registry (list/use/add/remove/add-source).")
    psub = project_p.add_subparsers(dest="project_cmd", required=True)
    psub.add_parser("list", help="List registered projects (the active one is marked *).")
    p_use = psub.add_parser("use", help="Set the active project (a from-anywhere default).")
    p_use.add_argument("name")
    p_add = psub.add_parser("add", help="Register a project (name → path).")
    p_add.add_argument("name", nargs="?", help="Name (default: the project's manifest name).")
    p_add.add_argument("path", nargs="?", type=Path, help="Project dir (default: the discovered project).")
    p_rm = psub.add_parser("remove", help="Unregister a project.")
    p_rm.add_argument("name")
    p_src = psub.add_parser("add-source", parents=[common],
                            help="Add a source to openwiki.toml (copy a file, or reference a URL / code repo).")
    p_src.add_argument("path", help="A source file/folder/glob (copied into sources/), an http(s) URL, "
                                    "or (with --repo) a code-repo directory.")
    p_src.add_argument("--repo", action="store_true",
                       help="Treat a directory as one code-repository source (referenced in place).")
    p_src.add_argument("--session", action="store_true",
                       help="Register the file as a session transcript (Path B memory tier), not a document.")

    ingest = sub.add_parser("ingest", parents=[common], help="Parse a source (PDF/Markdown/text/HTML/URL) and extract its content.")
    ingest.add_argument("pdf", metavar="source",
                        help="A source file (.pdf/.md/.txt/.html) or an http(s) URL to fetch.")
    ingest.add_argument(
        "-o", "--out", type=Path, default=None,
        help="Output directory (default: project's output, else ./output).",
    )
    ingest.add_argument("--no-tables", action="store_true", help="Skip table extraction.")
    ingest.add_argument("--images", action="store_true", help="Extract embedded images to <out>/images.")
    ingest.add_argument(
        "--max-pages", type=int, default=None,
        help="Parse only the first N pages (useful for quick tests).",
    )
    ingest.add_argument("-v", "--verbose", action="store_true", help="Verbose progress logging.")

    wiki = sub.add_parser("build-wiki", parents=[common], help="Split a parsed document into linked wiki pages.")
    wiki.add_argument("source", type=Path, help="A source (PDF/Markdown/text), or a .json produced by `ingest`.")
    wiki.add_argument(
        "-o", "--out", type=Path, default=None,
        help="Output directory (default: project's wiki, else ./output/wiki).",
    )
    wiki.add_argument(
        "--split-level", type=int, default=None,
        help="Outline depth that becomes its own page (default: manifest build.split_level, else 2).",
    )
    wiki.add_argument("--no-tables", action="store_true", help="Skip table rendering.")
    wiki.add_argument(
        "--images", action="store_true",
        help="If SOURCE is a PDF, extract images (to <out>/../images).",
    )
    wiki.add_argument("-v", "--verbose", action="store_true", help="Verbose progress logging.")

    index_p = sub.add_parser("index", parents=[common], help="Build a semantic search index over the wiki.")
    index_p.add_argument("source", type=Path, help="A source (PDF/Markdown/text), or a .json produced by `ingest`.")
    index_p.add_argument(
        "-o", "--out", type=Path, default=None,
        help="Index output directory (default: project's index, else ./output/index).",
    )
    index_p.add_argument(
        "--split-level", type=int, default=None,
        help="Outline depth used to build the wiki before chunking (default: manifest, else 2).",
    )
    index_p.add_argument("--model", default=None, help="Ollama embedding model (default: manifest models.embed, else bge-m3).")
    index_p.add_argument("--host", default=None, help="Ollama host URL.")
    index_p.add_argument("--chunk-size", type=int, default=None, help="Chunk size in words (default: 180).")
    index_p.add_argument("--overlap", type=int, default=None, help="Chunk overlap in words (default: 30).")
    index_p.add_argument("-v", "--verbose", action="store_true", help="Verbose progress logging.")

    search_p = sub.add_parser("search", parents=[common], help="Query the semantic index built by `index`.")
    search_p.add_argument("query", help="Search query text.")
    search_p.add_argument(
        "-i", "--index", type=Path, default=None,
        help="Index directory (default: project's index, else ./output/index).",
    )
    search_p.add_argument("-k", "--top-k", type=int, default=5, help="Number of results (default: 5).")
    search_p.add_argument("--host", default=None, help="Ollama host URL.")
    search_p.add_argument("--full", action="store_true", help="Print full chunk text instead of a snippet.")

    eval_p = sub.add_parser("eval", parents=[common],
                            help="Evaluate retrieval quality (RAG vs GraphRAG) over a ground-truth question set.")
    eval_p.add_argument("--eval-set", type=Path, default=None,
                        help="JSONL of {\"question\", \"pages\"} lines; a bare name resolves against "
                             "the project root (default: <project>/eval.jsonl).")
    eval_p.add_argument("-i", "--index", type=Path, default=None, help="Index dir (default: project's).")
    eval_p.add_argument("--graph", type=Path, default=None,
                        help="Graph dir; enables the GraphRAG column (default: project's graph).")
    eval_p.add_argument("--top-k", type=int, default=5, help="Semantic seed pages (default: 5).")
    eval_p.add_argument("--expand-k", type=int, default=3,
                        help="Graph-expanded pages added for GraphRAG (default: 3).")
    eval_p.add_argument("--no-graph", action="store_true", help="Skip the GraphRAG column.")
    eval_p.add_argument("--misses", action="store_true", help="List questions with no expected page in the top-k.")
    eval_p.add_argument("--hybrid", action="store_true",
                        help="Add a Hybrid row: fuse BM25 (lexical) with dense retrieval (RRF). No chat model needed.")
    eval_p.add_argument("--rerank", action="store_true",
                        help="Add a RAG+Rerank row: LLM-re-rank a wider pool down to the budget "
                             "(needs a chat model; one call per question — slow).")
    eval_p.add_argument("--rerank-pool", type=int, default=20,
                        help="Candidate pool size the re-ranker orders (default: 20).")
    eval_p.add_argument("--answers", action="store_true",
                        help="Also generate RAG & GraphRAG answers and score citation grounding (slow).")
    eval_p.add_argument("--judge", action="store_true",
                        help="With --answers/--global, an LLM judge picks the better answer per question (slower).")
    eval_p.add_argument("--global", dest="global_search", action="store_true",
                        help="Evaluate global search on a thematic set: community grounding "
                             "(+ --judge = Global vs RAG). Needs a graph with communities.")
    eval_p.add_argument("--cross-session", dest="cross_session", action="store_true",
                        help="Evaluate the Path B memory tier: cross-session task success "
                             "(cold vs raw-log vs assembled; + --judge = assembled vs raw-log). "
                             "Default set: <project>/eval_cross_session.jsonl.")
    eval_p.add_argument("--recall-k", type=int, default=10,
                        help="Facts recalled for the 'assembled' condition (--cross-session; default 10).")
    eval_p.add_argument("--limit", type=int, default=None, help="Only evaluate the first N questions.")
    eval_p.add_argument("--model", default=None, help="Chat model for --answers (default: project's models.chat).")
    eval_p.add_argument("--host", default=None, help="Ollama host URL.")

    ask_p = sub.add_parser("ask", parents=[common], help="Answer a question over the wiki with RAG (retrieval + chat model).")
    ask_p.add_argument("question", help="The question to answer.")
    ask_p.add_argument(
        "-i", "--index", type=Path, default=None,
        help="Index directory (default: project's index, else ./output/index).",
    )
    ask_p.add_argument("-k", "--top-k", type=int, default=5, help="Chunks to retrieve (default: 5).")
    ask_p.add_argument(
        "--graph", type=Path, default=None,
        help="Knowledge-graph dir; if present, retrieval is graph-augmented (default: project's graph).",
    )
    ask_p.add_argument("--expand-k", type=int, default=3,
                       help="Related pages to add via graph expansion (default: 3; 0 disables).")
    ask_p.add_argument("--no-graph", action="store_true", help="Disable graph-augmented retrieval.")
    ask_p.add_argument("--hybrid", action="store_true",
                       help="Hybrid seed retrieval: fuse BM25 (lexical) with dense cosine (RRF) — "
                            "helps exact terms/identifiers the embedder blurs.")
    ask_p.add_argument("--rerank", action="store_true",
                       help="LLM-re-rank a wider seed pool down to top-k before answering "
                            "(one extra chat call; higher-precision seeds).")
    ask_p.add_argument("--rerank-pool", type=int, default=20,
                       help="Candidate pool the re-ranker orders (default: 20).")
    ask_p.add_argument(
        "--model", default=None,
        help="Ollama chat model (default: manifest models.chat, else qwen3:30b-a3b-instruct-2507-q4_K_M).",
    )
    ask_p.add_argument("--host", default=None, help="Ollama host URL.")
    ask_p.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature (default: 0.2).")
    ask_p.add_argument("--show-context", action="store_true", help="Also print the retrieved excerpts.")
    ask_p.add_argument("--global", dest="global_search", action="store_true",
                       help="Answer a thematic/overview question from community summaries "
                            "(run `openwiki communities` first).")

    chat_p = sub.add_parser("chat", parents=[common], help="Multi-turn agent that can search, read, and edit wiki pages.")
    chat_p.add_argument(
        "-m", "--message", action="append", metavar="TEXT",
        help="A turn to send non-interactively (repeatable). Omit for an interactive REPL.",
    )
    chat_p.add_argument(
        "--wiki", type=Path, default=None,
        help="Wiki directory to read/edit (default: project's wiki, else ./output/wiki).",
    )
    chat_p.add_argument(
        "-i", "--index", type=Path, default=None,
        help="Search index directory (default: project's index, else ./output/index).",
    )
    chat_p.add_argument("--model", default=None, help="Ollama chat model.")
    chat_p.add_argument("--host", default=None, help="Ollama host URL.")
    chat_p.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature (default: 0.2).")
    chat_p.add_argument(
        "--graph", type=Path, default=None,
        help="Knowledge-graph directory; enables graph_neighbors/find_path tools (default: project's graph).",
    )
    chat_p.add_argument("--dry-run", action="store_true", help="Preview edits without writing files.")
    chat_p.add_argument("--show-tools", action="store_true", help="Print each tool call the agent makes.")
    chat_p.add_argument("--sync", action="store_true",
                        help="Hold the graph writable for live edit-sync (exclusive lock — blocks "
                             "other processes). Default: read-only, edits re-sync via the journal.")

    serve_p = sub.add_parser("serve", parents=[common], help="Serve a web UI over the wiki and the agent.")
    serve_p.add_argument(
        "--wiki", type=Path, default=None,
        help="Wiki directory to serve (default: project's wiki, else ./output/wiki).",
    )
    serve_p.add_argument(
        "-i", "--index", type=Path, default=None,
        help="Search index directory (default: project's index, else ./output/index).",
    )
    serve_p.add_argument(
        "--graph", type=Path, default=None,
        help="Knowledge-graph directory to serve, if present (default: project's graph).",
    )
    serve_p.add_argument("--bind", default=None, help="Address to bind (default: manifest serve.bind, else 127.0.0.1).")
    serve_p.add_argument("--port", type=int, default=None, help="Port to listen on (default: manifest serve.port, else 8000).")
    serve_p.add_argument("--model", default=None, help="Ollama chat model for the agent.")
    serve_p.add_argument("--host", default=None, help="Ollama host URL.")
    serve_p.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature (default: 0.2).")
    serve_p.add_argument("--dry-run", action="store_true", help="Agent previews edits without writing files.")
    serve_p.add_argument("--sync", action="store_true",
                         help="Hold the graph writable for live edit-sync (exclusive lock — blocks "
                              "ask/MCP/recall and a second serve). Default: read-only so readers run "
                              "concurrently; agent edits re-sync via the journal at start/shutdown.")

    mcp_p = sub.add_parser("mcp", parents=[common], help="Expose the wiki (RAG+GraphRAG) to coding agents over MCP (stdio).")
    mcp_p.add_argument("--wiki", type=Path, default=None,
                       help="Wiki directory (default: project's wiki, else ./output/wiki).")
    mcp_p.add_argument("-i", "--index", type=Path, default=None,
                       help="Search index directory (default: project's index, else ./output/index).")
    mcp_p.add_argument("--graph", type=Path, default=None,
                       help="Knowledge-graph directory, if present (default: project's graph).")
    mcp_p.add_argument("--model", default=None,
                       help="Ollama chat model for `wiki_ask`.")
    mcp_p.add_argument("--host", default=None, help="Ollama host URL.")
    mcp_p.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature (default: 0.2).")
    mcp_p.add_argument("--no-ask", action="store_true", help="Disable the `wiki_ask` tool (no chat model).")

    refs_p = sub.add_parser("references", parents=[common],
                            help="Re-extract the cross-references (+ the citation phrases the web UI "
                                 "links inline) into an existing graph, in place — no rebuild.")
    refs_p.add_argument("source", nargs="?", type=Path, default=None,
                        help="A parsed .json (or a source); default: the project's parsed corpus.")
    refs_p.add_argument("--graph", type=Path, default=None, help="Graph dir (default: project's graph).")
    refs_p.add_argument("--split-level", type=int, default=None,
                        help="Outline depth that became pages — must match the graph (default: manifest / 2).")

    graph_p = sub.add_parser("graph-build", parents=[common], help="Build the Kuzu knowledge graph over the wiki.")
    graph_p.add_argument("source", type=Path, help="A source (PDF/Markdown/text), or a .json produced by `ingest`.")
    graph_p.add_argument(
        "-o", "--out", type=Path, default=None,
        help="Graph database directory (default: project's graph, else ./output/graph).",
    )
    graph_p.add_argument(
        "-i", "--index", type=Path, default=None,
        help="Semantic index directory to mirror (default: project's index, else ./output/index).",
    )
    graph_p.add_argument(
        "--split-level", type=int, default=None,
        help="Outline depth for the wiki (must match the indexed wiki; default: manifest, else 2).",
    )
    graph_p.add_argument("--similar-k", type=int, default=None, help="SIMILAR_TO edges per page (default: 6).")
    graph_p.add_argument("--no-references", action="store_true",
                         help="Skip 'siehe Seite N' cross-reference (REFERENCES) edges.")
    graph_p.add_argument("--entities", action="store_true",
                         help="Extract typed entities via an LLM (one call/page; slow). Adds Entity + MENTIONS.")
    graph_p.add_argument("--relations", action="store_true",
                         help="Also extract typed Entity->Entity relations (implies --entities; a second "
                              "LLM call per entity-rich page). Adds RELATED_TO edges.")
    graph_p.add_argument("--resolve-entities", action="store_true",
                         help="Corpus-wide entity resolution (implies --entities): merge same-concept "
                              "surface variants into canonical entities with aliases + descriptions "
                              "(embedding candidates + one LLM call per cluster).")
    graph_p.add_argument("--entity-model", default=None,
                         help="Ollama model for entity extraction.")
    graph_p.add_argument("--entity-types", default=None, metavar="LIST",
                         help="Comma-separated entity types for --entities (e.g. "
                              "'Concept,Method,Component'); overrides the default ontology.")
    graph_p.add_argument("--entity-max-chars", type=int, default=None,
                         help="Chars of each page sent to the entity model (default: 8000).")
    graph_p.add_argument("--host", default=None, help="Ollama host URL (for --entities).")
    graph_p.add_argument("-v", "--verbose", action="store_true", help="Verbose progress logging.")

    comm_p = sub.add_parser("communities", parents=[common],
                            help="Consolidate the graph into topical communities + LLM summaries (enables `ask --global`).")
    comm_p.add_argument("--graph", type=Path, default=None,
                        help="Graph database dir (default: project's graph, else ./output/graph).")
    comm_p.add_argument("--max-pages", type=int, default=12,
                        help="Max member pages summarized per community (default: 12).")
    comm_p.add_argument("--model", default=None,
                        help="Chat model for the summaries (default: manifest models.chat).")
    comm_p.add_argument("--host", default=None, help="Ollama host URL.")

    decay_p = sub.add_parser("decay", parents=[common],
                             help="Age the graph's reinforced (usage-memory) edges and prune stale ones.")
    decay_p.add_argument("--graph", type=Path, default=None,
                         help="Graph database dir (default: project's graph, else ./output/graph).")
    decay_p.add_argument("--half-life", type=float, default=30.0,
                         help="Days after which an unused edge's weight halves (default: 30).")
    decay_p.add_argument("--floor", type=float, default=0.1,
                         help="Prune edges whose effective weight falls below this (default: 0.1).")

    cons_p = sub.add_parser("consolidate", parents=[common],
                            help="Path B 'sleep' pass: cluster remembered facts into themes + summaries, then fold usage + decay.")
    cons_p.add_argument("--graph", type=Path, default=None,
                        help="Graph database dir (default: project's graph, else ./output/graph).")
    cons_p.add_argument("--min-size", type=int, default=2,
                        help="Smallest fact cluster that becomes a theme (default: 2).")
    cons_p.add_argument("--max-facts", type=int, default=12,
                        help="Max member facts shown to the summarizer per theme (default: 12).")
    cons_p.add_argument("--similar-k", type=int, default=6,
                        help="Top-k similarity edges per fact for clustering (default: 6).")
    cons_p.add_argument("--half-life", type=float, default=30.0,
                        help="Half-life (days) for the decay step (default: 30).")
    cons_p.add_argument("--floor", type=float, default=0.1,
                        help="Prune usage edges below this effective weight in the decay step (default: 0.1).")
    cons_p.add_argument("--no-decay", action="store_true",
                        help="Only re-cluster/summarize; skip the fold-usage + decay 'forget' step.")
    cons_p.add_argument("--resummarize", action="store_true",
                        help="Re-summarize every theme from scratch (ignore the incremental cache "
                             "+ warm-start; a full rebuild of the consolidation layer).")
    cons_p.add_argument("--model", default=None,
                        help="Chat model for the theme summaries (default: manifest models.chat).")
    cons_p.add_argument("--host", default=None, help="Ollama host URL.")

    rem_p = sub.add_parser("remember", parents=[common],
                           help="Capture a session transcript into the graph's remembered tier (Path B).")
    rem_p.add_argument("transcript", type=Path, help="A text/markdown file with the session transcript.")
    rem_p.add_argument("--session", default=None, help="Session id (default: the transcript file stem).")
    rem_p.add_argument("--session-date", type=_date_arg, default=None, metavar="DATE",
                       help="When the session took place (YYYY-MM-DD) — facts are valid from it unless "
                            "the transcript states a date (B7). Default: a date in the session id, "
                            "else now. Lets an out-of-order backfill land in history.")
    rem_p.add_argument("--correct", action="store_true",
                       help="The transcript CORRECTS earlier facts ('it was never X, it is Y'): the "
                            "conflicting old fact is retracted (never true) instead of ended (B7).")
    rem_p.add_argument("-i", "--index", type=Path, default=None, help="Index dir (for the embedder; default: project's).")
    rem_p.add_argument("--graph", type=Path, default=None, help="Graph dir (default: project's graph).")
    rem_p.add_argument("--model", default=None, help="Chat model for fact extraction (default: manifest models.chat).")
    rem_p.add_argument("--host", default=None, help="Ollama host URL.")

    rec_p = sub.add_parser("recall", parents=[common],
                           help="Show the remembered facts most relevant to a query (Path B activation tier).")
    rec_p.add_argument("query", help="What to recall.")
    rec_p.add_argument("-k", "--top-k", type=int, default=5, help="Facts to return (default: 5).")
    rec_p.add_argument("--all", dest="include_superseded", action="store_true",
                       help="Also show superseded facts (B4 history), each marked — default is current only.")
    rec_p.add_argument("--as-of", type=_date_arg, default=None, metavar="DATE",
                       help="B7 point-in-time: the facts that were TRUE at DATE (valid time).")
    rec_p.add_argument("--known-at", type=_date_arg, default=None, metavar="DATE",
                       help="B7: what OpenWiki BELIEVED at DATE (transaction time; valid time "
                            "defaults to DATE too) — before later corrections/backfills.")
    rec_p.add_argument("--timeline", action="store_true",
                       help="B7: show the full history (every interval, when recorded, by which "
                            "session) of the subject+predicate pairs that best match the query.")
    rec_p.add_argument("-i", "--index", type=Path, default=None, help="Index dir (for the embedder; default: project's).")
    rec_p.add_argument("--graph", type=Path, default=None, help="Graph dir (default: project's graph).")
    rec_p.add_argument("--host", default=None, help="Ollama host URL.")

    ctx_p = sub.add_parser("context", parents=[common],
                           help="Assemble a session's memory context for a query (Path B / B6): "
                                "identity + recalled facts + relevant themes.")
    ctx_p.add_argument("query", help="The query/topic to assemble memory context for.")
    ctx_p.add_argument("-k", "--top-k", type=int, default=8, help="Recalled facts (activation tier; default: 8).")
    ctx_p.add_argument("--themes", type=int, default=4, help="Relevant themes (attractor tier; default: 4).")
    ctx_p.add_argument("--max-chars", type=int, default=None,
                       help="Fit the context within ~this many chars (~4/token); default: the "
                            "project's [memory] context_budget (2000). Use 0 for unbounded.")
    ctx_p.add_argument("--identity", default=None, help="Override the identity tier (default: the project's).")
    ctx_p.add_argument("--as-of", type=_date_arg, default=None, metavar="DATE",
                       help="B7: assemble the memory as it was true at DATE (default: now).")
    ctx_p.add_argument("-i", "--index", type=Path, default=None, help="Index dir (for the embedder; default: project's).")
    ctx_p.add_argument("--graph", type=Path, default=None, help="Graph dir (default: project's graph).")
    ctx_p.add_argument("--host", default=None, help="Ollama host URL.")

    an_p = sub.add_parser("analyze", parents=[common],
                          help="World-model analysis: measure graph↔semantic coupling, or mine "
                               "actionable gaps (missing refs, near-duplicates, merge candidates).")
    an_p.add_argument("what", nargs="?", choices=["coupling", "gaps", "memory"], default="coupling",
                      help="coupling (default) = where the graph agrees with vs. adds to the "
                           "embedding space; gaps = ranked, actionable improvement candidates; "
                           "memory = Path B memory-tier dynamics (revision/consolidation/temperature).")
    an_p.add_argument("-k", type=int, default=8, dest="k",
                      help="Embedding neighbors per page for the coupling overlap metric (default: 8).")
    an_p.add_argument("--top", type=int, default=15, help="Max candidates per gaps category (default: 15).")
    an_p.add_argument("--compare", metavar="PATH", default=None,
                      help="Compare the coupling fingerprint against another KB: a saved "
                           "`analyze --json` file, a project dir, or an output dir (index/ + graph/).")
    an_p.add_argument("-i", "--index", type=Path, default=None,
                      help="Index dir (the embedding space; default: project's).")
    an_p.add_argument("--graph", type=Path, default=None, help="Graph dir (default: project's graph).")
    an_p.add_argument("--json", action="store_true", dest="as_json",
                      help="Emit the raw coupling fingerprint as JSON (for compare/export) "
                           "instead of the report.")

    hook_p = sub.add_parser("hook",
                            help="Host-lifecycle memory hook (reads the event JSON on stdin) — wired "
                                 "into Claude Code by `claude-code --hooks`, not run by hand.")
    hook_p.add_argument("event", choices=["inject", "capture"],
                        help="inject = UserPromptSubmit (recall → inject context); "
                             "capture = SessionEnd/PreCompact (remember the session).")
    hook_p.add_argument("--project", default=None, metavar="DIR",
                        help="Bind the hook to this OpenWiki project (else: discovered from the "
                             "session's working directory — never the registry's active project).")
    hook_p.add_argument("--payload", type=Path, default=None, metavar="FILE",
                        help=argparse.SUPPRESS)   # internal: the detached capture worker's event file
    bf_redo_help = "Re-capture days whose session is already in memory (default: skip them — resume)."

    bf_p = sub.add_parser("backfill", parents=[common],
                          help="Backfill the memory tier from Claude Code transcripts (JSONL): one "
                               "dated session per day, so B7 orders the facts by when they happened.")
    bf_p.add_argument("transcripts", nargs="+", type=Path,
                      help="Claude Code transcript .jsonl file(s), or folder(s) of them "
                           "(e.g. ~/.claude/projects/<repo>).")
    bf_p.add_argument("--since", type=_date_arg, default=None, metavar="DATE", help="First day to include.")
    bf_p.add_argument("--until", type=_date_arg, default=None, metavar="DATE", help="Last day to include.")
    bf_p.add_argument("--max-chars", type=int, default=20000,
                      help="Window size per capture call (default 20000 chars).")
    bf_p.add_argument("--prefix", default="claude-",
                      help="Session-id prefix; the day is appended (default: claude-YYYY-MM-DD).")
    bf_p.add_argument("--dry-run", action="store_true",
                      help="Only list the per-day windows — no LLM calls, no writes.")
    bf_p.add_argument("--redo", action="store_true", help=bf_redo_help)
    bf_p.add_argument("-i", "--index", type=Path, default=None, help="Index dir (for the embedder; default: project's).")
    bf_p.add_argument("--graph", type=Path, default=None, help="Graph dir (default: project's graph).")
    bf_p.add_argument("--model", default=None, help="Chat model for capture (default: manifest models.chat).")
    bf_p.add_argument("--host", default=None, help="Ollama host URL.")
    return parser


def _attribute_resolver(model, host):
    """B9: the ``resolve(fact, candidates)`` callable for ``GraphStore.remember`` — one
    deterministic chat call per fact whose exact attribute key is new *and* has near
    candidates, mapping paraphrases ("is versioned" / "has version") onto one attribute."""
    judge = OllamaChat(model=model, host=host, temperature=0.0)
    return lambda fact, candidates: choose_attribute(judge, fact, candidates)


def _coexist_check(model, host):
    """B7: the ``coexist(older, newer)`` callable for ``GraphStore.remember`` — one deterministic
    chat call per actual conflict ("can both be true at once?"), overriding the noisy capture
    cardinality tag."""
    judge = OllamaChat(model=model, host=host, temperature=0.0)
    return lambda older, newer, subjects=None: facts_coexist(judge, older, newer, subjects)


def _date_arg(value: str) -> int:
    """argparse type: an ISO date (YYYY-MM-DD, YYYY-MM, YYYY) → epoch seconds (UTC)."""
    epoch = parse_date(value)
    if epoch is None:
        raise argparse.ArgumentTypeError(f"not a date: {value!r} (use YYYY-MM-DD)")
    return epoch


# ----------------------------------------------------------------- projects

def _expand_sources(raw) -> "list[Path]":
    """Expand each ``--source`` argument into concrete input files.

    A **directory** contributes its top-level supported files (pdf / md / txt); a
    **glob** (containing ``*``/``?``/``[``) contributes its matches; a plain **file**
    is taken as-is. Duplicates (by resolved path) are dropped; ``FileNotFoundError``
    is raised if an argument matches nothing.
    """
    files: "list[Path]" = []
    seen: set = set()

    def _add(path: Path) -> None:
        resolved = path.resolve()
        if path.is_file() and resolved not in seen:
            seen.add(resolved)
            files.append(path)

    for item in raw:
        text = str(item)
        if any(ch in text for ch in "*?["):
            matches = sorted(Path(m) for m in glob.glob(text))
            if not matches:
                raise FileNotFoundError(f"no files match: {text}")
            for match in matches:
                _add(match)
        elif Path(item).is_dir():
            found = sorted(p for p in Path(item).glob("*") if p.is_file() and is_supported(p))
            if not found:
                raise FileNotFoundError(f"no supported source files (pdf/md/txt) in directory: {item}")
            for path in found:
                _add(path)
        elif Path(item).is_file():
            _add(Path(item))
        else:
            raise FileNotFoundError(f"source not found: {item}")
    return files


def _resolve_source_specs(raw_sources, sources_dir: Path, repo: bool = False,
                          session: bool = False) -> "list[dict]":
    """Turn raw ``--source`` args into ``[{type, path}]`` manifest specs. **URLs**
    (and, with ``repo=True``, **directories**) are referenced in place; other local
    files/globs/scan-dirs are expanded and copied into ``sources_dir``. With
    ``session=True`` every argument is a transcript file copied in as a ``session``
    source (Path B memory tier), not a document."""
    if session:
        specs: "list[dict]" = []
        for src in _expand_sources([str(x) for x in raw_sources]):
            dest = sources_dir / src.name
            if src.resolve() != dest.resolve():
                sources_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            specs.append({"type": "session", "path": f"sources/{src.name}"})
        return specs
    project_root = sources_dir.parent
    specs: "list[dict]" = []
    to_copy: list = []
    for item in raw_sources:
        text = str(item)
        if is_url(text):
            specs.append({"type": "web", "path": text})
        elif repo and Path(text).is_dir():
            resolved = Path(text).resolve()
            try:
                path = resolved.relative_to(project_root).as_posix()
            except ValueError:
                path = str(resolved)   # repo outside the project → absolute reference
            specs.append({"type": "code", "path": path})
        else:
            to_copy.append(item)
    for src in _expand_sources(to_copy):
        dest = sources_dir / src.name
        if src.resolve() != dest.resolve():
            sources_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        specs.append({"type": source_type(src), "path": f"sources/{src.name}"})
    return specs


def _cmd_init(args: argparse.Namespace) -> int:
    root: Path = args.dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "openwiki.toml"
    if manifest.exists() and not args.force:
        print(f"error: {manifest} already exists (use --force to overwrite).", file=sys.stderr)
        return 2

    sources_dir = root / "sources"
    sources_dir.mkdir(exist_ok=True)
    try:
        specs = _resolve_source_specs(args.source or [], sources_dir,
                                      repo=getattr(args, "repo", False),
                                      session=getattr(args, "session", False))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    sources: list[dict] = []
    seen: set = set()
    for spec in specs:
        if spec["path"] in seen:
            continue
        seen.add(spec["path"])
        sources.append(spec)

    name = args.name or root.name
    # A session source opts the project into Second Brain mode (Path B).
    has_session = any(s["type"] == "session" for s in sources)
    manifest.write_text(render_manifest(name=name, sources=sources, memory=has_session),
                        encoding="utf-8")

    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("output/\n.openwiki/\n", encoding="utf-8")

    print(f"Initialized OpenWiki project '{name}' at {root}")
    print(f"  manifest -> {manifest}")
    if sources:
        print(f"  sources  -> {len(sources)} declared "
              f"({', '.join(s['type'] for s in sources)})")
    else:
        print(f"  add inputs under {sources_dir}/ and list them under [[sources]] in openwiki.toml")
    if getattr(args, "opencode", False):
        _scaffold_opencode_for(Project.load(root), force=args.force)
    print("  next: run `openwiki build` (or the individual ingest/build-wiki/index/graph-build stages)")
    if not getattr(args, "opencode", False):
        print("  tip: `openwiki opencode` adds a local OpenCode agent wired to this project")
    return 0


def _mcp_command() -> list:
    """The command a coding agent should spawn for the `openwiki` MCP server. Prefer
    the global `owiki` (portable — it discovers the project from its own folder);
    otherwise fall back to the exact interpreter running now (it has openwiki)."""
    if shutil.which("owiki"):
        return ["owiki", "mcp"]
    return [Path(sys.executable).as_posix(), "-m", "openwiki", "mcp"]


def _hook_command(event: str, project_root=None, portable: bool = True) -> str:
    """The shell command string a Claude Code hook runs for OpenWiki memory
    (``inject``/``capture``), optionally **bound** to a project (``--project``) — for hooks
    installed outside the project folder. ``portable`` prefers `owiki` on PATH; otherwise (and
    always with ``portable=False``) the current interpreter — pinning the hook to the exact
    OpenWiki that installed it (a stale `owiki` would fail on unknown args, and an argparse exit
    code 2 would *block* the user's prompt). Quoted — paths may contain spaces on Windows."""
    bind = f' --project "{Path(project_root).as_posix()}"' if project_root else ""
    if portable and shutil.which("owiki"):
        return f"owiki hook {event}{bind}"
    return f'"{Path(sys.executable).as_posix()}" -m openwiki hook {event}{bind}'


def _scaffold_opencode_for(project: Project, force: bool,
                           model: Optional[str] = None, host: Optional[str] = None) -> None:
    userconfig = UserConfig.load()
    chat = (model or project.setting("models", "chat", None)
            or userconfig.setting("models", "chat", None) or DEFAULT_CHAT)
    embed = (project.setting("models", "embed", None)
             or userconfig.setting("models", "embed", None) or DEFAULT_EMBED)
    host = (host or project.setting("models", "host", None)
            or userconfig.setting("models", "host", None) or DEFAULT_HOST)
    command = _mcp_command()
    written, skipped = scaffold_opencode(
        project.root, chat_model=chat, embed_model=embed, host=host,
        mcp_command=command, force=force,
    )
    for path in written:
        print(f"  wrote    {path.relative_to(project.root)}")
    for path in skipped:
        print(f"  skipped  {path.relative_to(project.root)}  (exists; --force to overwrite)")
    print(f"  agent 'openwiki' -> {chat} via `{' '.join(command)}` (MCP)")
    if not shutil.which("owiki"):
        print("  note: `owiki` is not on PATH; the MCP uses this Python. Install it globally "
              "(install-openwiki.ps1 / .sh) for a portable `owiki mcp`.", file=sys.stderr)


def _cmd_opencode(args: argparse.Namespace) -> int:
    project = getattr(args, "project_obj", None)
    if project is None:
        print("error: not inside an OpenWiki project (no openwiki.toml found). "
              "Run `openwiki init` first, or pass --project DIR.", file=sys.stderr)
        return 2
    print(f"Scaffolding OpenCode config into project '{project.name}' ({project.root})")
    _scaffold_opencode_for(project, force=args.force, model=args.model, host=args.host)
    print(f"\nDone. `cd \"{project.root}\"` and run `opencode` — the 'openwiki' agent "
          "will query this project on your local model.")
    return 0


def _cmd_claude_code(args: argparse.Namespace) -> int:
    project = getattr(args, "project_obj", None)
    if project is None:
        print("error: not inside an OpenWiki project (no openwiki.toml found). "
              "Run `openwiki init` first, or pass --project DIR.", file=sys.stderr)
        return 2
    userconfig = UserConfig.load()
    chat = (project.setting("models", "chat", None)
            or userconfig.setting("models", "chat", None) or DEFAULT_CHAT)
    embed = (project.setting("models", "embed", None)
             or userconfig.setting("models", "embed", None) or DEFAULT_EMBED)
    command = _mcp_command()
    hooks = getattr(args, "hooks", False)
    into = getattr(args, "into", None)
    if into is not None:
        if not hooks:
            print("error: --into installs the memory hooks — pass it together with --hooks.",
                  file=sys.stderr)
            return 2
        from .claude_code_template import install_hooks
        target = install_hooks(Path(into) / ".claude" / "settings.local.json",
                               _hook_command("inject", project.root, portable=False),
                               _hook_command("capture", project.root, portable=False))
        mode = "on" if project.memory_enabled else "OFF — enable [memory] to use them"
        print(f"Installed OpenWiki memory hooks → {target}\n"
              f"  bound to project '{project.name}' ({project.root}); Second Brain mode is {mode}\n"
              f"  UserPromptSubmit→inject, SessionEnd/PreCompact→capture — restart Claude Code in "
              f"{Path(into).resolve()} (or review them with /hooks) to activate.")
        return 0
    inject_cmd = _hook_command("inject") if hooks else ""
    capture_cmd = _hook_command("capture") if hooks else ""
    print(f"Scaffolding Claude Code config into project '{project.name}' ({project.root})")
    written, skipped = scaffold_claude_code(
        project.root, chat_model=chat, embed_model=embed, mcp_command=command, force=args.force,
        inject_command=inject_cmd, capture_command=capture_cmd)
    for path in written:
        print(f"  wrote    {path.relative_to(project.root)}")
    for path in skipped:
        print(f"  skipped  {path.relative_to(project.root)}  (exists; --force to overwrite)")
    print(f"  MCP server 'openwiki' via `{' '.join(command)}`")
    if hooks:
        mode = "on" if project.memory_enabled else "OFF — enable [memory] to use them"
        print(f"  memory hooks: UserPromptSubmit→inject, SessionEnd/PreCompact→capture "
              f"(`{inject_cmd}`); Second Brain mode is {mode}")
    if not shutil.which("owiki"):
        print("  note: `owiki` is not on PATH; the MCP uses this Python. Install it globally "
              "(install-openwiki.ps1 / .sh) for a portable `owiki mcp`.", file=sys.stderr)
    print(f"\nDone. `cd \"{project.root}\"` and run `claude` — the `openwiki_*` MCP tools "
          "and `/wiki-ask` / `/wiki-explore` commands query this project.")
    return 0


def _toml_quote(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _sample_texts(project: Project) -> list:
    """Text to sample for the ontology proposal: index chunks if present, else wiki pages."""
    index_json = project.index_dir / "index.json"
    if index_json.is_file():
        data = json.loads(index_json.read_text(encoding="utf-8"))
        return [c.get("text", "") for c in data.get("chunks", [])]
    return [p.read_text(encoding="utf-8") for p in sorted((project.wiki_dir / "pages").glob("*.md"))]


def _write_entity_types(project: Project, types: list) -> None:
    """Insert or replace ``[graph] entity_types`` in the manifest, preserving the rest."""
    path = project.root / MANIFEST
    text = path.read_text(encoding="utf-8")
    block = "entity_types = [\n" + "".join(f"  {_toml_quote(t)},\n" for t in types) + "]\n"
    if re.search(r"(?m)^entity_types\s*=\s*\[", text):
        text = re.sub(r"(?ms)^entity_types\s*=\s*\[.*?^\]\n", block, text, count=1)
    elif re.search(r"(?m)^entities\s*=", text):
        text = re.sub(r"(?m)^(entities\s*=.*\n)", r"\1" + block, text, count=1)
    else:
        text = text.rstrip() + "\n\n[graph]\nentities = true\n" + block
    path.write_text(text, encoding="utf-8")


def _cmd_ontology(args: argparse.Namespace) -> int:
    project: Optional[Project] = getattr(args, "project_obj", None)
    if project is None:
        print("error: not in an OpenWiki project — run `openwiki init` first.", file=sys.stderr)
        return 2
    texts = _sample_texts(project)
    if not texts:
        print("error: nothing to sample — build the wiki/index first (`openwiki build`).", file=sys.stderr)
        return 2

    chat = OllamaChat(model=args.model, host=args.host, temperature=0.2)
    print(f"Proposing an ontology from {len(texts)} text sample(s) with {chat.name} …", file=sys.stderr)
    sample = sample_corpus(texts)
    items: list = []
    for attempt in range(3):   # the model is occasionally non-JSON; retry a couple of times
        items = propose_ontology(chat, sample, n_types=args.types)
        if items:
            break
        if attempt < 2:
            print(f"  (attempt {attempt + 1} returned nothing — retrying…)", file=sys.stderr)
    if not items:
        print("error: the model returned no usable ontology — try again or a different --model.", file=sys.stderr)
        return 2
    types = format_entity_types(items)

    print("# Proposed [graph] entity_types:")
    print("entity_types = [")
    for entry in types:
        print(f"  {_toml_quote(entry)},")
    print("]")

    if args.write:
        _write_entity_types(project, types)
        print(f"\n✓ written to {project.root / MANIFEST}", file=sys.stderr)
        print("  Next: ensure `entities = true`, then `openwiki build --only graph`.", file=sys.stderr)
    else:
        print("\n(Review, add under [graph] with `entities = true`, then `openwiki build --only graph`. "
              "Use --write to insert it automatically.)", file=sys.stderr)
    return 0


def _resolve_project(explicit) -> Optional[Project]:
    """Location-first (``--project`` > ``$OPENWIKI_PROJECT`` > discovery), then fall
    back to the registry's active project when not inside one."""
    project = Project.resolve(explicit)
    if project is None and explicit is None and not os.environ.get("OPENWIKI_PROJECT"):
        active = Registry.load().active_path()
        if active is not None and (active / MANIFEST).is_file():
            project = Project.load(active)
    return project


def _cmd_project(args: argparse.Namespace) -> int:
    action = args.project_cmd
    reg = Registry.load()

    if action == "list":
        projects = reg.projects()
        if not projects:
            print("(no registered projects — `openwiki project add`)")
            return 0
        active = reg.active()
        for name in sorted(projects):
            root = Path(projects[name])
            mark = "*" if name == active else " "
            note = "" if (root / MANIFEST).is_file() else "   (missing)"
            print(f" {mark} {name:<20} {root}{note}")
        return 0

    if action == "use":
        if not reg.use(args.name):
            print(f"error: no registered project '{args.name}' (see `openwiki project list`).", file=sys.stderr)
            return 2
        print(f"Active project → {args.name}")
        return 0

    if action == "add":
        if args.path is not None:
            root = Path(args.path).resolve()
        else:
            found = Project.find(Path.cwd())
            if found is None:
                print("error: not in a project and no PATH given.", file=sys.stderr)
                return 2
            root = found.root
        if not (root / MANIFEST).is_file():
            print(f"error: no {MANIFEST} in {root}.", file=sys.stderr)
            return 2
        name = args.name or Project.load(root).name
        reg.add(name, root)
        print(f"Registered '{name}' → {root}")
        return 0

    if action == "remove":
        if not reg.remove(args.name):
            print(f"error: no registered project '{args.name}'.", file=sys.stderr)
            return 2
        print(f"Unregistered '{args.name}'")
        return 0

    if action == "add-source":
        project: Optional[Project] = getattr(args, "project_obj", None)
        if project is None:
            print("error: not in an OpenWiki project — run `openwiki init` first.", file=sys.stderr)
            return 2
        sources_dir = project.root / "sources"
        sources_dir.mkdir(exist_ok=True)
        try:
            specs = _resolve_source_specs([args.path], sources_dir, repo=getattr(args, "repo", False),
                                          session=getattr(args, "session", False))
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        existing = {s.path for s in project.sources}
        added: list[str] = []
        for spec in specs:
            if spec["path"] in existing:
                print(f"note: already declared, skipping {spec['path']}", file=sys.stderr)
                continue
            existing.add(spec["path"])
            with (project.root / MANIFEST).open("a", encoding="utf-8") as fh:
                fh.write(f'\n[[sources]]\ntype = {_toml_quote(spec["type"])}\npath = {_toml_quote(spec["path"])}\n')
            added.append(spec["path"])
        if not added:
            print("Nothing added (all matches already declared).")
            return 0
        # Adding a session source opts the project into Second Brain mode (Path B).
        if any(s["type"] == "session" for s in specs) and not project.memory_enabled:
            mpath = project.root / MANIFEST
            text = mpath.read_text(encoding="utf-8")
            if "[memory]" in text:
                text = text.replace("enabled = false", "enabled = true", 1)
            else:
                text += "\n[memory]\nenabled = true\n"
            mpath.write_text(text, encoding="utf-8")
            print("note: enabled Second Brain mode ([memory] enabled = true).", file=sys.stderr)
        print(f"Added {len(added)} source(s) to '{project.name}': {', '.join(added)}")
        return 0

    return 1


def _entity_chat(model: str, host: str) -> OllamaChat:
    """A near-deterministic chat model for entity extraction: greedy decoding
    (``temperature=0``) + a fixed ``seed``. Extraction is one free-form call per
    page, so sampling otherwise makes the entity set swing wildly run to run
    (identical pages once yielded 1053 entities, then 510). Greedy decoding removes
    that variance — the entity *count* and the bulk of the set become stable — with
    only minor residual flicker on borderline entities from GPU floating-point
    non-determinism (which no prompt/param can fully eliminate).

    ``num_predict`` caps the output: greedy decoding can fall into a repetition loop
    on some dense pages and generate until the timeout (dropping the page with no
    entities); the cap stops that in seconds. An entity list needs far fewer tokens
    than this bound. ``timeout`` is a generous backstop."""
    return OllamaChat(model=model, host=host, temperature=0.0, timeout=600.0,
                      options={"seed": 0, "num_predict": 4096})


def _entity_retry_chat(model: str, host: str) -> OllamaChat:
    """Fallback model for the retry-on-empty path: a *sampled* chat (temperature
    0.6) so it takes a different decoding path than the greedy primary and escapes
    the repetition loop that made a page yield nothing. A fixed seed keeps the retry
    reproducible; only pages the greedy pass dropped ever reach it, so the bulk of
    extraction stays deterministic."""
    return OllamaChat(model=model, host=host, temperature=0.6, timeout=600.0,
                      options={"seed": 1, "num_predict": 4096})


def _corpus_references(project, sources, doc, wiki, multi, labels: bool = True):
    """Cross-reference edges for the corpus: single-source direct, else per-source
    (each resolved within its own page span via the retained per-source IR). With
    ``labels`` (default) each edge carries its citation phrases — linked inline (ADR-28)."""
    if not multi:
        return extract_references(doc, wiki, labels=labels)
    metas = []
    start = 0
    for src in sources:
        per = project.parsed_dir / f"{source_stem(src)}.json"
        if not per.is_file():
            print(f"note: {per.name} missing — skipping cross-references "
                  f"(re-run `openwiki build --only ingest,graph`).", file=sys.stderr)
            return None
        parsed = _load_parsed(per)
        metas.append({"start": start, "count": len(parsed.pages),
                      "printed_offset": detect_page_offset(parsed)})
        start += len(parsed.pages)
    return extract_references_multi(doc, wiki, metas, labels=labels)


def _sum_llm(events) -> dict:
    """Sum the chat+embed calls + token counts over a metrics-event slice — a build
    stage's LLM spend (from the observability collector). ``{}`` when the stage made
    no model calls (ingest/wiki)."""
    calls = prompt = evalt = 0
    for e in events:
        if e.kind in ("chat", "embed"):
            calls += 1
            prompt += e.prompt_tokens or 0
            evalt += e.eval_tokens or 0
    return {"calls": calls, "prompt_tokens": prompt, "eval_tokens": evalt} if calls else {}


def _stage_start():
    """Start a build-stage meter: (wall-clock t0, metrics-collector sequence)."""
    from . import metrics
    return time.perf_counter(), metrics.COLLECTOR.seq


def _finish_stage(state, stage, fingerprint, output, stats, meter) -> None:
    """Record a build stage with its wall time + LLM token spend (the collector delta
    since the stage began) and persist — pipeline/build observability."""
    from . import metrics
    t0, seq0 = meter
    duration = time.perf_counter() - t0
    llm = _sum_llm(metrics.COLLECTOR.since(seq0))
    state.record(stage, fingerprint, output, stats, duration_s=duration, llm=llm)
    state.save()


def _cmd_build(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO if getattr(args, "verbose", False) else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    project: Optional[Project] = getattr(args, "project_obj", None)
    if project is None:
        print("error: not in an OpenWiki project — run `openwiki init` first.", file=sys.stderr)
        return 2

    sources = project.source_paths()               # document sources (session sources are separate)
    if not sources:
        if project.session_sources():
            print("error: only session sources declared — add a document [[sources]] too "
                  "(the memory tier anchors on the document graph).", file=sys.stderr)
        else:
            print("error: no [[sources]] declared in openwiki.toml.", file=sys.stderr)
        return 2
    missing = [s for s in sources if not source_exists(s)]
    if missing:
        for s in missing:
            print(f"error: source not found: {s}", file=sys.stderr)
        return 2
    multi = len(sources) > 1

    only: Optional[set] = None
    if args.only:
        only = {s.strip() for s in args.only.split(",") if s.strip()}
        unknown = only - set(STAGES)
        if unknown:
            print(f"error: unknown stage(s): {', '.join(sorted(unknown))} "
                  f"(choose from {', '.join(STAGES)}).", file=sys.stderr)
            return 2

    build = project.section("build")
    models = project.section("models")
    gcfg = project.section("graph")
    split = int(build.get("split_level", 2))
    tables = bool(build.get("tables", True))
    host = models.get("host", DEFAULT_HOST)
    parsed_path = project.parsed_dir / ("_corpus.json" if multi else f"{source_stem(sources[0])}.json")

    fps = compute_fingerprints(project, sources)
    exists = {
        "ingest": parsed_path.is_file(),
        "wiki": (project.wiki_dir / "wiki.json").is_file(),
        "index": (project.index_dir / "index.json").is_file(),
        "graph": project.graph_path.exists(),
        "memory": project.graph_path.exists(),   # the remembered tier lives inside the graph
    }
    state = BuildState.load(project)
    todo = stale_stages(state, fps, exists, only=only, force=args.force)

    print(f"build '{project.name}' — {len(sources)} source(s)", file=sys.stderr)
    for stage in STAGES:
        if only is not None and stage not in only:
            continue
        print(f"  [{'run ' if stage in todo else 'skip'}] {stage}", file=sys.stderr)
    if not todo:
        print("Everything up to date.", file=sys.stderr)
        return 0

    if ({"wiki", "index", "graph"} & set(todo)) and "ingest" not in todo and not parsed_path.is_file():
        print(f"error: {parsed_path.name} missing — run `openwiki build` (or include ingest) first.",
              file=sys.stderr)
        return 2

    doc = None

    def _doc():
        nonlocal doc
        if doc is None:
            doc = _load_parsed(parsed_path)
        return doc

    if "ingest" in todo:
        _meter = _stage_start()
        project.parsed_dir.mkdir(parents=True, exist_ok=True)
        parsed_docs = []
        synth = build.get("synthesize_outline", True)
        for src in sources:
            parsed = parse_source(src, extract_tables=tables)
            if synth and not parsed.outline:   # no PDF bookmarks → derive section pages from headings
                parsed.outline = synthesize_outline(parsed)
            if multi:  # keep each source's IR so per-source offsets survive caching
                (project.parsed_dir / f"{source_stem(src)}.json").write_text(
                    json.dumps(parsed.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            parsed_docs.append(parsed)
        doc = combine_documents(parsed_docs, [source_stem(s) for s in sources], title=project.name)
        parsed_path.write_text(
            json.dumps(doc.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _finish_stage(state, "ingest", fps["ingest"], parsed_path,
                      {"pages": len(doc.pages), "sources": len(sources)}, _meter)
        print(f"  ingest → {len(sources)} source(s), {len(doc.pages)} page(s) → {parsed_path}",
              file=sys.stderr)

    if "wiki" in todo:
        _meter = _stage_start()
        wiki = WikiBuilder(split_level=split).build(_doc())
        write_wiki(wiki, project.wiki_dir, include_tables=tables)
        _finish_stage(state, "wiki", fps["wiki"], project.wiki_dir, {"pages": len(wiki.pages)}, _meter)
        print(f"  wiki → {len(wiki.pages)} page(s) → {project.wiki_dir}", file=sys.stderr)

    index = None
    if "index" in todo:
        _meter = _stage_start()
        embedder = OllamaEmbedder(model=models.get("embed", DEFAULT_EMBED), host=host)
        wiki = WikiBuilder(split_level=split).build(_doc())
        index = SemanticIndex.build(
            wiki, embedder,
            size_words=int(build.get("chunk_size", 180)),
            overlap_words=int(build.get("overlap", 30)),
        )
        index.save(project.index_dir)
        _finish_stage(state, "index", fps["index"], project.index_dir,
                      {"chunks": len(index.chunks), "dim": int(index.embeddings.shape[1])}, _meter)
        print(f"  index → {len(index.chunks)} chunk(s) → {project.index_dir}", file=sys.stderr)

    if "graph" in todo:
        _meter = _stage_start()
        if index is None:
            if not (project.index_dir / "index.json").is_file():
                print("error: graph needs an index — run `openwiki build index` first.", file=sys.stderr)
                return 2
            index = SemanticIndex.load(project.index_dir)
        wiki = WikiBuilder(split_level=split).build(_doc())
        references = (_corpus_references(project, sources, _doc(), wiki, multi)
                     if gcfg.get("references", True) else None)
        entities = None
        relations = None
        want_relations = bool(gcfg.get("relations", False))
        want_resolve = bool(gcfg.get("resolve_entities", False))
        if gcfg.get("entities", False) or want_relations or want_resolve:
            model = models.get("chat", DEFAULT_CHAT)
            print("  graph: extracting entities (one LLM call per page) …", file=sys.stderr)
            entities = extract_entities(wiki, _entity_chat(model, host),
                                        types=gcfg.get("entity_types"),
                                        max_chars=int(gcfg.get("entity_max_chars", 8000)),
                                        retry_chat=_entity_retry_chat(model, host))
            if want_resolve:
                print("  graph: resolving entities (embedding candidates + LLM verify) …",
                      file=sys.stderr)
                entities, _n = resolve_entities(entities, index.embedder, _entity_chat(model, host))
            if want_relations:
                print("  graph: extracting relations (one LLM call per entity-rich page) …",
                      file=sys.stderr)
                relations = extract_relations(wiki, entities, _entity_chat(model, host),
                                              max_chars=int(gcfg.get("entity_max_chars", 8000)))
        stats = build_graph(wiki, index, project.graph_path,
                            similar_k=int(gcfg.get("similar_k", 6)),
                            references=references, entities=entities, relations=relations)
        graph_stats = {"pages": stats["pages"], "chunks": stats["chunks"],
                       "similar_to": stats["similar_edges"], "references": stats["reference_edges"]}
        if want_relations:
            graph_stats["relations"] = stats["relation_edges"]
        _finish_stage(state, "graph", fps["graph"], project.graph_path, graph_stats, _meter)
        print(f"  graph → {stats['pages']} page(s) / {stats['chunks']} chunk(s) → {project.graph_path}",
              file=sys.stderr)

    if "memory" in todo:
        _meter = _stage_start()
        session_paths = project.session_paths()
        if not project.memory_enabled or not session_paths:
            if session_paths and not project.memory_enabled:
                print("  memory: skipped — [memory] enabled = false (Wiki mode)", file=sys.stderr)
            _finish_stage(state, "memory", fps["memory"], project.graph_path,
                          {"remembered": 0, "sessions": len(session_paths)}, _meter)
        elif not project.graph_path.exists():
            print("error: memory stage needs a graph — build the graph first.", file=sys.stderr)
            return 2
        else:
            if index is None:
                if not (project.index_dir / "index.json").is_file():
                    print("error: memory stage needs an index (for the embedder).", file=sys.stderr)
                    return 2
                index = SemanticIndex.load(project.index_dir)
            if isinstance(index.embedder, OllamaEmbedder):
                index.embedder.host = host.rstrip("/")
            graph = _open_graph(project.graph_path, writable=True)
            if graph is None or not getattr(graph, "writable", False):
                print("error: could not open the graph writable for the memory stage "
                      "(stop `serve`/`chat` first).", file=sys.stderr)
                if graph is not None:
                    graph.close()
                return 2
            chat = OllamaChat(model=models.get("chat", DEFAULT_CHAT), host=host, temperature=0.2)
            coexist = _coexist_check(models.get("chat", DEFAULT_CHAT), host)
            resolve = _attribute_resolver(models.get("chat", DEFAULT_CHAT), host)
            total = 0
            try:
                print(f"  memory: capturing {len(session_paths)} session(s) with {chat.name} …",
                      file=sys.stderr)
                for spath in session_paths:
                    sid = Path(spath).stem
                    facts = capture_session(chat, Path(spath).read_text(encoding="utf-8"),
                                            session_date=session_date_of(sid))
                    res = graph.remember(sid, facts, index.embedder, coexist=coexist, resolve=resolve)
                    total += res["added"]
                    print(f"    · '{sid}' → {res['added']} new, {res['duplicates']} dup "
                          f"({res['facts']} captured)", file=sys.stderr)
            finally:
                graph.close()
            _finish_stage(state, "memory", fps["memory"], project.graph_path,
                          {"remembered": total, "sessions": len(session_paths)}, _meter)
            print(f"  memory → {total} new fact(s) from {len(session_paths)} session(s) "
                  f"→ {project.graph_path}", file=sys.stderr)

    print(f"Built project '{project.name}'.")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    project: Optional[Project] = getattr(args, "project_obj", None)
    if project is None:
        print("error: not in an OpenWiki project — run `openwiki init` first.", file=sys.stderr)
        return 2

    sources = project.source_paths()
    print(f"Project: {project.name}")
    print(f"  root   : {project.root}")
    if project.description:
        print(f"  about  : {project.description}")
    print(f"  models : embed={project.setting('models', 'embed', 'bge-m3')}  "
          f"chat={project.setting('models', 'chat', DEFAULT_CHAT)}  "
          f"host={project.setting('models', 'host', DEFAULT_HOST)}")
    print(f"  build  : split_level={project.setting('build', 'split_level', 2)}  "
          f"chunk={project.setting('build', 'chunk_size', 180)}w/"
          f"{project.setting('build', 'overlap', 30)}w  "
          f"entities={project.setting('graph', 'entities', False)}")
    print(f"  memory : {'Second Brain (enabled)' if project.memory_enabled else 'Wiki (disabled)'}")
    print("  sources:")
    for src in sources:
        rel = src.relative_to(project.root) if src.is_relative_to(project.root) else src
        print(f"    {'ok     ' if src.is_file() else 'MISSING'}  {rel}")
    if not sources:
        print("    (none — add [[sources]] to openwiki.toml)")
    session_paths = project.session_paths()
    if session_paths:
        print("  sessions:")
        for spath in session_paths:
            p = Path(spath)
            rel = p.relative_to(project.root) if p.is_relative_to(project.root) else p
            print(f"    {'ok     ' if p.is_file() else 'MISSING'}  {rel}")

    fps = compute_fingerprints(project, sources) if sources else {}
    stem = sources[0].stem if sources else ""
    exists = {
        "ingest": (project.parsed_dir / f"{stem}.json").is_file() if sources else False,
        "wiki": (project.wiki_dir / "wiki.json").is_file(),
        "index": (project.index_dir / "index.json").is_file(),
        "graph": project.graph_path.exists(),
        "memory": project.graph_path.exists(),
    }
    state = BuildState.load(project)
    print("  stages :")
    for stage in STAGES:
        rec = state.get(stage)
        if not exists.get(stage):
            label = "missing"
        elif not sources or state.fingerprint(stage) != fps.get(stage):
            label = "stale"
        else:
            label = "up to date"
        stats = rec.get("stats", {})
        extra = ("  " + json.dumps(stats, ensure_ascii=False)) if stats else ""
        meta = []
        if rec.get("duration_s") is not None:
            meta.append(f"{rec['duration_s']:.2f}s")
        llm = rec.get("llm") or {}
        if llm.get("calls"):
            meta.append(f"{llm['calls']} call(s), {llm.get('eval_tokens', 0)} tok")
        metastr = ("  [" + " · ".join(meta) + "]") if meta else ""
        print(f"    {stage:<7} {label:<11}{metastr}{extra}")
    return 0


def _apply_project(args: argparse.Namespace, project: Optional[Project],
                   userconfig: Optional[UserConfig] = None) -> None:
    """Fill unset (``None``) path/model/host/split-level args by precedence:
    ``flag > project manifest > ~/.openwiki/config.toml > built-in default``.

    Explicit flags (non-``None``) always win. With no project, the historical
    ``./output`` defaults apply, so behaviour is unchanged outside a project.
    """
    cmd = args.command

    def path(attr: str, proj_dir: Optional[Path], legacy: Path) -> None:
        if hasattr(args, attr) and getattr(args, attr) is None:
            setattr(args, attr, proj_dir if project is not None else legacy)

    def val(attr: str, section: str, key: str, default) -> None:
        if hasattr(args, attr) and getattr(args, attr) is None:
            chosen = project.setting(section, key, None) if project is not None else None
            if chosen is None and userconfig is not None:
                chosen = userconfig.setting(section, key, None)
            setattr(args, attr, chosen if chosen is not None else default)

    p = project
    if cmd == "ingest":
        path("out", p.out_dir if p else None, Path("output"))
    elif cmd == "build-wiki":
        path("out", p.wiki_dir if p else None, Path("output") / "wiki")
        val("split_level", "build", "split_level", 2)
    elif cmd == "index":
        path("out", p.index_dir if p else None, Path("output") / "index")
        val("split_level", "build", "split_level", 2)
        val("model", "models", "embed", DEFAULT_EMBED)
        val("host", "models", "host", DEFAULT_HOST)
        val("chunk_size", "build", "chunk_size", 180)
        val("overlap", "build", "overlap", 30)
    elif cmd == "search":
        path("index", p.index_dir if p else None, Path("output") / "index")
        val("host", "models", "host", DEFAULT_HOST)
    elif cmd == "eval":
        path("index", p.index_dir if p else None, Path("output") / "index")
        path("graph", p.graph_path if p else None, Path("output") / "graph")
        val("model", "models", "chat", DEFAULT_CHAT)
        val("host", "models", "host", DEFAULT_HOST)
    elif cmd == "ask":
        path("index", p.index_dir if p else None, Path("output") / "index")
        path("graph", p.graph_path if p else None, Path("output") / "graph")
        val("model", "models", "chat", DEFAULT_CHAT)
        val("host", "models", "host", DEFAULT_HOST)
    elif cmd == "chat":
        path("wiki", p.wiki_dir if p else None, Path("output") / "wiki")
        path("index", p.index_dir if p else None, Path("output") / "index")
        path("graph", p.graph_path if p else None, Path("output") / "graph")
        val("model", "models", "chat", DEFAULT_CHAT)
        val("host", "models", "host", DEFAULT_HOST)
    elif cmd == "serve":
        path("wiki", p.wiki_dir if p else None, Path("output") / "wiki")
        path("index", p.index_dir if p else None, Path("output") / "index")
        path("graph", p.graph_path if p else None, Path("output") / "graph")
        val("model", "models", "chat", DEFAULT_CHAT)
        val("host", "models", "host", DEFAULT_HOST)
        val("port", "serve", "port", 8000)
        val("bind", "serve", "bind", "127.0.0.1")
    elif cmd == "mcp":
        path("wiki", p.wiki_dir if p else None, Path("output") / "wiki")
        path("index", p.index_dir if p else None, Path("output") / "index")
        path("graph", p.graph_path if p else None, Path("output") / "graph")
        val("model", "models", "chat", DEFAULT_CHAT)
        val("host", "models", "host", DEFAULT_HOST)
    elif cmd == "ontology":
        val("model", "models", "chat", DEFAULT_CHAT)
        val("host", "models", "host", DEFAULT_HOST)
    elif cmd == "backfill":
        path("index", p.index_dir if p else None, Path("output") / "index")
        path("graph", p.graph_path if p else None, Path("output") / "graph")
        val("model", "models", "chat", DEFAULT_CHAT)
        val("host", "models", "host", DEFAULT_HOST)
    elif cmd == "references":
        path("graph", p.graph_path if p else None, Path("output") / "graph")
        val("split_level", "build", "split_level", 2)
    elif cmd == "graph-build":
        path("out", p.graph_path if p else None, Path("output") / "graph")
        path("index", p.index_dir if p else None, Path("output") / "index")
        val("split_level", "build", "split_level", 2)
        val("similar_k", "graph", "similar_k", 6)
        val("entity_model", "models", "chat", DEFAULT_CHAT)
        val("host", "models", "host", DEFAULT_HOST)
    elif cmd == "communities":
        path("graph", p.graph_path if p else None, Path("output") / "graph")
        val("model", "models", "chat", DEFAULT_CHAT)
        val("host", "models", "host", DEFAULT_HOST)
    elif cmd == "decay":
        path("graph", p.graph_path if p else None, Path("output") / "graph")
    elif cmd == "consolidate":
        path("graph", p.graph_path if p else None, Path("output") / "graph")
        val("model", "models", "chat", DEFAULT_CHAT)
        val("host", "models", "host", DEFAULT_HOST)
    elif cmd == "remember":
        path("index", p.index_dir if p else None, Path("output") / "index")
        path("graph", p.graph_path if p else None, Path("output") / "graph")
        val("model", "models", "chat", DEFAULT_CHAT)
        val("host", "models", "host", DEFAULT_HOST)
    elif cmd == "recall":
        path("index", p.index_dir if p else None, Path("output") / "index")
        path("graph", p.graph_path if p else None, Path("output") / "graph")
        val("host", "models", "host", DEFAULT_HOST)
    elif cmd == "context":
        path("index", p.index_dir if p else None, Path("output") / "index")
        path("graph", p.graph_path if p else None, Path("output") / "graph")
        val("host", "models", "host", DEFAULT_HOST)
    elif cmd == "analyze":
        path("index", p.index_dir if p else None, Path("output") / "index")
        path("graph", p.graph_path if p else None, Path("output") / "graph")


def _cmd_ingest(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = parse_source(
        args.pdf,
        extract_tables=not args.no_tables,
        extract_images=args.images,
        image_dir=(out_dir / "images") if args.images else None,
        max_pages=args.max_pages,
    )

    stem = source_stem(args.pdf)
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    json_path.write_text(
        json.dumps(doc.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(
        doc.to_markdown(include_tables=not args.no_tables), encoding="utf-8"
    )

    n_tables = sum(len(p.tables) for p in doc.pages)
    n_images = sum(len(p.images) for p in doc.pages)
    print(f"Parsed {len(doc.pages)} page(s) from {args.pdf}")
    print(f"  outline entries : {len(doc.outline)}")
    print(f"  tables extracted: {n_tables}")
    if args.images:
        print(f"  images extracted: {n_images}")
    print(f"  JSON     -> {json_path}")
    print(f"  Markdown -> {md_path}")
    return 0


def _cmd_build_wiki(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    source: Path = args.source
    if source.suffix.lower() == ".json":
        data = json.loads(source.read_text(encoding="utf-8"))
        doc = ParsedDocument.from_dict(data)
    else:
        doc = parse_source(
            source,
            extract_tables=not args.no_tables,
            extract_images=args.images,
            image_dir=(args.out.parent / "images") if args.images else None,
        )

    wiki = WikiBuilder(split_level=args.split_level).build(doc)
    write_wiki(wiki, args.out, include_tables=not args.no_tables)

    print(f"Built wiki: {len(wiki.pages)} page(s) from {source.name}")
    print(f"  split level : {args.split_level}")
    print(f"  top-level   : {len(wiki.root_pages)}")
    print(f"  index    -> {args.out / 'index.md'}")
    print(f"  manifest -> {args.out / 'wiki.json'}")
    print(f"  pages    -> {args.out / 'pages'}")
    return 0


def _load_parsed(source: Path, extract_tables: bool = False) -> ParsedDocument:
    """Load a ParsedDocument from a `.json` (fast) or by parsing a PDF."""
    if source.suffix.lower() == ".json":
        return ParsedDocument.from_dict(json.loads(source.read_text(encoding="utf-8")))
    return parse_source(source, extract_tables=extract_tables)


def _cmd_index(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    doc = _load_parsed(args.source)
    wiki = WikiBuilder(split_level=args.split_level).build(doc)
    embedder = OllamaEmbedder(model=args.model, host=args.host)
    index = SemanticIndex.build(
        wiki, embedder, size_words=args.chunk_size, overlap_words=args.overlap
    )
    index.save(args.out)
    print(f"Indexed {len(index.chunks)} chunk(s) from {len(wiki.pages)} wiki page(s)")
    print(f"  model  : {index.model_name}  (dim {index.embeddings.shape[1]})")
    print(f"  chunks : {args.chunk_size}w / {args.overlap}w overlap")
    print(f"  index -> {args.out}")
    return 0


def _snippet(text: str, limit: int = 240) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _cmd_search(args: argparse.Namespace) -> int:
    index = SemanticIndex.load(args.index)
    if isinstance(index.embedder, OllamaEmbedder):
        index.embedder.host = args.host.rstrip("/")
    results = index.search(args.query, k=args.top_k)
    if not results:
        print("No results.")
        return 0
    print(f'Query: "{args.query}"   (model: {index.model_name})\n')
    for rank, result in enumerate(results, start=1):
        body = result.text if args.full else _snippet(result.text)
        print(
            f"{rank}. [{result.score:.3f}] {result.page_title}"
            f"  ·  PDF p.{result.pdf_page_start}–{result.pdf_page_end}"
        )
        print(f"    pages/{result.page_slug}.md   ({result.chunk_id})")
        print(f"    {body}\n")
    return 0


def _resolve_eval_set(spec, project: Optional[Project]) -> Path:
    """Resolve the ``--eval-set`` argument to a path.

    An explicit path (absolute, or relative to the CWD) that exists is honored as
    given; otherwise a bare/relative name is resolved against the project root —
    matching the web UI's ``WikiWebApp.eval_set_path``. Default (no ``--eval-set``):
    ``<project>/eval.jsonl`` (or ``./eval.jsonl`` with no project). A path that
    resolves to nothing is returned unchanged so the caller can report it.
    """
    root = project.root if project is not None else Path.cwd()
    if spec is None:
        return root / "eval.jsonl"
    spec = Path(spec)
    if spec.is_file() or spec.is_absolute():
        return spec
    return root / spec


def _cmd_eval(args: argparse.Namespace) -> int:
    project = getattr(args, "project_obj", None)
    if getattr(args, "cross_session", False):
        return _cross_session_eval(args, project)
    path = _resolve_eval_set(args.eval_set, project)
    if not path.is_file():
        print(f"error: eval set not found: {path}\n"
              '  create a JSONL of {"question": "...", "pages": ["slug", ...]} lines.',
              file=sys.stderr)
        return 2
    items = load_eval_set(path)
    if not items:
        print(f"error: eval set is empty: {path}", file=sys.stderr)
        return 2
    if getattr(args, "global_search", False):
        return _global_eval(items, path, args)
    if not (args.index / "index.json").is_file():
        print(f"error: no index at {args.index} (run `openwiki index` first).", file=sys.stderr)
        return 2

    index = SemanticIndex.load(args.index)
    if isinstance(index.embedder, OllamaEmbedder):
        index.embedder.host = args.host.rstrip("/")
    top_k, expand_k = args.top_k, args.expand_k
    budget = top_k + expand_k

    graph = None if args.no_graph else _open_graph(args.graph, writable=False)
    rag_fn, graphrag_fn = make_retrievers(index, graph, top_k, expand_k)

    print(f"Eval: {path}  ({len(items)} questions)   k={budget}  (top_k={top_k} + expand_k={expand_k})",
          file=sys.stderr)
    reports = [("RAG (semantic)", evaluate(items, rag_fn, budget))]
    if graphrag_fn is not None:
        reports.append(("GraphRAG", evaluate(items, graphrag_fn, budget)))
    if getattr(args, "hybrid", False):
        from .eval import hybrid_pages
        reports.append(("Hybrid (BM25)",
                        evaluate(items, lambda q: hybrid_pages(index, q, budget), budget)))
    if getattr(args, "rerank", False):
        from .eval import reranking_retriever
        model = args.model or (project.setting("models", "chat", DEFAULT_CHAT) if project else DEFAULT_CHAT)
        pool = getattr(args, "rerank_pool", 20)
        print(f"  + re-ranking a pool of {pool} with {model} (one call/question) …", file=sys.stderr)
        rerank_fn = reranking_retriever(index, OllamaChat(model=model, host=args.host, temperature=0.0),
                                        budget, pool=pool)
        reports.append(("RAG+Rerank", evaluate(items, rerank_fn, budget)))

    print(f"\n{'retriever':<18}{'MRR':>8}{'hit@k':>9}{'recall@k':>10}")
    print("-" * 45)
    for name, report in reports:
        print(f"{name:<18}{report.mrr:>8.3f}{report.hit_rate:>8.1%} {report.recall:>9.1%}")

    if args.misses:
        # misses of the strongest retriever we ran
        name, report = reports[-1]
        misses = report.misses
        print(f"\n{len(misses)} miss(es) for {name} (no expected page in top-{budget}):")
        for r in misses:
            print(f"  · {r.question}")
            print(f"      expected {r.expected}  ·  got {r.ranked[:budget]}")

    if args.answers:
        if graph is None:
            print("\n(--answers needs a graph for the GraphRAG comparison)", file=sys.stderr)
        else:
            _answer_eval(items, index, graph, args, top_k, expand_k)

    if graph is not None:
        graph.close()
    return 0


def _global_eval(items, path, args: argparse.Namespace) -> int:
    """Thematic eval of global search: generate a global answer per question and score its
    community citations against the ground-truth communities (those covering an expected
    page). With --judge, also compare Global vs plain-RAG answers."""
    graph = _open_graph(args.graph, writable=False)
    if graph is None or not graph.has_communities():
        print("error: no communities in the graph. Run `openwiki communities` first.", file=sys.stderr)
        if graph is not None:
            graph.close()
        return 2
    comms = graph.communities()                                  # size-desc; marker = i+1
    members = graph.community_members()                          # {id: [slug]}
    member_by_marker = {i + 1: set(members.get(c["id"], [])) for i, c in enumerate(comms)}
    community_pairs = [(c["label"], c["summary"]) for c in comms]

    index = None
    if args.judge:
        if (args.index / "index.json").is_file():
            index = SemanticIndex.load(args.index)
            if isinstance(index.embedder, OllamaEmbedder):
                index.embedder.host = args.host.rstrip("/")
        else:
            print("(--judge needs an index for the RAG comparison; skipping judge)", file=sys.stderr)
    chat = OllamaChat(model=args.model, host=args.host, temperature=0.2)
    judge = OllamaChat(model=args.model, host=args.host, temperature=0.0) if (args.judge and index) else None

    subset = items[: args.limit] if args.limit else items
    print(f"Global eval: {path.name}  ({len(subset)} thematic questions, {len(comms)} communities) "
          f"— generating answers with {chat.name}{' + judge' if judge else ''} …", file=sys.stderr)
    result = run_global_eval(subset, community_pairs, member_by_marker, chat, index=index, judge=judge,
                             on_progress=lambda done, total: print(f"  {done}/{total} done", file=sys.stderr))
    graph.close()

    g = result["grounding"]
    print(f"\nGlobal search — community grounding   [{result['questions']} questions]")
    print(f"  cite-hit {g['cite_hit']:.1%}   ·   community-recall {g['recall']:.1%}   "
          f"·   community-precision {g['precision']:.1%}")
    if result["judged"]:
        t = result["tally"]
        print(f"\nLLM judge (Global vs RAG, position-balanced):  "
              f"Global {t['Global']}  ·  RAG {t['RAG']}  ·  tie {t['tie']}")
    return 0


def _answer_eval(items, index, graph, args, top_k: int, expand_k: int) -> None:
    """Generate RAG vs GraphRAG *answers* for the eval set and report answer quality:
    objective citation grounding (did the answer cite a ground-truth page?) and, with
    ``--judge``, an LLM's pairwise verdict (position-balanced across questions)."""
    from .eval import run_answer_eval

    subset = items[: args.limit] if args.limit else items
    chat = OllamaChat(model=args.model, host=args.host, temperature=0.2)
    judge = OllamaChat(model=args.model, host=args.host, temperature=0.0) if args.judge else None
    print(f"\nGenerating answers for {len(subset)} question(s) "
          f"(RAG + GraphRAG{' + judge' if judge else ''}) — slow …", file=sys.stderr)

    result = run_answer_eval(subset, index, graph, chat, top_k, expand_k, judge=judge,
                             on_progress=lambda done, total: print(f"  {done}/{total} done", file=sys.stderr))
    g = result["grounding"]
    print(f"\nAnswer grounding — cited a ground-truth page   [{result['questions']} questions]")
    print(f"{'retriever':<18}{'cite-hit':>10}{'exp-recall':>12}")
    print("-" * 40)
    for name in ("RAG", "GraphRAG"):
        print(f"{name:<18}{g[name]['cite_hit']:>9.1%}{g[name]['expected_recall']:>11.1%}")
    if result["judged"]:
        t = result["tally"]
        print(f"\nLLM judge (position-balanced):  GraphRAG {t['GraphRAG']}  ·  "
              f"RAG {t['RAG']}  ·  tie {t['tie']}")


def _build_stub_graph(tmp_dir: Path, embedder) -> Path:
    """A minimal throwaway Kuzu graph (one stub page) so the cross-session eval has the
    memory schema without touching — or depending on — the project's real graph."""
    from .graph import GraphBuilder
    from .wiki import Wiki, WikiPage

    page = WikiPage(slug="000-stub", title="stub", level=1, order=0,
                    pdf_page_start=1, pdf_page_end=1, text="stub")
    wiki = Wiki(title="stub", pages=[page], source="stub", split_level=1)
    index = SemanticIndex.build(wiki, embedder, size_words=50, overlap_words=10)
    gpath = tmp_dir / "graph"
    GraphBuilder(gpath).build(wiki, index)
    return gpath


def _cross_session_eval(args: argparse.Namespace, project) -> int:
    """Path B headline metric: cross-session task success (docs/path-b-memory.md §7).
    Remembers each scenario's setup sessions into a throwaway graph, then answers the probe
    cold / raw-log / assembled and reports objective task success (+ optional judge)."""
    import tempfile

    from .eval import load_cross_session_set, run_cross_session_eval

    root = project.root if project is not None else Path.cwd()
    spec = args.eval_set
    if spec is None:
        path = root / "eval_cross_session.jsonl"
    elif Path(spec).is_file() or Path(spec).is_absolute():
        path = Path(spec)
    else:
        path = root / spec
    if not path.is_file():
        print(f"error: cross-session set not found: {path}\n"
              '  create a JSONL of {"name","setup":[transcript,…],"question","expected":[…]} lines.',
              file=sys.stderr)
        return 2
    items = load_cross_session_set(path)
    if not items:
        print(f"error: cross-session set is empty: {path}", file=sys.stderr)
        return 2
    if not (args.index / "index.json").is_file():
        print(f"error: no index at {args.index} (run `openwiki index` — needed for the embedder).",
              file=sys.stderr)
        return 2
    index = SemanticIndex.load(args.index)
    if isinstance(index.embedder, OllamaEmbedder):
        index.embedder.host = args.host.rstrip("/")

    subset = items[: args.limit] if args.limit else items
    chat = OllamaChat(model=args.model, host=args.host, temperature=0.2)
    judge = OllamaChat(model=args.model, host=args.host, temperature=0.0) if args.judge else None
    print(f"Cross-session eval: {path.name}  ({len(subset)} scenarios) — "
          f"cold vs raw-log vs assembled with {chat.name}{' + judge' if judge else ''} …",
          file=sys.stderr)

    tmp_dir = Path(tempfile.mkdtemp(prefix="owiki-xsess-"))
    try:
        graph = _open_graph(_build_stub_graph(tmp_dir, index.embedder), writable=True)
        if graph is None or not getattr(graph, "writable", False):
            print("error: could not open a writable throwaway graph (is Kuzu installed?).",
                  file=sys.stderr)
            return 2
        try:
            result = run_cross_session_eval(
                subset, graph, index.embedder, chat, judge=judge, recall_k=args.recall_k,
                on_progress=lambda done, total: print(f"  {done}/{total} done", file=sys.stderr))
        finally:
            graph.close()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    s = result["success"]
    print(f"\nCross-session task success   [{result['scenarios']} scenarios]")
    print(f"{'condition':<14}{'success':>9}")
    print("-" * 23)
    for cond in ("cold", "raw-log", "assembled"):
        print(f"{cond:<14}{s[cond]:>8.1%}")
    if result.get("by_kind"):           # B7 temporal sets tag each scenario with a kind
        print(f"\n{'kind':<16}{'n':>3}{'cold':>8}{'raw-log':>9}{'assembled':>11}")
        print("-" * 47)
        for kind, v in result["by_kind"].items():
            print(f"{kind:<16}{v['n']:>3}{v['cold']:>8.0%}{v['raw-log']:>9.0%}{v['assembled']:>11.0%}")
        misses = [d for d in result["details"] if not d["success"]["assembled"]]
        for d in misses:
            print(f"  ✗ assembled  {d['name']}: {d['answers']['assembled'][:110]}")
    if result["judged"]:
        t = result["tally"]
        print(f"\nLLM judge (assembled vs raw-log, position-balanced):  "
              f"assembled {t['assembled']}  ·  raw-log {t['raw-log']}  ·  tie {t['tie']}")
    return 0


def _ask_global(args: argparse.Namespace, graph) -> int:
    """Global search: answer a thematic question from the community summaries."""
    if graph is None or not graph.has_communities():
        print("error: no communities in the graph. Run `openwiki communities` first.",
              file=sys.stderr)
        return 2
    comms = graph.communities()
    chat = OllamaChat(model=args.model, host=args.host, temperature=args.temperature)
    print(f"answering globally from {len(comms)} communities with {chat.name} …", file=sys.stderr)
    answer = answer_global(chat, args.question, [(c["label"], c["summary"]) for c in comms])
    print(answer)
    cited = {int(m) for m in re.findall(r"\[(\d+)\]", answer)}
    print("\nCommunities  (* = cited):")
    for i, c in enumerate(comms, 1):
        mark = "*" if i in cited else " "
        print(f" {mark}[{i}] {c['label']}  ({c['size']} pages)")
    return 0


def _cmd_ask(args: argparse.Namespace) -> int:
    if getattr(args, "global_search", False):
        graph = _open_graph(args.graph, writable=False)
        try:
            return _ask_global(args, graph)
        finally:
            if graph is not None:
                graph.close()

    index = SemanticIndex.load(args.index)
    if isinstance(index.embedder, OllamaEmbedder):
        index.embedder.host = args.host.rstrip("/")

    graph = None
    if not args.no_graph and args.graph.exists():
        try:
            graph = GraphStore(args.graph)
            # B1: in Second Brain mode, a read-only ask records usage to the log for a
            # later fold-in (serve/chat startup or `openwiki decay`) — reads teach the graph.
            project = getattr(args, "project_obj", None)
            graph.log_usage = bool(project is not None and project.memory_enabled)
        except Exception as exc:
            print(f"(graph not loaded: {exc})", file=sys.stderr)

    chat = OllamaChat(model=args.model, host=args.host, temperature=args.temperature)
    agent = RAGAgent(index, chat, top_k=args.top_k, graph=graph, expand_k=args.expand_k,
                     rerank=getattr(args, "rerank", False),
                     rerank_pool=getattr(args, "rerank_pool", 20),
                     hybrid=getattr(args, "hybrid", False))

    mode = ("hybrid " if getattr(args, "hybrid", False) else "") + \
           ("re-ranked " if getattr(args, "rerank", False) else "") + \
           ("graph-augmented " if graph else "")
    print(f"{mode}retrieving and asking {chat.name} …", file=sys.stderr)
    result = agent.answer(args.question)

    print(result.answer)
    if result.sources:
        cited = result.cited_markers()
        print("\nSources  (* = cited, + = related via graph):")
        for s in result.sources:
            cite = "*" if s.marker in cited else " "
            rel = "+" if s.kind == "related" else " "
            print(
                f" {cite}{rel}[{s.marker}] {s.page_title}  ·  PDF p.{s.pdf_page_start}–{s.pdf_page_end}"
                f"  ·  pages/{s.page_slug}.md  ({s.score:.3f})"
            )
        if args.show_context:
            print("\nContext:")
            for s in result.sources:
                print(f"\n[{s.marker}] {s.page_title} (PDF p.{s.pdf_page_start}–{s.pdf_page_end})")
                print(f"    {_snippet(s.text, 400)}")
    _print_llm_telemetry(chat)
    return 0


def _print_llm_telemetry(chat) -> None:
    """One-line latency/token footer (to stderr) from the model's last call — the CLI
    face of the observability layer (the web UI's System tab shows the full picture)."""
    stats = getattr(chat, "last_stats", None)
    if not stats:
        return
    bits = []
    if stats.get("duration_ms"):
        bits.append(f"{stats['duration_ms'] / 1000:.2f}s")
    if stats.get("eval_tokens"):
        bits.append(f"{stats['eval_tokens']} tok")
    if stats.get("tokens_per_sec"):
        bits.append(f"{stats['tokens_per_sec']} tok/s")
    if bits:
        print("  ⏱ " + " · ".join(bits), file=sys.stderr)


def _fmt_args(arguments: dict) -> str:
    parts = []
    for key, value in arguments.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        parts.append(f"{key}={_snippet(str(text), 60)}")
    return ", ".join(parts)


def _run_turn(agent: WikiAgent, message: str, show_tools: bool) -> None:
    turn = agent.send(message)
    if show_tools:
        for call in turn.tool_calls:
            print(
                f"  · {call.name}({_fmt_args(call.arguments)}) → {_snippet(call.result, 100)}",
                file=sys.stderr,
            )
    print(f"\nassistant> {turn.reply}\n")


def _print_edits(tools: WikiTools) -> None:
    if tools.edits:
        label = "Proposed edits (dry-run)" if tools.dry_run else "Edits written"
        print(f"\n{label}:", file=sys.stderr)
        for entry in tools.edits:
            print(f"  - {entry}", file=sys.stderr)


def _open_graph(path: Path, writable: bool, retries: int = 0, backoff: float = 0.15):
    """Open the graph, falling back to read-only if a writable open is refused.

    Kuzu's writable lock is exclusive, so a writable open fails while any other
    process holds the graph. When ``retries`` > 0 we retry with a short linear
    backoff — enough to ride out *transient* contention (two writers briefly racing,
    e.g. ``decay`` and a hook ``capture``). A writer blocked by a long-lived reader
    (a running read-only ``serve``) won't clear; those callers queue to the journal
    instead. On a read-only open we don't retry (it only fails under a writer)."""
    if not path.exists():
        return None
    attempt = 0
    while True:
        try:
            return GraphStore(path, writable=writable)
        except Exception as exc:
            if writable and attempt < retries:
                time.sleep(backoff * (attempt + 1))
                attempt += 1
                continue
            if writable:
                try:
                    print(f"(graph opened read-only: {exc})", file=sys.stderr)
                    return GraphStore(path, writable=False)
                except Exception as exc2:
                    print(f"(graph not loaded: {exc2})", file=sys.stderr)
                    return None
            print(f"(graph not loaded: {exc})", file=sys.stderr)
            return None


def _transient_fold(path: Path, embedder) -> None:
    """Open the graph writable *transiently* (retry-with-backoff), fold the write-ahead
    journals (usage pairs + queued remember/reindex ops), and close — so a read-only
    ``serve``/``chat`` absorbs deferred writes at start and shutdown. Best-effort: if the
    lock is held (another reader/writer is up), skip; the journals persist for the next
    writable pass. Needs an embedder to apply memory/reindex ops (usage folds regardless)."""
    if path is None or not Path(path).exists():
        return
    graph = _open_graph(path, writable=True, retries=6)
    if graph is None:
        return
    if not getattr(graph, "writable", False):
        graph.close()
        return
    try:
        folded = graph.fold_usage()
        ops = graph.fold_journal(embedder) if embedder is not None else {"records": 0}
        if folded.get("records") or ops.get("records"):
            print(f"(folded {folded.get('records', 0)} usage + {ops.get('records', 0)} "
                  f"queued op(s) into the graph)", file=sys.stderr)
    except Exception:      # pragma: no cover - maintenance must never crash the command
        pass
    finally:
        graph.close()


def _cmd_chat(args: argparse.Namespace) -> int:
    index = None
    if (args.index / "index.json").is_file():
        index = SemanticIndex.load(args.index)
        if isinstance(index.embedder, OllamaEmbedder):
            index.embedder.host = args.host.rstrip("/")
    embedder = index.embedder if index else None
    project = getattr(args, "project_obj", None)
    mem = bool(project is not None and project.memory_enabled)
    # Read-only by default (concurrency — see `serve`): edits write page files, and the
    # graph re-sync is deferred to the journal, folded at start & exit. --sync = live sync.
    sync = getattr(args, "sync", False) and index is not None and not args.dry_run
    if sync:
        graph = _open_graph(args.graph, writable=True, retries=6)
        _fold_pending_usage(graph)
        if graph is not None and getattr(graph, "writable", False) and embedder is not None:
            try:
                graph.fold_journal(embedder)
            except Exception:
                pass
    else:
        _transient_fold(args.graph, embedder)
        graph = _open_graph(args.graph, writable=False)
        if graph is not None and mem:
            graph.log_usage = True
    tools = WikiTools(args.wiki, index=index, graph=graph, embedder=embedder, dry_run=args.dry_run)
    chat = OllamaChat(model=args.model, host=args.host, temperature=args.temperature)
    agent = WikiAgent(chat, tools, wiki_summary=summarize_wiki(args.wiki))

    try:
        if args.message:  # non-interactive: run the given turns in one session
            for message in args.message:
                print(f"you> {message}", file=sys.stderr)
                _run_turn(agent, message, args.show_tools)
            _print_edits(tools)
            return 0

        mode = " (dry-run)" if args.dry_run else ""
        print(f"OpenWiki chat{mode} — model {chat.name}. Type 'exit' to quit.", file=sys.stderr)
        while True:
            try:
                user = input("you> ")
            except EOFError:
                break
            if user.strip().lower() in {"exit", "quit", ":q"}:
                break
            if not user.strip():
                continue
            try:
                _run_turn(agent, user, args.show_tools)
            except RuntimeError as exc:
                print(f"error: {exc}", file=sys.stderr)
        _print_edits(tools)
        return 0
    finally:
        if graph is not None:
            graph.close()
        if not sync:
            _transient_fold(args.graph, embedder)


def _cmd_references(args: argparse.Namespace) -> int:
    """Refresh the graph's ``REFERENCES`` edges from the parsed corpus — with the citation
    phrases the web UI turns into inline links (ADR-28). In place: entities, relations,
    communities and memory are untouched, so an expensive graph needs no rebuild."""
    project = getattr(args, "project_obj", None)
    if args.source is not None:
        doc = _load_parsed(args.source)
        wiki = WikiBuilder(split_level=args.split_level).build(doc)
        refs = extract_references(doc, wiki, labels=True)
    elif project is not None:
        sources = project.source_paths()
        if not sources:
            print("error: the project declares no document sources.", file=sys.stderr)
            return 2
        multi = len(sources) > 1
        parsed_path = project.parsed_dir / ("_corpus.json" if multi else f"{source_stem(sources[0])}.json")
        if not parsed_path.is_file():
            print(f"error: no parsed corpus at {parsed_path} (run `openwiki build` first).", file=sys.stderr)
            return 2
        doc = _load_parsed(parsed_path)
        wiki = WikiBuilder(split_level=args.split_level).build(doc)
        refs = _corpus_references(project, sources, doc, wiki, multi, labels=True)
        if refs is None:
            return 2
    else:
        print("error: pass a parsed .json (or a source), or run inside a project.", file=sys.stderr)
        return 2
    graph = _open_graph(args.graph, writable=True, retries=6)
    if graph is None:
        print(f"error: no graph at {args.graph} (run `openwiki graph-build` first).", file=sys.stderr)
        return 2
    try:
        if not getattr(graph, "writable", False):
            print("error: the graph is locked (stop `serve`/`chat` first) — references not refreshed.",
                  file=sys.stderr)
            return 2
        written = graph.refresh_references(refs)
    finally:
        graph.close()
    phrases = sum(len(r[2]) for r in refs)
    print(f"References refreshed: {written} edge(s), {phrases} citation phrase(s) → {args.graph}")
    return 0


def _cmd_graph_build(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    doc = _load_parsed(args.source)
    wiki = WikiBuilder(split_level=args.split_level).build(doc)
    index = SemanticIndex.load(args.index)
    references = None if args.no_references else extract_references(doc, wiki, labels=True)

    entities = None
    relations = None
    want_relations = getattr(args, "relations", False)
    want_resolve = getattr(args, "resolve_entities", False)
    if args.entities or want_relations or want_resolve:
        chat = _entity_chat(args.entity_model, args.host)
        print(f"Extracting entities with {chat.name} (one call per page) …", file=sys.stderr)

        def _progress(done, total, found):
            print(f"  page {done}/{total} — {found} entities so far", file=sys.stderr)

        types = [t.strip() for t in args.entity_types.split(",")] if args.entity_types else None
        entities = extract_entities(wiki, chat, types=types,
                                    max_chars=args.entity_max_chars or 8000,
                                    on_progress=_progress if args.verbose else None,
                                    retry_chat=_entity_retry_chat(args.entity_model, args.host))
        if want_resolve:
            print(f"Resolving entities with {chat.name} (embedding candidates + LLM verify) …",
                  file=sys.stderr)
            raw = len(entities)
            entities, n_clusters = resolve_entities(entities, index.embedder, chat)
            print(f"  resolved {raw} → {len(entities)} canonical ({n_clusters} cluster(s) checked)",
                  file=sys.stderr)
        if want_relations:
            print(f"Extracting relations with {chat.name} (one call per entity-rich page) …",
                  file=sys.stderr)

            def _rprogress(done, total, found):
                print(f"  page {done}/{total} — {found} relations so far", file=sys.stderr)

            relations = extract_relations(wiki, entities, chat,
                                          max_chars=args.entity_max_chars or 8000,
                                          on_progress=_rprogress if args.verbose else None)

    stats = build_graph(wiki, index, args.out, similar_k=args.similar_k,
                        references=references, entities=entities, relations=relations)
    print(f"Built graph from {args.source.name}")
    print(f"  pages         : {stats['pages']}")
    print(f"  chunks (dim {stats['dim']}): {stats['chunks']}")
    print(f"  SIMILAR_TO    : {stats['similar_edges']}")
    print(f"  REFERENCES    : {stats['reference_edges']}")
    if args.entities or want_relations or want_resolve:
        canon = "  (canonical, aliases resolved)" if want_resolve else ""
        print(f"  entities      : {stats['entities']}  (MENTIONS: {stats['mention_edges']}){canon}")
    if want_relations:
        print(f"  relations     : {stats['relation_edges']}  (RELATED_TO)")
    print(f"  graph -> {args.out}")
    return 0


def _cmd_communities(args: argparse.Namespace) -> int:
    """Consolidation pass: detect topical communities over the built graph and write
    an LLM summary per community (Page-[:IN_COMMUNITY]->Community), powering `ask --global`."""
    from collections import defaultdict

    graph = _open_graph(args.graph, writable=True)
    if graph is None:
        print(f"error: no graph at {args.graph} (run `openwiki graph-build` first).", file=sys.stderr)
        return 2
    if not getattr(graph, "writable", False):
        print("error: graph is locked by another process (stop `serve`/`chat` first).", file=sys.stderr)
        graph.close()
        return 2
    try:
        pg = graph.page_graph()
        if len(pg["pages"]) < 2:
            print("error: graph has too few pages for communities.", file=sys.stderr)
            return 2

        assignment = detect_communities(pg["edges"], list(pg["pages"]))
        members: dict = defaultdict(list)
        for slug, cid in assignment.items():
            members[cid].append(slug)
        degree: dict = defaultdict(float)
        for a, b, w in pg["edges"]:
            degree[a] += w
            degree[b] += w
        titles = pg["pages"]

        chat = OllamaChat(model=args.model, host=args.host, temperature=0.2)
        print(f"Detected {len(members)} communities over {len(titles)} pages. "
              f"Summarizing with {chat.name} …", file=sys.stderr)

        summaries, labels = {}, {}
        for cid in sorted(members):
            ranked = sorted(members[cid], key=lambda s: (-degree.get(s, 0.0), s))
            fallback = titles.get(ranked[0], f"Community {cid}")   # hub title if the model gives none
            mem = [(titles.get(s, s), graph.page_snippet(s)) for s in ranked[: args.max_pages]]
            labels[cid], summaries[cid] = summarize_community(chat, mem, fallback_label=fallback)
            print(f"  [{cid}] {len(members[cid]):>3} pages — {labels[cid]}", file=sys.stderr)

        result = graph.upsert_communities(assignment, summaries, labels)
    finally:
        graph.close()

    print(f"Wrote {result['communities']} communities over {result['pages']} pages → {args.graph}")
    print('  now try:  openwiki ask --global "<a thematic question>"')
    return 0


def _cmd_consolidate(args: argparse.Namespace) -> int:
    """Path B 'sleep' pass (B5): cluster the current remembered facts into topical themes,
    LLM-summarize each (MemoryConcept + CONSOLIDATES), then fold usage + decay — compress
    the accumulated memory into structure and forget the noise. Re-runnable + bounded."""
    from collections import defaultdict

    project = getattr(args, "project_obj", None)
    if project is not None and not project.memory_enabled:
        print("(memory is disabled — Wiki mode; set [memory] enabled = true to consolidate)")
        return 0
    graph = _open_graph(args.graph, writable=True)
    if graph is None:
        print(f"error: no graph at {args.graph} (run `openwiki graph-build` first).", file=sys.stderr)
        return 2
    if not getattr(graph, "writable", False):
        print("error: graph is locked by another process (stop `serve`/`chat` first).", file=sys.stderr)
        graph.close()
        return 2
    reused = summarized = 0
    try:
        if not graph.has_memory():
            print("(no remembered facts yet — capture sessions with `openwiki remember` first)")
            return 0
        # B5 stability + incrementality: warm-start clustering from the prior partition, and
        # reuse an existing theme's summary when its member set is unchanged (skip the LLM call).
        prior_assign = {} if args.resummarize else graph.concept_assignment()
        prior_by_set = {}
        if not args.resummarize:
            members_prev = graph.concept_members()
            prior_by_set = {frozenset(members_prev.get(c["id"], set())): (c["label"], c["summary"])
                            for c in graph.memory_concepts()}
        ag = graph.assertion_graph(similar_k=args.similar_k)
        facts = ag["facts"]
        assignment0 = detect_communities(ag["edges"], list(facts), seed=prior_assign or None)
        members: dict = defaultdict(list)
        for aid, cid in assignment0.items():
            members[cid].append(aid)
        # keep only clusters that form a real theme (>= min-size), largest first, renumbered
        kept = [cid for cid in sorted(members, key=lambda c: (-len(members[c]), min(members[c])))
                if len(members[cid]) >= args.min_size]
        degree: dict = defaultdict(float)
        for a, b, w in ag["edges"]:
            degree[a] += w
            degree[b] += w

        chat = OllamaChat(model=args.model, host=args.host, temperature=0.2)
        if kept:
            print(f"Consolidating {len(facts)} fact(s) into {len(kept)} theme(s) with {chat.name} "
                  f"(unchanged themes reuse their summary) …", file=sys.stderr)
        else:
            print(f"No themes yet — need a cluster of ≥{args.min_size} related facts "
                  f"({len(facts)} fact(s) so far).", file=sys.stderr)
        assignment, summaries, labels = {}, {}, {}
        for new_id, cid in enumerate(kept):
            member_set = frozenset(members[cid])
            cached = prior_by_set.get(member_set)
            if cached is not None:                     # membership unchanged → reuse (no LLM call)
                labels[new_id], summaries[new_id] = cached
                reused += 1
                tag = "reuse"
            else:
                ranked = sorted(members[cid], key=lambda a: (-degree.get(a, 0.0), a))
                fact_texts = [facts[a] for a in ranked[: args.max_facts]]
                labels[new_id], summaries[new_id] = summarize_facts(
                    chat, fact_texts, fallback_label=f"Thema {new_id}")
                summarized += 1
                tag = "new"
            for aid in members[cid]:
                assignment[aid] = new_id
            print(f"  [{new_id}] {len(members[cid]):>3} facts — {labels[new_id]}  ({tag})", file=sys.stderr)

        result = graph.upsert_memory_concepts(assignment, summaries, labels)
        folded = decayed = None
        if not args.no_decay:
            folded = graph.fold_usage()
            decayed = graph.decay(half_life_days=args.half_life, floor=args.floor)
    finally:
        graph.close()

    print(f"Consolidated {result['assertions']} fact(s) into {result['concepts']} theme(s) "
          f"({summarized} summarized, {reused} reused) → {args.graph}")
    if decayed is not None:
        fold_note = f"folded {folded['records']} usage record(s); " if folded and folded["records"] else ""
        print(f"  {fold_note}decayed {decayed['edges']} usage edge(s) ({decayed['pruned']} pruned).")
    return 0


def _decay_embedder(args: argparse.Namespace):
    """Best-effort: the project's / ``--index`` embedder, so ``decay`` can also fold queued
    remember/reindex ops. Returns None if there's no index (decay still folds usage)."""
    idx = getattr(args, "index", None)
    if idx is None:
        project = getattr(args, "project_obj", None)
        idx = project.index_dir if project is not None else None
    if idx is None or not (Path(idx) / "index.json").is_file():
        return None
    try:
        index = SemanticIndex.load(idx)
        if isinstance(index.embedder, OllamaEmbedder):
            index.embedder.host = getattr(args, "host", None) or DEFAULT_HOST
            index.embedder.host = index.embedder.host.rstrip("/")
        return index.embedder
    except Exception:
        return None


def _cmd_decay(args: argparse.Namespace) -> int:
    """Maintenance pass over the usage-memory edges: first **fold in** any pending
    read-path usage + queued deferred writes (B1), then age each REINFORCES edge to now
    (persisting its decayed weight) and prune those below the floor."""
    graph = _open_graph(args.graph, writable=True, retries=6)
    if graph is None:
        print(f"error: no graph at {args.graph} (run `openwiki graph-build` first).", file=sys.stderr)
        return 2
    if not getattr(graph, "writable", False):
        print("error: graph is locked by another process (stop `serve`/`chat` first).", file=sys.stderr)
        graph.close()
        return 2
    ops = {"records": 0}
    try:
        folded = graph.fold_usage()
        embedder = _decay_embedder(args)
        if embedder is not None and graph.pending_ops():
            try:
                ops = graph.fold_journal(embedder)
            except Exception:
                pass
        result = graph.decay(half_life_days=args.half_life, floor=args.floor)
    finally:
        graph.close()
    if folded["records"]:
        print(f"Folded in {folded['records']} pending read-usage record(s) "
              f"({folded['reinforced']} reinforcement(s)).")
    if ops.get("records"):
        print(f"Folded in {ops['records']} queued op(s) "
              f"({ops.get('remembered', 0)} fact(s), {ops.get('reindexed', 0)} page re-sync(s)).")
    print(f"Decayed {result['edges']} reinforced edge(s): "
          f"{result['decayed']} kept, {result['pruned']} pruned "
          f"(half-life {args.half_life}d, floor {args.floor}) → {args.graph}")
    return 0


def _cmd_remember(args: argparse.Namespace) -> int:
    """Path B (B2/B3): capture a session transcript into the graph's remembered tier."""
    project = getattr(args, "project_obj", None)
    if project is not None and not project.memory_enabled:
        print(f"error: memory is disabled for project '{project.name}' (Wiki mode).\n"
              "  enable Second Brain mode: add  enabled = true  under [memory] in openwiki.toml.",
              file=sys.stderr)
        return 2
    if not (args.index / "index.json").is_file():
        print(f"error: no index at {args.index} (run `openwiki index` — needed for the embedder).",
              file=sys.stderr)
        return 2
    if not args.transcript.is_file():
        print(f"error: transcript not found: {args.transcript}", file=sys.stderr)
        return 2
    index = SemanticIndex.load(args.index)
    if isinstance(index.embedder, OllamaEmbedder):
        index.embedder.host = args.host.rstrip("/")
    session_id = args.session or args.transcript.stem
    sdate = args.session_date if args.session_date is not None else session_date_of(session_id)
    graph = _open_graph(args.graph, writable=True, retries=6)
    if graph is None:
        print(f"error: no graph at {args.graph} (run `openwiki graph-build` first).", file=sys.stderr)
        return 2
    try:
        chat = OllamaChat(model=args.model, host=args.host, temperature=0.2)
        print(f"Capturing session '{session_id}' with {chat.name} …", file=sys.stderr)
        facts = capture_session(chat, args.transcript.read_text(encoding="utf-8"), session_date=sdate)
        for f in facts:
            since = f"  (since {format_date(f.valid_from)})" if f.valid_from is not None else ""
            many = "  [many]" if f.cardinality == "many" else ""
            print(f"  · {f.subject} {f.predicate} {f.object}{since}{many}", file=sys.stderr)
        if not getattr(graph, "writable", False):
            # Locked by a running read-only serve/chat: queue to the journal instead of
            # failing — the next writable pass (serve/chat restart, `openwiki decay`, or the
            # next `remember`) folds it in. Writes are deferred, never lost.
            n = graph.queue_remember(session_id, facts, session_date=sdate, correct=args.correct)
            print(f"Graph busy (serve/chat running) — queued {n} fact(s) for '{session_id}' to the "
                  f"journal; they'll be folded on the next writable pass → {args.graph}")
            return 0
        coexist = _coexist_check(args.model, args.host)
        resolve = _attribute_resolver(args.model, args.host)
        result = graph.remember(session_id, facts, index.embedder, session_date=sdate,
                                correct=args.correct, coexist=coexist, resolve=resolve)
        try:
            graph.fold_journal(index.embedder, coexist=coexist, resolve=resolve)   # drain queued ops
        except Exception:
            pass
    finally:
        graph.close()
    sup = f", {result['superseded']} superseded" if result.get("superseded") else ""
    if result.get("retracted"):
        sup += f" ({result['retracted']} retracted)"
    hist = f", {result['historical']} historical" if result.get("historical") else ""
    if result.get("resolved"):
        hist += f", {result['resolved']} matched to an existing attribute"
    when = f" [session {format_date(sdate)}]" if sdate is not None else ""
    print(f"Remembered '{session_id}'{when}: {result['added']} new, {result['duplicates']} duplicate"
          f"{sup}{hist} ({result['facts']} captured) → {args.graph}")
    print('  now try:  openwiki recall "<a question>"')
    return 0


def _cmd_backfill(args: argparse.Namespace) -> int:
    """Path B / B7: backfill the memory tier from Claude Code transcripts — each UTC day becomes
    one dated session (``<prefix>YYYY-MM-DD``), cut into bounded capture windows, so the
    valid-time merge orders facts by when they happened (a later day's change closes an earlier
    value). Host-injected blocks + compaction summaries are skipped. Needs a writable graph."""
    from .claude_code_template import split_transcripts_by_window

    project = getattr(args, "project_obj", None)
    if project is not None and not project.memory_enabled:
        print(f"error: memory is disabled for project '{project.name}' (Wiki mode).", file=sys.stderr)
        return 2
    files: list = []
    for spec in args.transcripts:
        spec = Path(spec).expanduser()
        files.extend(sorted(spec.glob("*.jsonl")) if spec.is_dir() else [spec])
    files = [f for f in files if f.is_file()]
    if not files:
        print("error: no Claude Code transcript (.jsonl) found.", file=sys.stderr)
        return 2
    days = split_transcripts_by_window([f.read_text(encoding="utf-8", errors="ignore") for f in files],
                                       max_chars=max(2000, int(args.max_chars)))
    lo = format_date(args.since) if args.since is not None else ""
    hi = format_date(args.until) if args.until is not None else "9999"
    days = [(d, w) for d, w in days if lo <= d <= hi]
    n_windows = sum(len(w) for _, w in days)
    print(f"Backfill: {len(files)} transcript(s) → {len(days)} day(s), {n_windows} capture window(s)",
          file=sys.stderr)
    if args.dry_run or not days:
        for day, windows in days:
            print(f"  {args.prefix}{day}: {len(windows)} window(s), "
                  f"{sum(len(w) for _, w in windows) // 1000}k chars")
        return 0
    if not (args.index / "index.json").is_file():
        print(f"error: no index at {args.index} (run `openwiki build` — needed for the embedder).",
              file=sys.stderr)
        return 2
    index = SemanticIndex.load(args.index)
    if isinstance(index.embedder, OllamaEmbedder):
        index.embedder.host = args.host.rstrip("/")
    graph = _open_graph(args.graph, writable=True, retries=6)
    if graph is None or not getattr(graph, "writable", False):
        print("error: could not open the graph writable (stop `serve`/`chat` first).", file=sys.stderr)
        if graph is not None:
            graph.close()
        return 2
    chat = _capture_chat(args.model, args.host)
    coexist = _coexist_check(args.model, args.host)
    resolve = _attribute_resolver(args.model, args.host)
    totals = {"facts": 0, "added": 0, "duplicates": 0, "superseded": 0, "retracted": 0,
              "historical": 0, "resolved": 0}
    failed: list = []
    skipped = 0
    started = time.time()
    try:
        done = set() if args.redo else {r[0] for r in graph._rows(
            "MATCH (s:Session)-[:ASSERTS]->(:Assertion) RETURN DISTINCT s.id;")}
        for i, (day, windows) in enumerate(days, 1):
            sid, sdate = f"{args.prefix}{day}", parse_date(day)
            if sid in done:                     # resume: this day is already in memory
                skipped += 1
                continue
            for j, (start, window) in enumerate(windows, 1):
                # valid from the window's first turn (intra-day order), else the day
                wdate = parse_date(start[:19]) or sdate
                try:                            # one bad window (timeout, garbage) never aborts the run
                    facts = capture_session(chat, window, session_date=wdate)
                    res = graph.remember(sid, facts, index.embedder, session_date=wdate,
                                         coexist=coexist, resolve=resolve)
                except Exception as exc:
                    failed.append(f"{sid}#{j}")
                    print(f"  ! {sid} window {j}/{len(windows)} failed: {exc}", file=sys.stderr, flush=True)
                    continue
                for key in totals:
                    totals[key] += res.get(key, 0)
            print(f"  [{i}/{len(days)}] {sid}: {len(windows)} window(s) · {totals['added']} new / "
                  f"{totals['duplicates']} dup / {totals['resolved']} resolved / "
                  f"{totals['superseded']} superseded so far "
                  f"({(time.time() - started) / 60:.1f} min)", file=sys.stderr, flush=True)
    finally:
        graph.close()
    note = f", {skipped} day(s) already in memory skipped" if skipped else ""
    print(f"Backfilled {len(days) - skipped} day(s): {totals['added']} new fact(s), {totals['duplicates']} "
          f"re-affirmed, {totals['resolved']} matched to an existing attribute, "
          f"{totals['superseded']} superseded ({totals['retracted']} retracted), "
          f"{totals['facts']} captured{note} → {args.graph}")
    if failed:
        print(f"  {len(failed)} window(s) failed ({', '.join(failed[:8])}{' …' if len(failed) > 8 else ''}) "
              f"— re-run with --since <day> --redo to retry.", file=sys.stderr)
    return 0


def _cmd_recall(args: argparse.Namespace) -> int:
    """Path B (B6): show the remembered facts most relevant to a query."""
    project = getattr(args, "project_obj", None)
    if project is not None and not project.memory_enabled:
        print("(memory is disabled — Wiki mode; set [memory] enabled = true to enable recall)")
        return 0
    if not (args.index / "index.json").is_file():
        print(f"error: no index at {args.index} (run `openwiki index` — needed for the embedder).",
              file=sys.stderr)
        return 2
    index = SemanticIndex.load(args.index)
    if isinstance(index.embedder, OllamaEmbedder):
        index.embedder.host = args.host.rstrip("/")
    graph = _open_graph(args.graph, writable=False)
    if graph is None:
        print(f"error: no graph at {args.graph}.", file=sys.stderr)
        return 2
    as_of, known_at = getattr(args, "as_of", None), getattr(args, "known_at", None)
    timeline = getattr(args, "timeline", False)
    try:
        if timeline:
            hits = graph.timeline(args.query, index.embedder, groups=min(args.top_k, 3))
        else:
            hits = graph.recall(args.query, index.embedder, k=args.top_k,
                                include_superseded=getattr(args, "include_superseded", False),
                                as_of=as_of, known_at=known_at)
    finally:
        graph.close()
    if not hits:
        print("(no relevant memory — capture sessions with `openwiki remember` first)")
        return 0
    if timeline:
        print(_format_timeline(hits))
        return 0
    view = ([f"true as of {format_date(as_of)}"] if as_of is not None else []) + \
           ([f"as believed on {format_date(known_at)}"] if known_at is not None else [])
    if view:
        print(f"[{' · '.join(view)}]")
    print(format_memory(hits))
    print("\nscores:", file=sys.stderr)
    for h in hits:
        mark = "" if h.get("in_view", True) else f"  ⊘{h.get('status', 'superseded')}"
        print(f"  {h['score']:.3f} (cos {h['cos']:.3f})  {h['subject']} {h['predicate']} "
              f"{h['object']}  [{h['session_id']}]{mark}", file=sys.stderr)
    return 0


_TIMELINE_MARK = {"current": "●", "past": "○", "future": "◌", "retracted": "✗"}


def _format_timeline(groups: list) -> str:
    """B7 ``recall --timeline``: each subject+predicate's history, oldest valid time first —
    the interval, the value, when it was recorded (and retracted), and which session."""
    out = []
    for g in groups:
        out.append(f"{g['subject']} {g['predicate']} …   (match {g['cos']:.2f})")
        for r in g["records"]:
            when = format_interval(r["valid_from"], r["valid_to"]) or "?"
            rec = f"recorded {format_date(r['created_at'])}"
            if r.get("expired_at") is not None:
                rec += f", retracted {format_date(r['expired_at'])}"
            out.append(f"  {_TIMELINE_MARK.get(r['status'], '?')} {when:<26} {r['object']}"
                       f"   ({rec}; {r['session_id']})")
        out.append("")
    out.append("● current  ○ past  ◌ planned  ✗ retracted")
    return "\n".join(out)


def _cmd_context(args: argparse.Namespace) -> int:
    """Path B (B6): assemble a session's memory context for a query — identity (DNA) +
    decay-weighted recall (activation) + relevant consolidated themes (attractors)."""
    project = getattr(args, "project_obj", None)
    if project is not None and not project.memory_enabled:
        print("(memory is disabled — Wiki mode; set [memory] enabled = true to assemble context)")
        return 0
    if not (args.index / "index.json").is_file():
        print(f"error: no index at {args.index} (run `openwiki index` — needed for the embedder).",
              file=sys.stderr)
        return 2
    index = SemanticIndex.load(args.index)
    if isinstance(index.embedder, OllamaEmbedder):
        index.embedder.host = args.host.rstrip("/")
    graph = _open_graph(args.graph, writable=False)
    if graph is None:
        print(f"error: no graph at {args.graph}.", file=sys.stderr)
        return 2
    identity = args.identity if args.identity is not None else (project.identity if project else "")
    # default the budget to the project's; --max-chars overrides; 0 means unbounded
    if args.max_chars is not None:
        max_chars = None if args.max_chars <= 0 else args.max_chars
    else:
        max_chars = project.context_budget if project else None
    try:
        context = graph.context_for(args.query, index.embedder, identity=identity,
                                    k=args.top_k, max_themes=args.themes, max_chars=max_chars,
                                    as_of=getattr(args, "as_of", None))
    finally:
        graph.close()
    if not context.strip():
        print("(no memory to assemble — capture sessions with `openwiki remember` "
              "and `openwiki consolidate` first)")
        return 0
    print(context)
    return 0


_KIND_LABEL = {
    "similar": "SIMILAR_TO", "references": "REFERENCES", "shared_entity": "shared-entity",
    "relation": "RELATED_TO", "child_of": "CHILD_OF", "next": "NEXT",
}


def _print_coupling_report(res: dict) -> None:
    """Human-readable rendering of the graph↔semantic coupling fingerprint."""
    prof = res["edge_profile"]
    overlap = res["neighbor_overlap"]
    null = prof.get("_null", {})
    print(f"\nWorld-model coupling · {res['pages']} pages "
          f"· random-pair cosine ≈ {null.get('mean', 0):.3f}\n")
    print(f"  {'edge type':<14}{'n':>6}{'cos(mean)':>11}{'median':>9}"
          f"{'vs null':>9}{'kNN overlap':>13}")
    print(f"  {'-' * 61}")
    for kind in ("similar", "references", "shared_entity", "relation", "child_of", "next"):
        p = prof.get(kind, {})
        n = p.get("n", 0)
        ov = overlap.get(kind)
        ov_s = "—" if ov is None else f"{ov:.2f}"
        if not n:
            print(f"  {_KIND_LABEL[kind]:<14}{0:>6}{'—':>11}{'—':>9}{'—':>9}{ov_s:>13}")
            continue
        print(f"  {_KIND_LABEL[kind]:<14}{n:>6}{p['mean']:>11.3f}{p['median']:>9.3f}"
              f"{p['lift']:>+9.3f}{ov_s:>13}")

    coh = res["community_coherence"]
    print()
    if coh.get("available"):
        print(f"  Community coherence: silhouette {coh['silhouette']:+.3f} · "
              f"ARI vs k-means {coh['ari']:+.3f}  ({coh['communities']} communities)")
    else:
        print(f"  Community coherence: n/a ({coh.get('reason', 'unavailable')})")

    reach = res["graph_reach"]
    frac = reach.get("non_semantic_fraction")
    print("\n  ── Headline: graph reach ──")
    if frac is None:
        print("  No non-similarity edges (references/shared-entity/relations) to measure —")
        print("  build entities/relations (`graph-build --relations`) for the reach metric.")
    else:
        print(f"  {frac:.0%} of the graph's {reach['pairs']} non-similarity connections link pages")
        print(f"  the embedder would NOT rank as neighbors (cosine ≤ the random-pair median "
              f"{reach['null_median']:.3f}).")
        print(f"  → that much of the graph's structure is reach semantic similarity alone misses.")
    print()


def _trunc(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def _print_gaps_report(res: dict) -> None:
    """Human-readable rendering of the gap/hygiene mining — an actionable to-do list."""
    links = res["link_candidates"]
    print("\n── Missing cross-references ── (co-mention entities, but neither cites the other)")
    if not links:
        print("  none — every entity-sharing page pair is already connected (or no entities).")
    for c in links:
        print(f"  {c['shared_entities']:>2} shared · cos {c['cosine']:.2f}  "
              f"{_trunc(c['a_title'], 32):<32} ↔ {_trunc(c['b_title'], 32)}")

    red = res["redundant_pages"]
    print("\n── Near-duplicate pages ── (very high embedding similarity)")
    if not red:
        print("  none above the redundancy threshold.")
    for c in red:
        print(f"  cos {c['cosine']:.3f}  {_trunc(c['a_title'], 32):<32} ↔ {_trunc(c['b_title'], 32)}")

    iso = res["isolated_pages"]
    print("\n── Isolated pages ──")
    print("  semantic outliers (nearest neighbor is far):")
    for p in iso["semantic_outliers"]:
        print(f"    nn-cos {p['nn_cosine']:.3f}  {_trunc(p['title'], 48)}")
    orphans = iso["structural_orphans"]
    if orphans:
        print("  structural orphans (no similar / reference / entity edge):")
        for o in orphans:
            print(f"    {_trunc(o.get('title', o.get('slug', '?')), 48)}")

    ents = res["entity_merge_candidates"]
    print("\n── Entity-merge candidates ── (same type, near-duplicate names, not resolved)")
    if not ents:
        print("  none — run `graph-build --resolve-entities`, or the graph has no entities.")
    for e in ents:
        print(f"  sim {e['similarity']:.2f}  [{_trunc(e['type'], 14):<14}]  "
              f"{_trunc(e['a'], 26):<26} ≈ {_trunc(e['b'], 26)}")
    print()


def _fmt_num(v, signed: bool = False) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return (f"{v:+.3f}" if signed else f"{v:.3f}")
    return (f"{v:+d}" if signed and isinstance(v, int) else str(v))


def _print_compare_report(rows: list, label_a: str, label_b: str) -> None:
    """Side-by-side world-model fingerprint diff (A = current, B = the --compare target)."""
    print(f"\nWorld-model comparison\n  A = {label_a}\n  B = {label_b}\n")
    print(f"  {'metric':<20}{'A':>10}{'B':>10}{'Δ (B−A)':>12}")
    print("  " + "-" * 52)
    for r in rows:
        print(f"  {r['metric']:<20}{_fmt_num(r['a']):>10}{_fmt_num(r['b']):>10}"
              f"{_fmt_num(r['delta'], signed=True):>12}")
    notable = notable_differences(rows, top=3)
    if notable:
        print("\n  Notable differences:")
        for r in notable:
            direction = "higher" if r["delta"] > 0 else "lower"
            print(f"    B's {r['metric']} is {direction} by {abs(r['delta']):.3f}")
    else:
        print("\n  No rate differences — the two world models are structurally equivalent.")
    print()


def _resolve_compare_fingerprint(path: str, k: int):
    """Load the fingerprint to compare against: a saved `analyze --json` file, or compute it
    live from a project dir / an output dir (index/ + graph/). Returns ``(fingerprint, label)``."""
    p = Path(path)
    if p.is_file():
        fp = json.loads(p.read_text(encoding="utf-8"))
        return fp, p.name
    if p.is_dir():
        if (p / "openwiki.toml").is_file():
            proj = Project.load(p)
            index_dir, graph_dir = proj.index_dir, proj.graph_path
        elif (p / "index" / "index.json").is_file():
            index_dir, graph_dir = p / "index", p / "graph"
        else:
            raise FileNotFoundError(
                f"{p} is not a project (openwiki.toml) or an output dir (index/ + graph/).")
        if not (index_dir / "index.json").is_file():
            raise FileNotFoundError(f"no index at {index_dir}.")
        other = SemanticIndex.load(index_dir)
        g = _open_graph(graph_dir, writable=False)
        if g is None:
            raise FileNotFoundError(f"no graph at {graph_dir}.")
        try:
            return analyze_coupling(other, g, k=k), str(p)
        finally:
            g.close()
    raise FileNotFoundError(f"nothing to compare at {path}.")


def _print_memory_report(res: dict) -> None:
    """Human-readable rendering of the Path B memory-tier dynamics."""
    c = res["counts"]
    print(f"\nMemory-tier dynamics · {c['sessions']} sessions · {c['current']} current facts "
          f"({c['superseded']} superseded) · {c['themes']} themes\n")

    rev = res["revision"]
    print(f"  Belief revision:  {rev['revision_rate']:.0%} of all facts have been superseded "
          f"({rev['superseded']} overwritten: {rev.get('world_changes', rev['superseded'])} world "
          f"change(s), {rev.get('corrections', 0)} correction(s))"
          + (f" · {rev['planned']} planned" if rev.get("planned") else ""))

    con = res["consolidation"]
    ts = con["theme_sizes"]
    print(f"  Consolidation:    {con['coverage']:.0%} of current facts folded into {con['themes']} "
          f"themes · avg {con['avg_theme_size']} facts/theme (sizes {ts['min']}–{ts['max']})")

    t = res["temperature"]
    print(f"  Temperature:      {t['hot']} hot / {t['warm']} warm / {t['cold']} cold "
          f"(half-life {t['half_life_days']:g}d) · mean eff-weight {t['mean_effective_weight']:.2f}")
    print(f"                    mean confidence {t['mean_confidence']:.2f} · "
          f"{t['reaffirmed_fraction']:.0%} re-affirmed (>1)")

    b = res["breadth"]
    tops = ", ".join(f"{p['predicate']}×{p['count']}" for p in b["top_predicates"][:5])
    print(f"  Breadth:          {b['distinct_subjects']} subjects · {b['distinct_predicates']} "
          f"predicates{('  ·  top: ' + tops) if tops else ''}")

    growth = res["growth"]
    if growth:
        print("\n  Growth (facts per session, oldest first):")
        peak = max(g["facts"] for g in growth) or 1
        for g in growth:
            bar = "█" * max(1, round(20 * g["facts"] / peak))
            print(f"    {_trunc(g['session_id'], 22):<22} {bar} {g['facts']}")
    print()


def _cmd_analyze(args: argparse.Namespace) -> int:
    """World-model analysis: **coupling** (P1 — where the graph agrees with vs. adds to the
    embedding space), **gaps** (P3 — ranked improvement candidates), a coupling **--compare**
    diff (P3b), or **memory** (P4 — Path B memory-tier dynamics). Read-only + offline."""
    if args.what != "coupling" and args.compare:
        print("error: --compare works with coupling only.", file=sys.stderr)
        return 2

    if args.what == "memory":            # graph-only (no embeddings needed)
        graph = _open_graph(args.graph, writable=False)
        if graph is None:
            print(f"error: no graph at {args.graph} (build it with `openwiki graph-build`).",
                  file=sys.stderr)
            return 2
        try:
            res = analyze_memory(graph)
        finally:
            graph.close()
        if not res.get("available"):
            print("(no memory tier — Second Brain mode with `remember`ed sessions populates it)")
            return 0
        if args.as_json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            _print_memory_report(res)
        return 0

    if not (args.index / "index.json").is_file():
        print(f"error: no index at {args.index} (run `openwiki index` first).", file=sys.stderr)
        return 2
    graph = _open_graph(args.graph, writable=False)
    if graph is None:
        print(f"error: no graph at {args.graph} (build it with `openwiki graph-build`).", file=sys.stderr)
        return 2
    try:
        index = SemanticIndex.load(args.index)
        if args.what == "gaps":
            res = analyze_gaps(index, graph, top=args.top)
        else:
            res = analyze_coupling(index, graph, k=args.k)
    finally:
        graph.close()

    if args.compare:                     # coupling-only (guarded above)
        try:
            other, label_b = _resolve_compare_fingerprint(args.compare, args.k)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if not is_coupling_fingerprint(other):
            print(f"error: {args.compare} is not a coupling fingerprint "
                  "(save one with `analyze --json`).", file=sys.stderr)
            return 2
        rows = diff_fingerprints(res, other)
        if args.as_json:
            print(json.dumps({"a": res, "b": other, "diff": rows}, ensure_ascii=False, indent=2))
        else:
            _print_compare_report(rows, str(args.index), label_b)
        return 0

    if args.as_json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.what == "gaps":
        _print_gaps_report(res)
    else:
        _print_coupling_report(res)
    return 0


def _cmd_hook(args: argparse.Namespace) -> int:
    """Host-lifecycle memory hook (B6): reads the Claude Code event JSON on stdin and either
    **injects** recalled memory (UserPromptSubmit → stdout) or **captures** the session
    (SessionEnd/PreCompact → remember). **Always exits 0** — a hook must never block the
    session (exit 2 on UserPromptSubmit would reject the prompt). Fail-soft throughout."""
    try:
        job = getattr(args, "payload", None)
        if job is not None:                        # the detached capture worker (see _spawn_capture)
            payload = json.loads(Path(job).read_text(encoding="utf-8"))
            Path(job).unlink(missing_ok=True)
            payload["_worker"] = True
        else:
            raw = sys.stdin.read()
            payload = json.loads(raw) if raw.strip() else {}
        if isinstance(payload, dict):
            _run_hook(args.event, payload, getattr(args, "project", None))
    except Exception as exc:      # fail-soft: stderr goes to the host's debug log only
        print(f"openwiki hook '{getattr(args, 'event', '?')}': {exc}", file=sys.stderr)
    return 0


def _run_hook(event: str, payload: dict, project_dir=None) -> None:
    # an explicit binding wins; else discover from the session's cwd — deliberately *not* the
    # registry's active project (that would feed every Claude Code session into one memory)
    project = Project.load(project_dir) if project_dir else Project.find(payload.get("cwd") or None)
    if project is None or not project.memory_enabled:
        return   # no project / Wiki mode → nothing to inject or capture
    if event == "inject":
        _hook_inject(project, payload)
    elif event == "capture":
        _hook_capture(project, payload)


def _hook_embedder(project: Project):
    """Load the project's index embedder with its host set (or None if no index)."""
    if not (project.index_dir / "index.json").is_file():
        return None
    index = SemanticIndex.load(project.index_dir)
    if isinstance(index.embedder, OllamaEmbedder):
        index.embedder.host = project.setting("models", "host", DEFAULT_HOST).rstrip("/")
    return index.embedder


def _hook_inject(project: Project, payload: dict) -> None:
    """UserPromptSubmit → assemble the three-tier memory context for the prompt and print
    it (Claude Code adds a hook's stdout to the prompt context)."""
    prompt = str(payload.get("prompt") or "").strip()
    embedder = _hook_embedder(project) if prompt else None
    if not embedder or not project.graph_path.exists():
        return
    graph = GraphStore(project.graph_path)   # read-only
    try:
        context = graph.context_for(prompt, embedder, identity=project.identity,
                                    max_chars=project.context_budget)
    finally:
        graph.close()
    if context.strip():
        sys.stdout.write(
            "Relevant memory from earlier sessions (OpenWiki Second Brain) — use if helpful; "
            "this is not the user's current message:\n\n" + context + "\n")


def _capture_chat(model, host) -> OllamaChat:
    """The chat model for session capture (hook worker + backfill): a generous timeout — a
    20k-char window takes ~1 min on a local 30B — and an output cap, so a sampling repetition
    loop ends in bounded time instead of running into the timeout."""
    return OllamaChat(model=model, host=host, temperature=0.2, timeout=900.0,
                      options={"num_predict": 4096})


def _spawn_capture(project: Project, payload: dict) -> bool:
    """Hand the capture to a **detached** worker process and return at once: a capture is one
    ~1-min LLM call, longer than a host hook may run (SessionEnd/PreCompact are killed after their
    timeout). The event is parked under the project's ``.openwiki/``; the worker logs to
    ``.openwiki/hook.log``. Returns ``False`` if the spawn failed (→ capture inline instead)."""
    import subprocess
    try:
        state = project.state_dir
        state.mkdir(parents=True, exist_ok=True)
        job = state / f"capture-{os.getpid()}-{int(time.time() * 1000)}.json"
        job.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        cmd = [sys.executable, "-m", "openwiki", "hook", "capture",
               "--project", str(project.root), "--payload", str(job)]
        log = (state / "hook.log").open("a", encoding="utf-8")
        kw = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": log, "close_fds": True}
        if os.name == "nt":
            kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        subprocess.Popen(cmd, **kw)
        return True
    except Exception as exc:       # never let the hook fail — fall back to inline capture
        print(f"openwiki hook: could not spawn capture worker ({exc}); capturing inline",
              file=sys.stderr)
        return False


def _hook_capture(project: Project, payload: dict) -> None:
    """SessionEnd/PreCompact → capture the transcript into the remembered tier (best-effort).
    The hook itself only **spawns a detached worker** and returns (the LLM call outlives a hook's
    timeout); the worker captures *first* and only then opens the graph writable (so the exclusive
    Kuzu lock is held for the short write, not the ~1-min LLM call). If the graph is locked by a
    running serve/chat, the facts are **queued** to the write-ahead journal instead of dropped."""
    from .claude_code_template import parse_claude_transcript

    if not payload.get("_worker") and _spawn_capture(project, payload):
        return
    tpath = payload.get("transcript_path")
    embedder = _hook_embedder(project)
    if not tpath or not Path(tpath).is_file() or embedder is None or not project.graph_path.exists():
        return
    transcript = parse_claude_transcript(Path(tpath).read_text(encoding="utf-8", errors="ignore"))
    if not transcript.strip():
        return
    model = project.setting("models", "chat", DEFAULT_CHAT)
    host = project.setting("models", "host", DEFAULT_HOST)
    # the session ends now → today is the date relative mentions ("since yesterday") resolve against
    facts = capture_session(_capture_chat(model, host), transcript, session_date=int(time.time()))
    if not facts:
        return
    sid = str(payload.get("session_id") or "session")
    graph = _open_graph(project.graph_path, writable=True, retries=6)
    if graph is None:
        return
    try:
        if getattr(graph, "writable", False):
            coexist = _coexist_check(model, host)
            resolve = _attribute_resolver(model, host)
            graph.remember(sid, facts, embedder, coexist=coexist, resolve=resolve)
            try:
                graph.fold_journal(embedder, coexist=coexist, resolve=resolve)
            except Exception:
                pass
        else:
            graph.queue_remember(sid, facts)   # locked → queue; a later writer folds it in
    finally:
        graph.close()


def _fold_pending_usage(graph) -> None:
    """B1: when a writable process starts, fold any read-path usage logged since the
    last writer into the graph (best-effort). No-op on a read-only/None graph or empty log."""
    if graph is None or not getattr(graph, "writable", False):
        return
    try:
        folded = graph.fold_usage()
        if folded["records"]:
            print(f"(folded in {folded['records']} pending read-usage record(s) → "
                  f"{folded['reinforced']} reinforcement(s))", file=sys.stderr)
    except Exception:
        pass


def _cmd_serve(args: argparse.Namespace) -> int:
    index = None
    if (args.index / "index.json").is_file():
        index = SemanticIndex.load(args.index)
        if isinstance(index.embedder, OllamaEmbedder):
            index.embedder.host = args.host.rstrip("/")
    embedder = index.embedder if index else None
    project = getattr(args, "project_obj", None)
    mem = bool(project is not None and project.memory_enabled)

    # Default: open the graph READ-ONLY so other processes (ask / MCP / recall / context,
    # a second reader) run concurrently while serving — Kuzu is reader-XOR-writer, so a
    # writable serve blocks them all. Agent edits still write page files; their graph
    # re-sync is deferred to the write-ahead journal, folded at start & shutdown. --sync
    # restores the old exclusive-writable mode (live graph sync, but blocks other access).
    sync = getattr(args, "sync", False) and index is not None and not args.dry_run
    if sync:
        graph = _open_graph(args.graph, writable=True, retries=6)
        _fold_pending_usage(graph)
        if graph is not None and getattr(graph, "writable", False) and embedder is not None:
            try:
                graph.fold_journal(embedder)
            except Exception:
                pass
    else:
        _transient_fold(args.graph, embedder)      # absorb pending deferred writes first
        graph = _open_graph(args.graph, writable=False)
        if graph is not None and mem:
            graph.log_usage = True                  # read-path reinforcement → journal

    tools = WikiTools(args.wiki, index=index, graph=graph, embedder=embedder, dry_run=args.dry_run)
    chat = OllamaChat(model=args.model, host=args.host, temperature=args.temperature)
    agent = WikiAgent(chat, tools, wiki_summary=summarize_wiki(args.wiki))
    app = WikiWebApp(args.wiki, index=index, agent=agent, tools=tools, graph=graph,
                     project=getattr(args, "project_obj", None))

    graph_feat = ("graph+sync" if graph and getattr(graph, "writable", False) else
                  ("graph (read-only, concurrent)" if graph else None))
    features = ["search" if index else None, "chat", graph_feat]
    print(f"Serving wiki '{args.wiki}' — {', '.join(f for f in features if f)}.", file=sys.stderr)
    try:
        serve(app, host=args.bind, port=args.port)
    finally:
        if graph is not None:
            graph.close()
        if not sync:
            _transient_fold(args.graph, embedder)   # fold what accumulated this session
    return 0


def _cmd_mcp(args: argparse.Namespace) -> int:
    from . import __version__

    index = None
    if (args.index / "index.json").is_file():
        index = SemanticIndex.load(args.index)
        if isinstance(index.embedder, OllamaEmbedder):
            index.embedder.host = args.host.rstrip("/")
    graph = None
    if args.graph.exists():
        try:
            graph = GraphStore(args.graph)   # read-only: coding agents only read
            # B1: log read-path usage in Second Brain mode (folded in on the next writer).
            project = getattr(args, "project_obj", None)
            graph.log_usage = bool(project is not None and project.memory_enabled)
        except Exception as exc:
            print(f"(graph not loaded: {exc})", file=sys.stderr)

    agent = None
    if index is not None and not args.no_ask:
        chat = OllamaChat(model=args.model, host=args.host, temperature=args.temperature)
        agent = RAGAgent(index, chat, graph=graph)

    project = getattr(args, "project_obj", None)
    identity = project.identity if (project is not None and project.memory_enabled) else ""
    budget = project.context_budget if (project is not None and project.memory_enabled) else None
    server = build_server(args.wiki, index=index, graph=graph, agent=agent,
                          version=__version__, identity=identity, context_budget=budget)
    server.serve()   # blocks on stdio (JSON-RPC)
    return 0


_DISPATCH = {
    "build": _cmd_build,
    "status": _cmd_status,
    "project": _cmd_project,
    "opencode": _cmd_opencode,
    "claude-code": _cmd_claude_code,
    "ontology": _cmd_ontology,
    "ingest": _cmd_ingest,
    "build-wiki": _cmd_build_wiki,
    "index": _cmd_index,
    "search": _cmd_search,
    "eval": _cmd_eval,
    "ask": _cmd_ask,
    "chat": _cmd_chat,
    "serve": _cmd_serve,
    "mcp": _cmd_mcp,
    "graph-build": _cmd_graph_build,
    "references": _cmd_references,
    "backfill": _cmd_backfill,
    "communities": _cmd_communities,
    "decay": _cmd_decay,
    "consolidate": _cmd_consolidate,
    "remember": _cmd_remember,
    "recall": _cmd_recall,
    "context": _cmd_context,
    "analyze": _cmd_analyze,
    "hook": _cmd_hook,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    # Ensure non-ASCII output (German umlauts, ·, –) prints correctly on Windows,
    # where stdout may otherwise default to a non-UTF-8 code page.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    args = _build_argparser().parse_args(argv)

    if args.command == "init":
        return _cmd_init(args)

    # Resolve the active project (location-first, then registry) and fill unset
    # defaults from its manifest and the user-global config.
    try:
        project = _resolve_project(getattr(args, "project", None))
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if project is not None and args.command != "project":
        print(f"[openwiki] project '{project.name}'  ({project.root})", file=sys.stderr)
    args.project_obj = project
    _apply_project(args, project, UserConfig.load())

    handler = _DISPATCH.get(args.command)
    return handler(args) if handler else 1


if __name__ == "__main__":
    sys.exit(main())
