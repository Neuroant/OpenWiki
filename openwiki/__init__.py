"""OpenWiki — learning to build agentic wikis, starting with PDF ingestion."""

from .models import (
    DocumentMetadata,
    ImageRef,
    OutlineItem,
    Page,
    ParsedDocument,
    TableData,
)
from .agent import RAGAgent, RAGAnswer, Source, build_messages
from .chat_agent import AgentTurn, ToolCall, WikiAgent
from .chunking import Chunk, chunk_wiki
from .embeddings import Embedder, OllamaEmbedder, get_embedder
from .graph import (
    Entity, GraphBuilder, GraphStore, build_graph, detect_page_offset,
    extract_entities, extract_references,
)
from .llm import ChatModel, OllamaChat
from .merge import combine_documents
from .mcp_server import MCPStdioServer, build_server
from .ontology import format_entity_types, propose_ontology, sample_corpus
from .outline import synthesize_outline
from .code_parser import CodeParser
from .html_parser import WebParser
from .markdown_parser import MarkdownParser
from .pdf_parser import PDFParser
from .project import Project, Source, render_manifest
from .sources import parse_source, source_type
from .userconfig import Registry, UserConfig
from .search import SearchResult, SemanticIndex
from .tools import WikiTools
from .web import WikiWebApp, serve
from .wiki import Wiki, WikiBuilder, WikiPage, slugify, write_wiki

__version__ = "0.37.0"

__all__ = [
    "PDFParser",
    "MarkdownParser",
    "WebParser",
    "CodeParser",
    "parse_source",
    "source_type",
    "ParsedDocument",
    "DocumentMetadata",
    "OutlineItem",
    "Page",
    "TableData",
    "ImageRef",
    "WikiBuilder",
    "Wiki",
    "WikiPage",
    "write_wiki",
    "slugify",
    "Chunk",
    "chunk_wiki",
    "Embedder",
    "OllamaEmbedder",
    "get_embedder",
    "SemanticIndex",
    "SearchResult",
    "ChatModel",
    "OllamaChat",
    "RAGAgent",
    "RAGAnswer",
    "Source",
    "build_messages",
    "WikiTools",
    "WikiAgent",
    "AgentTurn",
    "ToolCall",
    "WikiWebApp",
    "serve",
    "GraphBuilder",
    "GraphStore",
    "build_graph",
    "extract_references",
    "detect_page_offset",
    "extract_entities",
    "Entity",
    "build_server",
    "MCPStdioServer",
    "Project",
    "Source",
    "render_manifest",
    "UserConfig",
    "Registry",
    "combine_documents",
    "propose_ontology",
    "sample_corpus",
    "format_entity_types",
    "synthesize_outline",
    "__version__",
]
