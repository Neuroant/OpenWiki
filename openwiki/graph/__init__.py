"""Kuzu-backed knowledge-graph layer over the wiki (structural + vector-derived)."""

from .builder import GraphBuilder, build_graph
from .references import detect_page_offset, extract_references
from .store import GraphStore

__all__ = [
    "GraphBuilder", "build_graph", "GraphStore",
    "extract_references", "detect_page_offset",
]
