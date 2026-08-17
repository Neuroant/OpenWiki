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
from pathlib import Path
from typing import Optional, Sequence

from .agent import RAGAgent
from .eval import evaluate, load_eval_set, make_retrievers
from .chat_agent import WikiAgent, summarize_wiki
from .claude_code_template import scaffold_claude_code
from .opencode_template import scaffold_opencode
from .graph import (
    GraphStore, build_graph, detect_page_offset, extract_entities,
    extract_references, extract_references_multi,
)
from .embeddings import OllamaEmbedder
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

    build_p = sub.add_parser("build", parents=[common],
                             help="Run the pipeline (ingest → wiki → index → graph) from the manifest.")
    build_p.add_argument("--only", default=None, metavar="STAGES",
                         help="Comma-separated stages to run (ingest,wiki,index,graph).")
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
                        help="JSONL of {\"question\", \"pages\"} lines (default: <project>/eval.jsonl).")
    eval_p.add_argument("-i", "--index", type=Path, default=None, help="Index dir (default: project's).")
    eval_p.add_argument("--graph", type=Path, default=None,
                        help="Graph dir; enables the GraphRAG column (default: project's graph).")
    eval_p.add_argument("--top-k", type=int, default=5, help="Semantic seed pages (default: 5).")
    eval_p.add_argument("--expand-k", type=int, default=3,
                        help="Graph-expanded pages added for GraphRAG (default: 3).")
    eval_p.add_argument("--no-graph", action="store_true", help="Skip the GraphRAG column.")
    eval_p.add_argument("--misses", action="store_true", help="List questions with no expected page in the top-k.")
    eval_p.add_argument("--answers", action="store_true",
                        help="Also generate RAG & GraphRAG answers and score citation grounding (slow).")
    eval_p.add_argument("--judge", action="store_true",
                        help="With --answers, an LLM judge picks the better answer per question (slower).")
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
    ask_p.add_argument(
        "--model", default=None,
        help="Ollama chat model (default: manifest models.chat, else qwen3:30b-a3b-instruct-2507-q4_K_M).",
    )
    ask_p.add_argument("--host", default=None, help="Ollama host URL.")
    ask_p.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature (default: 0.2).")
    ask_p.add_argument("--show-context", action="store_true", help="Also print the retrieved excerpts.")

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
    graph_p.add_argument("--entity-model", default=None,
                         help="Ollama model for entity extraction.")
    graph_p.add_argument("--entity-types", default=None, metavar="LIST",
                         help="Comma-separated entity types for --entities (e.g. "
                              "'Concept,Method,Component'); overrides the default ontology.")
    graph_p.add_argument("--entity-max-chars", type=int, default=None,
                         help="Chars of each page sent to the entity model (default: 8000).")
    graph_p.add_argument("--host", default=None, help="Ollama host URL (for --entities).")
    graph_p.add_argument("-v", "--verbose", action="store_true", help="Verbose progress logging.")
    return parser


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


def _resolve_source_specs(raw_sources, sources_dir: Path, repo: bool = False) -> "list[dict]":
    """Turn raw ``--source`` args into ``[{type, path}]`` manifest specs. **URLs**
    (and, with ``repo=True``, **directories**) are referenced in place; other local
    files/globs/scan-dirs are expanded and copied into ``sources_dir``."""
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
        specs = _resolve_source_specs(args.source or [], sources_dir, repo=getattr(args, "repo", False))
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
    manifest.write_text(render_manifest(name=name, sources=sources), encoding="utf-8")

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
    print(f"Scaffolding Claude Code config into project '{project.name}' ({project.root})")
    written, skipped = scaffold_claude_code(
        project.root, chat_model=chat, embed_model=embed, mcp_command=command, force=args.force)
    for path in written:
        print(f"  wrote    {path.relative_to(project.root)}")
    for path in skipped:
        print(f"  skipped  {path.relative_to(project.root)}  (exists; --force to overwrite)")
    print(f"  MCP server 'openwiki' via `{' '.join(command)}`")
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
            specs = _resolve_source_specs([args.path], sources_dir, repo=getattr(args, "repo", False))
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


def _corpus_references(project, sources, doc, wiki, multi):
    """Cross-reference edges for the corpus: single-source direct, else per-source
    (each resolved within its own page span via the retained per-source IR)."""
    if not multi:
        return extract_references(doc, wiki)
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
    return extract_references_multi(doc, wiki, metas)


def _cmd_build(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO if getattr(args, "verbose", False) else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    project: Optional[Project] = getattr(args, "project_obj", None)
    if project is None:
        print("error: not in an OpenWiki project — run `openwiki init` first.", file=sys.stderr)
        return 2

    sources = project.source_paths()
    if not sources:
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
        state.record("ingest", fps["ingest"], parsed_path,
                     {"pages": len(doc.pages), "sources": len(sources)})
        state.save()
        print(f"  ingest → {len(sources)} source(s), {len(doc.pages)} page(s) → {parsed_path}",
              file=sys.stderr)

    if "wiki" in todo:
        wiki = WikiBuilder(split_level=split).build(_doc())
        write_wiki(wiki, project.wiki_dir, include_tables=tables)
        state.record("wiki", fps["wiki"], project.wiki_dir, {"pages": len(wiki.pages)})
        state.save()
        print(f"  wiki → {len(wiki.pages)} page(s) → {project.wiki_dir}", file=sys.stderr)

    index = None
    if "index" in todo:
        embedder = OllamaEmbedder(model=models.get("embed", DEFAULT_EMBED), host=host)
        wiki = WikiBuilder(split_level=split).build(_doc())
        index = SemanticIndex.build(
            wiki, embedder,
            size_words=int(build.get("chunk_size", 180)),
            overlap_words=int(build.get("overlap", 30)),
        )
        index.save(project.index_dir)
        state.record("index", fps["index"], project.index_dir,
                     {"chunks": len(index.chunks), "dim": int(index.embeddings.shape[1])})
        state.save()
        print(f"  index → {len(index.chunks)} chunk(s) → {project.index_dir}", file=sys.stderr)

    if "graph" in todo:
        if index is None:
            if not (project.index_dir / "index.json").is_file():
                print("error: graph needs an index — run `openwiki build index` first.", file=sys.stderr)
                return 2
            index = SemanticIndex.load(project.index_dir)
        wiki = WikiBuilder(split_level=split).build(_doc())
        references = (_corpus_references(project, sources, _doc(), wiki, multi)
                     if gcfg.get("references", True) else None)
        entities = None
        if gcfg.get("entities", False):
            model = models.get("chat", DEFAULT_CHAT)
            print("  graph: extracting entities (one LLM call per page) …", file=sys.stderr)
            entities = extract_entities(wiki, _entity_chat(model, host),
                                        types=gcfg.get("entity_types"),
                                        max_chars=int(gcfg.get("entity_max_chars", 8000)),
                                        retry_chat=_entity_retry_chat(model, host))
        stats = build_graph(wiki, index, project.graph_path,
                            similar_k=int(gcfg.get("similar_k", 6)),
                            references=references, entities=entities)
        state.record("graph", fps["graph"], project.graph_path,
                     {"pages": stats["pages"], "chunks": stats["chunks"],
                      "similar_to": stats["similar_edges"], "references": stats["reference_edges"]})
        state.save()
        print(f"  graph → {stats['pages']} page(s) / {stats['chunks']} chunk(s) → {project.graph_path}",
              file=sys.stderr)

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
    print("  sources:")
    for src in sources:
        rel = src.relative_to(project.root) if src.is_relative_to(project.root) else src
        print(f"    {'ok     ' if src.is_file() else 'MISSING'}  {rel}")
    if not sources:
        print("    (none — add [[sources]] to openwiki.toml)")

    fps = compute_fingerprints(project, sources) if sources else {}
    stem = sources[0].stem if sources else ""
    exists = {
        "ingest": (project.parsed_dir / f"{stem}.json").is_file() if sources else False,
        "wiki": (project.wiki_dir / "wiki.json").is_file(),
        "index": (project.index_dir / "index.json").is_file(),
        "graph": project.graph_path.exists(),
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
        print(f"    {stage:<7} {label:<11}{extra}")
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
    elif cmd == "graph-build":
        path("out", p.graph_path if p else None, Path("output") / "graph")
        path("index", p.index_dir if p else None, Path("output") / "index")
        val("split_level", "build", "split_level", 2)
        val("similar_k", "graph", "similar_k", 6)
        val("entity_model", "models", "chat", DEFAULT_CHAT)
        val("host", "models", "host", DEFAULT_HOST)


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


def _cmd_eval(args: argparse.Namespace) -> int:
    project = getattr(args, "project_obj", None)
    path = args.eval_set or ((project.root / "eval.jsonl") if project else Path("eval.jsonl"))
    if not Path(path).is_file():
        print(f"error: eval set not found: {path}\n"
              '  create a JSONL of {"question": "...", "pages": ["slug", ...]} lines.',
              file=sys.stderr)
        return 2
    items = load_eval_set(path)
    if not items:
        print(f"error: eval set is empty: {path}", file=sys.stderr)
        return 2
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


def _cmd_ask(args: argparse.Namespace) -> int:
    index = SemanticIndex.load(args.index)
    if isinstance(index.embedder, OllamaEmbedder):
        index.embedder.host = args.host.rstrip("/")

    graph = None
    if not args.no_graph and args.graph.exists():
        try:
            graph = GraphStore(args.graph)
        except Exception as exc:
            print(f"(graph not loaded: {exc})", file=sys.stderr)

    chat = OllamaChat(model=args.model, host=args.host, temperature=args.temperature)
    agent = RAGAgent(index, chat, top_k=args.top_k, graph=graph, expand_k=args.expand_k)

    mode = "graph-augmented " if graph else ""
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
    return 0


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


def _open_graph(path: Path, writable: bool):
    """Open the graph, falling back to read-only if a writable open is refused."""
    if not path.exists():
        return None
    try:
        return GraphStore(path, writable=writable)
    except Exception as exc:
        if writable:
            try:
                print(f"(graph opened read-only: {exc})", file=sys.stderr)
                return GraphStore(path, writable=False)
            except Exception as exc2:
                print(f"(graph not loaded: {exc2})", file=sys.stderr)
                return None
        print(f"(graph not loaded: {exc})", file=sys.stderr)
        return None


def _cmd_chat(args: argparse.Namespace) -> int:
    index = None
    if (args.index / "index.json").is_file():
        index = SemanticIndex.load(args.index)
        if isinstance(index.embedder, OllamaEmbedder):
            index.embedder.host = args.host.rstrip("/")
    # Writable graph (+ embedder) → agent edits update the graph incrementally.
    graph = _open_graph(args.graph, writable=index is not None and not args.dry_run)
    embedder = index.embedder if index else None
    tools = WikiTools(args.wiki, index=index, graph=graph, embedder=embedder, dry_run=args.dry_run)
    chat = OllamaChat(model=args.model, host=args.host, temperature=args.temperature)
    agent = WikiAgent(chat, tools, wiki_summary=summarize_wiki(args.wiki))

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


def _cmd_graph_build(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    doc = _load_parsed(args.source)
    wiki = WikiBuilder(split_level=args.split_level).build(doc)
    index = SemanticIndex.load(args.index)
    references = None if args.no_references else extract_references(doc, wiki)

    entities = None
    if args.entities:
        chat = _entity_chat(args.entity_model, args.host)
        print(f"Extracting entities with {chat.name} (one call per page) …", file=sys.stderr)

        def _progress(done, total, found):
            print(f"  page {done}/{total} — {found} entities so far", file=sys.stderr)

        types = [t.strip() for t in args.entity_types.split(",")] if args.entity_types else None
        entities = extract_entities(wiki, chat, types=types,
                                    max_chars=args.entity_max_chars or 8000,
                                    on_progress=_progress if args.verbose else None,
                                    retry_chat=_entity_retry_chat(args.entity_model, args.host))

    stats = build_graph(wiki, index, args.out, similar_k=args.similar_k,
                        references=references, entities=entities)
    print(f"Built graph from {args.source.name}")
    print(f"  pages         : {stats['pages']}")
    print(f"  chunks (dim {stats['dim']}): {stats['chunks']}")
    print(f"  SIMILAR_TO    : {stats['similar_edges']}")
    print(f"  REFERENCES    : {stats['reference_edges']}")
    if args.entities:
        print(f"  entities      : {stats['entities']}  (MENTIONS: {stats['mention_edges']})")
    print(f"  graph -> {args.out}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    index = None
    if (args.index / "index.json").is_file():
        index = SemanticIndex.load(args.index)
        if isinstance(index.embedder, OllamaEmbedder):
            index.embedder.host = args.host.rstrip("/")

    # Writable graph (+ embedder) → agent edits update the graph incrementally.
    graph = _open_graph(args.graph, writable=index is not None and not args.dry_run)
    embedder = index.embedder if index else None

    tools = WikiTools(args.wiki, index=index, graph=graph, embedder=embedder, dry_run=args.dry_run)
    chat = OllamaChat(model=args.model, host=args.host, temperature=args.temperature)
    agent = WikiAgent(chat, tools, wiki_summary=summarize_wiki(args.wiki))
    app = WikiWebApp(args.wiki, index=index, agent=agent, tools=tools, graph=graph,
                     project=getattr(args, "project_obj", None))

    graph_feat = ("graph+sync" if graph and getattr(graph, "writable", False) else
                  ("graph" if graph else None))
    features = ["search" if index else None, "chat", graph_feat]
    print(f"Serving wiki '{args.wiki}' — {', '.join(f for f in features if f)}.", file=sys.stderr)
    serve(app, host=args.bind, port=args.port)
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
        except Exception as exc:
            print(f"(graph not loaded: {exc})", file=sys.stderr)

    agent = None
    if index is not None and not args.no_ask:
        chat = OllamaChat(model=args.model, host=args.host, temperature=args.temperature)
        agent = RAGAgent(index, chat, graph=graph)

    server = build_server(args.wiki, index=index, graph=graph, agent=agent, version=__version__)
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
