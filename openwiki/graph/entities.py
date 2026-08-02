"""LLM-based entity extraction — the semantic layer over the wiki.

One local chat-model call per wiki page pulls out typed named entities (Modes,
Effects, Features, Parameters, …). Entities are resolved by normalized
name-within-type (lowercase, strip German articles) so surface variants merge.
The result feeds `Entity` nodes + `Page-[:MENTIONS]->Entity` edges into the
graph, which connect pages that discuss the same concept even when they neither
cross-reference nor are cosine-similar.

Imports a `ChatModel` (see :mod:`openwiki.llm`) — no Kuzu here.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from ..llm import ChatModel
from ..wiki import Wiki

logger = logging.getLogger(__name__)

# The typed ontology, tuned to a synthesizer manual. Kept small so the model
# stays consistent; descriptions go into the prompt.
ENTITY_TYPES: dict[str, str] = {
    "Mode": "an operating mode (PROGRAM, COMBINATION, SEQUENCER, SAMPLING, GLOBAL, SET LIST)",
    "SoundObject": "a sound/data object (Program, Combination, Multisample, Drumkit, Wave Sequence, Sample)",
    "Effect": "an audio effect or effect slot (Reverb, Delay, IFX, MFX, …)",
    "Feature": "a named function/feature (Arpeggiator, Drum Track, Vector Synthesis, Quick Layer, Smooth Sound Transitions)",
    "Parameter": "a named parameter/setting (Amp Level, Hold Time, Cutoff, Resonance, …)",
    "Hardware": "a physical control/connector/component (MASTER VOLUME slider, joystick, damper pedal, USB port)",
}

_SYSTEM_PROMPT = (
    "You extract named entities from German synthesizer-manual text. "
    "Return ONLY a JSON array of objects {\"name\": ..., \"type\": ...}. "
    "Allowed types and what they mean:\n"
    + "\n".join(f"- {t}: {d}" for t, d in ENTITY_TYPES.items())
    + "\nRules: only concrete, named things (skip generic words like 'Sound', 'Taste', "
    "'Wert'); use the canonical name as written; 3–40 characters; no duplicates. "
    "If none, return []. Output the JSON array and nothing else."
)

_ARTICLES = {"der", "die", "das", "dem", "den", "des", "ein", "eine", "einen",
             "einem", "einer", "the", "a", "an"}
_JSON_ARRAY = re.compile(r"\[.*\]", re.DOTALL)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


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


def _parse(reply: str) -> list[tuple[str, str]]:
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
            if isinstance(name, str) and etype in ENTITY_TYPES:
                out.append((name.strip(), etype))
    return out


def _extract_page(chat: ChatModel, title: str, text: str) -> list[tuple[str, str]]:
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"Page title: {title}\n\nText:\n{text}"},
    ]
    try:
        return _parse(chat.chat(messages))
    except Exception as exc:  # a bad page shouldn't abort the whole run
        logger.warning("entity extraction failed on '%s': %s", title, exc)
        return []


def extract_entities(wiki: Wiki, chat: ChatModel, max_chars: int = 8000,
                     on_progress=None) -> list[Entity]:
    """Extract and resolve entities across all wiki pages (one call per page)."""
    by_key: dict[str, Entity] = {}
    for i, page in enumerate(wiki.pages):
        text = page.text.strip()[:max_chars]
        if text:
            for name, etype in _extract_page(chat, page.title, text):
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
    logger.info("Entities: %d unique across %d pages", len(by_key), len(wiki.pages))
    return list(by_key.values())
