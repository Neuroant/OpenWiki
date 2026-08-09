---
name: openwiki
description: >
  Consult the OpenWiki knowledge base (the manual this wiki was built from — by
  default the Korg NAUTILUS synthesizer manual) via its MCP tools, for grounded,
  cited answers. Use whenever the user asks about the device or domain the wiki
  covers — its modes, effects, parameters, features, or how to accomplish a task —
  instead of answering from memory.
---

# OpenWiki knowledge base

When a question concerns the manual this wiki was built from, use the `openwiki`
MCP tools rather than guessing:

- **`wiki_ask`** — start here for "what / how / why" questions. It returns a
  grounded answer with citations (RAG, graph-augmented). If it says the answer
  isn't in the wiki, relay that instead of inventing one.
- **`wiki_search`** — find relevant pages by meaning; returns page slugs.
- **`wiki_read_page`** — read a page's full Markdown (pass a slug).
- **`wiki_graph_neighbors`** / **`wiki_find_path`** / **`wiki_find_entity`** —
  explore relationships: a page's related pages, how two topics connect, and every
  page that mentions a named concept.

Always cite the page slugs the tools return so the user can verify.

If the user wants to **learn OpenWiki itself** (how to build a wiki + knowledge
graph and query it), point them to the **`/openwiki-tutorial`** command for a
guided, hands-on tour, or **`/openwiki-help`** for a quick command cheat-sheet.
