"""World-model analysis — measure the *structure and organization* of the gathered
knowledge, treating the two representations OpenWiki holds over the same corpus as
facets of one object: the **symbolic graph** (Kuzu) and the **semantic space** (the
embedding matrix).

P1 is the **graph↔semantic coupling** analysis (`coupling.py`): where do the graph's
edges *agree* with the embedding geometry (redundant) vs *disagree* (the graph's own,
non-semantic structure)? The headline extends the project's RAG-vs-GraphRAG finding
from "does the graph help retrieval?" to "how much structure does the graph encode
that similarity alone would miss?".

Read-only + additive (never mutates the graph or index). numpy core; scikit-learn
(the ``[analysis]`` extra) enriches the community-coherence metric — its absence
degrades gracefully.
"""

from .compare import diff_fingerprints, flatten_fingerprint, is_coupling_fingerprint
from .coupling import analyze_coupling, page_vectors
from .gaps import analyze_gaps
from .memory import analyze_memory
from .projection import project_2d

__all__ = ["analyze_coupling", "analyze_gaps", "analyze_memory", "diff_fingerprints",
           "flatten_fingerprint", "is_coupling_fingerprint", "page_vectors", "project_2d"]
