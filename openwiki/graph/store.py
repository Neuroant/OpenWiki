"""Query the knowledge graph: page neighborhoods + hybrid vector search.

`GraphStore` opens a Kuzu database built by :mod:`openwiki.graph.builder` and
answers the questions the Graph tab and (optionally) the agent need:
`neighborhood(slug)` for exploration, and `hybrid_search(vector)` to show the
vector index and graph traversal working together.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

try:
    import kuzu
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Kuzu is required for the graph layer. Install it with `pip install kuzu`."
    ) from exc

import numpy as np

from .builder import CHUNK_VECTOR_INDEX
from .community import REFERENCE_WEIGHT, SHARED_ENTITY_WEIGHT
from .decay import (
    DEFAULT_BOOST, DEFAULT_FLOOR, DEFAULT_HALF_LIFE_DAYS,
    confidence_weight, effective_weight, reinforced_weight,
)
from .entities import _normalize
from .usage import append_usage, clear_usage, read_usage, usage_log_path


class GraphStore:
    def __init__(self, db_path, writable: bool = False) -> None:
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"No graph at {self.db_path}. Build it with `openwiki graph-build`."
            )
        self.writable = writable
        # Read-only by default: the store only reads, which avoids Kuzu's exclusive
        # write lock (so a serving process won't block a rebuild or second reader).
        # Incremental updates (agent edits) need a writable connection.
        try:
            self._db = kuzu.Database(str(self.db_path), read_only=not writable)
        except TypeError:  # older kuzu without the kwarg
            self._db = kuzu.Database(str(self.db_path))
        self._conn = kuzu.Connection(self._db)
        # Re-entrant: an upsert holds the lock across a batch and calls _rows within.
        self._lock = threading.RLock()
        self._reinforce_ensured = False   # lazy REINFORCES-table check (old graphs)
        self._memory_ensured = False      # lazy Session/Assertion-table check (old graphs)
        self._page_comm: Optional[dict] = None   # lazy {slug: community_id} for coloring
        # B1 read-path reinforcement: a read-only store may append usage to a sidecar
        # log (opt-in via log_usage) that a writable process folds in (fold_usage).
        self.log_usage = False
        self._usage_path = usage_log_path(self.db_path)

    def close(self) -> None:
        self._conn.close()
        self._db.close()

    # -- helpers --------------------------------------------------------

    def _rows(self, query: str, params: Optional[dict] = None) -> list[list]:
        with self._lock:  # a Kuzu connection is not safe for concurrent execute
            res = self._conn.execute(query, parameters=params) if params else self._conn.execute(query)
            out = []
            while res.has_next():
                out.append(res.get_next())
        return out

    def _exec(self, query: str, params: Optional[dict] = None) -> None:
        with self._lock:
            self._conn.execute(query, parameters=params) if params else self._conn.execute(query)

    @staticmethod
    def _node(row) -> dict:
        return {"slug": row[0], "title": row[1], "level": row[2],
                "pdf_start": row[3], "pdf_end": row[4]}

    _P = "p.slug, p.title, p.level, p.pdf_start, p.pdf_end"

    # -- API ------------------------------------------------------------

    def stats(self) -> dict:
        def count(q):
            return self._rows(q)[0][0]
        by_type = [{"type": t, "count": n} for t, n in
                   self._rows("MATCH (e:Entity) RETURN e.type, count(e) AS n ORDER BY n DESC;")]
        return {
            "pages": count("MATCH (p:Page) RETURN count(p);"),
            "chunks": count("MATCH (c:Chunk) RETURN count(c);"),
            "entities": count("MATCH (e:Entity) RETURN count(e);"),
            "child_of": count("MATCH ()-[r:CHILD_OF]->() RETURN count(r);"),
            "next": count("MATCH ()-[r:NEXT]->() RETURN count(r);"),
            "part_of": count("MATCH ()-[r:PART_OF]->() RETURN count(r);"),
            "similar_to": count("MATCH ()-[r:SIMILAR_TO]->() RETURN count(r);"),
            "references": count("MATCH ()-[r:REFERENCES]->() RETURN count(r);"),
            "mentions": count("MATCH ()-[r:MENTIONS]->() RETURN count(r);"),
            "entity_types": by_type,
        }

    def health(self, hub_limit: int = 12) -> dict:
        """Knowledge-base *quality* signals (for the Evaluation tab's health panel):
        connectivity, orphan/gap pages, entity singleton ratio, concept hubs."""
        def one(q):
            return self._rows(q)[0][0]

        pages = one("MATCH (p:Page) RETURN count(p);")
        no_similar = one("MATCH (p:Page) OPTIONAL MATCH (p)-[r:SIMILAR_TO]-(:Page) "
                         "WITH p, count(r) AS n WHERE n = 0 RETURN count(p);")
        no_entities = one("MATCH (p:Page) OPTIONAL MATCH (p)-[m:MENTIONS]->(:Entity) "
                          "WITH p, count(m) AS n WHERE n = 0 RETURN count(p);")
        avg_degree = float(self._rows(
            "MATCH (p:Page) OPTIONAL MATCH (p)-[r:SIMILAR_TO|REFERENCES]-(:Page) "
            "WITH p, count(r) AS d RETURN avg(d);")[0][0] or 0)
        # Orphans: no similar, no reference, and no entity mention at all.
        orphans = [{"slug": r[0], "title": r[1]} for r in self._rows(
            "MATCH (p:Page) "
            "OPTIONAL MATCH (p)-[s:SIMILAR_TO]-(:Page) "
            "OPTIONAL MATCH (p)-[rf:REFERENCES]-(:Page) "
            "OPTIONAL MATCH (p)-[m:MENTIONS]->(:Entity) "
            "WITH p, count(s) + count(rf) + count(m) AS deg WHERE deg = 0 "
            "RETURN p.slug, p.title ORDER BY p.slug;")]
        top_pages = [{"slug": r[0], "title": r[1], "degree": r[2]} for r in self._rows(
            "MATCH (p:Page) OPTIONAL MATCH (p)-[r:SIMILAR_TO|REFERENCES]-(:Page) "
            f"WITH p, count(r) AS deg RETURN p.slug, p.title, deg ORDER BY deg DESC LIMIT {int(hub_limit)};")]
        entities = one("MATCH (e:Entity) RETURN count(e);")
        singletons = one("MATCH (:Page)-[:MENTIONS]->(e:Entity) "
                         "WITH e, count(*) AS n WHERE n = 1 RETURN count(e);") if entities else 0
        hubs = [{"name": r[0], "type": r[1], "pages": r[2]} for r in self._rows(
            "MATCH (p:Page)-[:MENTIONS]->(e:Entity) WITH e, count(p) AS n "
            f"RETURN e.name, e.type, n ORDER BY n DESC LIMIT {int(hub_limit)};")]
        return {
            "pages": pages, "avg_degree": round(avg_degree, 2),
            "pages_no_similar": no_similar, "pages_no_entities": no_entities,
            "orphans": orphans, "top_pages": top_pages,
            "entities": entities, "singleton_entities": singletons,
            "cross_page_entities": entities - singletons, "hubs": hubs,
        }

    def has_entities(self) -> bool:
        return self._rows("MATCH (e:Entity) RETURN count(e);")[0][0] > 0

    def neighborhood(self, slug: str, similar_k: int = 6) -> dict:
        """Return the center page and its parent/children/prev/next/similar."""
        center = self._rows(
            f"MATCH (p:Page {{slug:$s}}) RETURN {self._P};", {"s": slug}
        )
        if not center:
            raise KeyError(f"page '{slug}' not in graph")

        groups = {
            "parent": self._rows(
                f"MATCH (:Page {{slug:$s}})-[:CHILD_OF]->(p:Page) RETURN {self._P};", {"s": slug}),
            "child": self._rows(
                f"MATCH (:Page {{slug:$s}})<-[:CHILD_OF]-(p:Page) RETURN {self._P};", {"s": slug}),
            "prev": self._rows(
                f"MATCH (:Page {{slug:$s}})<-[:NEXT]-(p:Page) RETURN {self._P};", {"s": slug}),
            "next": self._rows(
                f"MATCH (:Page {{slug:$s}})-[:NEXT]->(p:Page) RETURN {self._P};", {"s": slug}),
            "references": self._rows(
                f"MATCH (:Page {{slug:$s}})-[:REFERENCES]->(p:Page) RETURN {self._P} LIMIT 8;", {"s": slug}),
            "referenced_by": self._rows(
                f"MATCH (:Page {{slug:$s}})<-[:REFERENCES]-(p:Page) RETURN {self._P} LIMIT 6;", {"s": slug}),
            "shared_entity": self._rows(
                f"MATCH (:Page {{slug:$s}})-[:MENTIONS]->(e:Entity)<-[:MENTIONS]-(p:Page) "
                f"WHERE p.slug <> $s RETURN {self._P}, count(e) AS shared "
                f"ORDER BY shared DESC LIMIT 6;", {"s": slug}),
            "similar": self._rows(
                f"MATCH (:Page {{slug:$s}})-[r:SIMILAR_TO]->(p:Page) "
                f"RETURN {self._P}, r.score ORDER BY r.score DESC LIMIT $k;",
                {"s": slug, "k": similar_k}),
            # Usage-memory neighbors, ranked by time-decayed weight (may be empty).
            "reinforced": self._reinforced_neighbors(slug, int(time.time()), k=similar_k),
        }

        nodes = {slug: {**self._node(center[0]), "rel": "center"}}
        edges = []
        for rel, rows in groups.items():
            for row in rows:
                node = self._node(row)
                nodes.setdefault(node["slug"], {**node, "rel": rel})
                edge = {"source": slug, "target": node["slug"], "type": rel}
                if rel in ("similar", "reinforced"):
                    edge["score"] = round(float(row[5]), 3)
                edges.append(edge)

        return {"center": slug, "nodes": list(nodes.values()), "edges": edges}

    # -- explorable subgraph (web Graph tab) ---------------------------
    #
    # Nodes carry a ``kind`` ("page" | "entity") and a stable ``id`` (page slug or
    # entity key — the two never collide). Edges are typed. The frontend keeps an
    # accumulating graph and expands nodes on click.

    def _community_of(self, slug: str):
        """The community id a page belongs to (or None) — for Graph-tab coloring."""
        if self._page_comm is None:
            self._page_comm = {s: cid for cid, slugs in self.community_members().items()
                               for s in slugs}
        return self._page_comm.get(slug)

    def _page_gnode(self, row) -> dict:
        return {"id": row[0], "kind": "page", "label": row[1],
                "pdf_start": row[3], "pdf_end": row[4],
                "community": self._community_of(row[0])}

    def _page_entities(self, slug: str, k: int) -> list[list]:
        # A page's entities, most cross-cutting first (by how many pages mention them).
        return self._rows(
            "MATCH (:Page {slug:$s})-[:MENTIONS]->(e:Entity)<-[:MENTIONS]-(p:Page) "
            "RETURN e.key, e.name, e.type, count(p) AS m ORDER BY m DESC, e.name LIMIT $k;",
            {"s": slug, "k": k})

    def expand_page(self, slug: str, entity_k: int = 8) -> dict:
        """A page's page-neighbors (typed) plus its most salient entities."""
        nb = self.neighborhood(slug)
        nodes = [self._page_gnode([n["slug"], n["title"], n["level"], n["pdf_start"], n["pdf_end"]])
                 for n in nb["nodes"] if n["rel"] != "center"]
        edges = [{"source": e["source"], "target": e["target"], "type": e["type"]}
                 for e in nb["edges"]]
        for key, name, etype, _m in self._page_entities(slug, entity_k):
            nodes.append({"id": key, "kind": "entity", "label": name, "etype": etype})
            edges.append({"source": slug, "target": key, "type": "mentions"})
        return {"nodes": nodes, "edges": edges}

    def expand_entity(self, key: str, page_k: int = 15) -> dict:
        """The pages that mention an entity (expanding an entity node)."""
        rows = self._rows(
            "MATCH (e:Entity {key:$k})<-[:MENTIONS]-(p:Page) "
            "RETURN p.slug, p.title, p.level, p.pdf_start, p.pdf_end LIMIT $pk;",
            {"k": key, "pk": page_k})
        nodes = [self._page_gnode(r) for r in rows]
        edges = [{"source": r[0], "target": key, "type": "mentions"} for r in rows]
        return {"nodes": nodes, "edges": edges}

    def explore(self, slug: str) -> dict:
        """Initial explorer view: the page as root, its neighbors, and its entities."""
        row = self._rows(f"MATCH (p:Page {{slug:$s}}) RETURN {self._P};", {"s": slug})
        if not row:
            raise KeyError(f"page '{slug}' not in graph")
        root = {**self._page_gnode(row[0]), "root": True}
        sub = self.expand_page(slug)
        return {"root": slug, "nodes": [root] + sub["nodes"], "edges": sub["edges"]}

    def expand(self, node_type: str, node_id: str) -> dict:
        return self.expand_entity(node_id) if node_type == "entity" else self.expand_page(node_id)

    # -- entities -------------------------------------------------------

    def entities_for_page(self, slug: str) -> list[dict]:
        rows = self._rows(
            "MATCH (:Page {slug:$s})-[:MENTIONS]->(e:Entity) "
            "RETURN e.name, e.type ORDER BY e.type, e.name;", {"s": slug})
        return [{"name": r[0], "type": r[1]} for r in rows]

    def pages_for_entity(self, query: str, limit: int = 20) -> list[dict]:
        """Pages that mention an entity whose name contains ``query`` (case-insensitive)."""
        rows = self._rows(
            "MATCH (e:Entity)<-[:MENTIONS]-(p:Page) "
            "WHERE contains(lower(e.name), lower($q)) "
            "RETURN e.name, e.type, p.slug, p.title ORDER BY e.name LIMIT $k;",
            {"q": str(query), "k": limit})
        return [{"entity": r[0], "type": r[1], "slug": r[2], "title": r[3]} for r in rows]

    # Page-to-page relationships only (never route through Chunk/PART_OF).
    _PAGE_RELS = "CHILD_OF|NEXT|SIMILAR_TO|REFERENCES"

    def find_path(self, from_slug: str, to_slug: str, max_hops: int = 5) -> Optional[dict]:
        """Shortest path of related pages between two pages (or None if none)."""
        for slug in (from_slug, to_slug):
            if not self._rows("MATCH (p:Page {slug:$s}) RETURN p.slug;", {"s": slug}):
                raise KeyError(f"page '{slug}' not in graph")
        if from_slug == to_slug:
            return {"from": from_slug, "to": to_slug, "hops": 0,
                    "nodes": [from_slug], "titles": [], "rels": []}

        rows = self._rows(
            "MATCH (a:Page {slug:$a}), (b:Page {slug:$b}), "
            f"p = (a)-[:{self._PAGE_RELS}* SHORTEST 1..{int(max_hops)}]-(b) "
            "RETURN length(p), "
            "list_transform(nodes(p), x -> x.slug), "
            "list_transform(nodes(p), x -> x.title), "
            "list_transform(rels(p), x -> label(x));",
            {"a": from_slug, "b": to_slug},
        )
        if not rows:
            return None
        hops, slugs, titles, rels = rows[0]
        return {"from": from_slug, "to": to_slug, "hops": hops,
                "nodes": slugs, "titles": titles, "rels": rels}

    # -- incremental updates (agent edits) -----------------------------

    _H1 = re.compile(r"^#\s+(.+)$", re.MULTILINE)

    def upsert_page(self, slug: str, text: str, title: Optional[str] = None,
                    embedder=None, similar_k: int = 6,
                    chunk_size: int = 180, overlap: int = 30) -> dict:
        """Add or refresh a page in the graph so an agent edit joins it live.

        Upserts the `Page` node, replaces its `Chunk`s (+embeddings; the HNSW index
        self-maintains), and recomputes its `SIMILAR_TO` edges. Structural
        (CHILD_OF/NEXT), REFERENCES and entity edges are not derived here — a full
        `graph-build` is still what produces those.
        """
        if not self.writable:
            raise RuntimeError("GraphStore is read-only; open it writable to upsert.")
        if embedder is None:
            raise ValueError("upsert_page needs an embedder.")
        from ..chunking import chunk_text, normalize_text

        if title is None:
            m = self._H1.search(text or "")
            title = m.group(1).strip() if m else slug
        chunks = chunk_text(normalize_text(text or ""), chunk_size, overlap)

        emb = None
        if chunks:
            emb = embedder.embed_documents(chunks).astype(np.float32)
            norms = np.linalg.norm(emb, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            emb = emb / norms

        with self._lock:
            self._exec(
                "MERGE (p:Page {slug:$s}) "
                "ON CREATE SET p.title=$t, p.level=0, p.pdf_start=0, p.pdf_end=0, p.seq=-1 "
                "ON MATCH SET p.title=$t;", {"s": slug, "t": title})
            self._exec("MATCH (c:Chunk {page_slug:$s}) DETACH DELETE c;", {"s": slug})
            # undirected delete is unsupported in Kuzu — clear both directions
            self._exec("MATCH (:Page {slug:$s})-[r:SIMILAR_TO]->() DELETE r;", {"s": slug})
            self._exec("MATCH (:Page {slug:$s})<-[r:SIMILAR_TO]-() DELETE r;", {"s": slug})

            if chunks:
                for i, (ctext, vec) in enumerate(zip(chunks, emb)):
                    cid = f"{slug}#{i}"
                    self._exec("CREATE (:Chunk {id:$id, page_slug:$s, text:$t, emb:$e});",
                               {"id": cid, "s": slug, "t": ctext, "e": vec.astype(float).tolist()})
                    self._exec("MATCH (c:Chunk {id:$id}),(p:Page {slug:$s}) CREATE (c)-[:PART_OF]->(p);",
                               {"id": cid, "s": slug})

            n_sim = 0
            if emb is not None:
                mean = emb.mean(axis=0)
                mean = mean / (np.linalg.norm(mean) or 1.0)
                sims = self._rows(
                    f"CALL QUERY_VECTOR_INDEX('Chunk', '{CHUNK_VECTOR_INDEX}', $v, $k) "
                    "WITH node AS c, distance MATCH (c)-[:PART_OF]->(p:Page) "
                    "WHERE p.slug <> $s RETURN p.slug, min(distance) AS d ORDER BY d LIMIT $lim;",
                    {"v": mean.astype(float).tolist(), "k": similar_k * 6, "s": slug, "lim": similar_k})
                for other, d in sims:
                    score = max(0.0, 1.0 - float(d))
                    for a, b in ((slug, other), (other, slug)):  # bidirectional
                        self._exec(
                            "MATCH (a:Page {slug:$a}),(b:Page {slug:$b}) "
                            "CREATE (a)-[:SIMILAR_TO {score:$sc}]->(b);", {"a": a, "b": b, "sc": score})
                    n_sim += 1
        return {"slug": slug, "title": title, "chunks": len(chunks), "similar": n_sim}

    # -- communities (consolidation layer) -----------------------------

    def ensure_community_schema(self) -> None:
        """Create the Community / IN_COMMUNITY tables if a pre-existing graph lacks
        them (built before this layer). New builds create them empty in the schema."""
        for ddl in (
            "CREATE NODE TABLE IF NOT EXISTS Community("
            "id INT64, label STRING, summary STRING, size INT64, PRIMARY KEY(id));",
            "CREATE REL TABLE IF NOT EXISTS IN_COMMUNITY(FROM Page TO Community);",
        ):
            try:
                self._exec(ddl)
            except Exception:  # pragma: no cover - already exists / older syntax
                pass

    def has_communities(self) -> bool:
        try:
            return self._rows("MATCH (c:Community) RETURN count(c);")[0][0] > 0
        except Exception:
            return False   # table absent on graphs built before this layer

    def communities(self) -> list:
        """All communities with their LLM summaries (largest first)."""
        try:
            rows = self._rows("MATCH (c:Community) "
                              "RETURN c.id, c.label, c.summary, c.size "
                              "ORDER BY c.size DESC, c.id;")
        except Exception:
            return []
        return [{"id": r[0], "label": r[1], "summary": r[2], "size": r[3]} for r in rows]

    def community_members(self) -> dict:
        """``{community_id: [page_slug, …]}`` from the IN_COMMUNITY edges (for the
        thematic eval: map a cited community back to the pages it covers)."""
        try:
            rows = self._rows("MATCH (p:Page)-[:IN_COMMUNITY]->(c:Community) RETURN c.id, p.slug;")
        except Exception:
            return {}
        out: dict = {}
        for cid, slug in rows:
            out.setdefault(int(cid), []).append(slug)
        return out

    def page_graph(self) -> dict:
        """The undirected, weighted Page↔Page graph for community detection:
        SIMILAR_TO (by score) ∪ REFERENCES ∪ shared-entity, folded per unordered pair.
        Returns ``{"pages": {slug: title}, "edges": [(a, b, weight), ...]}``."""
        pages = {r[0]: r[1] for r in self._rows("MATCH (p:Page) RETURN p.slug, p.title;")}
        weights: dict = {}

        def add(a, b, w):
            if a == b or w <= 0:
                return
            key = (a, b) if a < b else (b, a)
            weights[key] = weights.get(key, 0.0) + float(w)

        for a, b, s in self._rows(
                "MATCH (a:Page)-[r:SIMILAR_TO]->(b:Page) WHERE a.slug < b.slug "
                "RETURN a.slug, b.slug, r.score;"):
            add(a, b, s)
        for a, b in self._rows("MATCH (a:Page)-[:REFERENCES]->(b:Page) RETURN a.slug, b.slug;"):
            add(a, b, REFERENCE_WEIGHT)
        if self.has_entities():
            for a, b, n in self._rows(
                    "MATCH (a:Page)-[:MENTIONS]->(:Entity)<-[:MENTIONS]-(b:Page) "
                    "WHERE a.slug < b.slug RETURN a.slug, b.slug, count(*);"):
                add(a, b, min(int(n), 3) * SHARED_ENTITY_WEIGHT)
        return {"pages": pages, "edges": [(a, b, w) for (a, b), w in weights.items()]}

    def page_snippet(self, slug: str, max_chars: int = 400) -> str:
        """A short text excerpt for a page (its first chunks), for summarization."""
        rows = self._rows("MATCH (c:Chunk {page_slug:$s}) RETURN c.text LIMIT 3;", {"s": slug})
        return " ".join(r[0] for r in rows)[:max_chars]

    def upsert_communities(self, assignment: dict, summaries: dict, labels: dict) -> dict:
        """Replace the community layer: (re)create Community nodes + IN_COMMUNITY edges
        from ``{page_slug: community_id}`` plus per-community ``summaries``/``labels``."""
        if not self.writable:
            raise RuntimeError("GraphStore is read-only; open it writable to write communities.")
        self.ensure_community_schema()
        sizes: dict = {}
        for cid in assignment.values():
            sizes[cid] = sizes.get(cid, 0) + 1
        with self._lock:
            self._exec("MATCH (c:Community) DETACH DELETE c;")   # clear the old layer
            for cid in sorted(sizes):
                self._exec(
                    "CREATE (:Community {id:$id, label:$l, summary:$s, size:$n});",
                    {"id": int(cid), "l": labels.get(cid, ""), "s": summaries.get(cid, ""),
                     "n": int(sizes[cid])})
            for slug, cid in assignment.items():
                self._exec(
                    "MATCH (p:Page {slug:$s}),(c:Community {id:$id}) "
                    "CREATE (p)-[:IN_COMMUNITY]->(c);", {"s": slug, "id": int(cid)})
        return {"communities": len(sizes), "pages": len(assignment)}

    # -- usage memory: reinforced, time-decaying edges -----------------

    def _ensure_reinforce(self) -> None:
        """Create the REINFORCES table if a pre-existing graph lacks it (once)."""
        if self._reinforce_ensured:
            return
        try:
            self._exec("CREATE REL TABLE IF NOT EXISTS REINFORCES("
                       "FROM Page TO Page, weight DOUBLE, last_seen INT64);")
        except Exception:  # pragma: no cover - already exists / older syntax
            pass
        self._reinforce_ensured = True

    def _reinforced_neighbors(self, slug: str, now: int, k: int = 6,
                              floor: float = DEFAULT_FLOOR) -> list:
        """Reinforced neighbors of ``slug`` (either direction), ranked by effective
        (time-decayed) weight, dropping any below ``floor``. Rows match the ``similar``
        shape (slug, title, level, pdf_start, pdf_end, score) so the caller reuses them."""
        try:
            rows = self._rows(
                "MATCH (:Page {slug:$s})-[r:REINFORCES]-(p:Page) WHERE p.slug <> $s "
                "RETURN p.slug, p.title, p.level, p.pdf_start, p.pdf_end, r.weight, r.last_seen;",
                {"s": slug})
        except Exception:
            return []   # table absent on graphs built before this layer
        best: dict = {}
        for row in rows:
            eff = effective_weight(float(row[5] or 0.0), int(row[6] or 0), now)
            if eff >= floor and eff > best.get(row[0], (None, -1.0))[1]:
                best[row[0]] = ([row[0], row[1], row[2], row[3], row[4], eff], eff)
        ranked = sorted((v[0] for v in best.values()), key=lambda x: -x[5])
        return ranked[:k]

    def reinforce(self, from_slug: str, to_slug: str, now: Optional[int] = None,
                  boost: float = DEFAULT_BOOST) -> Optional[dict]:
        """Strengthen (or create) the ``from_slug -> to_slug`` usage edge and stamp it
        ``now`` — the Hebbian half of the memory model. No-op for a self-edge."""
        if not self.writable:
            raise RuntimeError("GraphStore is read-only; open it writable to reinforce.")
        if from_slug == to_slug:
            return None
        now = int(now if now is not None else time.time())
        self._ensure_reinforce()
        with self._lock:
            existing = self._rows(
                "MATCH (:Page {slug:$a})-[r:REINFORCES]->(:Page {slug:$b}) RETURN r.weight;",
                {"a": from_slug, "b": to_slug})
            if existing:
                w = reinforced_weight(float(existing[0][0] or 0.0), boost)
                self._exec("MATCH (:Page {slug:$a})-[r:REINFORCES]->(:Page {slug:$b}) "
                           "SET r.weight=$w, r.last_seen=$t;",
                           {"a": from_slug, "b": to_slug, "w": w, "t": now})
            else:
                w = reinforced_weight(0.0, boost)
                self._exec("MATCH (a:Page {slug:$a}),(b:Page {slug:$b}) "
                           "CREATE (a)-[:REINFORCES {weight:$w, last_seen:$t}]->(b);",
                           {"a": from_slug, "b": to_slug, "w": w, "t": now})
        return {"from": from_slug, "to": to_slug, "weight": w}

    def decay(self, now: Optional[int] = None,
              half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
              floor: float = DEFAULT_FLOOR) -> dict:
        """Age every REINFORCES edge to ``now`` (persist its decayed weight, reset the
        clock) and prune those that fall below ``floor``. The 'forgetting' half."""
        if not self.writable:
            raise RuntimeError("GraphStore is read-only; open it writable to decay.")
        now = int(now if now is not None else time.time())
        self._ensure_reinforce()
        with self._lock:
            rows = self._rows("MATCH (a:Page)-[r:REINFORCES]->(b:Page) "
                              "RETURN a.slug, b.slug, r.weight, r.last_seen;")
            decayed = pruned = 0
            for a, b, w, seen in rows:
                eff = effective_weight(float(w or 0.0), int(seen or 0), now, half_life_days)
                if eff < floor:
                    self._exec("MATCH (:Page {slug:$a})-[r:REINFORCES]->(:Page {slug:$b}) DELETE r;",
                               {"a": a, "b": b})
                    pruned += 1
                else:
                    self._exec("MATCH (:Page {slug:$a})-[r:REINFORCES]->(:Page {slug:$b}) "
                               "SET r.weight=$w, r.last_seen=$t;", {"a": a, "b": b, "w": eff, "t": now})
                    decayed += 1
        return {"edges": len(rows), "decayed": decayed, "pruned": pruned}

    def record_usage(self, pairs) -> None:
        """B1: record ``(from_slug, to_slug)`` usage from a retrieval. On a **writable**
        store (serve/chat) reinforce immediately; on a **read-only** store with
        ``log_usage`` on (ask/MCP in Second Brain mode) append to the usage log for a
        later ``fold_usage``; otherwise a no-op. Best-effort — a memory write must never
        break retrieval."""
        pairs = [(a, b) for a, b in pairs if a and b and a != b]
        if not pairs:
            return
        try:
            if self.writable:
                for a, b in pairs:
                    self.reinforce(a, b)
            elif self.log_usage:
                append_usage(self._usage_path, pairs)
        except Exception:      # pragma: no cover - never surface a memory-write error
            pass

    def fold_usage(self, now: Optional[int] = None) -> dict:
        """B1: fold the pending usage log into REINFORCES edges (reinforce each pair,
        skipping slugs that no longer exist) and clear the log. Writable-only; a no-op
        when the log is empty. Called on a writable open (serve/chat) and by ``decay``."""
        if not self.writable:
            raise RuntimeError("GraphStore is read-only; open it writable to fold usage.")
        records = read_usage(self._usage_path)
        if not records:
            return {"records": 0, "pairs": 0, "reinforced": 0}
        now = int(now if now is not None else time.time())
        reinforced = pairs = 0
        for rec in records:
            for pair in rec.get("pairs", []):
                if not (isinstance(pair, list) and len(pair) == 2):
                    continue
                pairs += 1
                if self.reinforce(str(pair[0]), str(pair[1]), now=now) is not None:
                    reinforced += 1
        clear_usage(self._usage_path)
        return {"records": len(records), "pairs": pairs, "reinforced": reinforced}

    def pending_usage(self) -> int:
        """How many usage records are queued in the log (0 if none)."""
        return len(read_usage(self._usage_path))

    # -- remembered tier (Path B: session memory) ----------------------

    def _ensure_memory_schema(self, dim: int) -> None:
        """Create Session/Assertion/ASSERTS if a pre-existing graph lacks them (once)."""
        if self._memory_ensured:
            return
        for ddl in (
            "CREATE NODE TABLE IF NOT EXISTS Session(id STRING, created_at INT64, PRIMARY KEY(id));",
            f"CREATE NODE TABLE IF NOT EXISTS Assertion(id STRING, subject STRING, predicate STRING, "
            f"object STRING, session_id STRING, created_at INT64, confidence DOUBLE, last_seen INT64, "
            f"emb FLOAT[{int(dim)}], PRIMARY KEY(id));",
            "CREATE REL TABLE IF NOT EXISTS ASSERTS(FROM Session TO Assertion);",
            "CREATE REL TABLE IF NOT EXISTS SUPERSEDES(FROM Assertion TO Assertion);",   # B4
        ):
            try:
                self._exec(ddl)
            except Exception:  # pragma: no cover - already exists / older syntax
                pass
        # B6 confidence: migrate pre-0.54 Assertion tables that lack the columns (idempotent —
        # ALTER errors "already has property", which we swallow). Writable connections only.
        if self.writable:
            for ddl in ("ALTER TABLE Assertion ADD confidence DOUBLE DEFAULT 1.0;",
                        "ALTER TABLE Assertion ADD last_seen INT64 DEFAULT 0;"):
                try:
                    self._exec(ddl)
                except Exception:  # pragma: no cover - column already present
                    pass
        self._memory_ensured = True

    def has_memory(self) -> bool:
        try:
            return self._rows("MATCH (a:Assertion) RETURN count(a);")[0][0] > 0
        except Exception:
            return False

    def _superseded_ids(self) -> set:
        """Ids of assertions with an incoming SUPERSEDES edge (i.e. no longer current)."""
        try:
            return {r[0] for r in self._rows(
                "MATCH (:Assertion)-[:SUPERSEDES]->(o:Assertion) RETURN o.id;")}
        except Exception:
            return set()

    def remember(self, session_id: str, facts, embedder, now: Optional[int] = None) -> dict:
        """B3 merge + **B4 contradiction handling**: embed each fact, dedup against the
        **current** assertions (normalized triple), persist Session + Assertion + ASSERTS,
        and — when a new fact shares a subject+predicate with a current one but gives a
        *different* object — mark the old one superseded via a `SUPERSEDES` edge (kept, not
        deleted, so history survives). Re-asserting a superseded fact revives it. Returns counts."""
        if not self.writable:
            raise RuntimeError("GraphStore is read-only; open it writable to remember.")
        facts = list(facts)
        if not facts:
            return {"facts": 0, "added": 0, "duplicates": 0, "superseded": 0}
        if embedder is None:
            raise ValueError("remember needs an embedder.")
        now = int(now if now is not None else time.time())
        emb = embedder.embed_documents([f.text() for f in facts]).astype(np.float32)
        norms = np.linalg.norm(emb, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        emb = emb / norms
        self._ensure_memory_schema(emb.shape[1])
        added = dupes = superseded = 0
        with self._lock:
            self._exec("MERGE (s:Session {id:$id}) ON CREATE SET s.created_at=$t;",
                       {"id": session_id, "t": now})
            # Index the *current* assertions (a superseded one no longer dedups or conflicts):
            # by_key {triple → (id, confidence)} for dedup/reinforce, (subj,pred) → [(id, obj)] for contradiction.
            sup = self._superseded_ids()
            by_key, current = {}, {}
            for aid_e, s, p, o, conf in self._rows(
                    "MATCH (a:Assertion) RETURN a.id, a.subject, a.predicate, a.object, a.confidence;"):
                if aid_e in sup:
                    continue
                ns, pp, no = _normalize(s), (p or "").strip().lower(), _normalize(o)
                by_key[(ns, pp, no)] = (aid_e, float(conf if conf is not None else 1.0))
                current.setdefault((ns, pp), []).append((aid_e, no))
            for fact, vec in zip(facts, emb):
                key = fact.key()                       # (nsubj, npred, nobj)
                if key in by_key:                      # re-affirming a current fact → B6: raise its confidence
                    aid_e, conf = by_key[key]
                    self._exec("MATCH (a:Assertion {id:$id}) SET a.confidence=$c, a.last_seen=$t;",
                               {"id": aid_e, "c": reinforced_weight(conf, DEFAULT_BOOST), "t": now})
                    dupes += 1
                    continue
                aid = uuid.uuid4().hex
                self._exec(
                    "CREATE (:Assertion {id:$id, subject:$s, predicate:$p, object:$o, "
                    "session_id:$sid, created_at:$t, confidence:1.0, last_seen:$t, emb:$e});",
                    {"id": aid, "s": fact.subject, "p": fact.predicate, "o": fact.object,
                     "sid": session_id, "t": now, "e": vec.astype(float).tolist()})
                self._exec("MATCH (s:Session {id:$sid}),(a:Assertion {id:$id}) "
                           "CREATE (s)-[:ASSERTS]->(a);", {"sid": session_id, "id": aid})
                added += 1
                # B4: this fact supersedes any current fact with the same subject+predicate
                # but a different object.
                nsp = (key[0], key[1])
                for old_id, old_no in current.get(nsp, []):
                    if old_no != key[2]:
                        self._exec("MATCH (n:Assertion {id:$n}),(o:Assertion {id:$o}) "
                                   "CREATE (n)-[:SUPERSEDES]->(o);", {"n": aid, "o": old_id})
                        superseded += 1
                by_key[key] = (aid, 1.0)               # so a repeat in the same batch reinforces it
                current[nsp] = [(aid, key[2])]         # the new fact is now the current one
        return {"facts": len(facts), "added": added, "duplicates": dupes, "superseded": superseded}

    def recall(self, query: str, embedder, k: int = 5,
               half_life_days: float = DEFAULT_HALF_LIFE_DAYS, now: Optional[int] = None,
               include_superseded: bool = False) -> list:
        """B6 (activation tier) + **B4**: the remembered facts most relevant to ``query`` —
        cosine over assertion embeddings, weighted by recency (time-decayed via `decay`).
        **Superseded facts are excluded by default** (the agent gets the current fact);
        pass ``include_superseded`` to also return them, each flagged ``superseded``. Read-only."""
        if embedder is None:
            raise ValueError("recall needs an embedder.")
        # B6 per-fact confidence: score = cos × decayed confidence (base weight = the stored
        # confidence, aged from last_seen). Read them if present; a pre-0.54 / read-only graph
        # without the columns falls back to confidence 1.0 (the prior recency-only behavior).
        try:
            rows = self._rows("MATCH (a:Assertion) RETURN a.id, a.subject, a.predicate, a.object, "
                              "a.session_id, a.created_at, a.emb, a.confidence, a.last_seen;")
            has_conf = True
        except Exception:
            try:
                rows = self._rows("MATCH (a:Assertion) RETURN a.id, a.subject, a.predicate, "
                                  "a.object, a.session_id, a.created_at, a.emb;")
                has_conf = False
            except Exception:
                return []   # no memory table on graphs built before this layer
        if not rows:
            return []
        sup = self._superseded_ids()
        now = int(now if now is not None else time.time())
        q = np.asarray(embedder.embed_query(query), dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        scored = []
        for row in rows:
            if has_conf:
                aid, subj, pred, obj, sid, created, emb, conf, seen = row
            else:
                aid, subj, pred, obj, sid, created, emb = row
                conf, seen = 1.0, 0
            is_sup = aid in sup
            if is_sup and not include_superseded:
                continue
            conf = float(conf if conf is not None else 1.0)
            ref = int(seen) if seen else int(created or 0)      # last affirmed, else first stated
            cos = float(q @ np.asarray(emb, dtype=np.float32))   # stored normalized
            # gentle, log-scaled confidence lift, decayed by recency — relevance (cos) still dominates
            score = cos * effective_weight(confidence_weight(conf), ref, now, half_life_days)
            scored.append({"id": aid, "subject": subj, "predicate": pred, "object": obj,
                           "session_id": sid, "cos": round(cos, 3), "confidence": round(conf, 3),
                           "score": round(score, 3), "superseded": is_sup})
        scored.sort(key=lambda x: -x["score"])
        return scored[:k]

    def forget_all(self) -> None:
        """Reset the remembered tier — delete every Session + Assertion (and their edges).
        Writable-only; used to isolate scenarios in the cross-session eval."""
        if not self.writable:
            raise RuntimeError("GraphStore is read-only; open it writable to forget.")
        with self._lock:
            for query in ("MATCH (c:MemoryConcept) DETACH DELETE c;",
                          "MATCH (a:Assertion) DETACH DELETE a;",
                          "MATCH (s:Session) DETACH DELETE s;"):
                try:
                    self._exec(query)
                except Exception:      # pragma: no cover - tables may not exist yet
                    pass

    # -- B5 consolidation ("sleep"): themes over the remembered tier -----

    def ensure_consolidation_schema(self) -> None:
        """Create the MemoryConcept / CONSOLIDATES tables if a pre-existing graph lacks them."""
        for ddl in (
            "CREATE NODE TABLE IF NOT EXISTS MemoryConcept(id INT64, label STRING, summary STRING, "
            "size INT64, created_at INT64, PRIMARY KEY(id));",
            "CREATE REL TABLE IF NOT EXISTS CONSOLIDATES(FROM MemoryConcept TO Assertion);",
        ):
            try:
                self._exec(ddl)
            except Exception:      # pragma: no cover - already exists / older syntax
                pass

    def current_assertions(self) -> list:
        """Current (not superseded) assertions with their stored embeddings:
        ``[(id, subject, predicate, object, emb), ...]``."""
        try:
            rows = self._rows("MATCH (a:Assertion) "
                              "RETURN a.id, a.subject, a.predicate, a.object, a.emb;")
        except Exception:
            return []
        sup = self._superseded_ids()
        return [(r[0], r[1], r[2], r[3], r[4]) for r in rows if r[0] not in sup]

    def assertion_graph(self, similar_k: int = 6) -> dict:
        """Undirected similarity graph over **current** assertions (top-``k`` cosine per
        node), for B5 consolidation. Returns ``{"facts": {id: text}, "edges": [(a, b, w), …]}``."""
        facts = self.current_assertions()
        texts = {aid: f"{s} {p} {o}".strip() for aid, s, p, o, _ in facts}
        ids = [f[0] for f in facts]
        if len(ids) < 2:
            return {"facts": texts, "edges": []}
        mat = np.vstack([np.asarray(f[4], dtype=np.float32) for f in facts])
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        mat = mat / norms                       # defensively normalize (recall stores normalized)
        sims = mat @ mat.T
        np.fill_diagonal(sims, -np.inf)
        weights: dict = {}
        for i, aid in enumerate(ids):
            picked = 0
            for j in np.argsort(-sims[i]):
                if picked >= similar_k or sims[i, j] <= 0:
                    break
                other = ids[j]
                key = (aid, other) if aid < other else (other, aid)
                weights[key] = max(weights.get(key, 0.0), float(sims[i, j]))
                picked += 1
        return {"facts": texts, "edges": [(a, b, w) for (a, b), w in weights.items()]}

    def upsert_memory_concepts(self, assignment: dict, summaries: dict, labels: dict,
                               now: Optional[int] = None) -> dict:
        """Replace the consolidation layer: (re)create MemoryConcept nodes + CONSOLIDATES
        edges from ``{assertion_id: concept_id}`` + per-concept ``summaries``/``labels``.
        Writable-only; an empty ``assignment`` just clears the old layer."""
        if not self.writable:
            raise RuntimeError("GraphStore is read-only; open it writable to consolidate.")
        self.ensure_consolidation_schema()
        now = int(now if now is not None else time.time())
        sizes: dict = {}
        for cid in assignment.values():
            sizes[cid] = sizes.get(cid, 0) + 1
        with self._lock:
            self._exec("MATCH (c:MemoryConcept) DETACH DELETE c;")   # clear the old layer
            for cid in sorted(sizes):
                self._exec(
                    "CREATE (:MemoryConcept {id:$id, label:$l, summary:$s, size:$n, created_at:$t});",
                    {"id": int(cid), "l": labels.get(cid, ""), "s": summaries.get(cid, ""),
                     "n": int(sizes[cid]), "t": now})
            for aid, cid in assignment.items():
                self._exec("MATCH (c:MemoryConcept {id:$id}),(a:Assertion {id:$aid}) "
                           "CREATE (c)-[:CONSOLIDATES]->(a);", {"id": int(cid), "aid": aid})
        return {"concepts": len(sizes), "assertions": len(assignment)}

    def memory_concepts(self) -> list:
        """All consolidated memory themes with their summaries (largest first)."""
        try:
            rows = self._rows("MATCH (c:MemoryConcept) "
                              "RETURN c.id, c.label, c.summary, c.size ORDER BY c.size DESC, c.id;")
        except Exception:
            return []
        return [{"id": r[0], "label": r[1], "summary": r[2], "size": r[3]} for r in rows]

    def has_memory_concepts(self) -> bool:
        try:
            return self._rows("MATCH (c:MemoryConcept) RETURN count(c);")[0][0] > 0
        except Exception:
            return False

    def concept_members(self) -> dict:
        """``{concept_id: set(assertion_ids)}`` from CONSOLIDATES — for the incremental
        sleep pass (reuse a theme's summary when its member set is unchanged, B5)."""
        try:
            rows = self._rows("MATCH (c:MemoryConcept)-[:CONSOLIDATES]->(a:Assertion) RETURN c.id, a.id;")
        except Exception:
            return {}
        out: dict = {}
        for cid, aid in rows:
            out.setdefault(int(cid), set()).add(aid)
        return out

    def concept_assignment(self) -> dict:
        """``{assertion_id: concept_id}`` from CONSOLIDATES — the prior partition, used to
        **warm-start** clustering so a re-run is stable and edits stay local (B5)."""
        try:
            rows = self._rows("MATCH (c:MemoryConcept)-[:CONSOLIDATES]->(a:Assertion) RETURN a.id, c.id;")
        except Exception:
            return {}
        return {aid: int(cid) for aid, cid in rows}

    def relevant_concepts(self, assertion_ids, limit: int = 4) -> list:
        """B6 attractor tier: the consolidated themes (`MemoryConcept`) that contain any of
        the given (activated) assertions, ranked by how many they cover, then size. Small
        memory, so we read the CONSOLIDATES edges and aggregate in Python (no list-param SQL)."""
        ids = set(assertion_ids)
        if not ids:
            return []
        try:
            rows = self._rows("MATCH (c:MemoryConcept)-[:CONSOLIDATES]->(a:Assertion) "
                              "RETURN c.id, c.label, c.summary, c.size, a.id;")
        except Exception:
            return []
        agg: dict = {}
        for cid, label, summary, size, aid in rows:
            if aid in ids:
                e = agg.setdefault(cid, {"id": cid, "label": label, "summary": summary,
                                         "size": size, "hits": 0})
                e["hits"] += 1
        return sorted(agg.values(), key=lambda c: (-c["hits"], -(c["size"] or 0), c["id"]))[:limit]

    def context_for(self, query: str, embedder, identity: str = "",
                    k: int = 8, max_themes: int = 4, max_chars=None) -> str:
        """B6: assemble a session's context for ``query`` from the three memory tiers —
        identity + decay-weighted ``recall`` (activation) + the relevant consolidated themes
        (attractors), optionally fit within a ``max_chars`` budget. Read-only + **fail-soft**
        (missing embedder / empty memory → identity only, or ``""``)."""
        from .memory import assemble_context
        facts = []
        if embedder is not None:
            try:
                facts = self.recall(query, embedder, k=k)
            except Exception:      # never let a memory read break the caller
                facts = []
        themes = self.relevant_concepts([f["id"] for f in facts], limit=max_themes) if facts else []
        return assemble_context(identity, facts, themes, max_facts=k, max_themes=max_themes,
                                max_chars=max_chars)

    def hybrid_search(self, vector, k: int = 5) -> list[dict]:
        """Vector k-NN over chunks, then hop to the owning page (GraphRAG)."""
        rows = self._rows(
            f"CALL QUERY_VECTOR_INDEX('Chunk', '{CHUNK_VECTOR_INDEX}', $v, $k) "
            "WITH node AS c, distance "
            "MATCH (c)-[:PART_OF]->(p:Page) "
            "RETURN c.id, c.text, p.slug, p.title, distance ORDER BY distance;",
            {"v": list(vector), "k": k},
        )
        return [
            {"chunk_id": r[0], "text": r[1], "page_slug": r[2],
             "page_title": r[3], "distance": float(r[4])}
            for r in rows
        ]
