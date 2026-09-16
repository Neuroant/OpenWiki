"""Gap & hygiene mining — the actionable half of world-model analysis (P3).

Where `coupling.py` *characterizes* the world model, this module produces a **to-do list**:
concrete, ranked candidates for improving the knowledge base, each read straight off the
graph + the embedding space. All offline (stored embeddings + lexical name matching — no
Ollama), read-only, and fake-testable.

- **link_candidates** — page pairs that co-mention entities but have **no reference edge**
  between them: topically-related pages that don't cite each other → candidate cross-refs.
- **redundant_pages** — page pairs whose embeddings are near-identical (cosine ≥ a high
  threshold) → candidate duplicate/overlapping content to merge.
- **isolated_pages** — semantic outliers (whose nearest neighbor is far) + structural
  orphans (no similar/reference/entity edge) → thin or disconnected regions.
- **entity_merge_candidates** — same-type entity names that are near-duplicates
  (`difflib` ratio) but weren't resolved → candidates for `--resolve-entities` / manual merge.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

import numpy as np

from .coupling import page_vectors

_TRAILING_NUM = re.compile(r"[\s\-_.]*\d+\s*$")


def _titles(index) -> dict:
    return {c.page_slug: getattr(c, "page_title", c.page_slug) for c in index.chunks}


def link_candidates(index, graph, top: int = 15, min_shared: int = 1) -> list:
    """Page pairs that share ≥``min_shared`` entities but have no REFERENCES (or structural)
    edge — the pages discuss the same things yet neither cites the other. Ranked by shared
    entity count, then embedding cosine."""
    slugs, vecs = page_vectors(index)
    idx = {s: i for i, s in enumerate(slugs)}
    present = set(slugs)
    edges = graph.coupling_edges()
    linked = set()
    for kind in ("references", "child_of", "next"):
        for a, b in edges.get(kind, []):
            if a in present and b in present:
                linked.add(frozenset((a, b)))
    titles = _titles(index)
    out = []
    for a, b, n in graph.shared_entity_pairs():
        if a not in present or b not in present or n < min_shared:
            continue
        if frozenset((a, b)) in linked:
            continue
        cos = float(vecs[idx[a]] @ vecs[idx[b]])
        out.append({"a": a, "a_title": titles.get(a, a), "b": b, "b_title": titles.get(b, b),
                    "shared_entities": int(n), "cosine": round(cos, 3)})
    out.sort(key=lambda d: (d["shared_entities"], d["cosine"]), reverse=True)
    return out[:top]


def redundant_pages(index, top: int = 10, min_cos: float = 0.90) -> list:
    """Page pairs whose mean embeddings are near-identical (cosine ≥ ``min_cos``) — likely
    duplicate or heavily-overlapping content. Highest cosine first."""
    slugs, vecs = page_vectors(index)
    if len(slugs) < 2:
        return []
    sims = vecs @ vecs.T
    iu = np.triu_indices(len(slugs), 1)
    cos = sims[iu]
    titles = _titles(index)
    out = []
    for t in np.argsort(-cos):
        c = float(cos[t])
        if c < min_cos:
            break
        i, j = int(iu[0][t]), int(iu[1][t])
        out.append({"a": slugs[i], "a_title": titles.get(slugs[i], slugs[i]),
                    "b": slugs[j], "b_title": titles.get(slugs[j], slugs[j]),
                    "cosine": round(c, 3)})
        if len(out) >= top:
            break
    return out


def isolated_pages(index, graph, bottom: int = 8) -> dict:
    """Pages weakly attached to the rest of the world model: **semantic outliers** (lowest
    nearest-neighbor cosine in the embedding space) + **structural orphans** (no similar /
    reference / entity edge, from ``GraphStore.health``)."""
    slugs, vecs = page_vectors(index)
    titles = _titles(index)
    semantic = []
    if len(slugs) >= 2:
        sims = vecs @ vecs.T
        np.fill_diagonal(sims, -1.0)
        nn = sims.max(axis=1)
        for i in np.argsort(nn)[:bottom]:
            semantic.append({"slug": slugs[i], "title": titles.get(slugs[i], slugs[i]),
                             "nn_cosine": round(float(nn[i]), 3)})
    orphans = []
    try:
        orphans = graph.health().get("orphans", [])
    except Exception:
        pass
    return {"semantic_outliers": semantic, "structural_orphans": orphans}


def _name_sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _numbered_siblings(a: str, b: str) -> bool:
    """True when two names differ only by a trailing number (``Effect Control 1`` vs ``2``) —
    distinct numbered items, not a merge candidate. Precision guard against the lexical
    matcher's blind spot."""
    sa, sb = _TRAILING_NUM.sub("", a).strip(), _TRAILING_NUM.sub("", b).strip()
    return bool(sa) and sa.lower() == sb.lower() and a.strip() != b.strip()


def entity_merge_candidates(graph, top: int = 15, min_sim: float = 0.80,
                            min_len: int = 3) -> list:
    """Same-type entity names that are near-duplicates (``difflib`` ratio ≥ ``min_sim``) but
    remain separate entities — candidates the resolver missed (or that ``--resolve-entities``
    would catch). Blocked by type; short names (< ``min_len`` chars) skipped to cut noise."""
    if not graph.has_entities():
        return []
    by_type: dict = {}
    for e in graph.all_entities():
        name = (e.get("name") or "").strip()
        if len(name) >= min_len:
            by_type.setdefault(e.get("type") or "?", set()).add(name)
    out = []
    for typ, names in by_type.items():
        uniq = sorted(names)
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                if _numbered_siblings(uniq[i], uniq[j]):
                    continue
                s = _name_sim(uniq[i], uniq[j])
                if s >= min_sim:
                    out.append({"type": typ, "a": uniq[i], "b": uniq[j], "similarity": round(s, 3)})
    out.sort(key=lambda d: d["similarity"], reverse=True)
    return out[:top]


def analyze_gaps(index, graph, top: int = 15) -> dict:
    """Run the full gap/hygiene report over a loaded ``SemanticIndex`` + ``GraphStore``.
    Returns a JSON-serializable dict of ranked, actionable candidates. Read-only + offline."""
    return {
        "link_candidates": link_candidates(index, graph, top=top),
        "redundant_pages": redundant_pages(index, top=max(8, top // 2)),
        "isolated_pages": isolated_pages(index, graph, bottom=8),
        "entity_merge_candidates": entity_merge_candidates(graph, top=top),
    }
