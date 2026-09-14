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
    name: str         # display (surface) form
    type: str
    pages: list[str] = field(default_factory=list)  # slugs mentioning it


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
    pages_entities: dict[str, list[tuple[str, str]]] = {}
    for entity in entities:
        for slug in entity.pages:
            pages_entities.setdefault(slug, []).append((entity.name, entity.key))
    page_by_slug = {p.slug: p for p in wiki.pages}
    slugs = [s for s in pages_entities if len(pages_entities[s]) >= 2 and s in page_by_slug]
    agg: dict[tuple, Relation] = {}
    for i, slug in enumerate(slugs):
        ents = pages_entities[slug]
        norm_to_key = {_normalize(name): key for name, key in ents}
        text = page_by_slug[slug].text.strip()[:max_chars]
        if text:
            for subj, pred, obj in _extract_page_relations(
                    chat, page_by_slug[slug].title, [n for n, _ in ents], text):
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
