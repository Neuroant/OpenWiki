"""Tests for the PDF ingestion tool.

These run against the sample Korg NAUTILUS manual shipped in the repo root and
are skipped cleanly if either PyMuPDF or the sample PDF is unavailable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fitz")  # skip the whole module if PyMuPDF isn't installed

from openwiki import PDFParser, ParsedDocument, TableData

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_PDF = REPO_ROOT / "301357_NAUTILUS_OG_G1.pdf"


@pytest.fixture(scope="module")
def parsed() -> ParsedDocument:
    if not SAMPLE_PDF.is_file():
        pytest.skip(f"Sample PDF missing: {SAMPLE_PDF}")
    return PDFParser(extract_tables=True).parse(SAMPLE_PDF, max_pages=5)


def test_metadata(parsed: ParsedDocument) -> None:
    assert parsed.metadata.page_count > 0
    assert parsed.metadata.source_path.endswith(".pdf")


def test_pages_have_text(parsed: ParsedDocument) -> None:
    assert len(parsed.pages) == 5
    assert any(page.text.strip() for page in parsed.pages)
    # German content must survive extraction as proper Unicode.
    joined = "\n".join(page.text for page in parsed.pages)
    assert "NAUTILUS" in joined


def test_outline_is_wellformed(parsed: ParsedDocument) -> None:
    assert isinstance(parsed.outline, list)
    for item in parsed.outline:
        assert item.level >= 1
        assert item.title


def test_serialization_roundtrip(parsed: ParsedDocument) -> None:
    data = parsed.to_dict()
    assert "metadata" in data and "pages" in data
    assert data["metadata"]["page_count"] == parsed.metadata.page_count
    assert parsed.to_markdown().startswith("#")


def test_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        PDFParser().parse("does_not_exist.pdf")


def test_table_to_markdown_ragged_rows() -> None:
    table = TableData(page_number=1, rows=[["a", "b"], ["only-one"]])
    md = table.to_markdown().splitlines()
    assert md[0] == "| a | b |"
    assert md[1] == "| --- | --- |"
    assert md[2] == "| only-one |  |"  # short row padded to full width
