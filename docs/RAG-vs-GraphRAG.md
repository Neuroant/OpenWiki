# RAG vs GraphRAG on OpenWiki

Does adding a knowledge graph to retrieval make the RAG agent better? OpenWiki was
built partly to answer that honestly, with numbers rather than vibes. This doc
records what we measured.

**TL;DR** — On this corpus GraphRAG **does not improve retrieval recall** (pure
semantic RAG is equal or better at every budget, on both a definitional and a
relational question set) — but it **does improve answer quality** (better citation
grounding and a clear LLM-judge preference, on both sets). The graph's payoff is in
the *answer*, not the *ranking*: the topically-connected pages it pulls in help the
model even when they displace a semantic hit, so page-recall drops while answer
grounding rises.

---

## The setup

Everything is a **same-budget** comparison — the fair question is not "does the graph
retrieve more pages" (of course it can, if you let it) but "does graph expansion beat
spending that same budget on *more semantic hits*." So both retrievers get the same
total page budget `k = top_k + expand_k`:

- **RAG** — the top `k` semantically nearest wiki pages (bge-m3 cosine).
- **GraphRAG** — `top_k` semantic **seeds**, then `expand_k` pages reached by walking
  the graph from those seeds (`SIMILAR_TO` / `REFERENCES` / `CHILD_OF` / `NEXT`, plus
  `shared_entity` when the entity layer is present), re-ranked against the query by
  `best_chunk_per_page`. Same expansion the live agent uses (`agent._EXPAND_RELS`).

Two question sets, both ground-truthed to page slugs (per-project JSONL):

- **`eval.jsonl`** — 14 **definitional** questions ("Was ist eine Halbgruppe?"). The
  answer usually lives on a single page.
- **`eval_relational.jsonl`** — 12 **relational** questions built from graph-connected
  page pairs ("Wie hängen X und Y zusammen?"). The answer genuinely spans pages the
  graph links.

**Metrics.** Retrieval: `MRR` (mean reciprocal rank of the first ground-truth page),
`hit@k` (≥1 ground-truth page in the top `k`), `recall@k` (fraction of ground-truth
pages in the top `k`). Answer quality: **cite-hit** (did the generated answer cite
≥1 ground-truth page?), **exp-recall** (fraction of ground-truth pages the answer
cited) — both objective, read straight off the eval set from the answer's `[n]`
citation markers — and an **LLM-as-judge** pairwise verdict (position-balanced across
questions to cancel A/B order bias).

**Config (measured 2026-08-17).** Corpus: the German *informatik* CS-lecture project
— 16 PDFs → 799 source pages → **76 wiki pages** → **2703 chunks** (bge-m3, 1024-dim)
→ graph of 76 pages / 2703 chunks / **760 `SIMILAR_TO`** / **32 `REFERENCES`** edges
(+ an LLM-extracted entity layer). Budget: `top_k=5 + expand_k=3` (`k=8`). Embedder
`bge-m3`; answer + judge model `qwen3:30b-a3b-instruct-2507-q4_K_M`; local Ollama.

---

## Finding 1 — retrieval recall: the graph does not help

| Set | Retriever | MRR | hit@k | recall@k |
|---|---|---:|---:|---:|
| Definitional (14q) | RAG | **0.849** | **100.0%** | **100.0%** |
| Definitional (14q) | GraphRAG | 0.839 | 92.9% | 92.9% |
| Relational (12q) | RAG | 0.468 | 100.0% | **91.7%** |
| Relational (12q) | GraphRAG | **0.470** | 100.0% | 87.5% |

Pure RAG matches or beats GraphRAG on recall on **both** sets (definitional −7.1 pts,
relational −4.2 pts), and across every budget we've tried the gap runs ~4–12 pts the
same direction. MRR and hit@k are essentially a wash (relational MRR even edges
GraphRAG by 0.002 — noise at this N).

**Why.** bge-m3 already ranks the relevant pages highly. At a fixed budget, GraphRAG
*restricts* the candidate pool to the seeds' graph neighbours and re-ranks them by the
*same* query — a strictly smaller candidate set than ranking over the whole wiki. So
expansion can only **displace** good semantic hits, never surface a page that ranking
over everything wouldn't have found at the same `k`. On a corpus where the embedder is
strong, that trade is a small net loss.

---

## Finding 2 — answer quality: the graph wins

Now generate the actual answers (RAG agent, graph off vs on) and score them:

| Set | Retriever | cite-hit | exp-recall | LLM judge |
|---|---|---:|---:|:--|
| Definitional (14q) | RAG | 85.7% | 82.1% | 3 |
| Definitional (14q) | GraphRAG | 85.7% | **85.7%** | **11** |
| Relational (12q) | RAG | 58.3% | 45.8% | 4 |
| Relational (12q) | GraphRAG | **66.7%** | **58.3%** | **8** |

GraphRAG wins answer quality on both sets — but the *shape* of the win differs, and
the split is itself the interesting result:

- **Relational set** — the graph helps on *every* axis: cite-hit +8.4 pts, exp-recall
  +12.5 pts, judge **8–4**. When the answer genuinely spans connected pages, pulling in
  the neighbour is exactly right.
- **Definitional set** — the *objective* grounding lift shrinks (cite-hit **ties** at
  85.7%, exp-recall only +3.6 pts). That fits intuition: a definition lives on one page,
  so there's little extra citation for expansion to add. Yet the judge preferred GraphRAG
  **even more** strongly, **11–3**.

**Why the judge still prefers GraphRAG when grounding is tied.** The graph pulls in
topically-adjacent pages that don't change *which* ground-truth page gets cited, but do
give the model more relevant surrounding context — so it writes a fuller, better-situated
answer that the judge rewards. The extra context helps the *writing* even when it doesn't
move the *citation*.

**The core pattern, on both sets:** retrieval recall drops (definitional 92.9% vs 100%,
relational 87.5% vs 91.7%) **while answer grounding holds or rises**. Page-recall down,
answer quality up. Optimising retrieval recall would have told you to drop the graph;
optimising the answer says keep it.

---

## Caveats / threats to validity

- **Small N** (12–14 questions per set), one corpus, one embedder. The result is robust
  *for what it is* because two independent signals agree — an objective metric
  (exp-recall, read from citation markers) and a subjective one (the judge) — but treat
  the exact percentages as directional, not decimal-precise.
- **Judge length/verbosity bias.** GraphRAG answers carry more retrieved context, so they
  tend to be longer, and LLM judges lean toward longer answers. The lopsided **11–3** on
  the definitional set — where objective grounding is basically tied — is partly this. The
  objective **exp-recall** edge (+3.6 def, +12.5 rel) is independent of length and still
  favours GraphRAG, which is why we lead with it.
- **Self-preference.** The judge (`qwen3:30b…`) is the same model family that wrote the
  answers. Position bias is controlled (A/B balanced); model-family self-preference is not.
- **Strong embedder.** bge-m3 ranks this German corpus well, which is precisely why graph
  expansion can't add retrieval recall. A weaker or monolingual embedder would leave more
  room — the retrieval conclusion may not transfer.

---

## Take-aways

1. **Don't** adopt GraphRAG expecting better retrieval recall when your embedder already
   ranks the corpus well — same-budget graph expansion *costs* recall by displacing hits.
2. **Do** value the graph for **answer quality** — grounding and completeness — most on
   relational questions, but the judge favours it broadly.
3. The graph's largest value here isn't in either number: it's **human exploration**
   (the Graph tab, `find_path`, `find_entity`) and structural navigation, which these
   retrieval/answer metrics don't capture at all.

---

## Reproduce

Retrieval only (fast — no LLM, just embedding search):

```
owiki eval --project <proj>                                            # default eval.jsonl
owiki eval --project <proj> --eval-set <proj>/eval_relational.jsonl
```

Retrieval **+ answer quality** (slow — 2–3 chat calls/question):

```
owiki eval --project <proj> --answers --judge
owiki eval --project <proj> --answers --judge --eval-set <proj>/eval_relational.jsonl
```

Note: the CLI `--eval-set` takes a path relative to the **current directory**, not the
project root — pass an absolute or project-relative path for a non-default set. (The
web UI's Evaluation tab resolves bare set names against the project and runs the same
benchmark live, including the answer-quality job as an async background task.)

Implementation: `openwiki/eval.py` (pure metrics + `evaluate` driver + `run_answer_eval`
/ `grounding` / `judge_pairwise`), wired into the CLI (`owiki eval`) and the web UI
(`/api/eval`, `/api/compare`, `/api/answer-eval`).
