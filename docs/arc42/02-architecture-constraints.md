# 2. Architecture Constraints

> arc42 §2 — Constraints the architecture must respect (technical, organizational, conventions,
> legal). **Status: complete.**

## 2.1 Technical Constraints

| # | Constraint | Background / consequence |
|---|---|---|
| TC1 | **Local inference only (Ollama)** | No cloud LLM/embedding APIs, no API keys. All embeddings + chat go through a local Ollama HTTP server (`http://localhost:11434`). Enables Q1 (privacy). |
| TC2 | **Python 3.10–3.13** (`requires-python = ">=3.10,<3.14"`) | Kuzu has **no Windows wheel for 3.14**, so the ceiling is 3.13; 3.13 is used in practice. Code targets 3.10+ (`from __future__ import annotations`; `tomli` backport for <3.11). |
| TC3 | **Windows-primary** | Primary dev/runtime is Windows 11 + a `.venv`. Paths, stdout UTF-8 reconfiguration, and the pipx installer target Windows first; cross-platform is an aim, not yet CI-verified. |
| TC4 | **Minimal dependencies** | Runtime deps are essentially **NumPy, PyMuPDF, Kuzu** (+ pytest for dev). Everything else is Python stdlib (`http.server`, `urllib`, `html.parser`, `argparse`, `tomllib`, `json`). |
| TC5 | **Heavy deps isolated behind boundaries** | `fitz` (PyMuPDF) lives only in `pdf_parser.py`; `kuzu` only in `graph/builder.py` + `graph/store.py`. Non-PDF, non-graph use paths import neither (lazy imports). |
| TC6 | **UTF-8 everywhere** | Sample corpora are German (non-ASCII); all file I/O is UTF-8 with `ensure_ascii=False`; `cli.main()` reconfigures stdout/stderr to UTF-8 for Windows code pages. |
| TC7 | **No build step for the web UI** | The SPA is hand-written vanilla JS + a vendored `marked.min.js`; served by a stdlib `http.server`. No Node/bundler toolchain. |
| TC8 | **Embedded, file-based persistence** | Kuzu stores the graph as a single file (+ `.wal`); the index is `embeddings.npy` + `index.json`; the wiki is Markdown + `wiki.json`. No external database server. |
| TC9 | **Local model resources** | Assumes enough RAM/VRAM to run the configured Ollama models. The *default* chat model (`qwen3:30b-a3b-instruct-2507-q4_K_M`, a 30B-parameter MoE, q4) needs a capable machine; smaller models can be configured (ADR-2), since the agent depends on the `ChatModel` protocol, not a specific model. |

## 2.2 Organizational & Convention Constraints

| # | Constraint | Consequence |
|---|---|---|
| OC1 | **IR boundary** | Everything downstream of ingestion depends only on `openwiki/models.py` (the IR), never on a parser's internals. New parsers slot in behind `sources.parse_source`. |
| OC2 | **Backends behind protocols** | Embedding + chat backends implement the `Embedder` / `ChatModel` protocols; the agent/search never depend on Ollama directly. |
| OC3 | **Capabilities as subcommands** | New features are added as new CLI subcommands, not as more flags on existing ones. |
| OC4 | **Offline, deterministic tests** | Tests never hit the network; Ollama/Kuzu are faked or `pytest.importorskip`-gated; pure logic (ranking, decay, community detection) is unit-tested with plain values. |
| OC5 | **Additive graph layers** | Optional graph layers (entities, communities, reinforcement) use always-created (possibly empty) tables so store code degrades gracefully without them. |
| OC6 | **The index is the source of truth** | The Kuzu graph is a *mirror* (embeddings copied in); the NumPy `SemanticIndex` remains authoritative. |
| OC7 | **Local commit → user pushes** | Work is committed locally; pushing/tagging is an explicit user action. Version policy: features = minor bump, fixes/polish = patch. |

## 2.3 Legal / licensing constraints

| # | Constraint | Note |
|---|---|---|
| LC1 | **PyMuPDF is AGPL-3.0** (dual-licensed; commercial option from Artifex) | The strongest copyleft in the stack. It's isolated in `pdf_parser.py` and imported lazily, so a non-PDF deployment doesn't load it — but any redistribution/network-service use that *includes* PDF parsing must respect AGPL. A future non-AGPL PDF backend (behind the same boundary) is an option if this becomes a problem. |
| LC2 | **Other deps are permissive** | NumPy (BSD-3), Kuzu (MIT), tomli (MIT), pytest (MIT) — no copyleft obligations. |
| LC3 | **No project license declared yet** | The repo ships without a `LICENSE` file (a learning project). A license must be chosen before any redistribution, and it must be **AGPL-compatible** while PyMuPDF is a dependency (see LC1). |
| LC4 | **arc42 template attribution** | These docs follow the arc42 template (© Dr. Gernot Starke, Dr. Peter Hruschka), used under CC-BY-SA for structure only. |

## 2.4 Constraints summary diagram

```mermaid
flowchart TB
  subgraph Runtime["Runtime environment (local)"]
    Py["Python 3.13 venv"]
    Ollama["Ollama server\n(bge-m3 + qwen3)"]
    Kuzu["Kuzu (embedded, single file)"]
  end
  subgraph Deps["Allowed dependencies"]
    NumPy["NumPy"]
    PyMuPDF["PyMuPDF"]
    KuzuLib["kuzu"]
    Stdlib["stdlib only<br>(http.server, urllib, argparse, tomllib)"]
  end
  Py --> Deps
  Py --> Ollama
  Py --> Kuzu
```

---
*Chapter complete. LC1 (PyMuPDF AGPL) + LC3 (no declared license) are the two licensing items
to resolve before any redistribution — they belong on the §11 risk radar if that becomes a goal.*
