"""Build a Kuzu graph that mirrors the wiki + its embeddings.

This is an *additive* layer: it reads the `Wiki` (page tree + order) and the
existing `SemanticIndex` (chunk text + embeddings) and writes a property graph
to `output/graph/`. Neither the wiki files nor the NumPy index are modified — the
embeddings are **mirrored** into `Chunk` nodes so the graph can do vector search
and graph traversal together, while the original `SemanticIndex` keeps working.

Graph model (all edges deterministic or vector-derived — no LLM):
  (Page)-[:CHILD_OF]->(Page)      hierarchy, from the outline
  (Page)-[:NEXT]->(Page)          reading order
  (Chunk)-[:PART_OF]->(Page)      provenance
  (Page)-[:SIMILAR_TO {score}]->(Page)   top-k semantic neighbors (mirrored vectors)
plus an HNSW vector index on Chunk.emb.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import numpy as np

try:
    import kuzu
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Kuzu is required for the graph layer. Install it with `pip install kuzu`. "
        "Note: on Windows it needs Python <=3.13 (no 3.14 wheel yet)."
    ) from exc

from ..search import SemanticIndex
from ..wiki import Wiki

logger = logging.getLogger(__name__)

CHUNK_VECTOR_INDEX = "chunk_vec_index"


class GraphBuilder:
    """Populate a Kuzu database from a :class:`Wiki` and a :class:`SemanticIndex`."""

    def __init__(self, db_path, similar_k: int = 6) -> None:
        self.db_path = Path(db_path)
        self.similar_k = similar_k

    def build(self, wiki: Wiki, index: SemanticIndex, references=None) -> dict:
        if not index.chunks:
            raise ValueError("The semantic index is empty; run `openwiki index` first.")
        dim = int(index.embeddings.shape[1])

        # A clean rebuild each time keeps the graph a pure function of its inputs.
        # Kuzu 0.11 stores the DB as a single file (+ a .wal sibling), but older
        # versions used a directory — handle both.
        self._remove_existing()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        db = kuzu.Database(str(self.db_path))
        conn = kuzu.Connection(db)
        try:
            self._create_schema(conn, dim)
            self._insert_pages(conn, wiki)
            self._insert_chunks(conn, index)
            conn.execute(
                f"CALL CREATE_VECTOR_INDEX('Chunk', '{CHUNK_VECTOR_INDEX}', 'emb');"
            )
            n_similar = self._insert_similarities(conn, wiki, index)
            n_refs = self._insert_references(conn, references or [])
        finally:
            conn.close()
            db.close()

        stats = {
            "pages": len(wiki.pages),
            "chunks": len(index.chunks),
            "similar_edges": n_similar,
            "reference_edges": n_refs,
            "dim": dim,
            "db": str(self.db_path),
        }
        logger.info("Graph built: %s", stats)
        return stats

    def _remove_existing(self) -> None:
        p = self.db_path
        for cand in (p, p.with_name(p.name + ".wal"), p.with_name(p.name + ".tmp")):
            if cand.is_dir():
                shutil.rmtree(cand, ignore_errors=True)
            elif cand.exists():
                cand.unlink()

    # -- schema ---------------------------------------------------------

    def _create_schema(self, conn, dim: int) -> None:
        conn.execute(
            "CREATE NODE TABLE Page("
            "slug STRING, title STRING, level INT64, "
            "pdf_start INT64, pdf_end INT64, seq INT64, PRIMARY KEY(slug));"
        )
        conn.execute(
            f"CREATE NODE TABLE Chunk("
            f"id STRING, page_slug STRING, text STRING, emb FLOAT[{dim}], PRIMARY KEY(id));"
        )
        conn.execute("CREATE REL TABLE CHILD_OF(FROM Page TO Page);")
        conn.execute("CREATE REL TABLE NEXT(FROM Page TO Page);")
        conn.execute("CREATE REL TABLE PART_OF(FROM Chunk TO Page);")
        conn.execute("CREATE REL TABLE SIMILAR_TO(FROM Page TO Page, score DOUBLE);")
        conn.execute("CREATE REL TABLE REFERENCES(FROM Page TO Page);")

    # -- nodes / structural edges --------------------------------------

    def _insert_pages(self, conn, wiki: Wiki) -> None:
        for seq, page in enumerate(wiki.pages):
            conn.execute(
                "CREATE (:Page {slug:$slug, title:$title, level:$level, "
                "pdf_start:$s, pdf_end:$e, seq:$seq});",
                parameters={
                    "slug": page.slug, "title": page.title, "level": page.level,
                    "s": page.pdf_page_start, "e": page.pdf_page_end, "seq": seq,
                },
            )
        for page in wiki.pages:
            if page.parent_slug:
                conn.execute(
                    "MATCH (c:Page {slug:$c}),(p:Page {slug:$p}) CREATE (c)-[:CHILD_OF]->(p);",
                    parameters={"c": page.slug, "p": page.parent_slug},
                )
        for a, b in zip(wiki.pages, wiki.pages[1:]):
            conn.execute(
                "MATCH (a:Page {slug:$a}),(b:Page {slug:$b}) CREATE (a)-[:NEXT]->(b);",
                parameters={"a": a.slug, "b": b.slug},
            )

    def _insert_chunks(self, conn, index: SemanticIndex) -> None:
        page_slugs = self._existing_page_slugs(conn)
        for i, chunk in enumerate(index.chunks):
            emb = index.embeddings[i].astype(float).tolist()
            conn.execute(
                "CREATE (:Chunk {id:$id, page_slug:$slug, text:$text, emb:$emb});",
                parameters={"id": chunk.id, "slug": chunk.page_slug,
                            "text": chunk.text, "emb": emb},
            )
            if chunk.page_slug in page_slugs:
                conn.execute(
                    "MATCH (c:Chunk {id:$id}),(p:Page {slug:$slug}) "
                    "CREATE (c)-[:PART_OF]->(p);",
                    parameters={"id": chunk.id, "slug": chunk.page_slug},
                )
            if (i + 1) % 200 == 0:
                logger.info("  inserted %d/%d chunks", i + 1, len(index.chunks))

    # -- vector-derived edges ------------------------------------------

    def _insert_similarities(self, conn, wiki: Wiki, index: SemanticIndex) -> int:
        """Materialize top-k SIMILAR_TO edges from per-page mean embeddings."""
        page_vecs = self._page_embeddings(wiki, index)
        slugs = [p.slug for p in wiki.pages if p.slug in page_vecs]
        if len(slugs) < 2:
            return 0
        matrix = np.vstack([page_vecs[s] for s in slugs])  # already L2-normalized
        sims = matrix @ matrix.T
        np.fill_diagonal(sims, -np.inf)

        # Don't create SIMILAR_TO where a structural edge already exists.
        related = {p.slug: {p.parent_slug, *p.child_slugs} for p in wiki.pages}

        count = 0
        for i, slug in enumerate(slugs):
            order = np.argsort(-sims[i])
            picked = 0
            for j in order:
                if picked >= self.similar_k or sims[i, j] <= 0:
                    break
                other = slugs[j]
                if other in related[slug]:
                    continue
                conn.execute(
                    "MATCH (a:Page {slug:$a}),(b:Page {slug:$b}) "
                    "CREATE (a)-[:SIMILAR_TO {score:$score}]->(b);",
                    parameters={"a": slug, "b": other, "score": float(sims[i, j])},
                )
                picked += 1
                count += 1
        return count

    def _insert_references(self, conn, references) -> int:
        """Materialize REFERENCES edges (from the 'siehe Seite N' cross-refs)."""
        page_slugs = self._existing_page_slugs(conn)
        count = 0
        for src, dst in references:
            if src in page_slugs and dst in page_slugs and src != dst:
                conn.execute(
                    "MATCH (a:Page {slug:$a}),(b:Page {slug:$b}) CREATE (a)-[:REFERENCES]->(b);",
                    parameters={"a": src, "b": dst},
                )
                count += 1
        return count

    def _page_embeddings(self, wiki: Wiki, index: SemanticIndex) -> dict:
        """Mean of each page's chunk vectors, L2-normalized."""
        by_page: dict[str, list[np.ndarray]] = {}
        for i, chunk in enumerate(index.chunks):
            by_page.setdefault(chunk.page_slug, []).append(index.embeddings[i])
        out: dict[str, np.ndarray] = {}
        for slug, vecs in by_page.items():
            mean = np.mean(np.vstack(vecs), axis=0)
            norm = np.linalg.norm(mean)
            out[slug] = mean / norm if norm else mean
        return out

    @staticmethod
    def _existing_page_slugs(conn) -> set:
        res = conn.execute("MATCH (p:Page) RETURN p.slug;")
        slugs = set()
        while res.has_next():
            slugs.add(res.get_next()[0])
        return slugs


def build_graph(wiki: Wiki, index: SemanticIndex, db_path,
                similar_k: int = 6, references=None) -> dict:
    return GraphBuilder(db_path, similar_k=similar_k).build(wiki, index, references=references)
