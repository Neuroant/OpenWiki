"""Command-line interface for OpenWiki.

Currently exposes a single ``ingest`` subcommand. New capabilities (e.g.
``build-wiki``, ``search``) should be added as additional subcommands.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

from .agent import RAGAgent
from .chat_agent import WikiAgent
from .graph import GraphStore, build_graph, extract_references
from .embeddings import OllamaEmbedder
from .llm import OllamaChat
from .models import ParsedDocument
from .pdf_parser import PDFParser
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

    ingest = sub.add_parser("ingest", help="Parse a PDF and extract its content.")
    ingest.add_argument("pdf", type=Path, help="Path to the PDF file.")
    ingest.add_argument(
        "-o", "--out", type=Path, default=Path("output"),
        help="Output directory (default: ./output).",
    )
    ingest.add_argument("--no-tables", action="store_true", help="Skip table extraction.")
    ingest.add_argument("--images", action="store_true", help="Extract embedded images to <out>/images.")
    ingest.add_argument(
        "--max-pages", type=int, default=None,
        help="Parse only the first N pages (useful for quick tests).",
    )
    ingest.add_argument("-v", "--verbose", action="store_true", help="Verbose progress logging.")

    wiki = sub.add_parser("build-wiki", help="Split a parsed document into linked wiki pages.")
    wiki.add_argument("source", type=Path, help="A PDF, or a .json produced by `ingest`.")
    wiki.add_argument(
        "-o", "--out", type=Path, default=Path("output") / "wiki",
        help="Output directory (default: ./output/wiki).",
    )
    wiki.add_argument(
        "--split-level", type=int, default=2,
        help="Outline depth that becomes its own page (default: 2).",
    )
    wiki.add_argument("--no-tables", action="store_true", help="Skip table rendering.")
    wiki.add_argument(
        "--images", action="store_true",
        help="If SOURCE is a PDF, extract images (to <out>/../images).",
    )
    wiki.add_argument("-v", "--verbose", action="store_true", help="Verbose progress logging.")

    index_p = sub.add_parser("index", help="Build a semantic search index over the wiki.")
    index_p.add_argument("source", type=Path, help="A PDF, or a .json produced by `ingest`.")
    index_p.add_argument(
        "-o", "--out", type=Path, default=Path("output") / "index",
        help="Index output directory (default: ./output/index).",
    )
    index_p.add_argument(
        "--split-level", type=int, default=2,
        help="Outline depth used to build the wiki before chunking (default: 2).",
    )
    index_p.add_argument("--model", default="bge-m3", help="Ollama embedding model (default: bge-m3).")
    index_p.add_argument("--host", default="http://localhost:11434", help="Ollama host URL.")
    index_p.add_argument("--chunk-size", type=int, default=180, help="Chunk size in words (default: 180).")
    index_p.add_argument("--overlap", type=int, default=30, help="Chunk overlap in words (default: 30).")
    index_p.add_argument("-v", "--verbose", action="store_true", help="Verbose progress logging.")

    search_p = sub.add_parser("search", help="Query the semantic index built by `index`.")
    search_p.add_argument("query", help="Search query text.")
    search_p.add_argument(
        "-i", "--index", type=Path, default=Path("output") / "index",
        help="Index directory (default: ./output/index).",
    )
    search_p.add_argument("-k", "--top-k", type=int, default=5, help="Number of results (default: 5).")
    search_p.add_argument("--host", default="http://localhost:11434", help="Ollama host URL.")
    search_p.add_argument("--full", action="store_true", help="Print full chunk text instead of a snippet.")

    ask_p = sub.add_parser("ask", help="Answer a question over the wiki with RAG (retrieval + chat model).")
    ask_p.add_argument("question", help="The question to answer.")
    ask_p.add_argument(
        "-i", "--index", type=Path, default=Path("output") / "index",
        help="Index directory (default: ./output/index).",
    )
    ask_p.add_argument("-k", "--top-k", type=int, default=5, help="Chunks to retrieve (default: 5).")
    ask_p.add_argument(
        "--model", default="qwen3:30b-a3b-instruct-2507-q4_K_M",
        help="Ollama chat model (default: qwen3:30b-a3b-instruct-2507-q4_K_M).",
    )
    ask_p.add_argument("--host", default="http://localhost:11434", help="Ollama host URL.")
    ask_p.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature (default: 0.2).")
    ask_p.add_argument("--show-context", action="store_true", help="Also print the retrieved excerpts.")

    chat_p = sub.add_parser("chat", help="Multi-turn agent that can search, read, and edit wiki pages.")
    chat_p.add_argument(
        "-m", "--message", action="append", metavar="TEXT",
        help="A turn to send non-interactively (repeatable). Omit for an interactive REPL.",
    )
    chat_p.add_argument(
        "--wiki", type=Path, default=Path("output") / "wiki",
        help="Wiki directory to read/edit (default: ./output/wiki).",
    )
    chat_p.add_argument(
        "-i", "--index", type=Path, default=Path("output") / "index",
        help="Search index directory (default: ./output/index).",
    )
    chat_p.add_argument("--model", default="qwen3:30b-a3b-instruct-2507-q4_K_M", help="Ollama chat model.")
    chat_p.add_argument("--host", default="http://localhost:11434", help="Ollama host URL.")
    chat_p.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature (default: 0.2).")
    chat_p.add_argument("--dry-run", action="store_true", help="Preview edits without writing files.")
    chat_p.add_argument("--show-tools", action="store_true", help="Print each tool call the agent makes.")

    serve_p = sub.add_parser("serve", help="Serve a web UI over the wiki and the agent.")
    serve_p.add_argument(
        "--wiki", type=Path, default=Path("output") / "wiki",
        help="Wiki directory to serve (default: ./output/wiki).",
    )
    serve_p.add_argument(
        "-i", "--index", type=Path, default=Path("output") / "index",
        help="Search index directory (default: ./output/index).",
    )
    serve_p.add_argument(
        "--graph", type=Path, default=Path("output") / "graph",
        help="Knowledge-graph directory to serve, if present (default: ./output/graph).",
    )
    serve_p.add_argument("--bind", default="127.0.0.1", help="Address to bind (default: 127.0.0.1).")
    serve_p.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000).")
    serve_p.add_argument("--model", default="qwen3:30b-a3b-instruct-2507-q4_K_M", help="Ollama chat model for the agent.")
    serve_p.add_argument("--host", default="http://localhost:11434", help="Ollama host URL.")
    serve_p.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature (default: 0.2).")
    serve_p.add_argument("--dry-run", action="store_true", help="Agent previews edits without writing files.")

    graph_p = sub.add_parser("graph-build", help="Build the Kuzu knowledge graph over the wiki.")
    graph_p.add_argument("source", type=Path, help="A PDF, or a .json produced by `ingest`.")
    graph_p.add_argument(
        "-o", "--out", type=Path, default=Path("output") / "graph",
        help="Graph database directory (default: ./output/graph).",
    )
    graph_p.add_argument(
        "-i", "--index", type=Path, default=Path("output") / "index",
        help="Semantic index directory to mirror (default: ./output/index).",
    )
    graph_p.add_argument(
        "--split-level", type=int, default=2,
        help="Outline depth for the wiki (must match the indexed wiki; default: 2).",
    )
    graph_p.add_argument("--similar-k", type=int, default=6, help="SIMILAR_TO edges per page (default: 6).")
    graph_p.add_argument("--no-references", action="store_true",
                         help="Skip 'siehe Seite N' cross-reference (REFERENCES) edges.")
    graph_p.add_argument("-v", "--verbose", action="store_true", help="Verbose progress logging.")
    return parser


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
    chat = OllamaChat(model=args.model, host=args.host, temperature=args.temperature)
    agent = RAGAgent(index, chat, top_k=args.top_k)

    print(f"Retrieving {args.top_k} excerpt(s) and asking {chat.name} …", file=sys.stderr)
    result = agent.answer(args.question)

    print(result.answer)
    if result.sources:
        cited = result.cited_markers()
        print("\nSources  (* = cited):")
        for s in result.sources:
            mark = "*" if s.marker in cited else " "
            print(
                f" {mark}[{s.marker}] {s.page_title}  ·  PDF p.{s.pdf_page_start}–{s.pdf_page_end}"
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


def _cmd_chat(args: argparse.Namespace) -> int:
    index = None
    if (args.index / "index.json").is_file():
        index = SemanticIndex.load(args.index)
        if isinstance(index.embedder, OllamaEmbedder):
            index.embedder.host = args.host.rstrip("/")
    tools = WikiTools(args.wiki, index=index, dry_run=args.dry_run)
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
    stats = build_graph(wiki, index, args.out, similar_k=args.similar_k, references=references)
    print(f"Built graph from {args.source.name}")
    print(f"  pages         : {stats['pages']}")
    print(f"  chunks (dim {stats['dim']}): {stats['chunks']}")
    print(f"  SIMILAR_TO    : {stats['similar_edges']}")
    print(f"  REFERENCES    : {stats['reference_edges']}")
    print(f"  graph -> {args.out}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    index = None
    if (args.index / "index.json").is_file():
        index = SemanticIndex.load(args.index)
        if isinstance(index.embedder, OllamaEmbedder):
            index.embedder.host = args.host.rstrip("/")
    tools = WikiTools(args.wiki, index=index, dry_run=args.dry_run)
    chat = OllamaChat(model=args.model, host=args.host, temperature=args.temperature)
    agent = WikiAgent(chat, tools)

    graph = None
    if args.graph.exists():
        try:
            graph = GraphStore(args.graph)
        except Exception as exc:  # missing/corrupt graph shouldn't stop the server
            print(f"(graph not loaded: {exc})", file=sys.stderr)
    app = WikiWebApp(args.wiki, index=index, agent=agent, tools=tools, graph=graph)

    features = ["search" if index else None, "chat", "graph" if graph else None]
    print(f"Serving wiki '{args.wiki}' — {', '.join(f for f in features if f)}.", file=sys.stderr)
    serve(app, host=args.bind, port=args.port)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    # Ensure non-ASCII output (German umlauts, ·, –) prints correctly on Windows,
    # where stdout may otherwise default to a non-UTF-8 code page.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = _build_argparser().parse_args(argv)
    if args.command == "ingest":
        return _cmd_ingest(args)
    if args.command == "build-wiki":
        return _cmd_build_wiki(args)
    if args.command == "index":
        return _cmd_index(args)
    if args.command == "search":
        return _cmd_search(args)
    if args.command == "ask":
        return _cmd_ask(args)
    if args.command == "chat":
        return _cmd_chat(args)
    if args.command == "serve":
        return _cmd_serve(args)
    if args.command == "graph-build":
        return _cmd_graph_build(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
