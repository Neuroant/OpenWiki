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

# A "session" source feeds the **memory tier** (Path B), not the document pipeline —
# it's captured/remembered, never parsed into the wiki. Everything else is a doc source.
SESSION_TYPE = "session"

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
        """Every declared ``[[sources]]`` entry (documents **and** sessions)."""
        out: list[Source] = []
        for s in self.data.get("sources", []) or []:
            if isinstance(s, dict) and s.get("path"):
                out.append(Source(type=s.get("type", "pdf"), path=s["path"]))
        return out

    def doc_sources(self) -> list[Source]:
        """Document sources — the wiki/index/graph pipeline (excludes ``session``)."""
        return [s for s in self.sources if s.type != SESSION_TYPE]

    def session_sources(self) -> list[Source]:
        """Session/experience sources — captured into the memory tier (Path B)."""
        return [s for s in self.sources if s.type == SESSION_TYPE]

    def section(self, name: str) -> dict:
        value = self.data.get(name, {})
        return value if isinstance(value, dict) else {}

    def setting(self, section: str, key: str, default=None):
        """Manifest value for ``[section] key``, else ``default``."""
        value = self.section(section).get(key)
        return default if value is None else value

    @property
    def identity(self) -> str:
        """The stable 'who am I' text for Second Brain context assembly (B6 identity tier):
        an explicit ``[memory] identity``, else the project description, else its name."""
        return str(self.setting("memory", "identity", None) or self.description or self.name or "").strip()

    @property
    def context_budget(self) -> int:
        """Char budget for an assembled memory context (B6, ~4 chars/token). Bounds what the
        auto-inject hook / `wiki_memory` put into a prompt. ``[memory] context_budget``, else 3000
        (v0.96: 2000 → 3000 so the larger ``context_k`` fits next to identity + themes)."""
        try:
            return max(0, int(self.setting("memory", "context_budget", 3000)))
        except (TypeError, ValueError):
            return 3000

    @property
    def context_k(self) -> int:
        """Facts recalled into an assembled memory context (B6 activation tier) — the inject hook,
        `wiki_memory`, `context` and the web context box. ``[memory] context_k``, else 16 (v0.96:
        8 → 16, measured on the live path — path-b-memory.md §13.12)."""
        try:
            return max(1, int(self.setting("memory", "context_k", 16)))
        except (TypeError, ValueError):
            return 16

    @property
    def agent_writes(self) -> bool:
        """Whether a coding agent may **write** memory through MCP ``wiki_remember`` (record a
        decision or a new state when it makes the change, closing what it replaces). ``[memory]
        agent_writes``, **off by default** — memory is injected into every later prompt, so write
        access is granted deliberately (the P0 policy still screens every fact)."""
        return bool(self.setting("memory", "agent_writes", False))

    @property
    def memory_probes(self) -> bool:
        """P1 cue-trigger recall for assembled contexts (hook inject, ``context``, ``wiki_memory``,
        the web context box): one extra chat call per read guesses the user's implicit constraints
        on the request, which get reserved recall slots. ``[memory] probes``, **off by default** —
        it costs a local LLM call on every prompt the inject hook sees."""
        return bool(self.setting("memory", "probes", False))

    @property
    def lexical_weight(self) -> float:
        """Hybrid recall (v0.103): the weight of BM25 over the remembered facts next to the dense
        score — names, versions, file and function names the embedding blurs; ``0`` = dense only.
        ``[memory] lexical_weight``, default ``lexical.RECALL_WEIGHT`` (0.2): on real prompts the facts
        it swaps in were judged helpful 21.9 % of the time vs 12.0 % for those they displaced."""
        from .lexical import RECALL_WEIGHT
        value = self.setting("memory", "lexical_weight", RECALL_WEIGHT)
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return RECALL_WEIGHT

    @property
    def temporal_weight(self) -> float:
        """Time-window recall (v0.104): the bonus for facts whose valid time falls in the window a
        query names ("in July 2023", "last week") — ``0`` = off. ``[memory] temporal_weight``, default
        ``temporal.WINDOW_WEIGHT`` (0.1): on LoCoMo's dated questions J rose 46.2 → 56.2 % (+24 / −3)."""
        from .graph.temporal import WINDOW_WEIGHT
        value = self.setting("memory", "temporal_weight", WINDOW_WEIGHT)
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return WINDOW_WEIGHT

    @property
    def approve_writes(self) -> bool:
        """Stage the agent's memory writes (``wiki_remember``) for a person's approval —
        ``[memory] approve_writes``, off by default (v0.110): ``openwiki memory pending`` lists them,
        ``approve`` / ``reject`` decide. Captures are not staged (the memory policy screens them)."""
        return bool(self.setting("memory", "approve_writes", False))

    @property
    def repeat_facts(self) -> bool:
        """Re-inject facts already injected earlier in the session (``[memory] repeat_facts``). **Off by
        default** (v0.107): each fact, theme and the identity is injected once per stretch — until a
        compaction or ``/clear`` — since the earlier injection is still in the agent's context; measured on
        the dogfooding session, 55 % of injected facts were such repeats."""
        return bool(self.setting("memory", "repeat_facts", False))

    @property
    def skip_chores(self) -> bool:
        """No memory injection for chore prompts — git operations ("push and tag v1.2"), slash
        commands, and bare acknowledgements once a session is under way (``cli.chore_kind``).
        ``[memory] skip_chores``, on by default (v0.101): 16 % of the dogfooding prompts were such
        chores, each receiving ~710 tokens of memory it didn't need."""
        return bool(self.setting("memory", "skip_chores", True))

    @property
    def skip_prompts(self) -> list:
        """Project-specific chore prompts that get no memory either — regular expressions matched
        against the whole prompt, case-insensitive (``[memory] skip_prompts``, e.g.
        ``["sync arc42 docs"]``)."""
        value = self.setting("memory", "skip_prompts", [])
        return [str(v) for v in value] if isinstance(value, list) else [str(value)]

    @property
    def transcripts(self) -> list:
        """More session transcripts for session search (``[memory] transcripts``, v0.108) — files or folders
        (Claude Code ``*.jsonl`` transcripts, ``*.md`` / ``*.txt``), ``~`` expanded, relative to the project
        root. The sessions the hooks capture are found without it."""
        raw = self.setting("memory", "transcripts", []) or []
        out = []
        for item in ([raw] if isinstance(raw, str) else raw):
            p = Path(os.path.expanduser(str(item)))
            out.append(p if p.is_absolute() else self.root / p)
        return out

    @property
    def memory_markdown_dir(self) -> Optional[Path]:
        """Where ``sleep`` writes the readable Markdown view of the memory — ``[memory]
        markdown_dir``, relative to the project root (default ``memory``; ``""`` turns it off).
        Deterministic files, so a git repo around the project shows what the memory learned."""
        value = self.setting("memory", "markdown_dir", "memory")
        if not value:
            return None
        p = Path(str(value))
        return p if p.is_absolute() else self.root / p

    @property
    def memory_enabled(self) -> bool:
        """Second Brain mode — whether the remembered tier (Path B) is active for this
        project. **Off by default** (Wiki mode, §3.1 / ADR-14); turn it on with
        ``[memory] enabled = true``. Gates the ``remember``/``recall`` writes + reads and
        the ``build`` memory stage; the remembered tier itself is additive (ADR-7), so
        "off" simply means the memory tables stay empty."""
        return bool(self.setting("memory", "enabled", False))

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

    def _resolve_path(self, path: str):
        """A declared source path resolved: a URL as-is (a ``str``), an absolute path
        as-is, a relative path joined to the project root."""
        if str(path).lower().startswith(("http://", "https://")):
            return path
        p = Path(path)
        return p if p.is_absolute() else self.root / p

    def source_paths(self) -> list:
        """**Document** sources resolved (URL as-is, else a path). Repo/URL sources
        point in place; file sources point under ``sources/``. Session sources are
        excluded — they flow into the memory tier via :meth:`session_paths`."""
        return [self._resolve_path(s.path) for s in self.doc_sources()]

    def session_paths(self) -> list:
        """Session-source transcript paths resolved (local files under ``sources/``)."""
        return [self._resolve_path(s.path) for s in self.session_sources()]


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
    memory: bool = False,
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
            '# path = "sources/manual.pdf"   # a file (copied into sources/),\n'
            '#                               # or type="web" + path="https://…" (a URL),\n'
            '#                               # or type="code" + path="../my-repo" (a code repo)\n'
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
synthesize_outline = true   # derive section pages from headings when a PDF has no bookmarks
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
# relations = false        # also extract typed Entity->Entity relations (RELATED_TO); implies entities, +1 LLM call/page
# resolve_entities = false # merge same-concept surface variants into canonical entities (aliases + descriptions); implies entities
# entity_types = ["Concept", "Method", "Component", "Property"]   # domain ontology used when entities = true
# entity_max_chars = 8000   # how much of each page the entity model sees

[memory]
# Second Brain mode (Path B): capture sessions into a remembered tier the graph keeps
# across doc rebuilds, and recall them later. Off = Wiki mode (docs only).
enabled = {str(memory).lower()}
# context_budget = 3000   # chars (~4/token) for an assembled memory context (B6 / hooks)
# context_k = 16          # facts recalled into that context
# probes = false          # cue-trigger recall: +1 chat call per context read to surface the user's implicit constraints
# lexical_weight = 0.2    # hybrid recall: BM25 over the facts next to the embedding (names, versions, files); 0 = off
# temporal_weight = 0.1   # favour facts from the time window a request names ("in July", "last week"); 0 = off
# agent_writes = false    # let a coding agent record facts / new states via MCP wiki_remember
# skip_chores = true      # no memory for chore prompts (push / tag / commit, slash commands, a later "ok")
# repeat_facts = false    # re-inject facts the session already got (default: each once, until a compaction)
# approve_writes = false  # stage the agent's wiki_remember writes until a person approves them (memory pending)
# skip_prompts = []       # more chore prompts for this project, as regexes, e.g. ["sync arc42 docs"]
# markdown_dir = "memory" # sleep writes a readable Markdown view of the memory here ("" = off)
# transcripts = []        # more transcripts for session search (files / folders); hooked sessions are found anyway

[serve]
port = {port}
bind = "127.0.0.1"
temperature = 0.2
"""
