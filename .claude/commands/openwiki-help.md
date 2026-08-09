---
description: Quick OpenWiki help — explain any feature/command or answer a "how do I …" usage question
argument-hint: "[a feature, command, or 'how do I …' question]"
---
Answer the user's OpenWiki **usage** question concisely and accurately:
**$ARGUMENTS** (if empty, **just print the cheat-sheet below verbatim and stop — do not call any tools**; offer the full,
hands-on walkthrough via `/openwiki-tutorial`).

Show the exact command + its key options, then a one-line example. If the question
is about the *content* of the wiki (not how to use OpenWiki), use the `wiki_*` MCP
tools instead of guessing — `wiki_ask` for a grounded, cited answer. For a guided
tour of every feature, point the user to **`/openwiki-tutorial`**.

### Cheat-sheet (`<python> -m openwiki …`; Ollama running with `bge-m3` + `qwen3:30b-a3b-instruct-2507-q4_K_M`)
- `ingest <pdf>` → `output/<stem>.json` (+ `.md`). `--images`, `--max-pages N`, `--no-tables`.
- `build-wiki <pdf|json>` → `output/wiki/` (`index.md`, `wiki.json`, `pages/*.md`). `--split-level N`.
- `index <pdf|json>` → `output/index/` (embeddings, `bge-m3`). `--split-level N`, `--chunk-size`, `--overlap`.
- `search "query"` — semantic search. `-k N`, `--full`.
- `ask "question"` — RAG + citations; `--graph` / `--expand-k` / `--no-graph` for GraphRAG; `--show-context`.
- `chat` — multi-turn editing agent; `--dry-run`, `-m "msg"`.
- `graph-build <pdf|json>` → `output/graph/` (Kuzu). `--entities`, `--similar-k`, `--no-references`. Keep `--split-level` = the one used for `index`.
- `serve --port 8137` — web UI (Wiki / Hilfe / Tutorial + Graph tab).
- `mcp --wiki … -i … --graph …` — expose the wiki to coding agents (the `wiki_*` tools).

MCP query tools: `wiki_ask`, `wiki_search`, `wiki_read_page`, `wiki_list_pages`,
`wiki_graph_neighbors`, `wiki_find_path`, `wiki_find_entity`. Full docs: the in-app
**Hilfe** tab, `README.md`, `CLAUDE.md`, `docs/coding-agents.md`.
