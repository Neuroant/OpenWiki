"""Tools the multi-turn agent can call to read and edit the wiki.

Every method returns a plain string (what the model reads back). Write tools go
through :meth:`WikiTools._write`, which honors ``dry_run`` and appends to an
``edits`` log. All file access is confined to the wiki's ``pages/`` directory and
slugs are validated, so the model cannot escape it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .search import SemanticIndex

_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _shorten(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _first_heading(path: Path) -> str:
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("# "):
                    return line[2:].strip()
    except OSError:
        pass
    return path.stem


class WikiTools:
    def __init__(self, wiki_dir, index: Optional[SemanticIndex] = None,
                 graph=None, dry_run: bool = False) -> None:
        self.wiki_dir = Path(wiki_dir)
        self.pages_dir = self.wiki_dir / "pages"
        self.index = index
        self.graph = graph  # optional GraphStore (enables the graph-aware tools)
        self.dry_run = dry_run
        self.edits: list[str] = []
        self._has_entities: Optional[bool] = None  # cached graph.has_entities()

    def _graph_has_entities(self) -> bool:
        if self.graph is None:
            return False
        if self._has_entities is None:
            try:
                self._has_entities = self.graph.has_entities()
            except Exception:
                self._has_entities = False
        return self._has_entities

    # -- schema advertised to the model --------------------------------

    def schemas(self) -> list[dict]:
        def fn(name, description, properties, required):
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {"type": "object", "properties": properties, "required": required},
                },
            }

        slug = {"type": "string", "description": "Page slug, e.g. '025-smooth-sound-transitions-sst'."}
        tools = [
            fn("search_wiki", "Semantic search over the wiki; returns relevant page excerpts with slugs.",
               {"query": {"type": "string"}, "k": {"type": "integer", "description": "Max results (default 5)."}},
               ["query"]),
            fn("list_pages", "List every wiki page slug and title.", {}, []),
            fn("read_page", "Return the full Markdown of a page.", {"slug": slug}, ["slug"]),
            fn("edit_page",
               "Replace an exact, unique snippet of a page with new text. Read the page first to copy exact text.",
               {"slug": slug,
                "old_text": {"type": "string", "description": "Exact text to replace (must be unique in the page)."},
                "new_text": {"type": "string"}},
               ["slug", "old_text", "new_text"]),
            fn("append_section",
               "Append a new '## heading' section (with body) to a page, before its navigation footer.",
               {"slug": slug, "heading": {"type": "string"}, "body": {"type": "string"}},
               ["slug", "heading", "body"]),
            fn("create_page", "Create a new wiki page file with a title and Markdown body.",
               {"slug": {"type": "string", "description": "Filename-safe slug (letters, digits, hyphens)."},
                "title": {"type": "string"}, "body": {"type": "string"}},
               ["slug", "title", "body"]),
        ]
        if self.graph is not None:
            tools += [
                fn("graph_neighbors",
                   "List a page's related pages in the knowledge graph: parent/child (hierarchy), "
                   "previous/next (reading order), references / referenced-by (the manual's cross-refs), "
                   "and semantically similar pages. Use it to discover connected topics around a page.",
                   {"slug": slug}, ["slug"]),
                fn("find_path",
                   "Find the shortest chain of related pages between two pages, to explain how two "
                   "topics are connected. Returns the pages and the relationship between each step.",
                   {"from_slug": slug, "to_slug": slug}, ["from_slug", "to_slug"]),
            ]
            if self._graph_has_entities():
                tools.append(fn(
                    "find_entity",
                    "Find wiki pages that mention a named concept/entity (a Mode, Effect, Feature, "
                    "Parameter, Hardware control, …) — useful to gather every page discussing a topic.",
                    {"name": {"type": "string", "description": "Entity name or substring, e.g. 'Arpeggiator'."}},
                    ["name"]))
        return tools

    # -- read tools -----------------------------------------------------

    def search_wiki(self, query: str, k: int = 5) -> str:
        if self.index is None:
            return "Search unavailable: no index is loaded."
        results = self.index.search(str(query), k=int(k or 5))
        if not results:
            return "No results."
        return "\n".join(
            f"[{r.score:.3f}] {r.page_slug} — {r.page_title} "
            f"(PDF p.{r.pdf_page_start}-{r.pdf_page_end}): {_shorten(r.text)}"
            for r in results
        )

    def list_pages(self) -> str:
        files = sorted(self.pages_dir.glob("*.md"))
        if not files:
            return "No pages found."
        return "\n".join(f"{f.stem} — {_first_heading(f)}" for f in files)

    def read_page(self, slug: str) -> str:
        path = self._page_path(slug)
        if not path.is_file():
            return f"ERROR: page '{slug}' does not exist."
        return path.read_text(encoding="utf-8")

    # -- graph-aware tools ---------------------------------------------

    _REL_LABEL = {
        "parent": "parent", "child": "child", "prev": "previous", "next": "next",
        "references": "references", "referenced_by": "referenced by", "similar": "similar",
    }

    def graph_neighbors(self, slug: str) -> str:
        if self.graph is None:
            return "Graph unavailable: no knowledge graph is loaded."
        try:
            data = self.graph.neighborhood(str(slug))
        except KeyError:
            return f"ERROR: page '{slug}' is not in the graph."
        center = next((n for n in data["nodes"] if n["rel"] == "center"), None)
        lines = [f"Neighbors of {slug} ({center['title'] if center else ''}):"]
        others = [n for n in data["nodes"] if n["rel"] != "center"]
        if not others:
            lines.append("  (no related pages)")
        for n in others:
            lines.append(f"  [{self._REL_LABEL.get(n['rel'], n['rel'])}] {n['slug']} — {n['title']}")
        return "\n".join(lines)

    def find_path(self, from_slug: str, to_slug: str) -> str:
        if self.graph is None:
            return "Graph unavailable: no knowledge graph is loaded."
        try:
            path = self.graph.find_path(str(from_slug), str(to_slug))
        except KeyError as exc:
            return f"ERROR: {exc}"
        if path is None:
            return f"No path found between '{from_slug}' and '{to_slug}'."
        if path["hops"] == 0:
            return f"'{from_slug}' and '{to_slug}' are the same page."
        parts = [path["titles"][0]]
        for rel, title in zip(path["rels"], path["titles"][1:]):
            parts.append(f" --{rel}--> {title}")
        return (f"Path ({path['hops']} hop(s)): " + "".join(parts)
                + "\n  slugs: " + " -> ".join(path["nodes"]))

    def find_entity(self, name: str) -> str:
        if self.graph is None:
            return "Graph unavailable: no knowledge graph is loaded."
        hits = self.graph.pages_for_entity(str(name))
        if not hits:
            return f"No entity matching '{name}' found in the graph."
        grouped: dict = {}
        for h in hits:
            grouped.setdefault((h["entity"], h["type"]), []).append(f"{h['slug']} ({h['title']})")
        return "\n".join(
            f"{ent} [{etype}] is mentioned on: " + ", ".join(pages)
            for (ent, etype), pages in grouped.items()
        )

    # -- write tools ----------------------------------------------------

    def edit_page(self, slug: str, old_text: str, new_text: str) -> str:
        path = self._page_path(slug)
        if not path.is_file():
            return f"ERROR: page '{slug}' does not exist."
        content = path.read_text(encoding="utf-8")
        occurrences = content.count(old_text)
        if occurrences == 0:
            return f"ERROR: old_text not found in '{slug}'. Read the page and copy exact text."
        if occurrences > 1:
            return f"ERROR: old_text appears {occurrences} times in '{slug}'; add surrounding context to make it unique."
        updated = content.replace(old_text, new_text, 1)
        return self._write(path, slug, updated, f"edit_page({slug})")

    def append_section(self, slug: str, heading: str, body: str) -> str:
        path = self._page_path(slug)
        if not path.is_file():
            return f"ERROR: page '{slug}' does not exist."
        content = path.read_text(encoding="utf-8")
        section = f"\n## {heading}\n\n{body}\n"
        footer = content.rfind("\n---\n")  # keep the nav footer at the bottom
        if footer != -1:
            updated = content[:footer] + "\n" + section + content[footer:]
        else:
            updated = content.rstrip() + "\n" + section
        return self._write(path, slug, updated, f"append_section({slug}: '{heading}')")

    def create_page(self, slug: str, title: str, body: str) -> str:
        if not _SLUG_RE.match(slug or ""):
            return f"ERROR: invalid slug '{slug}' (use letters, digits, '-', '_', '.')."
        path = self._page_path(slug)
        if path.exists():
            return f"ERROR: page '{slug}' already exists; use edit_page or append_section."
        body = body.strip()
        # avoid a duplicated H1 when the model already puts a title in the body
        content = f"{body}\n" if body.startswith("# ") else f"# {title}\n\n{body}\n"
        return self._write(path, slug, content, f"create_page({slug})")

    # -- dispatch + helpers --------------------------------------------

    def dispatch(self, name: str, args: dict) -> str:
        handlers = {
            "search_wiki": lambda: self.search_wiki(args.get("query", ""), args.get("k", 5)),
            "list_pages": self.list_pages,
            "read_page": lambda: self.read_page(args["slug"]),
            "edit_page": lambda: self.edit_page(args["slug"], args["old_text"], args["new_text"]),
            "append_section": lambda: self.append_section(args["slug"], args["heading"], args["body"]),
            "create_page": lambda: self.create_page(args["slug"], args["title"], args["body"]),
            "graph_neighbors": lambda: self.graph_neighbors(args["slug"]),
            "find_path": lambda: self.find_path(args["from_slug"], args["to_slug"]),
            "find_entity": lambda: self.find_entity(args["name"]),
        }
        handler = handlers.get(name)
        if handler is None:
            return f"ERROR: unknown tool '{name}'."
        try:
            return handler()
        except KeyError as exc:
            return f"ERROR: missing argument {exc} for tool '{name}'."
        except Exception as exc:  # keep the loop alive; let the model react
            return f"ERROR in {name}: {exc}"

    def _page_path(self, slug: str) -> Path:
        if not _SLUG_RE.match(slug or ""):
            raise ValueError(f"invalid slug '{slug}'")
        path = (self.pages_dir / f"{slug}.md").resolve()
        if self.pages_dir.resolve() not in path.parents:
            raise ValueError("refusing path outside the pages directory")
        return path

    def _write(self, path: Path, slug: str, content: str, summary: str) -> str:
        if self.dry_run:
            self.edits.append(f"[dry-run] {summary}")
            return f"[dry-run] would write '{slug}' ({len(content)} chars); no changes made."
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.edits.append(summary)
        return f"OK: wrote '{slug}' ({len(content)} chars)."
