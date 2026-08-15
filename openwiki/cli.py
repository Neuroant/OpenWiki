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
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Optional, Sequence

from .agent import RAGAgent
from .chat_agent import WikiAgent
from .graph import GraphStore, build_graph, extract_entities, extract_references
from .embeddings import OllamaEmbedder
from .llm import OllamaChat
from .mcp_server import build_server
from .models import ParsedDocument
from .pdf_parser import PDFParser
from .project import (
    DEFAULT_CHAT, DEFAULT_EMBED, DEFAULT_HOST, Project, render_manifest,
)
from .search import SemanticIndex
from .tools import WikiTools
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
    init_p.add_argument("--source", action="append", type=Path, metavar="PATH",
                        help="A source document to register (repeatable); copied into sources/.")
    init_p.add_argument("--force", action="store_true", help="Overwrite an existing openwiki.toml.")

    ingest = sub.add_parser("ingest", parents=[common], help="Parse a PDF and extract its content.")
    ingest.add_argument("pdf", type=Path, help="Path to the PDF file.")
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
    wiki.add_argument("source", type=Path, help="A PDF, or a .json produced by `ingest`.")
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
    index_p.add_argument("source", type=Path, help="A PDF, or a .json produced by `ingest`.")
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
    graph_p.add_argument("source", type=Path, help="A PDF, or a .json produced by `ingest`.")
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
    graph_p.add_argument("--host", default=None, help="Ollama host URL (for --entities).")
    graph_p.add_argument("-v", "--verbose", action="store_true", help="Verbose progress logging.")
    return parser


# ----------------------------------------------------------------- projects

def _source_type(path: Path) -> str:
    return {".txt": "text", ".md": "text"}.get(path.suffix.lower(), "pdf")


def _cmd_init(args: argparse.Namespace) -> int:
    root: Path = args.dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "openwiki.toml"
    if manifest.exists() and not args.force:
        print(f"error: {manifest} already exists (use --force to overwrite).", file=sys.stderr)
        return 2

    sources_dir = root / "sources"
    sources_dir.mkdir(exist_ok=True)
    sources: list[dict] = []
    for src in args.source or []:
        src = Path(src)
        if not src.is_file():
            print(f"error: source not found: {src}", file=sys.stderr)
            return 2
        dest = sources_dir / src.name
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        sources.append({"type": _source_type(src), "path": f"sources/{src.name}"})

    name = args.name or root.name
    manifest.write_text(render_manifest(name=name, sources=sources), encoding="utf-8")

    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("output/\n.openwiki/\n", encoding="utf-8")

    print(f"Initialized OpenWiki project '{name}' at {root}")
    print(f"  manifest -> {manifest}")
    if sources:
        print(f"  sources  -> {sources_dir}  ({len(sources)} file(s) registered)")
    else:
        print(f"  add inputs under {sources_dir}/ and list them under [[sources]] in openwiki.toml")
    print("  next: run `openwiki ingest/build-wiki/index/graph-build` (or `openwiki build`, coming soon)")
    return 0


def _apply_project(args: argparse.Namespace, project: Optional[Project]) -> None:
    """Fill unset (``None``) path/model/host/split-level args from the project.

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

    parser = PDFParser(
        extract_tables=not args.no_tables,
        extract_images=args.images,
        image_dir=(out_dir / "images") if args.images else None,
    )
    doc = parser.parse(args.pdf, max_pages=args.max_pages)

    stem = args.pdf.stem
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
    print(f"Parsed {len(doc.pages)} page(s) from {args.pdf.name}")
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
        parser = PDFParser(
            extract_tables=not args.no_tables,
            extract_images=args.images,
            image_dir=(args.out.parent / "images") if args.images else None,
        )
        doc = parser.parse(source)

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
    return PDFParser(extract_tables=extract_tables).parse(source)


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
    agent = WikiAgent(chat, tools)

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
        chat = OllamaChat(model=args.entity_model, host=args.host)
        print(f"Extracting entities with {chat.name} (one call per page) …", file=sys.stderr)

        def _progress(done, total, found):
            print(f"  page {done}/{total} — {found} entities so far", file=sys.stderr)

        entities = extract_entities(wiki, chat, on_progress=_progress if args.verbose else None)

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
    agent = WikiAgent(chat, tools)
    app = WikiWebApp(args.wiki, index=index, agent=agent, tools=tools, graph=graph)

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
    "ingest": _cmd_ingest,
    "build-wiki": _cmd_build_wiki,
    "index": _cmd_index,
    "search": _cmd_search,
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

    # Resolve the active project and fill unset defaults from its manifest.
    try:
        project = Project.resolve(getattr(args, "project", None))
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if project is not None:
        print(f"[openwiki] project '{project.name}'  ({project.root})", file=sys.stderr)
    _apply_project(args, project)

    handler = _DISPATCH.get(args.command)
    return handler(args) if handler else 1


if __name__ == "__main__":
    sys.exit(main())
