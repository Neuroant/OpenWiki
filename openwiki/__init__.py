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
from .pdf_parser import PDFParser
from .search import SearchResult, SemanticIndex
from .tools import WikiTools
from .web import WikiWebApp, serve
from .wiki import Wiki, WikiBuilder, WikiPage, slugify, write_wiki

__version__ = "0.13.3"

__all__ = [
    "PDFParser",
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
    "__version__",
]
