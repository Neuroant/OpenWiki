"""Propose a domain entity ontology from a sample of the corpus.

`owiki ontology` samples a project's text, makes **one** chat-model call to propose
a small, domain-specific set of entity types (names + descriptions + examples), and
prints (or `--write`s) them as the manifest's ``[graph] entity_types``. It's a
one-time scaffolding step — like ``init`` — that you review and edit; entity
extraction itself stays the deterministic pipeline stage. See ``docs/projects.md``.

Pure helpers here (sampling + the LLM call + formatting); the CLI wires them to a
project and the manifest. Depends only on a ``ChatModel`` — no Kuzu, no PDF.
"""

from __future__ import annotations

import json
import re
from typing import Sequence

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_JSON_ARRAY = re.compile(r"\[.*\]", re.DOTALL)

_SYSTEM = (
    "You design a COMPACT entity ontology for a knowledge graph over a document "
    "corpus. From the sample text, propose {n} entity TYPES that best capture the "
    "named things worth extracting in THIS domain. Return ONLY a JSON array of "
    'objects {{"name": <a short PascalCase type>, "description": <one line with '
    "2-3 concrete example names drawn from the domain>}}. The types must be distinct "
    "and domain-specific; avoid a vague catch-all. Output the JSON array and nothing else."
)


def sample_corpus(texts: Sequence[str], n: int = 40, budget: int = 6000) -> str:
    """A spread sample of the corpus text (evenly across ``texts``), ≤ ``budget`` chars."""
    usable = [t for t in texts if t and t.strip()]
    if not usable:
        return ""
    step = max(1, len(usable) // n)
    picked = usable[::step][:n]
    per = max(120, budget // max(1, len(picked)))
    return "\n---\n".join(t.strip()[:per] for t in picked)[:budget]


_FENCE = re.compile(r"```(?:json)?", re.IGNORECASE)


def _extract_items(reply: str) -> list:
    """Best-effort pull of a JSON array of objects from a chat reply.

    Tolerates ``<think>`` blocks, ```` ```json ```` fences, and surrounding prose
    (including stray brackets) by trying object-only arrays first, then a greedy span.
    """
    reply = _FENCE.sub("", _THINK.sub("", reply))
    candidates = re.findall(r"\[[^\[\]]*\]", reply, re.DOTALL)  # arrays w/o nested arrays (i.e. of objects)
    greedy = _JSON_ARRAY.search(reply)
    if greedy:
        candidates.append(greedy.group(0))
    for cand in candidates:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            items = [x for x in data if isinstance(x, dict) and str(x.get("name", "")).strip()]
            if items:
                return items
    return []


def propose_ontology(chat, sample_text: str, n_types: int = 7) -> list[dict]:
    """One chat call → a de-duplicated list of ``{"name", "description"}`` type proposals."""
    messages = [
        {"role": "system", "content": _SYSTEM.format(n=n_types)},
        {"role": "user", "content": "Sample text from the corpus:\n\n" + sample_text},
    ]
    out: list[dict] = []
    seen: set = set()
    for item in _extract_items(chat.chat(messages)):
        name = str(item.get("name", "")).strip()
        desc = str(item.get("description", "")).strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append({"name": name, "description": desc})
    return out[:n_types]


def format_entity_types(items: Sequence) -> list[str]:
    """Render proposals as ``["Name: description", ...]`` for `entity_types`."""
    result = []
    for item in items:
        if isinstance(item, dict):
            name, desc = item.get("name", ""), item.get("description", "")
        else:
            name, desc = str(item), ""
        name = str(name).strip()
        desc = str(desc).strip()
        if name:
            result.append(f"{name}: {desc}" if desc else name)
    return result
