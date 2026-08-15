"""OpenWiki **project**: a folder with an ``openwiki.toml`` manifest and its own
outputs (wiki + index + graph), so state persists and you can jump between several
knowledge bases.

This module is the foundation (roadmap Phase 1): the :class:`Project` model
(discovery, loading, layout, setting precedence) plus :func:`render_manifest`
(a small hand-rolled TOML writer for our schema — stdlib ``tomllib`` reads TOML
but cannot write it). See ``docs/projects.md``.

Only this module and the CLI know about projects; the pipeline modules stay
project-agnostic and keep taking explicit paths.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # Python 3.10
    import tomli as _toml  # type: ignore[no-redef]

MANIFEST = "openwiki.toml"
STATE_DIR = ".openwiki"

# Built-in defaults (mirror the CLI) — used when neither a flag nor the manifest
# provides a value.
DEFAULT_EMBED = "bge-m3"
DEFAULT_CHAT = "qwen3:30b-a3b-instruct-2507-q4_K_M"
DEFAULT_HOST = "http://localhost:11434"


@dataclass(frozen=True)
class Source:
    """A declared input document."""

    type: str
    path: str


@dataclass
class Project:
    """A loaded ``openwiki.toml`` and the folder that contains it."""

    root: Path
    data: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, root) -> "Project":
        root = Path(root).resolve()
        manifest = root / MANIFEST
        if not manifest.is_file():
            raise FileNotFoundError(f"no {MANIFEST} in {root}")
        with manifest.open("rb") as fh:
            data = _toml.load(fh)
        return cls(root=root, data=data)

    @classmethod
    def find(cls, start=None) -> Optional["Project"]:
        """Nearest project at or above ``start`` (defaults to CWD), like git."""
        start = Path(start or Path.cwd()).resolve()
        for parent in (start, *start.parents):
            if (parent / MANIFEST).is_file():
                return cls.load(parent)
        return None

    @classmethod
    def resolve(cls, explicit=None) -> Optional["Project"]:
        """``--project`` > ``$OPENWIKI_PROJECT`` > discovery. ``None`` = no project."""
        if explicit:
            return cls.load(explicit)
        env = os.environ.get("OPENWIKI_PROJECT")
        if env:
            return cls.load(env)
        return cls.find()

    # -------------------------------------------------------------- identity
    @property
    def name(self) -> str:
        return self.section("project").get("name", self.root.name)

    @property
    def description(self) -> str:
        return self.section("project").get("description", "")

    @property
    def sources(self) -> list[Source]:
        out: list[Source] = []
        for s in self.data.get("sources", []) or []:
            if isinstance(s, dict) and s.get("path"):
                out.append(Source(type=s.get("type", "pdf"), path=s["path"]))
        return out

    def section(self, name: str) -> dict:
        value = self.data.get(name, {})
        return value if isinstance(value, dict) else {}

    def setting(self, section: str, key: str, default=None):
        """Manifest value for ``[section] key``, else ``default``."""
        value = self.section(section).get(key)
        return default if value is None else value

    # ---------------------------------------------------------------- layout
    @property
    def out_dir(self) -> Path:
        return self.root / self.section("layout").get("out", "output")

    @property
    def parsed_dir(self) -> Path:
        return self.out_dir / "parsed"

    @property
    def wiki_dir(self) -> Path:
        return self.out_dir / "wiki"

    @property
    def index_dir(self) -> Path:
        return self.out_dir / "index"

    @property
    def graph_path(self) -> Path:
        return self.out_dir / "graph"

    @property
    def state_dir(self) -> Path:
        return self.root / STATE_DIR

    def source_paths(self) -> list[Path]:
        """Declared sources resolved against the project root."""
        return [self.root / s.path for s in self.sources]


def _toml_str(value: str) -> str:
    r"""Encode a Python string as a TOML basic string (quotes, backslashes)."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_manifest(
    name: str,
    description: str = "",
    sources: Optional[list[dict]] = None,
    *,
    embed: str = DEFAULT_EMBED,
    chat: str = DEFAULT_CHAT,
    host: str = DEFAULT_HOST,
    split_level: int = 2,
    tables: bool = True,
    chunk_size: int = 180,
    overlap: int = 30,
    similar_k: int = 6,
    references: bool = True,
    entities: bool = False,
    port: int = 8137,
) -> str:
    """Render an ``openwiki.toml`` for our schema (stdlib has no TOML writer)."""
    sources = sources or []
    if sources:
        blocks = "\n".join(
            f"[[sources]]\ntype = {_toml_str(s.get('type', 'pdf'))}\n"
            f"path = {_toml_str(s['path'])}\n"
            for s in sources
        )
    else:
        blocks = (
            "# [[sources]]         # add one block per input; all merge into one corpus\n"
            '# type = "pdf"\n'
            '# path = "sources/manual.pdf"\n'
        )
    return f"""# openwiki.toml — OpenWiki project manifest.
# Rebuild every artifact from this file with:  openwiki build

[project]
name = {_toml_str(name)}
description = {_toml_str(description)}

# One or more sources; all merge into a single corpus (wiki + index + graph).
{blocks}
[build]
split_level = {split_level}   # shared by index & graph so their page slugs can't drift
tables = {str(tables).lower()}
chunk_size = {chunk_size}
overlap = {overlap}

[models]
host  = {_toml_str(host)}
embed = {_toml_str(embed)}
chat  = {_toml_str(chat)}

[graph]
similar_k = {similar_k}
references = {str(references).lower()}
entities = {str(entities).lower()}

[serve]
port = {port}
bind = "127.0.0.1"
temperature = 0.2
"""
