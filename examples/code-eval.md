# `code-eval.jsonl` — a code-corpus retrieval eval

A 14-question retrieval eval set over **OpenWiki's own source** ingested as a code repo. It
demonstrates **Finding 4** of [`docs/RAG-vs-GraphRAG.md`](../docs/RAG-vs-GraphRAG.md): hybrid
retrieval (BM25 + dense) *ties* pure dense on prose but **wins clearly on code** — where the
answer is an exact identifier a text embedder blurs.

Each line is `{"question", "pages"}`; the ground-truth `pages` are the wiki slugs of the file
that **defines** the identifier (e.g. `reciprocal_rank_fusion` → `022-lexical-py`).

## Reproduce

```bash
# 1. ingest openwiki/ as a code corpus (CodeParser → one page per .py file)
owiki init /tmp/owsrc --source /path/to/openwiki --repo
cd /tmp/owsrc && owiki build            # ingest → wiki → index (needs Ollama + bge-m3)

# 2. compare pure-dense RAG vs hybrid (BM25+dense) — no LLM needed for retrieval eval
owiki eval --eval-set /path/to/openwiki/examples/code-eval.jsonl \
           -i output/index --no-graph --top-k 1 --expand-k 0 --hybrid
```

Result (v0.62, bge-m3): hybrid lifts **hit@1 57.1% → 85.7%** (+28.6 pts) and MRR 0.74 → 0.91.

> **Note:** the ground-truth slugs (`003-agent-py`, `022-lexical-py`, …) are numbered by
> `CodeParser`'s file order, so they track the `openwiki/` tree at the time of writing. If the
> package's file set changes, regenerate the slugs (`wiki.json`) and update the `pages` here.
