# OpenWiki projects — design & roadmap

Status: **Phase 1 in progress.**

An **OpenWiki project** is a folder with a declarative manifest (`openwiki.toml`)
and its own outputs, so state persists and you can keep several knowledge bases
side by side and jump between them — the classical software-project model.

## Concept map

| Classical software | OpenWiki | Role |
|---|---|---|
| `git init` / `cargo new` | `openwiki init` | scaffold a project |
| `pyproject.toml` / `Cargo.toml` | **`openwiki.toml`** | identity + declarative config |
| `src/` | `sources/` | inputs (PDF today; txt/web/repo later) |
| `target/` · `build/` | `output/` (`wiki/`, `index/`, `graph`) | generated artifacts (gitignored) |
| `Cargo.lock` | `.openwiki/state.json` | build provenance + staleness |
| `cargo build` / `make` | `openwiki build` | run the pipeline from the manifest |
| find `.git` upward | discover `openwiki.toml` upward | "which project am I in?" |
| `conda activate` / `kubectl use-context` | `openwiki use <name>` | switch project by name (registry) |
| `git config` layering | flag > env > manifest > user config > default | config resolution |

## Decisions (locked)

- **One corpus per project, multi-source** — one wiki/index/graph built from one or
  more sources merged into a single corpus.
- **`openwiki.toml`** (pyproject-style, with comments). Read via stdlib `tomllib`
  (3.11+) / `tomli` (3.10); **written** via a small hand-rolled emitter for our
  schema (no `tomli-w` dependency).
- **Location-based** resolution is primary (walk up for `openwiki.toml`; `cd` to
  switch; `--project` to override). A named **registry** is an optional fallback
  used only when you are *not* inside a project.
- **Full v1** target: `init` · `build` · `status` · registry (`use`/`project list`)
  · `~/.openwiki/config.toml` global defaults · `.openwiki/state.json` staleness.

## Resolution precedence

```
project:      --project PATH  >  $OPENWIKI_PROJECT  >  nearest openwiki.toml (cwd upward)
                              >  registry active (openwiki use)  >  legacy ./output
per setting:  CLI flag  >  env  >  project [table]  >  ~/.openwiki/config.toml  >  built-in default
```
Location always wins over the registry: `openwiki use X` sets a from-anywhere
default, but `cd`-ing into another project selects *that* one (git-style).

## Manifest (`openwiki.toml`)

```toml
[project]
name = "nautilus-manual"
description = "Korg NAUTILUS DE manual → wiki + graph"

[[sources]]                 # one or more; all merge into ONE corpus
type = "pdf"
path = "sources/301357_NAUTILUS_OG_G1.pdf"

[build]
split_level = 2             # single source of truth — index & graph can't drift
tables = true
chunk_size = 180
overlap = 30

[models]
host  = "http://localhost:11434"
embed = "bge-m3"
chat  = "qwen3:30b-a3b-instruct-2507-q4_K_M"

[graph]
similar_k = 6
references = true
entities = false

[serve]
port = 8137
```

## Layout

```
my-project/
├─ openwiki.toml            # manifest — committed
├─ sources/                 # inputs — committed
├─ output/                  # artifacts — gitignored, regenerable
│   ├─ parsed/  wiki/  index/  graph
└─ .openwiki/state.json     # build provenance / staleness — gitignored
```

## CLI surface

```
openwiki init [DIR] --name N --source PATH…   # scaffold
openwiki build [--only ingest,wiki,index,graph] [--force]   # (Phase 2) run the pipeline from the manifest
openwiki status                                # (Phase 2) name · sources · counts · staleness
openwiki project list | use NAME | add NAME PATH   # (Phase 3) registry
openwiki search|ask|chat|serve|mcp|ingest|build-wiki|index|graph-build   # project-aware; flags override
# global: --project PATH  (+ $OPENWIKI_PROJECT)
```

## Multi-source merge (the one pipeline change)

`build-wiki` takes a single `ParsedDocument`; multi-source needs a combine step:
ingest each source, concatenate pages with a running page offset, wrap each source
under a synthetic top-level outline node (its name) so slugs don't collide.
**Cross-references stay per-source** — `detect_page_offset` runs per source and
"siehe Seite N" resolves only within that source's physical page span. Staged last.

## Roadmap

- [ ] **Phase 1 — foundation** *(in progress)*
  - [x] `Project` model: discover (`find`), `load`, `resolve`, layout dirs, setting precedence
  - [x] `openwiki init` (scaffold `openwiki.toml` + `sources/` + `.gitignore`)
  - [x] Make existing commands project-aware (`--project`, manifest fills path/model/host/split-level defaults)
  - [x] Back-compat: no manifest → today's `./output`
  - [x] `tomllib`/`tomli` read + hand-rolled TOML writer; tests
- [ ] **Phase 2 — build & status**
  - [ ] `openwiki build` (single-source: ingest → wiki → index → graph from the manifest)
  - [ ] `.openwiki/state.json` staleness (incremental builds; split-level mismatch guard)
  - [ ] `openwiki status`
- [ ] **Phase 3 — registry & global config**
  - [ ] `~/.openwiki/config.toml` cross-project defaults
  - [ ] registry: `openwiki project list` / `use` / `add` / `remove`
  - [ ] general TOML emitter (`project add-source` rewrites the manifest)
- [ ] **Phase 4 — multi-source merge**
  - [ ] combine `ParsedDocument`s (page offset + per-source top node)
  - [ ] per-source reference offsets in the graph builder
