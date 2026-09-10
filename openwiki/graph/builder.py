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
from typing import Optional

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

    def build(self, wiki: Wiki, index: SemanticIndex, references=None, entities=None) -> dict:
        if not index.chunks:
            raise ValueError("The semantic index is empty; run `openwiki index` first.")
        dim = int(index.embeddings.shape[1])

        # B0 (Path B): the graph is authoritative for *remembered* content, so a rebuild
        # from documents must preserve the remembered tier. Snapshot it before the
        # destructive rebuild and restore it into the fresh schema afterwards.
        preserved = self._snapshot_memory()

        # A clean rebuild each time keeps the *derived* subgraph a pure function of its
        # inputs. Kuzu 0.11 stores the DB as a single file (+ a .wal sibling), but older
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
            n_entities, n_mentions = self._insert_entities(conn, entities or [])
            n_assertions, n_reinf = self._restore_memory(conn, preserved, dim)
        finally:
            conn.close()
            db.close()

        stats = {
            "pages": len(wiki.pages),
            "chunks": len(index.chunks),
            "similar_edges": n_similar,
            "reference_edges": n_refs,
            "entities": n_entities,
            "mention_edges": n_mentions,
            "preserved_assertions": n_assertions,
            "preserved_reinforced": n_reinf,
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

    # -- remembered tier: preserve across a doc rebuild (B0) ------------

    def _snapshot_memory(self) -> Optional[dict]:
        """Read the remembered tier out of an existing graph so the doc rebuild can put
        it back (B0 exit criterion). Returns ``None`` when there is no existing DB or
        nothing remembered. Each table is read independently, so an older graph missing
        some of them still yields whatever it does have."""
        if not self.db_path.exists():
            return None
        try:
            db = kuzu.Database(str(self.db_path))
            conn = kuzu.Connection(db)
        except Exception as exc:      # locked / unreadable — don't clobber, but warn loudly
            logger.warning("could not open existing graph to preserve memory: %s", exc)
            return None
        try:
            snap = {
                "sessions": self._read_rows(conn, "MATCH (s:Session) RETURN s.id, s.created_at;"),
                "assertions": self._read_rows(
                    conn, "MATCH (a:Assertion) RETURN a.id, a.subject, a.predicate, a.object, "
                          "a.session_id, a.created_at, a.emb;"),
                "asserts": self._read_rows(
                    conn, "MATCH (s:Session)-[:ASSERTS]->(a:Assertion) RETURN s.id, a.id;"),
                "supersedes": self._read_rows(
                    conn, "MATCH (n:Assertion)-[:SUPERSEDES]->(o:Assertion) RETURN n.id, o.id;"),
                "reinforces": self._read_rows(
                    conn, "MATCH (a:Page)-[r:REINFORCES]->(b:Page) "
                          "RETURN a.slug, b.slug, r.weight, r.last_seen;"),
            }
        finally:
            conn.close()
            db.close()
        return snap if any(snap.values()) else None

    def _restore_memory(self, conn, snap: Optional[dict], dim: int) -> tuple:
        """Re-insert a snapshot into the fresh schema. Assertions whose embedding dim no
        longer matches (the embedding model changed) are dropped with a warning;
        reinforced edges are kept only where both endpoint pages still exist."""
        if not snap:
            return 0, 0
        for sid, created in snap.get("sessions", []):
            conn.execute("MERGE (s:Session {id:$id}) ON CREATE SET s.created_at=$t;",
                         parameters={"id": sid, "t": created})
        kept, skipped = set(), 0
        for aid, subj, pred, obj, sid, created, emb in snap.get("assertions", []):
            if emb is None or len(emb) != dim:
                skipped += 1
                continue
            conn.execute(
                "CREATE (:Assertion {id:$id, subject:$s, predicate:$p, object:$o, "
                "session_id:$sid, created_at:$t, emb:$e});",
                parameters={"id": aid, "s": subj, "p": pred, "o": obj, "sid": sid,
                            "t": created, "e": [float(x) for x in emb]})
            kept.add(aid)
        for sid, aid in snap.get("asserts", []):
            if aid in kept:
                conn.execute("MATCH (s:Session {id:$sid}),(a:Assertion {id:$aid}) "
                             "CREATE (s)-[:ASSERTS]->(a);", parameters={"sid": sid, "aid": aid})
        for new_id, old_id in snap.get("supersedes", []):
            if new_id in kept and old_id in kept:
                conn.execute("MATCH (n:Assertion {id:$n}),(o:Assertion {id:$o}) "
                             "CREATE (n)-[:SUPERSEDES]->(o);", parameters={"n": new_id, "o": old_id})
        page_slugs = self._existing_page_slugs(conn)
        n_reinf = 0
        for a_slug, b_slug, weight, last_seen in snap.get("reinforces", []):
            if a_slug in page_slugs and b_slug in page_slugs:
                conn.execute(
                    "MATCH (a:Page {slug:$a}),(b:Page {slug:$b}) "
                    "CREATE (a)-[:REINFORCES {weight:$w, last_seen:$t}]->(b);",
                    parameters={"a": a_slug, "b": b_slug, "w": weight, "t": last_seen})
                n_reinf += 1
        if skipped:
            logger.warning("dropped %d preserved assertion(s) whose embedding dim changed "
                           "(embedding model differs); re-run `remember` to re-embed them.", skipped)
        if kept or n_reinf:
            logger.info("preserved remembered tier: %d assertion(s), %d reinforced edge(s)",
                        len(kept), n_reinf)
        return len(kept), n_reinf

    @staticmethod
    def _read_rows(conn, query: str) -> list:
        """Run a read query, returning all rows; ``[]`` if the table doesn't exist."""
        try:
            res = conn.execute(query)
        except Exception:      # table absent on an older graph
            return []
        rows = []
        while res.has_next():
            rows.append(res.get_next())
        return rows

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
        conn.execute("CREATE NODE TABLE Entity(key STRING, name STRING, type STRING, PRIMARY KEY(key));")
        conn.execute("CREATE REL TABLE MENTIONS(FROM Page TO Entity);")
        # Consolidation layer (populated by `openwiki communities`, empty otherwise),
        # so store code degrades gracefully — as with Entity/MENTIONS above.
        conn.execute("CREATE NODE TABLE Community("
                     "id INT64, label STRING, summary STRING, size INT64, PRIMARY KEY(id));")
        conn.execute("CREATE REL TABLE IN_COMMUNITY(FROM Page TO Community);")
        # Usage-memory overlay: reinforced page↔page edges that decay over time
        # (empty until `reinforce()` runs; see openwiki/graph/decay.py).
        conn.execute("CREATE REL TABLE REINFORCES(FROM Page TO Page, weight DOUBLE, last_seen INT64);")
        # Remembered tier (Path B, B2/B3/B6): sessions + reified assertions (empty until
        # `remember()` runs; see openwiki/graph/memory.py). Assertions carry a mirrored
        # embedding so `recall()` can brute-force cosine over them.
        conn.execute("CREATE NODE TABLE Session(id STRING, created_at INT64, PRIMARY KEY(id));")
        conn.execute(
            f"CREATE NODE TABLE Assertion(id STRING, subject STRING, predicate STRING, "
            f"object STRING, session_id STRING, created_at INT64, emb FLOAT[{dim}], PRIMARY KEY(id));")
        conn.execute("CREATE REL TABLE ASSERTS(FROM Session TO Assertion);")
        # B4 contradiction/time-versioning: a newer assertion SUPERSEDES an older one
        # (same subject+predicate, different object). 'Current' = no incoming SUPERSEDES;
        # nothing is deleted, so the superseded history stays queryable.
        conn.execute("CREATE REL TABLE SUPERSEDES(FROM Assertion TO Assertion);")
        # B5 consolidation ("sleep"): topical MemoryConcept summaries over clusters of current
        # assertions (populated by `openwiki consolidate`, empty otherwise). Like Community, a
        # *derived* view — recomputed by the sleep pass, not snapshotted across rebuilds.
        conn.execute("CREATE NODE TABLE MemoryConcept("
                     "id INT64, label STRING, summary STRING, size INT64, created_at INT64, PRIMARY KEY(id));")
        conn.execute("CREATE REL TABLE CONSOLIDATES(FROM MemoryConcept TO Assertion);")

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

    def _insert_entities(self, conn, entities) -> tuple:
        """Insert Entity nodes + MENTIONS (Page->Entity) edges."""
        if not entities:
            return 0, 0
        page_slugs = self._existing_page_slugs(conn)
        n_mentions = 0
        for entity in entities:
            conn.execute(
                "CREATE (:Entity {key:$k, name:$n, type:$t});",
                parameters={"k": entity.key, "n": entity.name, "t": entity.type},
            )
            for slug in entity.pages:
                if slug in page_slugs:
                    conn.execute(
                        "MATCH (p:Page {slug:$s}),(e:Entity {key:$k}) CREATE (p)-[:MENTIONS]->(e);",
                        parameters={"s": slug, "k": entity.key},
                    )
                    n_mentions += 1
        return len(entities), n_mentions

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
                similar_k: int = 6, references=None, entities=None) -> dict:
    return GraphBuilder(db_path, similar_k=similar_k).build(
        wiki, index, references=references, entities=entities)
