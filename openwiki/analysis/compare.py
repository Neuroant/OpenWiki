"""Compare two world-model coupling fingerprints (P3b) — the *compare* in "measure,
compare, analyze".

A coupling fingerprint (`analyze_coupling`'s output) is a compact, normalized description of
a knowledge base's structure, so two of them can be diffed directly: the same corpus across
versions or settings, two corpora, or two embedders. Because the metrics are *relative*
(reach fractions, cosine lift over a null, neighbor overlap, silhouette/ARI), they compare
meaningfully even across different corpora.

Pure + testable: `flatten_fingerprint` reduces a fingerprint to a flat ``{metric: value}``
map; `diff_fingerprints` aligns two and reports per-metric deltas.
"""

from __future__ import annotations

_EDGE_KINDS = ("similar", "references", "shared_entity", "relation", "child_of", "next")


def is_coupling_fingerprint(fp) -> bool:
    """Whether ``fp`` looks like an `analyze_coupling` result (vs. a gaps report or junk)."""
    return isinstance(fp, dict) and "edge_profile" in fp and "graph_reach" in fp


def flatten_fingerprint(fp: dict) -> dict:
    """Reduce a coupling fingerprint to a flat ``{metric: scalar}`` map of the comparable
    numbers (missing/unavailable metrics are simply omitted)."""
    out: dict = {}
    if fp.get("pages") is not None:
        out["pages"] = fp["pages"]
    gr = fp.get("graph_reach") or {}
    if gr.get("non_semantic_fraction") is not None:
        out["graph_reach"] = gr["non_semantic_fraction"]
    coh = fp.get("community_coherence") or {}
    if coh.get("available"):
        if coh.get("silhouette") is not None:
            out["coherence.silhouette"] = coh["silhouette"]
        if coh.get("ari") is not None:
            out["coherence.ari"] = coh["ari"]
    prof = fp.get("edge_profile") or {}
    nul = prof.get("_null") or {}
    if nul.get("mean") is not None:
        out["null.cos_mean"] = nul["mean"]
    ov = fp.get("neighbor_overlap") or {}
    for k in _EDGE_KINDS:
        p = prof.get(k) or {}
        if p.get("n"):
            out[f"{k}.n"] = p["n"]
            if p.get("mean") is not None:
                out[f"{k}.cos_mean"] = p["mean"]
            if p.get("lift") is not None:
                out[f"{k}.lift"] = p["lift"]
        if ov.get(k) is not None:
            out[f"{k}.overlap"] = ov[k]
    return out


def diff_fingerprints(a: dict, b: dict) -> list:
    """Align two fingerprints into ``[{metric, a, b, delta}, …]`` (delta = ``b − a`` when both
    are numeric, else ``None``). Metric order: A's keys, then any B-only keys."""
    fa, fb = flatten_fingerprint(a), flatten_fingerprint(b)
    keys = list(dict.fromkeys(list(fa.keys()) + list(fb.keys())))
    rows = []
    for k in keys:
        va, vb = fa.get(k), fb.get(k)
        delta = (round(vb - va, 4)
                 if isinstance(va, (int, float)) and isinstance(vb, (int, float)) else None)
        rows.append({"metric": k, "a": va, "b": vb, "delta": delta})
    return rows


def notable_differences(rows: list, top: int = 3) -> list:
    """The largest-magnitude *rate* deltas (fractions/lift/overlap/coherence) — skips raw
    counts (``pages``, ``*.n``) whose deltas aren't scale-comparable."""
    rate = [r for r in rows if r["delta"] is not None and abs(r["delta"]) > 1e-9
            and r["metric"] != "pages" and not r["metric"].endswith(".n")]
    return sorted(rate, key=lambda r: abs(r["delta"]), reverse=True)[:top]
