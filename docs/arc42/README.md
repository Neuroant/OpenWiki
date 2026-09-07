# OpenWiki — Architecture Documentation (arc42)

This is the architecture documentation for **OpenWiki**, structured after the
[arc42](https://docs.arc42.org/home/) template. arc42 organises architecture knowledge
into 12 sections, from goals and constraints down to building blocks, runtime, decisions,
quality, and risks.

> **Status: COMPLETE.** All 12 chapters are complete (a first full pass). Diagrams use Mermaid
> (rendered by GitHub) and have been validated. Maintain alongside the code — Path B will
> re-open ADR-3/ADR-8 (§9) and the §11 debt items D1/D2/D6.

## Audience & relationship to the other docs

| Doc | Audience | Purpose |
|---|---|---|
| **This (arc42)** | Architects, contributors, "production-readiness" review | The *why* and *how* of the architecture, systematically |
| `CLAUDE.md` (repo root) | Claude Code / AI coding agents | Operational guide: commands, module map, conventions, gotchas |
| `docs/roadmap.md` | Anyone | What was built (history) + prioritized future directions |
| `docs/RAG-vs-GraphRAG.md` | Evaluators | The measured findings behind the graph design |
| `docs/projects.md` | Architects, users | Deep design of the **project** concept (§8.14): manifest, discovery, registry, layout, multi-source merge |
| `docs/path-b-memory.md` | Architects | **Path B (agent memory)** design — re-opens ADR-3/ADR-8, addresses §11 D1/D2/D6 |
| `docs/coding-agents.md` | Users | MCP / OpenCode / Claude Code setup |

The arc42 docs and `CLAUDE.md` overlap by design (both describe the architecture); this doc
cross-references `CLAUDE.md` for exhaustive module detail rather than duplicating it.

## Chapters

| # | Chapter | Status |
|---|---|---|
| 1 | [Introduction and Goals](01-introduction-and-goals.md) | **complete** |
| 2 | [Architecture Constraints](02-architecture-constraints.md) | **complete** |
| 3 | [Context and Scope](03-context-and-scope.md) | **complete** |
| 4 | [Solution Strategy](04-solution-strategy.md) | **complete** |
| 5 | [Building Block View](05-building-block-view.md) | **complete** |
| 6 | [Runtime View](06-runtime-view.md) | **complete** |
| 7 | [Deployment View](07-deployment-view.md) | **complete** |
| 8 | [Cross-cutting Concepts](08-crosscutting-concepts.md) | **complete** |
| 9 | [Architecture Decisions](09-architecture-decisions.md) | **complete** |
| 10 | [Quality Requirements](10-quality-requirements.md) | **complete** |
| 11 | [Risks and Technical Debt](11-risks-and-technical-debt.md) | **complete** |
| 12 | [Glossary](12-glossary.md) | **complete** |

## Conventions in this doc

- **Status** per chapter: `draft` (first pass) → `in progress` → `complete`.
- **`TODO:`** markers flag content to be added during completion steps.
- Module/file references use the repo path (e.g. `openwiki/graph/store.py`).
- arc42 template © Dr. Gernot Starke, Dr. Peter Hruschka — CC-BY-SA; used here for structure only.
