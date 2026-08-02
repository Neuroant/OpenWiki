"""Query the knowledge graph: page neighborhoods + hybrid vector search.

`GraphStore` opens a Kuzu database built by :mod:`openwiki.graph.builder` and
answers the questions the Graph tab and (optionally) the agent need:
`neighborhood(slug)` for exploration, and `hybrid_search(vector)` to show the
vector index and graph traversal working together.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

try:
    import kuzu
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Kuzu is required for the graph layer. Install it with `pip install kuzu`."
    ) from exc

from .builder import CHUNK_VECTOR_INDEX


class GraphStore:
    def __init__(self, db_path) -> None:
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"No graph at {self.db_path}. Build it with `openwiki graph-build`."
            )
        # Read-only: the store never writes, and this avoids taking Kuzu's
        # exclusive write lock (so a serving process won't block, e.g., a rebuild
        # or a second reader).
        try:
            self._db = kuzu.Database(str(self.db_path), read_only=True)
        except TypeError:  # older kuzu without the kwarg
            self._db = kuzu.Database(str(self.db_path))
        self._conn = kuzu.Connection(self._db)
        self._lock = threading.Lock()  # one Kuzu connection, possibly many web threads

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

    @staticmethod
    def _node(row) -> dict:
        return {"slug": row[0], "title": row[1], "level": row[2],
                "pdf_start": row[3], "pdf_end": row[4]}

    _P = "p.slug, p.title, p.level, p.pdf_start, p.pdf_end"

    # -- API ------------------------------------------------------------

    def stats(self) -> dict:
        def count(q):
            return self._rows(q)[0][0]
        return {
            "pages": count("MATCH (p:Page) RETURN count(p);"),
            "chunks": count("MATCH (c:Chunk) RETURN count(c);"),
            "child_of": count("MATCH ()-[r:CHILD_OF]->() RETURN count(r);"),
            "similar_to": count("MATCH ()-[r:SIMILAR_TO]->() RETURN count(r);"),
            "references": count("MATCH ()-[r:REFERENCES]->() RETURN count(r);"),
            "entities": count("MATCH (e:Entity) RETURN count(e);"),
            "mentions": count("MATCH ()-[r:MENTIONS]->() RETURN count(r);"),
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
        }

        nodes = {slug: {**self._node(center[0]), "rel": "center"}}
        edges = []
        for rel, rows in groups.items():
            for row in rows:
                node = self._node(row)
                nodes.setdefault(node["slug"], {**node, "rel": rel})
                edge = {"source": slug, "target": node["slug"], "type": rel}
                if rel == "similar":
                    edge["score"] = round(float(row[5]), 3)
                edges.append(edge)

        return {"center": slug, "nodes": list(nodes.values()), "edges": edges}

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
