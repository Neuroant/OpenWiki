# OpenWiki — Architecture Documentation (arc42)

This is the architecture documentation for **OpenWiki**, structured after the
[arc42](https://docs.arc42.org/home/) template. arc42 organises architecture knowledge
into 12 sections, from goals and constraints down to building blocks, runtime, decisions,
quality, and risks.

> **Status: DRAFT.** All 12 chapters exist as first-pass drafts. They are being completed
> incrementally — see the status table below. Diagrams use Mermaid (rendered by GitHub).

## Audience & relationship to the other docs

| Doc | Audience | Purpose |
|---|---|---|
| **This (arc42)** | Architects, contributors, "production-readiness" review | The *why* and *how* of the architecture, systematically |
| `CLAUDE.md` (repo root) | Claude Code / AI coding agents | Operational guide: commands, module map, conventions, gotchas |
| `docs/roadmap.md` | Anyone | What was built (history) + prioritized future directions |
| `docs/RAG-vs-GraphRAG.md` | Evaluators | The measured findings behind the graph design |
| `docs/projects.md`, `docs/coding-agents.md` | Users | Feature-level design for the project layer / MCP setup |

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
| 10 | [Quality Requirements](10-quality-requirements.md) | draft |
| 11 | [Risks and Technical Debt](11-risks-and-technical-debt.md) | draft |
| 12 | [Glossary](12-glossary.md) | draft |

## Conventions in this doc

- **Status** per chapter: `draft` (first pass) → `in progress` → `complete`.
- **`TODO:`** markers flag content to be added during completion steps.
- Module/file references use the repo path (e.g. `openwiki/graph/store.py`).
- arc42 template © Dr. Gernot Starke, Dr. Peter Hruschka — CC-BY-SA; used here for structure only.
