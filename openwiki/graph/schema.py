"""The graph's tables in one registry (Path C, C2.1 — ``docs/path-c-world-model.md`` §2.3, §5.2).

Every node and rel table is declared once, with its DDL and its standing. *Authoritative* tables hold what no rebuild
can recompute — the remembered facts, the usage overlay, the world model's variables, laws and events — so B0's
snapshot keeps them across a doc rebuild and ``memory export --full`` carries them; the others are derived from the
documents (pages, chunks, entities) or recomputed by a pass (communities, themes). The builder creates the schema from
this list and ``GraphStore`` creates the world tables lazily on older graphs, so a new table is declared here once and
every path follows. The memory tables keep their own reader (``custom``), which reads every schema generation since
v0.48. Pure — no Kuzu import: the snapshot / restore helpers take the query functions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

DOCUMENT, MEMORY, WORLD = "document", "memory", "world"


@dataclass(frozen=True)
class Table:
    name: str
    ddl: str                       # "{dim}" stands for the embedding dimension
    group: str
    authoritative: bool = False
    columns: tuple = ()            # node tables: the columns, in DDL order
    key: str = ""                  # node tables: the primary key
    ends: tuple = ()               # rel tables: (from table, to table)
    props: tuple = ()              # rel tables: the edge's properties
    custom: bool = False           # kept by B0's memory reader, not by the generic snapshot
    coltypes: tuple = ()           # node tables: each column's type, for ``ALTER TABLE … ADD`` on older graphs

    @property
    def rel(self) -> bool:
        return bool(self.ends)

    def create(self, dim: int, if_not_exists: bool = False) -> str:
        ddl = self.ddl.replace("{dim}", str(int(dim)))
        return ddl.replace(" TABLE ", " TABLE IF NOT EXISTS ", 1) if if_not_exists else ddl


def _node(name, columns, group, key="id", authoritative=False, custom=False, types=None) -> Table:
    types = types or {}
    cols = ", ".join(f"{c} {types.get(c, 'STRING')}" for c in columns)
    return Table(name, f"CREATE NODE TABLE {name}({cols}, PRIMARY KEY({key}));", group, authoritative,
                 tuple(columns), key, custom=custom, coltypes=tuple(types.get(c, "STRING") for c in columns))


def _rel(name, src, dst, group, props=(), authoritative=False, custom=False, types=None) -> Table:
    types = types or {}
    extra = "".join(f", {p} {types.get(p, 'STRING')}" for p in props)
    return Table(name, f"CREATE REL TABLE {name}(FROM {src} TO {dst}{extra});", group, authoritative,
                 ends=(src, dst), props=tuple(props), custom=custom)


_I, _D = "INT64", "DOUBLE"

TABLES: tuple = (
    # -- the documents (derived: rebuilt from the sources) ----------------------------------------------------
    _node("Page", ("slug", "title", "level", "pdf_start", "pdf_end", "seq"), DOCUMENT, key="slug",
          types={"level": _I, "pdf_start": _I, "pdf_end": _I, "seq": _I}),
    _node("Chunk", ("id", "page_slug", "text", "emb"), DOCUMENT, types={"emb": "FLOAT[{dim}]"}),
    _rel("CHILD_OF", "Page", "Page", DOCUMENT),
    _rel("NEXT", "Page", "Page", DOCUMENT),
    _rel("PART_OF", "Chunk", "Page", DOCUMENT),
    _rel("SIMILAR_TO", "Page", "Page", DOCUMENT, ("score",), types={"score": _D}),
    # labels = the citation phrases ("Abschnitt 1.6", JSON list) the web UI links inline (ADR-28)
    _rel("REFERENCES", "Page", "Page", DOCUMENT, ("labels",)),
    _node("Entity", ("key", "name", "type", "description", "aliases"), DOCUMENT, key="key"),
    _rel("MENTIONS", "Page", "Entity", DOCUMENT),
    # typed Entity→Entity relations (Direction B) — empty without --relations
    _rel("RELATED_TO", "Entity", "Entity", DOCUMENT, ("predicate", "weight", "pages"), types={"weight": _I}),
    # the consolidation layer (`openwiki communities`) — recomputed, never kept
    _node("Community", ("id", "label", "summary", "size"), DOCUMENT, types={"id": _I, "size": _I}),
    _rel("IN_COMMUNITY", "Page", "Community", DOCUMENT),
    # v0.112: git co-changes between the pages of a code corpus (empty for other corpora)
    _rel("CO_CHANGED", "Page", "Page", DOCUMENT, ("count", "weight", "last"),
         types={"count": _I, "weight": _D, "last": _I}),
    # -- the remembered tier (Path B): kept across rebuilds by B0's own reader --------------------------------
    # the usage overlay: reinforced page↔page edges that decay (openwiki/graph/decay.py)
    _rel("REINFORCES", "Page", "Page", MEMORY, ("weight", "last_seen"), authoritative=True, custom=True,
         types={"weight": _D, "last_seen": _I}),
    # B7 bi-temporal: valid time (valid_from/valid_to) + transaction time (created_at/expired_at); cardinality
    # "many" = values coexist; forgotten_at/forgotten = the sleep pass archived it — kept, out of every view
    _node("Session", ("id", "created_at", "session_date"), MEMORY, authoritative=True, custom=True,
          types={"created_at": _I, "session_date": _I}),
    _node("Assertion", ("id", "subject", "predicate", "object", "session_id", "created_at", "confidence",
                        "last_seen", "valid_from", "valid_to", "expired_at", "cardinality", "attr", "source",
                        "forgotten_at", "forgotten", "emb"), MEMORY, authoritative=True, custom=True,
          types={"created_at": _I, "confidence": _D, "last_seen": _I, "valid_from": _I, "valid_to": _I,
                 "expired_at": _I, "forgotten_at": _I, "emb": "FLOAT[{dim}]"}),
    _rel("ASSERTS", "Session", "Assertion", MEMORY, authoritative=True, custom=True),
    # B4/B7 provenance: (n)-[:SUPERSEDES]->(o) = n closed or retracted o's validity; nothing is deleted
    _rel("SUPERSEDES", "Assertion", "Assertion", MEMORY, authoritative=True, custom=True),
    # B5 themes — like Community, a derived view recomputed by the sleep pass
    _node("MemoryConcept", ("id", "label", "summary", "size", "created_at"), MEMORY,
          types={"id": _I, "size": _I, "created_at": _I}),
    _rel("CONSOLIDATES", "MemoryConcept", "Assertion", MEMORY),
    # -- the world model (Path C, C2): a person's and the sensors' work — kept, carried by --full ---------------
    # one Variable per binding (C2.2); id = the attribute key its observations carry (sensor:<binding>)
    _node("Variable", ("id", "label", "subject", "predicate", "unit", "sensor", "kind", "environment",
                       "created_at", "updated_at"), WORLD, authoritative=True,
          types={"created_at": _I, "updated_at": _I}),
    _rel("DESCRIBED_IN", "Variable", "Page", WORLD, authoritative=True),
    # a rule with its check (C2.3); status = proposed / accepted / rejected — the review is a person's work
    # spec (v0.127, JSON): what a law held in the graph needs to be checked again — its fixtures, a co-change rule
    _node("Law", ("id", "text", "kind", "check", "origin", "status", "health", "last_result", "last_checked",
                  "detail", "created_at", "updated_at", "spec"), WORLD, authoritative=True,
          types={"last_checked": _I, "created_at": _I, "updated_at": _I}),
    _rel("CONSTRAINS", "Law", "Variable", WORLD, authoritative=True),
    _rel("FROM_PAGE", "Law", "Page", WORLD, authoritative=True),
    _rel("FROM_FACT", "Law", "Assertion", WORLD, authoritative=True),
    # discrepancy, violation, drift, exogenous, contested (C2.6); resolvable by a person; key (v0.127) = what it is
    # about — one open event per key
    _node("Event", ("id", "kind", "at", "detail", "resolved_at", "resolution", "key"), WORLD, authoritative=True,
          types={"at": _I, "resolved_at": _I}),
    _rel("CONCERNS", "Event", "Variable", WORLD, authoritative=True),
    _rel("FROM_LAW", "Event", "Law", WORLD, authoritative=True),
    _rel("INVOLVES", "Event", "Assertion", WORLD, authoritative=True),
)

BY_NAME = {t.name: t for t in TABLES}


def added_columns(table: Table, present: set) -> list:
    """``ALTER TABLE … ADD`` statements for the columns ``table`` declares that an older graph's table lacks."""
    return [f"ALTER TABLE {table.name} ADD {c} {t.replace('{dim}', '0')};"
            for c, t in zip(table.columns, table.coltypes) if c not in present and "{dim}" not in t]


def node_keys() -> dict:
    """``{node table: its primary key}``."""
    return {t.name: t.key for t in TABLES if not t.rel}


def tables(group: Optional[str] = None, authoritative: Optional[bool] = None, custom: Optional[bool] = None) -> list:
    return [t for t in TABLES if (group is None or t.group == group)
            and (authoritative is None or t.authoritative == authoritative)
            and (custom is None or t.custom == custom)]


def generic() -> list:
    """The authoritative tables the generic snapshot keeps (all but the memory tables' own reader), nodes first."""
    out = tables(authoritative=True, custom=False)
    return [t for t in out if not t.rel] + [t for t in out if t.rel]


# -- the generic snapshot / restore (B0 and the portable export) -------------------------------------------------

def snapshot(rows: Callable[[str], list], present: Callable[[str], Optional[set]]) -> dict:
    """``{table: [row]}`` for every generic authoritative table — a node as ``{column: value}``, an edge as
    ``{"from": key, "to": key, **props}``. A table an older graph lacks gives nothing; a column it lacks, nothing
    either (the fresh schema's default). ``rows(query)`` runs a read; ``present(table)`` = its columns, or ``None``."""
    keys, out = node_keys(), {}
    for t in generic():
        have = present(t.name)
        if have is None:
            continue
        if t.rel:
            src, dst = t.ends
            props = [p for p in t.props if p in have]
            query = (f"MATCH (a:{src})-[r:{t.name}]->(b:{dst}) RETURN a.{keys[src]}, b.{keys[dst]}"
                     + "".join(f", r.{p}" for p in props) + ";")
            got = [dict({"from": r[0], "to": r[1]}, **dict(zip(props, r[2:]))) for r in rows(query)]
        else:
            cols = [c for c in t.columns if c in have]
            got = [dict(zip(cols, r)) for r in rows(f"MATCH (n:{t.name}) RETURN "
                                                    + ", ".join(f"n.{c}" for c in cols) + ";")]
        if got:
            out[t.name] = got
    return out


def restore(execute: Callable[[str, dict], None], snap: Optional[dict], known: Optional[dict] = None) -> dict:
    """Re-create ``snapshot``'s rows — nodes first (``MERGE`` on the key, so a restore into a graph that already
    holds a row updates it), then each edge whose two ends exist. ``known`` = ``{node table: keys present}`` for the
    tables this function doesn't restore (pages of the rebuilt wiki, the restored assertions); an edge to a page that
    is gone, or to an assertion dropped for its embedding, goes with it. Returns ``{table: rows written}``."""
    if not snap:
        return {}
    keys, counts = node_keys(), {}
    have = {k: set(v) for k, v in (known or {}).items()}
    for t in generic():
        rows = snap.get(t.name) or []
        if t.rel or not rows:
            continue
        cols = [c for c in t.columns if c != t.key]
        for row in rows:
            if row.get(t.key) is None:
                continue
            sets = [c for c in cols if c in row]
            execute(f"MERGE (n:{t.name} {{{t.key}: $k}})"
                    + (" SET " + ", ".join(f"n.{c} = ${c}" for c in sets) if sets else "") + ";",
                    dict({"k": row[t.key]}, **{c: row[c] for c in sets}))
            have.setdefault(t.name, set()).add(row[t.key])
            counts[t.name] = counts.get(t.name, 0) + 1
    for t in generic():
        rows = snap.get(t.name) or []
        if not t.rel or not rows:
            continue
        src, dst = t.ends
        for row in rows:
            if row.get("from") not in have.get(src, ()) or row.get("to") not in have.get(dst, ()):
                continue
            props = [p for p in t.props if p in row]
            execute(f"MATCH (a:{src} {{{keys[src]}: $a}}), (b:{dst} {{{keys[dst]}: $b}}) "
                    f"MERGE (a)-[r:{t.name}]->(b)" + (" SET " + ", ".join(f"r.{p} = ${p}" for p in props)
                                                      if props else "") + ";",
                    dict({"a": row["from"], "b": row["to"]}, **{p: row[p] for p in props}))
            counts[t.name] = counts.get(t.name, 0) + 1
    return counts


def present_columns(rows: Callable[[str], list]) -> Callable[[str], Optional[set]]:
    """``present`` for ``snapshot``: a table's column names through Kuzu's ``table_info``, ``None`` if absent."""
    def present(name: str) -> Optional[set]:
        try:
            return {str(r[1]) for r in rows(f"CALL table_info('{name}') RETURN *;")}
        except Exception:
            return None
    return present
