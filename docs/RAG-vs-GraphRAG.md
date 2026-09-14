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

## Finding 3 — global search: the community layer wins on thematic questions

Findings 1–2 are about *local* questions (a fact on a page, or a link between two pages).
The consolidation layer (`openwiki communities`) targets a different class entirely:
**thematic / whole-corpus** questions — "what are the main themes", "how do X and Y relate
across the wiki" — that chunk-RAG structurally can't answer (it retrieves a few chunks and
answers narrowly). **Global search** answers them instead by synthesizing over the LLM
community summaries.

To test it we built a 10-question **thematic** set (`eval_thematic.jsonl`, same
`{"question","pages"}` format but broad questions; `pages` = pages the answering themes
should cover). `owiki eval --global` generates a global answer and scores its `[n]`
**community** citations against the ground-truth communities — those containing an expected
page (mapped via `IN_COMMUNITY`). Metrics: **cite-hit** (cited any relevant theme),
**community-recall** (fraction of relevant themes cited), **community-precision** (fraction
of cited themes that are relevant — penalises citing everything). With `--judge`, a
position-balanced **Global vs plain-RAG** verdict.

| Metric (10 thematic questions) | Global search |
|---|---:|
| cite-hit | **100%** |
| community-recall | **95%** |
| community-precision | 56.7% |
| **LLM judge — Global vs RAG** | **9 – 1** |

Global search cites the relevant themes almost perfectly (95% recall) and an LLM judge
preferred it to plain RAG **9–1**. The moderate precision (56.7%) is expected and honest:
broad thematic questions legitimately span several communities, so citing 4–5 of 7 is
reasonable — precision penalises that breadth, recall rewards the coverage. The headline is
the judge: on the question class it was built for, the community layer decisively beats
local retrieval — the mirror image of Finding 1 (where the graph *didn't* help local
retrieval). Same caveats apply (small N, one corpus, judge verbosity/self-preference bias).

---

## Finding 4 — hybrid retrieval (BM25 + dense): ties on prose, **wins on code**

Finding 1 explained *why* graph expansion can't add retrieval recall here: **bge-m3 already
ranks this German prose corpus well**. That same reason predicts where a lexical signal
*would* help — a corpus of **exact tokens a text embedder blurs**: identifiers, acronyms,
symbol names. So we tested hybrid retrieval (BM25 fused with dense cosine via reciprocal
rank fusion, `owiki eval --hybrid`) on two corpora.

**On the NAUTILUS prose manual it ties** — identical MRR/hit/recall as pure dense at every
budget (1, 2, 8). There's nothing for BM25 to rescue: dense already finds the German terms
and acronyms (RPPR, USB, Arpeggiator). It doesn't *hurt* either (unlike LLM re-ranking, which
demoted the top page — MRR 0.81 → 0.57–0.60).

**On a code corpus it wins clearly.** Corpus: OpenWiki's *own source* ingested as a
`--repo` (49 files → 418 chunks), 14 questions asking where an identifier lives / what it
does (`reciprocal_rank_fusion`, `search_hybrid` vs `hybrid_search`, `parse_ollama_stats`,
`capture_session`, …), ground truth = the defining file's page.

| budget | RAG hit@k | **Hybrid hit@k** | RAG MRR | **Hybrid MRR** |
|---|---|---|---|---|
| top-1 | 57.1% | **85.7%** | 0.571 | **0.857** |
| top-3 | 85.7% | **92.9%** | 0.702 | **0.893** |
| top-8 | 100%  | 100%       | 0.735 | **0.911** |

Hybrid lifts **hit@1 by +28.6 points** and MRR from 0.74 → 0.91. Note *recall saturates* at
top-8 (both 100%) — the defining file is findable either way; hybrid's gain is in **ranking**
(getting it to #1), exactly what matters for a code-search "jump to definition" use. The
mechanism is the hypothesis confirmed: BM25 matches `search_hybrid` (search.py) as a literal
token and doesn't confuse it with `hybrid_search` (graph/store.py), whereas the text embedder,
seeing near-identical names, ranks them closer together.

**The retrieval story, whole:** on this project's prose, bge-m3's dense ranking is a strong
baseline that graph expansion (Finding 1), LLM re-ranking, and BM25 fusion each fail to beat —
three honest ties/losses. Hybrid is the one lever that produces a **decisive win**, and only
on the corpus type that predicts it (code). The lesson isn't "hybrid is good" or "bad" — it's
*measure on your corpus*: match the retrieval technique to where your embedder is weak.

*(Caveats: small N (14), one code corpus = OpenWiki itself, one embedder; the ground-truth
file slugs track the current `openwiki/` tree. Directional, not decimal-precise — but the
prose-vs-code contrast is the robust part.)*

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
4. **Match the retrieval technique to where the embedder is weak.** On prose bge-m3 is
   already strong, so graph expansion / re-ranking / BM25 fusion all tie-or-lose; on
   **code** (exact identifiers) **hybrid (BM25 + dense) wins big** (+28.6 pts hit@1). Don't
   adopt a retrieval add-on on faith — `owiki eval` tells you within one run whether it
   helps *your* corpus.

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
owiki eval --project <proj> --answers --judge --eval-set eval_relational.jsonl
```

**Global search** on the thematic set (needs `owiki communities` first):

```
owiki eval --project <proj> --global --judge --eval-set eval_thematic.jsonl
```

**Hybrid vs dense** (Finding 4) — add the `Hybrid (BM25)` row to any retrieval run (no LLM):

```
owiki eval --project <proj> --hybrid                    # ties on prose (NAUTILUS)
```

The **code-corpus** run (Finding 4's win) is reproducible from OpenWiki's own source; the
eval set is committed at `examples/code-eval.jsonl` (its ground-truth file slugs track the
current `openwiki/` tree):

```
owiki init /tmp/owsrc --source /path/to/openwiki --repo && cd /tmp/owsrc && owiki build
owiki eval --eval-set /path/to/openwiki/examples/code-eval.jsonl \
           -i output/index --no-graph --top-k 1 --expand-k 0 --hybrid
```

Note: `--eval-set` accepts a bare name (resolved against the project root) or a path.
(The web UI's Evaluation tab resolves bare set names against the project and runs the same
benchmark live, including the answer-quality job as an async background task.)

Implementation: `openwiki/eval.py` (pure metrics + `evaluate` driver + `run_answer_eval`
/ `grounding` / `judge_pairwise` + `run_global_eval` / `community_grounding`), wired into
the CLI (`owiki eval`, `--answers`/`--global`) and the web UI (`/api/eval`, `/api/compare`,
`/api/answer-eval`).
