"""Source-code repository ingestion for OpenWiki.

Turns a **directory** (a code repo, or any folder of text/code) into the shared
:class:`ParsedDocument` IR: a root **overview page** (the repo name + a file tree)
plus one page per source file (its content, wrapped in a fenced code block with a
language hint). Reuses the Markdown parser's ``sections_to_document`` — files are
just sections, so the wiki/index/graph pipeline works on a codebase unchanged.

Stdlib only. A sensible allowlist of source/text extensions + name matches is
included; noisy directories (``.git``/``node_modules``/``__pycache__``/build dirs,
and dotfolders), binary files (NUL-byte sniff), and oversized files are skipped.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Union

from .markdown_parser import sections_to_document
from .models import ParsedDocument

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]

_EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea", ".vscode",
    "target", ".next", ".cache", ".tox", "site-packages", "coverage", "vendor",
    "bin", "obj", ".terraform", ".gradle",
}
_CODE_SUFFIXES = {
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".java", ".go", ".rs",
    ".rb", ".php", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh", ".cs", ".swift",
    ".kt", ".kts", ".scala", ".sh", ".bash", ".zsh", ".ps1", ".sql", ".r", ".jl", ".lua",
    ".dart", ".ex", ".exs", ".clj", ".hs", ".ml", ".vue", ".svelte",
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".conf",
    ".xml", ".gradle", ".properties", ".env", ".tf", ".proto", ".graphql",
}
_INCLUDE_NAMES = {
    "Dockerfile", "Makefile", "README", "LICENSE", "CMakeLists.txt",
    "requirements.txt", "pyproject.toml", "package.json", "go.mod", "Cargo.toml",
}
_PROSE_SUFFIXES = {".md", ".rst", ".txt"}
_LANG = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".jsx": "jsx", ".mjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx", ".java": "java", ".go": "go", ".rs": "rust",
    ".rb": "ruby", ".php": "php", ".c": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp",
    ".h": "c", ".hpp": "cpp", ".cs": "csharp", ".swift": "swift", ".kt": "kotlin",
    ".scala": "scala", ".sh": "bash", ".bash": "bash", ".ps1": "powershell", ".sql": "sql",
    ".lua": "lua", ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss",
    ".vue": "vue", ".toml": "toml", ".yaml": "yaml", ".yml": "yaml", ".json": "json",
    ".xml": "xml", ".proto": "proto", ".graphql": "graphql",
}


def _included(path: Path) -> bool:
    return path.suffix.lower() in _CODE_SUFFIXES or path.name in _INCLUDE_NAMES


class CodeParser:
    """Parse a source-code repository (a directory) into a :class:`ParsedDocument`."""

    def __init__(self, max_bytes: int = 500_000) -> None:
        self.max_bytes = max_bytes   # skip files larger than this (generated/blobs)

    def parse(self, repo_path: PathLike, max_pages: Optional[int] = None) -> ParsedDocument:
        root = Path(repo_path)
        if not root.is_dir():
            raise NotADirectoryError(f"not a directory: {repo_path}")

        files = self._collect_files(root)
        if max_pages is not None:
            files = files[: max(0, max_pages - 1)]   # leave room for the overview page

        rels = [f.relative_to(root).as_posix() for f in files]
        tree = "\n".join(f"- `{rel}`" for rel in rels) or "*(no source files found)*"
        overview = f"# {root.name}\n\n{len(files)} source file(s).\n\n## Files\n\n{tree}"
        sections = [(1, root.name, overview)]
        for file, rel in zip(files, rels):
            sections.append((2, rel, self._render_file(file, rel)))

        logger.info("Done: %d file page(s) from %s", len(files), root)
        return sections_to_document(sections, str(root), "code", title_fallback=root.name)

    # -- internals ------------------------------------------------------

    def _collect_files(self, root: Path) -> list[Path]:
        found: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames
                                 if d not in _EXCLUDE_DIRS and not d.startswith("."))
            for name in sorted(filenames):
                path = Path(dirpath) / name
                if _included(path) and self._is_text(path):
                    found.append(path)
        # sorted by POSIX relative path → a valid pre-order over the tree
        found.sort(key=lambda p: p.relative_to(root).as_posix())
        return found

    def _is_text(self, path: Path) -> bool:
        try:
            if path.stat().st_size > self.max_bytes:
                return False
            with path.open("rb") as fh:
                return b"\x00" not in fh.read(8192)   # NUL byte ⇒ treat as binary
        except OSError:
            return False

    def _render_file(self, path: Path, rel: str) -> str:
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() in _PROSE_SUFFIXES:
            return f"`{rel}`\n\n{text}"                # prose renders as-is
        lang = _LANG.get(path.suffix.lower(), "")
        fence = "````"                                  # 4 backticks: survive ``` inside code
        return f"`{rel}`\n\n{fence}{lang}\n{text}\n{fence}"
