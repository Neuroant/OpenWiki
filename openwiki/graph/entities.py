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


def _normalize(name: str) -> str:
    words = re.sub(r"[^\w\s-]", " ", name.lower()).split()
    words = [w for w in words if w not in _ARTICLES]
    return " ".join(words).strip()


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


def extract_entities(wiki: Wiki, chat: ChatModel, types: Ontology = None,
                     max_chars: int = 8000, on_progress=None) -> list[Entity]:
    """Extract and resolve entities across all wiki pages (one call per page).

    ``types`` selects the ontology (see :func:`coerce_types`); ``max_chars`` caps
    how much of each page's text is sent to the model (raise it for coarse,
    document-sized pages, at the cost of a bigger prompt).
    """
    type_map = coerce_types(types)
    system_prompt = _system_prompt(type_map)
    allowed = set(type_map)
    by_key: dict[str, Entity] = {}
    for i, page in enumerate(wiki.pages):
        text = page.text.strip()[:max_chars]
        if text:
            for name, etype in _extract_page(chat, page.title, text, system_prompt, allowed):
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
    logger.info("Entities: %d unique across %d pages (%d types)",
                len(by_key), len(wiki.pages), len(type_map))
    return list(by_key.values())
