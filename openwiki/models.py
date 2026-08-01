"""Data models for parsed documents.

These lightweight dataclasses are OpenWiki's *intermediate representation*: the
ingestion layer fills them in, and every later stage (wiki-page generation,
search indexing, agent retrieval) reads from them instead of touching a PDF.
Keeping this boundary means a second source parser (HTML, Markdown, ...) can
slot in later without changing anything downstream.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class TableData:
    """A table extracted from a page, stored as rows of cell strings."""

    page_number: int
    rows: list[list[Optional[str]]]
    bbox: Optional[tuple[float, float, float, float]] = None

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def n_cols(self) -> int:
        return max((len(row) for row in self.rows), default=0)

    def to_markdown(self) -> str:
        """Render the table as GitHub-flavored Markdown."""
        if not self.rows:
            return ""
        n_cols = self.n_cols

        def fmt(cells: list[Optional[str]]) -> str:
            values = [(c or "").replace("\n", " ").strip() for c in cells]
            values += [""] * (n_cols - len(values))
            return "| " + " | ".join(values) + " |"

        header, *body = self.rows
        lines = [fmt(header), "| " + " | ".join(["---"] * n_cols) + " |"]
        lines += [fmt(row) for row in body]
        return "\n".join(lines)


@dataclass
class ImageRef:
    """Reference to an image that was extracted from the PDF and saved to disk."""

    page_number: int
    xref: int
    path: str
    width: int
    height: int
    ext: str


@dataclass
class Page:
    """A single page's extracted content."""

    number: int  # 1-based
    text: str = ""
    tables: list[TableData] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)


@dataclass
class OutlineItem:
    """One entry in the document's table of contents (a PDF bookmark).

    The tree implied by ``level`` is what the wiki's page hierarchy derives from.
    """

    level: int  # 1-based depth
    title: str
    page: int  # 1-based target page (-1 if the bookmark has no destination)


@dataclass
class DocumentMetadata:
    source_path: str
    page_count: int
    title: str = ""
    author: str = ""
    subject: str = ""
    keywords: str = ""
    creator: str = ""
    producer: str = ""
    format: str = ""


@dataclass
class ParsedDocument:
    """The whole parsed document: metadata, outline, and pages."""

    metadata: DocumentMetadata
    outline: list[OutlineItem] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)

    # -- serialization --------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Full-fidelity dict, ready for ``json.dumps`` (the canonical artifact)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParsedDocument":
        """Rebuild a document from :meth:`to_dict` output (round-trips the IR)."""
        meta = DocumentMetadata(**data["metadata"])
        outline = [OutlineItem(**item) for item in data.get("outline", [])]
        pages: list[Page] = []
        for pd in data.get("pages", []):
            tables = [
                TableData(
                    page_number=t["page_number"],
                    rows=t["rows"],
                    bbox=tuple(t["bbox"]) if t.get("bbox") else None,
                )
                for t in pd.get("tables", [])
            ]
            images = [ImageRef(**im) for im in pd.get("images", [])]
            pages.append(
                Page(
                    number=pd["number"],
                    text=pd.get("text", ""),
                    tables=tables,
                    images=images,
                )
            )
        return cls(metadata=meta, outline=outline, pages=pages)

    def to_markdown(self, include_tables: bool = True) -> str:
        """Human- and wiki-friendly Markdown rendering of the document."""
        out: list[str] = []
        out.append(f"# {self.metadata.title or 'Untitled Document'}\n")

        out.append("## Document metadata\n")
        for label, value in [
            ("Source", self.metadata.source_path),
            ("Pages", self.metadata.page_count),
            ("Author", self.metadata.author),
            ("Producer", self.metadata.producer),
        ]:
            if value:
                out.append(f"- **{label}:** {value}")
        out.append("")

        if self.outline:
            out.append("## Table of contents\n")
            for item in self.outline:
                indent = "  " * max(0, item.level - 1)
                out.append(f"{indent}- {item.title} (p. {item.page})")
            out.append("")

        for page in self.pages:
            out.append(f"\n---\n\n## Page {page.number}\n")
            if page.text.strip():
                out.append(page.text.strip())
                out.append("")
            if include_tables:
                for i, table in enumerate(page.tables, start=1):
                    out.append(f"\n**Table {page.number}.{i}**\n")
                    out.append(table.to_markdown())
                    out.append("")
            for img in page.images:
                out.append(f"\n![image p{img.page_number} #{img.xref}]({img.path})")

        return "\n".join(out).strip() + "\n"
