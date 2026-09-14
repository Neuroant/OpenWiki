"""LLM-based entity extraction — the semantic layer over the wiki.

One local chat-model call per wiki page pulls out typed named entities. The
**ontology** (the set of entity types) is *configurable per project*: the default
below is tuned to the sample synthesizer manual, but any domain can supply its own
via ``entity_types`` (a list of names, ``"Name: description"`` strings, or a dict).
Entities are resolved by normalized name-within-type (lowercase, strip German
articles) so surface variants merge, and feed ``Entity`` nodes +
``Page-[:MENTIONS]->Entity`` edges into the graph — connecting pages that discuss
the same concept even when they neither cross-reference nor are cosine-similar.

Imports a ``ChatModel`` (see :mod:`openwiki.llm`) — no Kuzu here.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Union

import numpy as np

from ..llm import ChatModel
from ..wiki import Wiki

logger = logging.getLogger(__name__)

# Default ontology, tuned to the sample synthesizer manual. Override per project
# with ``entity_types`` — e.g. for computer science:
#   ["Concept", "Definition", "Satz", "Algorithmus", "Datenstruktur", "Paradigma", "Notation"]
DEFAULT_ENTITY_TYPES: dict[str, str] = {
    "Mode": "an operating mode (PROGRAM, COMBINATION, SEQUENCER, SAMPLING, GLOBAL, SET LIST)",
    "SoundObject": "a sound/data object (Program, Combination, Multisample, Drumkit, Wave Sequence, Sample)",
    "Effect": "an audio effect or effect slot (Reverb, Delay, IFX, MFX, …)",
    "Feature": "a named function/feature (Arpeggiator, Drum Track, Vector Synthesis, Quick Layer, Smooth Sound Transitions)",
    "Parameter": "a named parameter/setting (Amp Level, Hold Time, Cutoff, Resonance, …)",
    "Hardware": "a physical control/connector/component (MASTER VOLUME slider, joystick, damper pedal, USB port)",
}
# Back-compat alias (older code/tests referenced ENTITY_TYPES).
ENTITY_TYPES = DEFAULT_ENTITY_TYPES

Ontology = Union[dict, list, tuple, None]

_ARTICLES = {"der", "die", "das", "dem", "den", "des", "ein", "eine", "einen",
             "einem", "einer", "the", "a", "an"}
_UMLAUTS = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss"})
# Conservative German plural / weak-inflection endings, longest first. Tuned to
# merge singular/plural (Signal/Signale, Datenstruktur/Datenstrukturen) without
# over-merging distinct compounds — verified to produce zero false merges on the
# informatik corpus. `-er`/`-s`/`-us` are deliberately excluded (too destructive:
# Rechner, Prozess).
_PLURAL_SUFFIXES = ("en", "n", "e")
_JSON_ARRAY = re.compile(r"\[.*\]", re.DOTALL)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def coerce_types(types: Ontology) -> dict[str, str]:
    """Normalize an ontology spec into a ``{type: description}`` dict.

    Accepts ``None`` (→ :data:`DEFAULT_ENTITY_TYPES`), a ``{name: description}``
    dict, or a sequence of names — each optionally ``"Name: description"``.
    """
    if types is None:
        return dict(DEFAULT_ENTITY_TYPES)
    if isinstance(types, dict):
        out = {str(k).strip(): str(v).strip() for k, v in types.items() if str(k).strip()}
        return out or dict(DEFAULT_ENTITY_TYPES)
    out: dict[str, str] = {}
    for item in types:
        text = str(item).strip()
        if not text:
            continue
        name, sep, desc = text.partition(":")
        out[name.strip()] = desc.strip() if sep else ""
    return out or dict(DEFAULT_ENTITY_TYPES)


def _system_prompt(types: dict[str, str]) -> str:
    lines = "\n".join(f"- {t}: {d}" if d else f"- {t}" for t, d in types.items())
    return (
        "You extract named domain entities from the given text (it may be German). "
        "Be exhaustive and consistent: extract EVERY entity of the allowed types "
        "that the text names — do not sample or summarize. "
        "Return ONLY a JSON array of objects {\"name\": ..., \"type\": ...}. "
        "Allowed types and what they mean:\n" + lines +
        "\nExtract only meaningful, named domain concepts. Do NOT extract:\n"
        "- variable / parameter / identifier names or code tokens (e.g. i, res, tmp, self, this, asize, size);\n"
        "- programming keywords or primitive types (int, bool, void, char, null, true, false);\n"
        "- bare operators or generic verbs (add, sub, mul, max, min, copy);\n"
        "- person or author names and bibliographic citations (e.g. 'Aho', 'Dahl', 'Goos');\n"
        "- generic filler ('thing' / 'value' / 'Sache' / 'Wert').\n"
        "Use the canonical name as written; 3–40 characters; no duplicates. If none, "
        "return []. Output the JSON array and nothing else."
    )


@dataclass
class Entity:
    key: str          # "<type>::<normalized name>" — unique
    name: str         # display (surface) form (canonical, after resolution)
    type: str
    pages: list[str] = field(default_factory=list)  # slugs mentioning it
    aliases: list[str] = field(default_factory=list)  # surface variants merged in (resolution)
    description: str = ""                              # one-line gloss (resolution, canonical only)


@dataclass
class Relation:
    """A typed relationship between two entities — the edge that turns co-mention into a
    real knowledge graph. ``subject``/``object`` are entity *keys*; ``predicate`` is a short
    verb phrase; ``pages`` are the slugs whose text stated it (its support)."""
    subject: str      # entity key
    predicate: str
    object: str       # entity key
    pages: list[str] = field(default_factory=list)

    @property
    def weight(self) -> int:
        return len(self.pages)


def _normalize(name: str) -> str:
    """Merge-key for an entity name, so surface variants resolve to one entity:
    lowercase, drop punctuation, split hyphens, strip German articles, fold
    umlauts/ß, then remove one conservative plural/inflection suffix. Deterministic
    and dependency-free. The display name keeps its original surface form; only this
    key is normalized. Merges e.g. Signal/Signale, Datenstruktur/Datenstrukturen,
    Flußdiagramm/Flussdiagramm, Teile-und-Herrsche/Teile und Herrsche — without
    merging distinct compounds (Systemgrenze ≠ Systemzustand)."""
    text = re.sub(r"[^\w\s-]", " ", name.lower()).replace("-", " ")
    words = [w.translate(_UMLAUTS) for w in text.split() if w not in _ARTICLES]
    base = " ".join(words).strip()
    for suffix in _PLURAL_SUFFIXES:
        if base.endswith(suffix) and len(base) - len(suffix) >= 4:
            return base[:-len(suffix)]
    return base


def _valid(norm: str) -> bool:
    return 2 <= len(norm) <= 40 and not norm.isdigit()


def _parse(reply: str, allowed: set) -> list[tuple[str, str]]:
    reply = _THINK.sub("", reply)
    match = _JSON_ARRAY.search(reply)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict):
            name, etype = item.get("name"), item.get("type")
            if isinstance(name, str) and etype in allowed:
                out.append((name.strip(), etype))
    return out


def _extract_page(chat: ChatModel, title: str, text: str, system_prompt: str,
                  allowed: set) -> list[tuple[str, str]]:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Page title: {title}\n\nText:\n{text}"},
    ]
    try:
        return _parse(chat.chat(messages), allowed)
    except Exception as exc:  # a bad page shouldn't abort the whole run
        logger.warning("entity extraction failed on '%s': %s", title, exc)
        return []


_RELATION_SYSTEM = (
    "You extract factual relationships BETWEEN the given entities from the text (it may be "
    "German). You are given the list of entities that appear in the text. Return ONLY "
    "relationships the text actually states between two of those entities, as a JSON array of "
    'objects {"subject": ..., "predicate": ..., "object": ...} where:\n'
    "- subject and object are EXACTLY names copied from the entity list (verbatim);\n"
    "- predicate is a short verb phrase for the relationship (1-4 words, e.g. 'is part of', "
    "'controls', 'generates', 'requires', 'is a kind of', 'is set in');\n"
    "- include only relationships explicitly supported by the text — do NOT invent, and do "
    "not relate an entity to itself.\n"
    "No duplicates. If none, return []. Output the JSON array and nothing else."
)


def _parse_relations(reply: str) -> list[tuple[str, str, str]]:
    """Parse a JSON array of ``{subject, predicate, object}`` into raw (surface) triples."""
    reply = _THINK.sub("", reply)
    match = _JSON_ARRAY.search(reply)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict):
            s, p, o = item.get("subject"), item.get("predicate"), item.get("object")
            if isinstance(s, str) and isinstance(p, str) and isinstance(o, str):
                out.append((s.strip(), p.strip(), o.strip()))
    return out


def _clean_predicate(pred: str) -> str:
    return " ".join(pred.split())[:40].strip()


def _extract_page_relations(chat: ChatModel, title: str, names: list, text: str) -> list:
    listing = "\n".join(f"- {n}" for n in names)
    messages = [
        {"role": "system", "content": _RELATION_SYSTEM},
        {"role": "user", "content": f"Page title: {title}\n\nEntities:\n{listing}\n\nText:\n{text}"},
    ]
    try:
        return _parse_relations(chat.chat(messages))
    except Exception as exc:  # a bad page shouldn't abort the whole run
        logger.warning("relation extraction failed on '%s': %s", title, exc)
        return []


def extract_relations(wiki: Wiki, entities: list, chat: ChatModel,
                      max_chars: int = 8000, on_progress=None) -> list:
    """Extract typed relationships **among the entities on each page** (one call per page that
    has ≥2 entities). Each returned subject/object is grounded to an extracted entity by
    normalized name (unresolved, self, or empty-predicate triples are dropped); identical
    ``(subject, predicate, object)`` across pages merge, accumulating provenance (``pages``).

    Needs the already-extracted ``entities`` (from :func:`extract_entities`) so relations point
    at real ``Entity`` nodes — turning co-mention into a real, traversable knowledge graph.
    """
    pages_entities: dict[str, list[tuple]] = {}
    for entity in entities:
        for slug in entity.pages:
            pages_entities.setdefault(slug, []).append((entity.name, entity.key, entity.aliases))
    page_by_slug = {p.slug: p for p in wiki.pages}
    slugs = [s for s in pages_entities if len(pages_entities[s]) >= 2 and s in page_by_slug]
    agg: dict[tuple, Relation] = {}
    for i, slug in enumerate(slugs):
        ents = pages_entities[slug]
        norm_to_key = {}                                  # canonical names + aliases → key
        for name, key, aliases in ents:
            norm_to_key[_normalize(name)] = key
            for alias in aliases:
                norm_to_key.setdefault(_normalize(alias), key)
        text = page_by_slug[slug].text.strip()[:max_chars]
        if text:
            for subj, pred, obj in _extract_page_relations(
                    chat, page_by_slug[slug].title, [n for n, _, _ in ents], text):
                sk = norm_to_key.get(_normalize(subj))
                ok = norm_to_key.get(_normalize(obj))
                pred = _clean_predicate(pred)
                if not sk or not ok or sk == ok or not (2 <= len(pred) <= 40):
                    continue
                mkey = (sk, pred.lower(), ok)
                rel = agg.get(mkey)
                if rel is None:
                    rel = Relation(subject=sk, predicate=pred, object=ok)
                    agg[mkey] = rel
                if slug not in rel.pages:
                    rel.pages.append(slug)
        if on_progress:
            on_progress(i + 1, len(slugs), len(agg))
    logger.info("Relations: %d unique across %d entity-rich page(s)", len(agg), len(slugs))
    return list(agg.values())


def extract_entities(wiki: Wiki, chat: ChatModel, types: Ontology = None,
                     max_chars: int = 8000, on_progress=None,
                     retry_chat: ChatModel = None, retry_min_chars: int = 400) -> list[Entity]:
    """Extract and resolve entities across all wiki pages (one call per page).

    ``types`` selects the ontology (see :func:`coerce_types`); ``max_chars`` caps
    how much of each page's text is sent to the model (raise it for coarse,
    document-sized pages, at the cost of a bigger prompt).

    ``retry_chat`` (optional): a fallback model used to re-extract a *substantial*
    page (text length ≥ ``retry_min_chars``) that the primary returned nothing for.
    Greedy decoding can fall into a repetition loop and yield ``[]`` on some pages;
    a sampled retry usually escapes it, so content pages aren't silently dropped.
    """
    type_map = coerce_types(types)
    system_prompt = _system_prompt(type_map)
    allowed = set(type_map)
    by_key: dict[str, Entity] = {}
    retried = 0
    for i, page in enumerate(wiki.pages):
        text = page.text.strip()[:max_chars]
        if text:
            found = _extract_page(chat, page.title, text, system_prompt, allowed)
            if not found and retry_chat is not None and len(text) >= retry_min_chars:
                retried += 1
                found = _extract_page(retry_chat, page.title, text, system_prompt, allowed)
                if found:
                    logger.info("entity retry recovered %d entities on '%s'", len(found), page.title)
            for name, etype in found:
                norm = _normalize(name)
                if not _valid(norm):
                    continue
                key = f"{etype}::{norm}"
                entity = by_key.get(key)
                if entity is None:
                    entity = Entity(key=key, name=name, type=etype)
                    by_key[key] = entity
                if page.slug not in entity.pages:
                    entity.pages.append(page.slug)
        if on_progress:
            on_progress(i + 1, len(wiki.pages), len(by_key))
    logger.info("Entities: %d unique across %d pages (%d types%s)",
                len(by_key), len(wiki.pages), len(type_map),
                f"; {retried} empty page(s) retried" if retried else "")
    return list(by_key.values())


# -- corpus-wide entity resolution (canonical entities + aliases + descriptions) ----------
#
# The per-page extraction resolves entities by a *deterministic* normalized key (spelling /
# plural / word-order variants merge). Resolution is the corpus-wide second pass that also
# merges same-concept surface variants the normalizer can't see — synonyms, acronym↔full
# form, near-duplicates — into **canonical** entities carrying the merged surface forms as
# ``aliases`` and an LLM one-line ``description``. It **blocks by type** (aliases share a
# type), generates candidates by **embedding similarity** (so only plausible near-duplicates
# are considered), and confirms each multi-member cluster with **one small LLM call** — so
# the cost is bounded (nothing for the many singletons) and the prompts stay short + reliable.
# It never drops an entity: anything the model doesn't group survives unchanged.

_RESOLVE_SYSTEM = (
    "You are given several candidate names of the same TYPE that may or may not name the same "
    "real-world concept. Group ONLY the names that are aliases of the *same* concept (synonyms, "
    "an acronym vs. its full form, spelling / word-order variants). Do NOT group two DISTINCT "
    "concepts just because they are related or the same kind of thing.\n"
    'Return ONLY a JSON array of objects {"canonical": <clearest full name from the group>, '
    '"aliases": [<every name in the group, including the canonical>], "description": <one short '
    "factual sentence, or \"\">}. Every input name must appear in exactly one group; a name that "
    "stands alone is its own group of one. Output the JSON array and nothing else."
)


def _normalize_rows(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


def _cluster_indices(idxs: list, embs: np.ndarray, similarity: float) -> list:
    """Union-find over the given row indices: merge any pair with cosine ≥ ``similarity``
    (vectors are pre-normalized, so the dot product is the cosine). Returns index groups."""
    parent = {i: i for i in idxs}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in range(len(idxs)):
        for b in range(a + 1, len(idxs)):
            i, j = idxs[a], idxs[b]
            if float(embs[i] @ embs[j]) >= similarity:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    groups: dict = {}
    for i in idxs:
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _parse_resolution(reply: str, allowed_names: list) -> list:
    """Parse the LLM's grouping into ``[{canonical, members, description}]``, restricted to the
    cluster's actual names (case-insensitive). Robust — ``[]`` on any parse failure (→ no merge)."""
    reply = _THINK.sub("", reply)
    match = _JSON_ARRAY.search(reply)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    allowed = {n.lower(): n for n in allowed_names}
    groups = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        members = [allowed[a.lower()] for a in (item.get("aliases") or [])
                   if isinstance(a, str) and a.lower() in allowed]
        if not members:
            continue
        canonical = item.get("canonical")
        canonical = allowed[canonical.lower()] if (isinstance(canonical, str)
                                                   and canonical.lower() in allowed) else members[0]
        desc = item.get("description")
        groups.append({"canonical": canonical, "members": members,
                       "description": desc.strip() if isinstance(desc, str) else ""})
    return groups


def _merge_entities(members: list, canonical_name: str, description: str) -> Entity:
    """Fold several same-type entities into one canonical Entity (union pages, collect the
    other surface forms as aliases)."""
    pages: list = []
    aliases: set = set()
    for e in members:
        for slug in e.pages:
            if slug not in pages:
                pages.append(slug)
        aliases.add(e.name)
        aliases.update(e.aliases)
    aliases.discard(canonical_name)
    etype = members[0].type
    return Entity(key=f"{etype}::{_normalize(canonical_name)}", name=canonical_name, type=etype,
                  pages=pages, aliases=sorted(aliases), description=description)


def resolve_entities(entities: list, embedder, chat: ChatModel, similarity: float = 0.80) -> list:
    """Corpus-wide entity resolution → canonical entities with ``aliases`` + ``description``.
    Blocks by type, generates candidate clusters by embedding cosine (≥ ``similarity``), and
    confirms each multi-member cluster with one LLM call. Bounded (singletons cost nothing) and
    safe (an entity the model doesn't group survives unchanged). ``embedder``/``chat`` ``None``
    → returned unchanged. Returns ``(canonical_entities, n_clusters)``.

    The ``0.80`` default is calibrated for bge-m3 on short entity names: it clusters true
    surface variants (``Drumkit``/``Drum Kit`` ≈ 0.84, ``Drumkit``/``Drumkits`` ≈ 0.92) while
    excluding distinct same-type entities (``Reverb``/``Delay`` ≈ 0.52). Candidate generation
    favours *recall* — the LLM provides precision by splitting a mixed cluster. **Limitation:**
    acronym↔full-form (``IFX``/``Insert-Effekt`` ≈ 0.40) is *not* embedding-close, so it isn't
    a candidate; embedding-based resolution catches spelling/spacing/plural/word-order/near-
    synonym variants, not acronyms."""
    entities = list(entities)
    if not entities or embedder is None or chat is None:
        return entities, 0
    embs = _normalize_rows(embedder.embed_documents([e.name for e in entities]).astype(np.float32))
    by_type: dict = {}
    for i, e in enumerate(entities):
        by_type.setdefault(e.type, []).append(i)

    out: dict = {}   # canonical key → Entity (dedup by key)

    def add(ent: Entity) -> None:
        cur = out.get(ent.key)
        if cur is None:
            out[ent.key] = ent
            return
        for slug in ent.pages:                       # key collision → union
            if slug not in cur.pages:
                cur.pages.append(slug)
        merged = set(cur.aliases) | set(ent.aliases) | {ent.name}
        merged.discard(cur.name)
        cur.aliases = sorted(merged)
        if not cur.description and ent.description:
            cur.description = ent.description

    n_clusters = 0
    for etype, idxs in by_type.items():
        for cluster in _cluster_indices(idxs, embs, similarity):
            if len(cluster) == 1:
                add(entities[cluster[0]])
                continue
            n_clusters += 1
            group = [entities[i] for i in cluster]
            names_in = [e.name for e in group]
            try:
                reply = chat.chat([
                    {"role": "system", "content": _RESOLVE_SYSTEM},
                    {"role": "user", "content": f"Type: {etype}\nNames:\n"
                     + "\n".join(f"- {n}" for n in names_in)},
                ])
                subgroups = _parse_resolution(reply, names_in)
            except Exception as exc:                 # a bad cluster shouldn't abort the run
                logger.warning("entity resolution failed on a %s cluster: %s", etype, exc)
                subgroups = []
            placed: set = set()
            for sg in subgroups:
                members = [e for e in group if e.name in sg["members"]]
                if not members:
                    continue
                add(_merge_entities(members, sg["canonical"], sg["description"]))
                placed.update(e.name for e in members)
            for e in group:                          # safety: never drop an ungrouped entity
                if e.name not in placed:
                    add(e)

    logger.info("Entity resolution: %d canonical from %d raw (%d candidate cluster(s))",
                len(out), len(entities), n_clusters)
    return list(out.values()), n_clusters
