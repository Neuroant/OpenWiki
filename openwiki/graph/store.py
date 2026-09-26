"""Query the knowledge graph: page neighborhoods + hybrid vector search.

`GraphStore` opens a Kuzu database built by :mod:`openwiki.graph.builder` and
answers the questions the Graph tab and (optionally) the agent need:
`neighborhood(slug)` for exploration, and `hybrid_search(vector)` to show the
vector index and graph traversal working together.
"""

from __future__ import annotations

import json
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
    DAY_SECONDS, DEFAULT_BOOST, DEFAULT_FLOOR, DEFAULT_HALF_LIFE_DAYS,
    confidence_weight, effective_weight, reinforced_weight,
)
from .entities import _normalize
from .journal import (
    append_reindex, append_remember, clear_journal, journal_path,
    pending_journal, read_journal,
)
from .temporal import (
    MANY, ONE, believed_at, close_times, derive_legacy_intervals, plan_merge, valid_at,
    valid_to_known_at,
)
from .temporal import session_date as session_date_of
from .temporal import status as temporal_status
from .usage import append_usage, clear_usage, read_usage, usage_log_path


# Assertion columns by schema generation (read defensively — older graphs lack the later ones).
_A_BASE = ("id", "subject", "predicate", "object", "session_id", "created_at")
_A_CONF = ("confidence", "last_seen")                                     # v0.54
_A_B7 = ("valid_from", "valid_to", "expired_at", "cardinality")           # B7 bi-temporal
_A_B9 = ("attr",)                                                         # B9 attribute key
_A_P0 = ("source",)                                                       # P0 provenance
_A_SLEEP = ("forgotten_at", "forgotten")                                  # sleep: archived (when, why)
MATERIAL_WEIGHT = 0.75                # P0: a claim from discussed material ranks below decisions
RECENCY_FLOOR = 0.6                   # recall: time decay never takes a fact below 60% of its score
ATTR_SEP = "\x1f"                     # canonical attribute key = normalized subject ␟ predicate
RESOLVE_THRESHOLD = 0.75              # min fact-embedding cosine for an attribute candidate (B9)
RESOLVE_K = 6                         # candidates shown to the attribute chooser


def attr_key(subject: str, predicate: str) -> str:
    """The exact (pre-B9) attribute key of a fact: normalized subject ␟ lowercased predicate."""
    return f"{_normalize(subject)}{ATTR_SEP}{(predicate or '').strip().lower()}"


def _key_of(rec: dict) -> str:
    """A record's attribute key: its resolved ``attr`` (B9), else its own exact key."""
    return rec.get("attr") or attr_key(rec["subject"], rec["predicate"])


_PERSONAL = re.compile(r"^(the )?user\b|^(i|me|my)\b", re.IGNORECASE)


def _is_personal(rec: dict) -> bool:
    """A fact about the user themself ("user | is allergic to | hazelnuts", "user's knee | …",
    "Bruno | is the dog of | user") — capture names the user "user"."""
    return any(_PERSONAL.match((rec.get(part) or "").strip()) for part in ("subject", "object"))


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
        # B1 concurrency: a read-only store queues deferred writes (remember / edit
        # re-sync) to the op journal; the next writable pass folds them (fold_journal).
        self._journal_path = journal_path(self.db_path)

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
            "relations": self._rel_count(),
            "entity_types": by_type,
        }

    def _rel_count(self) -> int:
        try:
            return self._rows("MATCH ()-[r:RELATED_TO]->() RETURN count(r);")[0][0]
        except Exception:      # pre-relations graph without the table
            return 0

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
            # Relation-aware (Direction B): pages a *typed* Entity→Entity relation connects
            # (MENTIONS→RELATED_TO→MENTIONS). Last, so it surfaces pages that similarity /
            # shared-entity don't — the connections only the knowledge graph knows.
            "relation": self._relation_neighbors(slug, limit=similar_k),
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

    def _relation_neighbors(self, slug: str, limit: int = 6) -> list:
        """Pages connected to ``slug`` by a **typed** entity relation
        (``MENTIONS → RELATED_TO → MENTIONS``), most-connected first. Guarded: ``[]`` on a
        graph built before the relation layer (no RELATED_TO table)."""
        try:
            return self._rows(
                f"MATCH (:Page {{slug:$s}})-[:MENTIONS]->(:Entity)-[:RELATED_TO]-(:Entity)"
                f"<-[:MENTIONS]-(p:Page) WHERE p.slug <> $s "
                f"RETURN {self._P}, count(*) AS rels ORDER BY rels DESC, p.slug LIMIT $k;",
                {"s": slug, "k": limit})
        except Exception:
            return []

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

    def expand_entity(self, key: str, page_k: int = 15, relation_k: int = 12) -> dict:
        """Expanding an entity node: the pages that mention it **plus** the entities it is
        typed-related to (RELATED_TO), so the Graph tab reveals the knowledge sub-graph."""
        rows = self._rows(
            "MATCH (e:Entity {key:$k})<-[:MENTIONS]-(p:Page) "
            "RETURN p.slug, p.title, p.level, p.pdf_start, p.pdf_end LIMIT $pk;",
            {"k": key, "pk": page_k})
        nodes = [self._page_gnode(r) for r in rows]
        edges = [{"source": r[0], "target": key, "type": "mentions"} for r in rows]
        # typed relations in both directions (empty on a graph without the layer)
        try:
            outgoing = self._rows(
                "MATCH (e:Entity {key:$k})-[r:RELATED_TO]->(o:Entity) "
                "RETURN o.key, o.name, o.type, r.predicate, r.weight "
                "ORDER BY r.weight DESC LIMIT $rk;", {"k": key, "rk": relation_k})
            incoming = self._rows(
                "MATCH (e:Entity {key:$k})<-[r:RELATED_TO]-(o:Entity) "
                "RETURN o.key, o.name, o.type, r.predicate, r.weight "
                "ORDER BY r.weight DESC LIMIT $rk;", {"k": key, "rk": relation_k})
        except Exception:
            outgoing = incoming = []
        for okey, oname, otype, predicate, _w in outgoing:
            nodes.append({"id": okey, "kind": "entity", "label": oname, "etype": otype})
            edges.append({"source": key, "target": okey, "type": "relation", "label": predicate})
        for okey, oname, otype, predicate, _w in incoming:
            nodes.append({"id": okey, "kind": "entity", "label": oname, "etype": otype})
            edges.append({"source": okey, "target": key, "type": "relation", "label": predicate})
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

    @staticmethod
    def _aliases(joined) -> list:
        return [a for a in (joined or "").split(", ") if a]

    # -- inline citations (ADR-28) ------------------------------------------

    def citations(self, slug: str) -> list[dict]:
        """The page's outgoing cross-references with the **citation phrases** that produced them
        (``[{"label": "Abschnitt 1.6", "slug", "title"}, …]``, longest phrase first) — what the
        web UI links inline. ``[]`` on graphs built before the ``labels`` property existed
        (refresh them with ``openwiki references``)."""
        try:
            rows = self._rows("MATCH (a:Page {slug:$s})-[r:REFERENCES]->(b:Page) "
                              "RETURN r.labels, b.slug, b.title;", {"s": slug})
        except Exception:
            return []
        out = []
        for labels, dst, title in rows:
            try:
                phrases = json.loads(labels) if labels else []
            except ValueError:
                phrases = []
            out.extend({"label": p, "slug": dst, "title": title} for p in phrases if p)
        return sorted(out, key=lambda c: (-len(c["label"]), c["label"]))

    def refresh_references(self, references) -> int:
        """Replace every ``REFERENCES`` edge with ``references`` — ``(src, dst)`` pairs or
        ``(src, dst, [citation phrases])`` triples — **in place**: the cheap upgrade path for a
        graph built before citation labels (no rebuild; entities/memory untouched). Adds the
        ``labels`` property on an older table first. Writable-only; returns edges written."""
        if not self.writable:
            raise RuntimeError("GraphStore is read-only; open it writable to refresh references.")
        slugs = {r[0] for r in self._rows("MATCH (p:Page) RETURN p.slug;")}
        count = 0
        with self._lock:
            try:
                self._exec("ALTER TABLE REFERENCES ADD labels STRING;")
            except Exception:      # already present
                pass
            self._exec("MATCH (:Page)-[r:REFERENCES]->(:Page) DELETE r;")
            for ref in references:
                src, dst = ref[0], ref[1]
                labels = list(ref[2]) if len(ref) > 2 and ref[2] else []
                if src in slugs and dst in slugs and src != dst:
                    self._exec("MATCH (a:Page {slug:$a}),(b:Page {slug:$b}) "
                               "CREATE (a)-[:REFERENCES {labels:$l}]->(b);",
                               {"a": src, "b": dst,
                                "l": json.dumps(labels, ensure_ascii=False) if labels else None})
                    count += 1
        return count

    def entities_for_page(self, slug: str) -> list[dict]:
        try:  # enriched (description + aliases from resolution); fall back for pre-0.66 graphs
            rows = self._rows(
                "MATCH (:Page {slug:$s})-[:MENTIONS]->(e:Entity) "
                "RETURN e.name, e.type, e.description, e.aliases ORDER BY e.type, e.name;", {"s": slug})
        except Exception:
            rows = [(r[0], r[1], "", "") for r in self._rows(
                "MATCH (:Page {slug:$s})-[:MENTIONS]->(e:Entity) "
                "RETURN e.name, e.type ORDER BY e.type, e.name;", {"s": slug})]
        return [{"name": r[0], "type": r[1], "description": r[2] or "",
                 "aliases": self._aliases(r[3])} for r in rows]

    def pages_for_entity(self, query: str, limit: int = 20) -> list[dict]:
        """Pages that mention an entity whose name **or an alias** contains ``query``
        (case-insensitive) — so resolution's alias table makes acronyms/synonyms findable."""
        try:
            rows = self._rows(
                "MATCH (e:Entity)<-[:MENTIONS]-(p:Page) "
                "WHERE contains(lower(e.name), lower($q)) OR contains(lower(e.aliases), lower($q)) "
                "RETURN e.name, e.type, p.slug, p.title, e.description, e.aliases "
                "ORDER BY e.name LIMIT $k;", {"q": str(query), "k": limit})
        except Exception:   # pre-0.66 graph without description/aliases columns
            rows = [(r[0], r[1], r[2], r[3], "", "") for r in self._rows(
                "MATCH (e:Entity)<-[:MENTIONS]-(p:Page) WHERE contains(lower(e.name), lower($q)) "
                "RETURN e.name, e.type, p.slug, p.title ORDER BY e.name LIMIT $k;",
                {"q": str(query), "k": limit})]
        return [{"entity": r[0], "type": r[1], "slug": r[2], "title": r[3],
                 "description": r[4] or "", "aliases": self._aliases(r[5])} for r in rows]

    def list_entities(self, query: str = "", etype: str = "", limit: int = 200) -> list[dict]:
        """Browse **canonical** entities (name · type · description · aliases · mention count),
        filtered by a name/alias substring and/or type, ranked by mentions — for the Begriffe
        tab. Empty without entities."""
        if not self.has_entities():
            return []
        clauses, params = [], {"k": int(limit)}
        if query:
            clauses.append("(contains(lower(e.name), lower($q)) OR contains(lower(e.aliases), lower($q)))")
            params["q"] = str(query)
        if etype:
            clauses.append("e.type = $t")
            params["t"] = str(etype)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        try:
            rows = self._rows(
                f"MATCH (e:Entity){where} OPTIONAL MATCH (e)<-[m:MENTIONS]-(:Page) "
                "WITH e, count(m) AS n RETURN e.name, e.type, e.description, e.aliases, n "
                "ORDER BY n DESC, e.name LIMIT $k;", params)
        except Exception:   # pre-0.66 graph without description/aliases columns
            q2 = query and "contains(lower(e.name), lower($q))"
            where2 = " WHERE " + " AND ".join(c for c in (q2, etype and "e.type = $t") if c) if (query or etype) else ""
            rows = [(r[0], r[1], "", "", r[2]) for r in self._rows(
                f"MATCH (e:Entity){where2} OPTIONAL MATCH (e)<-[m:MENTIONS]-(:Page) "
                "WITH e, count(m) AS n RETURN e.name, e.type, n ORDER BY n DESC, e.name LIMIT $k;", params)]
        return [{"name": r[0], "type": r[1], "description": r[2] or "",
                 "aliases": self._aliases(r[3]), "mentions": int(r[4])} for r in rows]

    def entity_detail(self, name: str) -> Optional[dict]:
        """Full record for one canonical entity (exact ``name``): type, description, aliases,
        mention count, the pages that mention it, and its typed relations (both directions,
        each with the ``other`` endpoint + direction). ``None`` if not found."""
        if not self.has_entities():
            return None
        try:
            rows = self._rows(
                "MATCH (e:Entity {name:$n}) OPTIONAL MATCH (e)<-[m:MENTIONS]-(:Page) "
                "RETURN e.name, e.type, e.description, e.aliases, count(m);", {"n": str(name)})
        except Exception:
            rows = [(r[0], r[1], "", "", r[2]) for r in self._rows(
                "MATCH (e:Entity {name:$n}) OPTIONAL MATCH (e)<-[m:MENTIONS]-(:Page) "
                "RETURN e.name, e.type, count(m);", {"n": str(name)})]
        if not rows or rows[0][0] is None:
            return None
        r = rows[0]
        pages = self._rows(
            "MATCH (:Entity {name:$n})<-[:MENTIONS]-(p:Page) "
            "RETURN DISTINCT p.slug, p.title ORDER BY p.title;", {"n": str(name)})
        try:
            rel_rows = self._rows(
                "MATCH (a:Entity)-[r:RELATED_TO]->(b:Entity) WHERE a.name = $n OR b.name = $n "
                "RETURN a.name, r.predicate, b.name, r.weight ORDER BY r.weight DESC, a.name LIMIT 40;",
                {"n": str(name)})
        except Exception:
            rel_rows = []
        relations = [{"subject": x[0], "predicate": x[1], "object": x[2], "weight": x[3],
                      "other": (x[2] if x[0] == name else x[0]),
                      "direction": ("out" if x[0] == name else "in")} for x in rel_rows]
        return {"name": r[0], "type": r[1], "description": r[2] or "",
                "aliases": self._aliases(r[3]), "mentions": int(r[4] or 0),
                "pages": [{"slug": p[0], "title": p[1]} for p in pages],
                "relations": relations}

    # -- typed entity relations (Direction B) --------------------------

    def has_relations(self) -> bool:
        return self._rel_count() > 0

    def relations_for_entity(self, query: str, limit: int = 30) -> list[dict]:
        """Typed relations touching any entity whose name contains ``query`` — both
        directions (subject or object), each as ``subject --predicate--> object`` with the
        support weight. Empty on a graph without the relation layer."""
        try:
            rows = self._rows(
                "MATCH (a:Entity)-[r:RELATED_TO]->(b:Entity) "
                "WHERE contains(lower(a.name), lower($q)) OR contains(lower(b.name), lower($q)) "
                "RETURN a.name, a.type, r.predicate, b.name, b.type, r.weight "
                "ORDER BY r.weight DESC, a.name LIMIT $k;",
                {"q": str(query), "k": limit})
        except Exception:
            return []
        return [{"subject": r[0], "subject_type": r[1], "predicate": r[2],
                 "object": r[3], "object_type": r[4], "weight": r[5]} for r in rows]

    def relations_for_page(self, slug: str, limit: int = 40) -> list[dict]:
        """Typed relations whose subject entity is mentioned on ``slug`` — the page's local
        knowledge sub-graph."""
        try:
            rows = self._rows(
                "MATCH (:Page {slug:$s})-[:MENTIONS]->(a:Entity)-[r:RELATED_TO]->(b:Entity) "
                "RETURN DISTINCT a.name, r.predicate, b.name, r.weight "
                "ORDER BY r.weight DESC, a.name LIMIT $k;",
                {"s": slug, "k": limit})
        except Exception:
            return []
        return [{"subject": r[0], "predicate": r[1], "object": r[2], "weight": r[3]} for r in rows]

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

    def coupling_edges(self) -> dict:
        """Undirected page↔page edge pairs per kind, for the world-model coupling
        analysis (``openwiki.analysis.coupling``). Each kind maps to a list of distinct
        ``(a, b)`` slug pairs. Guarded per kind so optional layers (entities/relations)
        yield an empty list on graphs that lack them, rather than raising."""
        def undirected(query: str) -> list:
            try:
                rows = self._rows(query)
            except Exception:       # optional table (Entity/RELATED_TO) absent
                return []
            seen: set = set()
            for a, b in rows:
                if a == b:
                    continue
                seen.add((a, b) if a < b else (b, a))
            return list(seen)

        return {
            "similar": undirected(
                "MATCH (a:Page)-[:SIMILAR_TO]->(b:Page) RETURN a.slug, b.slug;"),
            "references": undirected(
                "MATCH (a:Page)-[:REFERENCES]->(b:Page) RETURN a.slug, b.slug;"),
            "shared_entity": undirected(
                "MATCH (a:Page)-[:MENTIONS]->(:Entity)<-[:MENTIONS]-(b:Page) "
                "WHERE a.slug <> b.slug RETURN a.slug, b.slug;"),
            "relation": undirected(
                "MATCH (a:Page)-[:MENTIONS]->(:Entity)-[:RELATED_TO]-(:Entity)"
                "<-[:MENTIONS]-(b:Page) WHERE a.slug <> b.slug RETURN a.slug, b.slug;"),
            "child_of": undirected(
                "MATCH (a:Page)-[:CHILD_OF]->(b:Page) RETURN a.slug, b.slug;"),
            "next": undirected(
                "MATCH (a:Page)-[:NEXT]->(b:Page) RETURN a.slug, b.slug;"),
        }

    def shared_entity_pairs(self) -> list:
        """Distinct ``(a, b, n)`` page pairs (``a < b``) that co-mention ``n`` entities — the
        raw material for missing-cross-reference detection (``analysis.gaps``). Empty without
        entities."""
        if not self.has_entities():
            return []
        try:
            rows = self._rows(
                "MATCH (a:Page)-[:MENTIONS]->(:Entity)<-[:MENTIONS]-(b:Page) "
                "WHERE a.slug < b.slug RETURN a.slug, b.slug, count(*) AS n;")
        except Exception:
            return []
        return [(a, b, int(n)) for a, b, n in rows]

    def all_entities(self) -> list:
        """``[{"name", "type"}, …]`` for every entity — for entity-merge-candidate mining.
        Empty without entities."""
        try:
            rows = self._rows("MATCH (e:Entity) RETURN e.name, e.type;")
        except Exception:
            return []
        return [{"name": r[0], "type": r[1]} for r in rows]

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

    # -- deferred-write journal (B1 concurrency) -----------------------

    def queue_remember(self, session_id: str, facts, session_date: Optional[int] = None,
                       correct: bool = False) -> int:
        """Append a `remember` op to the write-ahead journal (works read-only — that's the
        point: a locked-out writer queues instead of failing). Folded by ``fold_journal``,
        which honors the queued record time + validity (B7). Returns the number queued."""
        return append_remember(self._journal_path, session_id, facts,
                               session_date=session_date, correct=correct)

    def queue_reindex(self, slug: str, text: str) -> int:
        """Append a `reindex` op (re-sync one page) to the journal — a read-only serve/chat
        defers an agent edit's graph sync here; a later writer folds it (``fold_journal``)."""
        return append_reindex(self._journal_path, slug, text)

    def pending_ops(self) -> int:
        """How many deferred-write ops (remember/reindex) are queued (0 if none)."""
        return pending_journal(self._journal_path)

    def fold_journal(self, embedder, now: Optional[int] = None, coexist=None,
                     resolve=None) -> dict:
        """B1: drain the write-ahead journal (queued `remember` + `reindex` ops) into the
        graph and clear it. Writable-only; needs an ``embedder`` (facts + page chunks are
        embedded at fold time). A queued `remember` keeps the time it was *queued* as its
        record time (B7 — no drift to the fold time; ``now`` only fills in for records
        without one). Best-effort per record — a bad op never aborts the batch."""
        if not self.writable:
            raise RuntimeError("GraphStore is read-only; open it writable to fold the journal.")
        if embedder is None:
            raise ValueError("fold_journal needs an embedder.")
        records = read_journal(self._journal_path)
        if not records:
            return {"records": 0, "remembered": 0, "reindexed": 0}
        from .memory import MemoryFact
        now = int(now if now is not None else time.time())
        remembered = reindexed = 0
        for rec in records:
            try:
                if rec.get("op") == "remember":
                    facts = [MemoryFact(t[0], t[1], t[2],
                                        valid_from=t[3] if len(t) >= 5 else None,
                                        cardinality=(t[4] if len(t) >= 5 else None) or ONE,
                                        source=t[5] if len(t) >= 6 else None)
                             for t in rec.get("facts", [])
                             if isinstance(t, list) and len(t) in (3, 5, 6)]
                    if facts:
                        res = self.remember(str(rec.get("session") or "session"), facts, embedder,
                                            now=int(rec.get("t") or now),
                                            session_date=rec.get("session_date"),
                                            correct=bool(rec.get("correct")), coexist=coexist,
                                            resolve=resolve)
                        remembered += res.get("added", 0)
                elif rec.get("op") == "reindex":
                    slug = str(rec.get("slug") or "")
                    if slug:
                        self.upsert_page(slug, rec.get("text") or "", embedder=embedder)
                        reindexed += 1
            except Exception:      # pragma: no cover - one bad op never aborts the fold
                continue
        clear_journal(self._journal_path)
        return {"records": len(records), "remembered": remembered, "reindexed": reindexed}

    # -- remembered tier (Path B: session memory) ----------------------

    def _ensure_memory_schema(self, dim: int) -> None:
        """Create Session/Assertion/ASSERTS if a pre-existing graph lacks them (once), and —
        writable only — migrate older tables in place (no rebuild): v0.54 confidence columns,
        then the B7 bi-temporal columns + their backfill (``_migrate_temporal``)."""
        if self._memory_ensured:
            return
        for ddl in (
            "CREATE NODE TABLE IF NOT EXISTS Session(id STRING, created_at INT64, "
            "session_date INT64, PRIMARY KEY(id));",
            f"CREATE NODE TABLE IF NOT EXISTS Assertion(id STRING, subject STRING, predicate STRING, "
            f"object STRING, session_id STRING, created_at INT64, confidence DOUBLE, last_seen INT64, "
            f"valid_from INT64, valid_to INT64, expired_at INT64, cardinality STRING, attr STRING, "
            f"source STRING, forgotten_at INT64, forgotten STRING, emb FLOAT[{int(dim)}], "
            f"PRIMARY KEY(id));",
            "CREATE REL TABLE IF NOT EXISTS ASSERTS(FROM Session TO Assertion);",
            "CREATE REL TABLE IF NOT EXISTS SUPERSEDES(FROM Assertion TO Assertion);",   # B4
        ):
            try:
                self._exec(ddl)
            except Exception:  # pragma: no cover - already exists / older syntax
                pass
        # Migrate older Assertion/Session tables that lack columns (idempotent — ALTER errors
        # "already has property", which we swallow). Writable connections only.
        if self.writable:
            for ddl in ("ALTER TABLE Assertion ADD confidence DOUBLE DEFAULT 1.0;",   # v0.54
                        "ALTER TABLE Assertion ADD last_seen INT64 DEFAULT 0;",
                        "ALTER TABLE Assertion ADD valid_from INT64;",                # B7
                        "ALTER TABLE Assertion ADD valid_to INT64;",
                        "ALTER TABLE Assertion ADD expired_at INT64;",
                        "ALTER TABLE Assertion ADD cardinality STRING;",
                        "ALTER TABLE Assertion ADD attr STRING;",                     # B9
                        "ALTER TABLE Assertion ADD source STRING;",                   # P0
                        "ALTER TABLE Assertion ADD forgotten_at INT64;",              # sleep
                        "ALTER TABLE Assertion ADD forgotten STRING;",
                        "ALTER TABLE Session ADD session_date INT64;"):
                try:
                    self._exec(ddl)
                except Exception:  # pragma: no cover - column already present
                    pass
            self._migrate_temporal()
        self._memory_ensured = True

    def _migrate_temporal(self) -> int:
        """B7 backfill (writable, idempotent): give each pre-B7 assertion (``valid_from IS
        NULL``) the validity interval its B4 ``SUPERSEDES`` edges imply — so what was current
        stays current — and date sessions whose id carries a date. Returns rows migrated."""
        try:
            legacy = {r[0] for r in self._rows(
                "MATCH (a:Assertion) WHERE a.valid_from IS NULL RETURN a.id;")}
        except Exception:
            return 0
        if legacy:
            for r in self._load_assertions():            # derives the legacy intervals
                if r["id"] in legacy:
                    self._exec("MATCH (a:Assertion {id:$id}) "
                               "SET a.valid_from=$vf, a.valid_to=$vt, a.expired_at=$x;",
                               {"id": r["id"], "vf": r["valid_from"], "vt": r["valid_to"],
                                "x": r["expired_at"]})
        try:
            for (sid,) in self._rows("MATCH (s:Session) WHERE s.session_date IS NULL RETURN s.id;"):
                d = session_date_of(sid)
                if d is not None:
                    self._exec("MATCH (s:Session {id:$id}) SET s.session_date=$d;", {"id": sid, "d": d})
        except Exception:      # pragma: no cover - older Session table
            pass
        return len(legacy)

    def has_memory(self) -> bool:
        try:
            return self._rows("MATCH (a:Assertion) RETURN count(a);")[0][0] > 0
        except Exception:
            return False

    def _supersedes_edges(self) -> list:
        """``[(new_id, old_id)]`` — the B4/B7 provenance edges."""
        try:
            return [(r[0], r[1]) for r in self._rows(
                "MATCH (n:Assertion)-[:SUPERSEDES]->(o:Assertion) RETURN n.id, o.id;")]
        except Exception:
            return []

    def _load_assertions(self, with_emb: bool = False) -> list:
        """Every Assertion as a dict with the full B7 field set, whatever the graph's schema
        generation: pre-0.54 (no confidence), pre-B7 (no validity columns — or a read-only open
        of an unmigrated graph), B7. Missing validity is derived from the ``SUPERSEDES`` edges
        exactly as the migration would, so every reader sees one model. ``[]`` without the table."""
        rows, cols = None, ()
        for extra in (_A_CONF + _A_B7 + _A_B9 + _A_P0 + _A_SLEEP, _A_CONF + _A_B7 + _A_B9 + _A_P0,
                      _A_CONF + _A_B7 + _A_B9, _A_CONF + _A_B7, _A_CONF, ()):
            fields = _A_BASE + extra + (("emb",) if with_emb else ())
            try:
                rows = self._rows("MATCH (a:Assertion) RETURN "
                                  + ", ".join(f"a.{c}" for c in fields) + ";")
            except Exception:
                continue
            cols = fields
            break
        if rows is None:
            return []
        recs = []
        for row in rows:
            r = dict(zip(cols, row))
            r["created_at"] = int(r.get("created_at") or 0)
            r["confidence"] = float(r["confidence"]) if r.get("confidence") is not None else 1.0
            r["last_seen"] = int(r.get("last_seen") or 0)
            for c in _A_B7 + _A_B9 + _A_P0 + _A_SLEEP:
                r.setdefault(c, None)
            r["cardinality"] = r["cardinality"] or ONE
            recs.append(r)
        if any(r["valid_from"] is None for r in recs):
            derive_legacy_intervals(recs, self._supersedes_edges())
        return recs

    def _view(self, recs: list, now: int, as_of: Optional[int] = None,
              known_at: Optional[int] = None) -> None:
        """Annotate records in place for one bi-temporal view: ``status`` (at ``now``),
        ``superseded`` (past or retracted), and ``in_view`` — believed at ``known_at``
        (transaction time; ``None`` = now) **and** valid at ``as_of`` (valid time; defaults
        to ``known_at``, else ``now``). ``known_at`` sees an interval as open until OpenWiki
        learned it had closed (derived from the provenance edges)."""
        closed = close_times(recs, self._supersedes_edges()) if known_at is not None else {}
        t_valid = as_of if as_of is not None else (known_at if known_at is not None else now)
        for r in recs:
            r["status"] = temporal_status(r, now)
            r["superseded"] = r["status"] in ("past", "retracted", "forgotten")
            vt = valid_to_known_at(r, known_at, closed)
            r["in_view"] = believed_at(r, known_at) and valid_at(r, t_valid, valid_to=vt)

    def remember(self, session_id: str, facts, embedder, now: Optional[int] = None,
                 session_date: Optional[int] = None, correct: bool = False,
                 coexist=None, resolve=None, resolve_threshold: float = RESOLVE_THRESHOLD) -> dict:
        """B3 merge + B4 contradiction handling + **B7 bi-temporal validity**: embed each fact,
        persist Session + Assertion + ASSERTS, and slot each fact into the history of its
        (subject, predicate) by **valid time** (``temporal.plan_merge``), not processing order —
        so a backfilled older session lands *in* history instead of overwriting the present.

        A fact is valid from its stated ``valid_from``, else the ``session_date`` (given, or a
        date in the session id), else ``now`` (the record time, ``created_at``). Re-affirming a
        fact that already holds raises its confidence (B6); a functional rival is **closed**
        (``valid_to`` — the world changed) or, starting at the same instant / with ``correct``,
        **retracted** (``expired_at`` — we were wrong); ``"many"``-cardinality facts coexist.
        Nothing is deleted; ``SUPERSEDES`` edges keep the provenance. Returns counts
        (``superseded`` = closed + retracted; ``historical`` = new facts that landed in the past).

        ``coexist(older_text, newer_text) -> bool`` (optional, e.g. ``memory.facts_coexist``
        bound to a chat model) is asked before a tag-based rival is invalidated — a pair that
        can hold at once is kept and both records are marked ``"many"`` (verdicts cached per
        call; see ``temporal.plan_merge``). Without it the capture tags alone decide.

        ``resolve(fact_text, candidate_labels) -> index | None`` (optional, e.g.
        ``memory.choose_attribute`` bound to a chat model) is **B9 fact identity**: a fact whose
        exact attribute key is new is matched against the existing attribute groups whose
        members are nearest in embedding space (cosine ≥ ``resolve_threshold``, top
        ``RESOLVE_K``); if the chooser picks one, the fact joins that group (its ``attr``) — so
        "project | is versioned" and "project | has version" are merged by valid time instead of
        both staying current. Counted as ``resolved``."""
        if not self.writable:
            raise RuntimeError("GraphStore is read-only; open it writable to remember.")
        facts = list(facts)
        empty = {"facts": 0, "added": 0, "duplicates": 0, "superseded": 0,
                 "retracted": 0, "historical": 0, "resolved": 0, "scrubbed": 0}
        if not facts:
            return empty
        if embedder is None:
            raise ValueError("remember needs an embedder.")
        # P0: the rule scrubber is the last line of defense for facts arriving by any path (journal,
        # a caller that skipped capture's audit) — an injected instruction never becomes memory
        from .memory import is_unsafe_instruction
        scrubbed = sum(1 for f in facts if is_unsafe_instruction(f))
        facts = [f for f in facts if not is_unsafe_instruction(f)]
        if not facts:
            return dict(empty, scrubbed=scrubbed)
        now = int(now if now is not None else time.time())
        sdate = int(session_date) if session_date is not None else session_date_of(session_id)
        # recency counts from when the fact was *said* (a backfilled session), not from when it was
        # recorded — else a whole backfilled history looks equally fresh ("hot") today
        said_at = min(now, sdate) if sdate is not None else now
        emb = embedder.embed_documents([f.text() for f in facts]).astype(np.float32)
        norms = np.linalg.norm(emb, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        emb = emb / norms
        self._ensure_memory_schema(emb.shape[1])
        n = dict(empty, facts=len(facts), scrubbed=scrubbed)
        with self._lock:
            self._exec("MERGE (s:Session {id:$id}) ON CREATE SET s.created_at=$t, s.session_date=$d;",
                       {"id": session_id, "t": now, "d": sdate})
            # Every record (current or not) grouped by attribute key (B9 ``attr``, else the exact
            # normalized subject+predicate): the merge needs the group's whole valid-time history.
            groups: dict = {}
            aliases: dict = {}                             # B9: exact key → the attribute it resolved to
            members: list = []                             # (key, normalized emb) — for B9 candidates
            for r in self._load_assertions(with_emb=resolve is not None):
                if r.get("forgotten_at") is not None:      # archived by sleep: a re-said fact is new
                    continue
                r["okey"] = _normalize(r["object"])
                key = _key_of(r)
                groups.setdefault(key, []).append(r)
                own = attr_key(r["subject"], r["predicate"])
                if own != key:
                    aliases[own] = key
                if resolve is not None and r.get("emb") is not None:
                    v = np.asarray(r.pop("emb"), dtype=np.float32)
                    members.append((key, v / (np.linalg.norm(v) or 1.0)))
            verdicts: dict = {}                            # (old okey, new okey) → coexist?
            batch_ids: set = set()                         # records created by this call
            for idx, (fact, vec) in enumerate(zip(facts, emb)):
                ns, pp, no = fact.key()
                own = attr_key(fact.subject, fact.predicate)
                key = aliases.get(own, own)              # a wording already resolved → no new call
                if key not in groups and resolve is not None and members:
                    chosen = self._resolve_attribute(fact, vec, groups, members, resolve,
                                                     resolve_threshold)
                    if chosen is not None:
                        key = aliases[own] = chosen
                        n["resolved"] += 1
                group = groups.setdefault(key, [])
                by_id = {r["id"]: r for r in group}
                vf = fact.valid_from if fact.valid_from is not None else (
                    sdate if sdate is not None else now)
                # the capture model often "states" the session's own date (midnight) — no more
                # informative than the session, and less precise than a timed one (a backfill
                # window's first turn): keep the session time so same-day changes stay ordered
                if (fact.valid_from is not None and sdate is not None
                        and fact.valid_from // DAY_SECONDS == sdate // DAY_SECONDS):
                    vf = sdate

                def coexists(r, fact=fact, no=no):
                    key = (r["okey"], no)
                    if key not in verdicts:
                        try:
                            verdicts[key] = bool(coexist(
                                f"{r['subject']} {r['predicate']} {r['object']}", fact.text(),
                                subjects=(r["subject"], fact.subject)))
                        except Exception:              # a failed check never blocks the merge
                            verdicts[key] = False
                    return verdicts[key]
                plan = plan_merge(group, no, int(vf), fact.cardinality, correct,
                                  coexists=coexists if coexist is not None else None,
                                  batch=batch_ids)
                # the captured tag is kept as-is — a coexistence verdict applies to *that pair*
                # only; persisting it as "many" would exempt the records from supersession for good
                card = fact.cardinality or ONE
                for rid, vt in plan["close"]:              # the world changed at vf
                    self._exec("MATCH (a:Assertion {id:$id}) SET a.valid_to=$vt;",
                               {"id": rid, "vt": int(vt)})
                    by_id[rid]["valid_to"] = int(vt)
                for rid in plan["expire"]:                 # we were wrong: stop believing it
                    self._exec("MATCH (a:Assertion {id:$id}) SET a.expired_at=$t;",
                               {"id": rid, "t": now})
                    by_id[rid]["expired_at"] = now
                n["superseded"] += len(plan["close"]) + len(plan["expire"])
                n["retracted"] += len(plan["expire"])
                if plan["action"] in ("reaffirm", "extend"):   # same fact → B6: raise confidence
                    tgt = by_id[plan["target"]]
                    conf = reinforced_weight(tgt["confidence"], DEFAULT_BOOST)
                    seen = max(int(tgt.get("last_seen") or 0), said_at)
                    self._exec("MATCH (a:Assertion {id:$id}) "
                               "SET a.confidence=$c, a.last_seen=$t, a.valid_from=$vf;",
                               {"id": tgt["id"], "c": conf, "t": seen, "vf": int(plan["valid_from"])})
                    tgt.update(confidence=conf, last_seen=seen, valid_from=int(plan["valid_from"]))
                    aid = tgt["id"]
                    n["duplicates"] += 1
                else:
                    aid = uuid.uuid4().hex
                    rec = {"id": aid, "subject": fact.subject, "predicate": fact.predicate,
                           "object": fact.object, "okey": no, "session_id": session_id,
                           "created_at": now, "confidence": 1.0, "last_seen": said_at,
                           "valid_from": int(plan["valid_from"]), "valid_to": plan["valid_to"],
                           "expired_at": None, "cardinality": card, "attr": key,
                           "source": getattr(fact, "source", None)}
                    self._exec(
                        "CREATE (:Assertion {id:$id, subject:$s, predicate:$p, object:$o, "
                        "session_id:$sid, created_at:$t, confidence:1.0, last_seen:$ls, "
                        "valid_from:$vf, valid_to:$vt, cardinality:$card, attr:$attr, "
                        "source:$src, emb:$e});",
                        {"id": aid, "s": fact.subject, "p": fact.predicate, "o": fact.object,
                         "sid": session_id, "t": now, "ls": said_at, "vf": rec["valid_from"], "vt": rec["valid_to"],
                         "card": rec["cardinality"], "attr": key, "src": rec["source"],
                         "e": vec.astype(float).tolist()})
                    if resolve is not None:
                        members.append((key, vec / (np.linalg.norm(vec) or 1.0)))
                    self._exec("MATCH (s:Session {id:$sid}),(a:Assertion {id:$id}) "
                               "CREATE (s)-[:ASSERTS]->(a);", {"sid": session_id, "id": aid})
                    group.append(rec)
                    batch_ids.add(aid)
                    n["added"] += 1
                    if rec["valid_to"] is not None and rec["valid_to"] <= now:
                        n["historical"] += 1               # a backfill landed in the past
                    if plan["superseded_by"]:              # a later record already ends it
                        self._exec("MATCH (n:Assertion {id:$n}),(o:Assertion {id:$o}) "
                                   "CREATE (n)-[:SUPERSEDES]->(o);",
                                   {"n": plan["superseded_by"], "o": aid})
                for rid in [c[0] for c in plan["close"]] + plan["expire"]:
                    self._exec("MATCH (n:Assertion {id:$n}),(o:Assertion {id:$o}) "
                               "CREATE (n)-[:SUPERSEDES]->(o);", {"n": aid, "o": rid})
        return n

    @staticmethod
    def _resolve_attribute(fact, vec, groups: dict, members: list, resolve,
                           threshold: float) -> Optional[str]:
        """B9: the existing attribute key ``fact`` should join, or ``None``. Candidates = groups
        with a member at cosine ≥ ``threshold`` to the fact (max over members — a group's
        paraphrases can sit apart), the ``RESOLVE_K`` nearest, shown to ``resolve`` with their
        latest value as an example. A failing chooser never blocks the merge (→ ``None``)."""
        keys = [k for k, _ in members]
        sims = np.vstack([v for _, v in members]) @ (vec / (np.linalg.norm(vec) or 1.0))
        best: dict = {}
        for k, s in zip(keys, sims):
            if s >= threshold and s > best.get(k, -1.0):
                best[k] = float(s)
        if not best:
            return None
        cands = sorted(best, key=lambda k: -best[k])[:RESOLVE_K]
        labels = []
        for k in cands:
            ex = max(groups[k], key=lambda r: (r.get("valid_from") or 0, r.get("created_at") or 0))
            labels.append(f"{ex['subject']} | {ex['predicate']}   (e.g. {str(ex['object'])[:60]})")
        try:
            pick = resolve(f"{fact.subject} | {fact.predicate} | {fact.object}", labels)
        except Exception:
            return None
        return cands[pick] if isinstance(pick, int) and 0 <= pick < len(cands) else None

    def recall(self, query: str, embedder, k: int = 5,
               half_life_days: float = DEFAULT_HALF_LIFE_DAYS, now: Optional[int] = None,
               include_superseded: bool = False, as_of: Optional[int] = None,
               known_at: Optional[int] = None) -> list:
        """B6 (activation tier) + **B7 point-in-time**: the remembered facts most relevant to
        ``query`` — cosine over assertion embeddings × decayed confidence. By default only the
        facts **valid now and still believed**; ``as_of`` = valid at that (valid) time,
        ``known_at`` = as OpenWiki believed at that (transaction) time (valid time then
        defaults to it too). ``include_superseded`` also returns everything outside the view,
        each flagged (``superseded``, ``status``, ``in_view``). Read-only."""
        if embedder is None:
            raise ValueError("recall needs an embedder.")
        recs = self._load_assertions(with_emb=True)   # [] on graphs built before this layer
        if not recs:
            return []
        now = int(now if now is not None else time.time())
        self._view(recs, now, as_of=as_of, known_at=known_at)
        q = np.asarray(embedder.embed_query(query), dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        scored = []
        for r in recs:
            if not (r["in_view"] or include_superseded):
                continue
            ref = r["last_seen"] or r["created_at"]                # last affirmed, else first stated
            cos = float(q @ np.asarray(r["emb"], dtype=np.float32))  # stored normalized
            # gentle, log-scaled confidence lift; recency as a *bounded* tie-breaker — relevance (cos)
            # dominates: unbounded decay let any recent, weakly related fact beat an old, highly
            # relevant one (a year-old "allergic to hazelnuts" scored ~0.0001 × cos — cue-trigger eval)
            decay = effective_weight(1.0, ref, now, half_life_days)
            score = (cos * confidence_weight(r["confidence"])
                     * (RECENCY_FLOOR + (1.0 - RECENCY_FLOOR) * decay))
            if r.get("source") == "material":                      # P0: a claim, not a decision
                score *= MATERIAL_WEIGHT
            scored.append({"id": r["id"], "subject": r["subject"], "predicate": r["predicate"],
                           "object": r["object"], "session_id": r["session_id"],
                           "cos": round(cos, 3), "confidence": round(r["confidence"], 3),
                           "score": round(score, 3), "_rank": score, "superseded": r["superseded"],
                           "status": r["status"], "in_view": r["in_view"],
                           "valid_from": r["valid_from"], "valid_to": r["valid_to"],
                           "created_at": r["created_at"], "expired_at": r["expired_at"],
                           "source": r.get("source")})
        scored.sort(key=lambda x: -x["_rank"])     # the unrounded score — rounding made ties
        for x in scored:
            x.pop("_rank")
        return scored[:k]

    def recall_probed(self, query: str, embedder, probes, k: int = 5, **kw) -> list:
        """P1 cue-trigger recall: ``recall`` for ``query`` plus one reserved slot per constraint
        ``probe`` (``memory.constraint_probes``) — each probe's best hit not already present comes
        first, the query's hits fill the rest; the total stays ``k``. The probes reach the user's
        implicit constraints ("can't stand noisy offices") that share no words with the request.
        A probe asks about the user, so its slot only takes a fact *about the user* among its top
        hits — else it stays empty: a topic fact ("client meetings are held in Room 4B") would take
        the slot it was meant to free, and a memory with no personal facts would be relabelled
        wholesale as "the user's circumstances" (measured: "we won't adopt SleepGate" became the
        user's circumstance and the answer flipped to "yes, we are adopting it")."""
        probes = [p for p in (probes or []) if p]
        base = self.recall(query, embedder, k=k, **kw)
        if not probes:
            return base
        picked, seen = [], set()
        for p in probes[:max(1, k // 2)]:
            hit = next((h for h in self.recall(p, embedder, k=3, **kw)
                        if h["id"] not in seen and _is_personal(h)), None)
            if hit is not None:
                picked.append(dict(hit, probe=p))
                seen.add(hit["id"])
        for hit in base:
            if hit["id"] not in seen:
                picked.append(hit)
                seen.add(hit["id"])
        return picked[:k]

    def timeline(self, query: str, embedder, groups: int = 3, now: Optional[int] = None) -> list:
        """B7: the full history of the (subject, predicate) pairs most relevant to ``query`` —
        every record (current, past, planned, retracted) ordered by valid time, with when it
        was recorded and by which session. ``[{"subject", "predicate", "cos", "records"}]``."""
        if embedder is None:
            raise ValueError("timeline needs an embedder.")
        recs = self._load_assertions(with_emb=True)
        if not recs:
            return []
        now = int(now if now is not None else time.time())
        self._view(recs, now)
        q = np.asarray(embedder.embed_query(query), dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        by_group: dict = {}
        for r in recs:
            key = _key_of(r)                               # B9: paraphrases share one timeline
            cos = float(q @ np.asarray(r["emb"], dtype=np.float32))
            g = by_group.setdefault(key, {"subject": r["subject"], "predicate": r["predicate"],
                                          "cos": cos, "records": []})
            g["cos"] = max(g["cos"], cos)
            g["records"].append({c: r[c] for c in (
                "id", "subject", "predicate", "object", "session_id", "valid_from", "valid_to",
                "created_at", "expired_at", "cardinality", "status")})
        top = sorted(by_group.values(), key=lambda g: -g["cos"])[:max(1, int(groups))]
        for g in top:
            g["cos"] = round(g["cos"], 3)
            g["records"].sort(key=lambda x: (x["valid_from"] or 0, x["created_at"]))
        return top

    def memory_overview(self) -> dict:
        """Counts for the Memory tab header: sessions, **current** facts, superseded (past or
        retracted — B7 splits out the retracted corrections), planned (valid from a future
        date), and consolidated themes. Read-only + defensive (0 on a graph without the tables)."""
        def count(query: str) -> int:
            try:
                return int(self._rows(query)[0][0])
            except Exception:
                return 0
        now = int(time.time())
        states = [temporal_status(r, now) for r in self._load_assertions()]
        return {
            "sessions": count("MATCH (s:Session) RETURN count(s);"),
            "assertions": states.count("current"),
            "superseded": states.count("past") + states.count("retracted"),
            "retracted": states.count("retracted"),
            "planned": states.count("future"),
            "forgotten": states.count("forgotten"),
            "themes": count("MATCH (c:MemoryConcept) RETURN count(c);"),
        }

    def list_assertions(self, limit: int = 200, include_superseded: bool = True) -> list:
        """All remembered facts with metadata (subject/predicate/object + session, confidence,
        timestamps, B7 validity + status, superseded flag), newest first (by ``last_seen`` else
        ``created_at``) — the Memory tab's browsable table. Read-only + defensive (older graphs
        lack confidence / validity columns → defaulted / derived)."""
        recs = self._load_assertions()
        self._view(recs, int(time.time()))
        out = []
        for r in recs:
            if r["superseded"] and not include_superseded:
                continue
            out.append({"id": r["id"], "subject": r["subject"], "predicate": r["predicate"],
                        "object": r["object"], "session_id": r["session_id"],
                        "created_at": r["created_at"], "confidence": round(r["confidence"], 2),
                        "last_seen": r["last_seen"], "superseded": r["superseded"],
                        "status": r["status"], "valid_from": r["valid_from"],
                        "valid_to": r["valid_to"], "expired_at": r["expired_at"],
                        "cardinality": r["cardinality"], "source": r.get("source"),
                        "forgotten": r.get("forgotten")})
        out.sort(key=lambda a: -(a["last_seen"] or a["created_at"]))
        return out[:limit]

    # -- sleep: forgetting (archive what the memory policy says not to keep) -----

    def forget_candidates(self) -> list:
        """What the sleep pass would forget: every held record (not retracted, not yet forgotten)
        that the memory policy says not to keep — ``reason`` ``"unsafe"`` (the P0 security-sensitive
        policy, re-applied to facts captured before it existed) or ``"ephemeral"`` (a one-off session
        event: "was pushed / tagged", a commit hash, "server is serving v0.78.0"). Pure policy, no LLM
        (an LLM review was measured unstable and harmful). Read-only."""
        from .memory import MemoryFact, is_ephemeral, is_unsafe_instruction
        out = []
        for r in self._load_assertions():
            if r.get("forgotten_at") is not None or r.get("expired_at") is not None:
                continue
            fact = MemoryFact(r["subject"], r["predicate"], r["object"])
            why = ("unsafe" if is_unsafe_instruction(fact)
                   else "ephemeral" if is_ephemeral(fact) else None)
            if why:
                out.append({"id": r["id"], "subject": r["subject"], "predicate": r["predicate"],
                            "object": r["object"], "session_id": r["session_id"], "reason": why})
        return out

    def forget(self, ids, reason: str, now: Optional[int] = None) -> int:
        """Archive assertions (sleep): stamp ``forgotten_at`` + ``forgotten`` (the reason). Nothing is
        deleted — a forgotten fact leaves every view (recall, context, consolidation, "current"), stays
        visible to ``known_at`` views of earlier times, and a later session that says it again adds it
        afresh. Writable; returns how many were newly forgotten."""
        if not self.writable:
            raise RuntimeError("GraphStore is read-only; open it writable to forget.")
        now = int(now if now is not None else time.time())
        n = 0
        with self._lock:
            for ddl in ("ALTER TABLE Assertion ADD forgotten_at INT64;",
                        "ALTER TABLE Assertion ADD forgotten STRING;"):
                try:
                    self._exec(ddl)
                except Exception:      # already present
                    pass
            for aid in ids:
                rows = self._rows("MATCH (a:Assertion {id:$id}) WHERE a.forgotten_at IS NULL "
                                  "SET a.forgotten_at=$t, a.forgotten=$why RETURN a.id;",
                                  {"id": aid, "t": now, "why": reason})
                n += len(rows)
        return n

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
        """Current assertions (valid now + still believed, B7) with their stored embeddings:
        ``[(id, subject, predicate, object, emb), ...]`` — what the sleep pass consolidates."""
        now = int(time.time())
        return [(r["id"], r["subject"], r["predicate"], r["object"], r["emb"])
                for r in self._load_assertions(with_emb=True)
                if temporal_status(r, now) == "current"]

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
                    k: int = 8, max_themes: int = 4, max_chars=None,
                    as_of: Optional[int] = None, probes=None) -> str:
        """B6: assemble a session's context for ``query`` from the three memory tiers —
        identity + decay-weighted ``recall`` (activation) + the relevant consolidated themes
        (attractors), optionally fit within a ``max_chars`` budget. Facts carry their validity
        (B7), and ``as_of`` assembles the memory as it was true at that date. ``probes`` (P1
        cue-trigger, ``memory.constraint_probes``) reserve slots for the user's implicit
        constraints, shown first under "Keep in mind". Read-only + **fail-soft** (missing
        embedder / empty memory → identity only, or ``""``)."""
        from .memory import assemble_context
        facts = []
        if embedder is not None:
            try:
                facts = (self.recall_probed(query, embedder, probes, k=k, as_of=as_of) if probes
                         else self.recall(query, embedder, k=k, as_of=as_of))
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
