# OpenWiki

Learning to build **agentic wikis** — pipelines that turn source documents into
structured, machine-navigable knowledge bases.

This repo starts at the beginning of that pipeline: **PDF ingestion**. The
`openwiki ingest` tool extracts a PDF's text, tables, table-of-contents outline,
and (optionally) images into a structured document model, then writes it as
**JSON** (for downstream agents / retrieval) and **Markdown** (for humans).

Then `openwiki build-wiki` splits that model into a tree of linked Markdown wiki
pages along the document's outline — an `index.md`, one file per section, and a
`wiki.json` manifest.

Then `openwiki index` + `openwiki search` add **semantic search**: the wiki pages
are chunked, embedded with a local Ollama model, and queried by meaning.

Then `openwiki ask` puts a **RAG agent** on top: it retrieves the most relevant
chunks and has a local Ollama chat model answer your question with citations back
to the wiki pages.

Then `openwiki chat` is a **multi-turn agent** that can not only answer but also
**edit** the wiki — searching, reading, and writing pages through tool calls.

Finally, `openwiki serve` puts it all in the browser: a **web UI** to browse
pages, search, and chat with the agent (including its editing tools).

The sample document is `301357_NAUTILUS_OG_G1.pdf` — the German Korg NAUTILUS
synthesizer manual (269 pages, 228 outline entries).

## Quickstart

Requires Python 3.10+.

```bash
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"        # Windows
# python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # macOS/Linux
```

Activate the environment (`.venv\Scripts\activate` on Windows,
`source .venv/bin/activate` elsewhere), then ingest the sample PDF:

```bash
openwiki ingest 301357_NAUTILUS_OG_G1.pdf
# equivalently: python -m openwiki ingest 301357_NAUTILUS_OG_G1.pdf
```

Results land in `./output/`:

- `301357_NAUTILUS_OG_G1.json` — full structured extraction
- `301357_NAUTILUS_OG_G1.md` — readable Markdown

### Options

| Flag | Effect |
| --- | --- |
| `--out DIR` | Output directory (default `./output`) |
| `--no-tables` | Skip table detection (much faster) |
| `--images` | Extract embedded images to `<out>/images/` |
| `--max-pages N` | Parse only the first N pages |
| `-v` | Verbose progress logging |

Quick check on the first few pages:

```bash
openwiki ingest 301357_NAUTILUS_OG_G1.pdf --max-pages 5 -v
```

### Build a wiki from the extracted content

```bash
openwiki build-wiki output/301357_NAUTILUS_OG_G1.json
# ...or straight from the PDF:  openwiki build-wiki 301357_NAUTILUS_OG_G1.pdf
```

This writes `output/wiki/`:

```
output/wiki/
  index.md        # nested table of contents linking to every page
  wiki.json       # manifest: page tree, titles, PDF page ranges
  pages/
    000-front-matter.md
    003-vorstellung-des-nautilus.md
    ...
```

Every page gets breadcrumbs, a subpage list, an in-page contents list, the
section text and tables, and prev/next navigation. Control granularity with
`--split-level N` (default `2` = chapters + major sections; `1` = chapters only).

### Semantic search

Semantic search needs a running [Ollama](https://ollama.com) with the embedding
model pulled:

```bash
ollama pull bge-m3        # multilingual, 1024-dim (good for the German content)
```

Build the index, then query by meaning:

```bash
openwiki index output/301357_NAUTILUS_OG_G1.json
openwiki search "Wie stelle ich die Lautstärke ein?" -k 5
```

Results are ranked by cosine similarity and point back to the wiki page and PDF
page range:

```
1. [0.703] Sampeln im SEQUENCER-Modus  ·  PDF p.154–156
    pages/030-sampeln-im-sequencer-modus.md   (030-sampeln-im-sequencer-modus#4)
    REC- und SAMPLING START/STOP-Button, um die Aufnahmebereitschaft zu …
```

Swap models with `--model` (e.g. `--model nomic-embed-text`); the backend is
pluggable via the `Embedder` protocol in `openwiki/embeddings.py`.

### Ask questions (RAG)

`ask` retrieves the top chunks and has a local chat model answer with citations.
Pull a chat model first (any instruct model works; this is the default):

```bash
ollama pull qwen2.5:14b-instruct-q4_K_M
```

```bash
openwiki ask "Was ist Smooth Sound Transitions (SST) und wozu dient es?"
```

```
Smooth Sound Transitions (SST) sorgt dafür, dass Sounds beim Wechsel zwischen
Programmen, Kombinationen oder Songs natürlich ausklingen statt abgeschnitten zu
werden [1][3].

Sources  (* = cited):
 *[1] Smooth Sound Transitions (SST)  ·  PDF p.127–128  ·  pages/025-…md  (0.729)
  [2] Inhalt  ·  PDF p.3–6  ·  pages/002-inhalt.md  (0.635)
 *[3] Set Lists  ·  PDF p.119–119  ·  pages/022-set-lists.md  (0.631)
```

Options: `--model` (default `qwen2.5:14b-instruct-q4_K_M`), `-k` (chunks to
retrieve), `--temperature`, `--show-context` (print the excerpts too). The system
prompt keeps the model **grounded** — it answers only from the retrieved excerpts
and says so when the answer isn't there.

### Chat + edit the wiki (agent)

`chat` is a multi-turn agent that can search, read, and **edit** wiki pages via
tool calls. Script turns with `-m` (repeatable, one session), or omit `-m` for an
interactive REPL:

```bash
openwiki chat --show-tools -m "Erstelle eine Seite 'glossar' mit den Begriffen Programm und Kombination." -m "Ergänze das Glossar um den Begriff 'Arpeggiator'."
```

```
  · create_page(slug=glossar, title=Glossar, …) → OK: wrote 'glossar' (338 chars).
  · append_section(slug=glossar, heading=Arpeggiator, …) → OK: wrote 'glossar' (508 chars).

assistant> Der Begriff 'Arpeggiator' wurde dem Glossar hinzugefügt.
```

Tools: `search_wiki`, `list_pages`, `read_page`, `edit_page`, `append_section`,
`create_page`. Edits are written into `output/wiki/pages/` — use `--dry-run` to
preview them first. File access is confined to the pages directory.

### Web UI

`serve` starts a local web UI (stdlib `http.server`, no extra dependencies) that
combines everything: browse the page tree, run semantic search, and chat with the
agent — including asking it to edit pages, which updates the open page live.

```bash
openwiki serve --port 8137
# then open http://127.0.0.1:8137
```

Left pane: search + nav tree. Center: the rendered page. Right: the agent chat
(tool calls are shown; write tools flagged with ✎). Use `--dry-run` to let the
agent preview edits without writing. Needs the wiki (`build-wiki`) and — for
search/chat — the index (`index`) and a running Ollama.

## Use as a library

```python
from openwiki import PDFParser

doc = PDFParser().parse("301357_NAUTILUS_OG_G1.pdf", max_pages=10)
print(doc.metadata.title, doc.metadata.page_count)

for item in doc.outline:                    # the table-of-contents tree
    print("  " * (item.level - 1), item.title)

print(doc.pages[0].text)
```

## Tests

```bash
python -m pytest
```

## How it fits together

```
PDF ──PDFParser──▶ ParsedDocument ──▶ JSON / Markdown
                        │  (metadata, outline, pages[text, tables, images])
                 WikiBuilder ──▶ Wiki ──▶ output/wiki/ (index.md, pages/*.md, wiki.json)
                        │
              chunk_wiki + Embedder ──▶ SemanticIndex ──▶ output/index/
                        │
                RAGAgent + ChatModel ──▶ cited answer
                        │
             WikiAgent + WikiTools ──▶ edits pages/*.md
                        │
              WikiWebApp (http.server) ──▶ browser UI
```

- `openwiki/models.py` — the structured document model shared by everything downstream
- `openwiki/pdf_parser.py` — PyMuPDF-based extraction (the only place PDF internals live)
- `openwiki/wiki.py` — splits the model into linked wiki pages along the outline
- `openwiki/chunking.py` — cuts page text into overlapping chunks with provenance
- `openwiki/embeddings.py` — pluggable embedding backends (`OllamaEmbedder`)
- `openwiki/search.py` — the semantic index (embed, persist, cosine query)
- `openwiki/llm.py` — pluggable chat backends (`OllamaChat`)
- `openwiki/agent.py` — the RAG agent (retrieve → grounded prompt → cited answer)
- `openwiki/tools.py` — the read/write tools the editing agent calls
- `openwiki/chat_agent.py` — the multi-turn editing agent (tool loop + history)
- `openwiki/web/` — stdlib web server + vanilla-JS SPA (browse, search, chat/edit)
- `openwiki/cli.py` — the `openwiki` command line (`ingest`, `build-wiki`, `index`, `search`, `ask`, `chat`, `serve`)

## Roadmap

- [x] **Ingestion** — PDF → structured model (JSON + Markdown)
- [x] **Wiki generation** — split into linked pages along the outline tree
- [x] **Semantic search** — chunk + embed (local Ollama) with cosine retrieval
- [x] **RAG agent** — grounded, cited answers via a local Ollama chat model
- [x] **Editing agent** — multi-turn, tool-using agent that edits wiki pages (write-back)
- [x] **Web UI** — browse, search, and chat/edit in the browser (`openwiki serve`)
