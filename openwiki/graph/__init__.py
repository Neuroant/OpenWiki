"""Kuzu-backed knowledge-graph layer over the wiki (structural + vector-derived)."""

from .builder import GraphBuilder, build_graph
from .entities import DEFAULT_ENTITY_TYPES, Entity, coerce_types, extract_entities
from .references import detect_page_offset, extract_references, extract_references_multi
from .store import GraphStore

__all__ = [
    "GraphBuilder", "build_graph", "GraphStore",
    "extract_references", "extract_references_multi", "detect_page_offset",
    "extract_entities", "Entity", "coerce_types", "DEFAULT_ENTITY_TYPES",
]
