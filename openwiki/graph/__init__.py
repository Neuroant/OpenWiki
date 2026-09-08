"""Kuzu-backed knowledge-graph layer over the wiki (structural + vector-derived)."""

from .builder import GraphBuilder, build_graph
from .community import answer_global, detect_communities, summarize_community
from .entities import DEFAULT_ENTITY_TYPES, Entity, coerce_types, extract_entities
from .memory import MemoryFact, capture_session, format_memory, parse_facts
from .references import detect_page_offset, extract_references, extract_references_multi
from .store import GraphStore
from .usage import append_usage, clear_usage, read_usage, usage_log_path

__all__ = [
    "GraphBuilder", "build_graph", "GraphStore",
    "extract_references", "extract_references_multi", "detect_page_offset",
    "extract_entities", "Entity", "coerce_types", "DEFAULT_ENTITY_TYPES",
    "detect_communities", "summarize_community", "answer_global",
    "capture_session", "format_memory", "parse_facts", "MemoryFact",
    "usage_log_path", "append_usage", "read_usage", "clear_usage",
]
