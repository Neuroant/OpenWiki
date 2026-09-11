"""Session capture → memory facts (Path B — the B2/B3/B6 thin vertical).

Turns a session transcript into a small set of typed memory facts (subject-
predicate-object), and formats recalled facts for injection into a later session's
context. **Pure + chat-injected** (no Kuzu, no network), mirroring
:mod:`~openwiki.graph.community` / :mod:`~openwiki.graph.entities`; the Kuzu I/O
(persist / recall) lives in :class:`~openwiki.graph.store.GraphStore`.

This is the *remembered tier* of the second-brain design — see `docs/path-b-memory.md`.
Thin-vertical scope: **B2** capture + **B3** dedup/merge (no contradiction — that's B4)
+ **B6** recall/format (the activation tier). The authoritative-graph reframe (B0),
read-path reinforcement (B1), and contradiction/time-versioning (B4) are deferred, as
is fusing the identity + attractor tiers into a full three-tier assembly.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .entities import _normalize  # reuse German-aware normalization for dedup keys

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_ARRAY = re.compile(r"\[.*\]", re.DOTALL)


@dataclass
class MemoryFact:
    """One remembered fact: a subject–predicate–object triple."""
    subject: str
    predicate: str
    object: str

    def text(self) -> str:
        return f"{self.subject} {self.predicate} {self.object}".strip()

    def key(self) -> tuple:
        """Dedup key — normalized so surface variants merge (B3 entity resolution)."""
        return (_normalize(self.subject), self.predicate.strip().lower(), _normalize(self.object))


CAPTURE_SYSTEM = (
    "You extract the durable facts worth remembering from a conversation, as a JSON array "
    'of {"subject","predicate","object"} triples. Capture stable, reusable facts — decisions, '
    "preferences, definitions, states, commitments — not chit-chat, greetings, or one-off "
    "phrasing. Keep subject/object as short noun phrases and predicate as a short verb phrase. "
    "Answer in the language of the conversation. Output ONLY the JSON array, nothing else."
)


def build_capture_messages(transcript: str) -> list:
    user = f"Conversation:\n{transcript}\n\nExtract the facts worth remembering."
    return [{"role": "system", "content": CAPTURE_SYSTEM},
            {"role": "user", "content": user}]


def parse_facts(raw: str) -> list:
    """Parse the model's JSON array into de-duplicated `MemoryFact`s (lenient)."""
    text = _THINK.sub("", raw or "").strip()
    match = _ARRAY.search(text)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return []
    facts, seen = [], set()
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        s = str(item.get("subject", "")).strip()
        p = str(item.get("predicate", "")).strip()
        o = str(item.get("object", "")).strip()
        if not (s and p and o):
            continue
        fact = MemoryFact(s, p, o)
        if fact.key() in seen:      # dedup within the session (B3, phase 2)
            continue
        seen.add(fact.key())
        facts.append(fact)
    return facts


def capture_session(chat, transcript: str) -> list:
    """B2: one LLM call → the session's memory facts (``<think>`` stripped, parsed)."""
    return parse_facts(chat.chat(build_capture_messages(transcript)))


def format_memory(recalled: list) -> str:
    """Format recalled facts as a compact block for injection (or ``""``).
    A fact flagged ``superseded`` (only present with ``recall(include_superseded=True)``)
    is marked so; the default recall returns only current facts, so injection is unaffected."""
    if not recalled:
        return ""
    lines = ["Relevant memory from earlier sessions:"]
    for r in recalled:
        mark = "  [superseded]" if r.get("superseded") else ""
        lines.append(f"- {r['subject']} {r['predicate']} {r['object']}{mark}"
                     f"  ({r.get('session_id', '?')})")
    return "\n".join(lines)


_FACT_BUDGET_SHARE = 0.6   # facts (activation) get the majority of the char budget; themes the rest


def _fit_section(header: str, lines: list, budget) -> tuple:
    """Header + as many ``lines`` as fit within ``budget`` chars → ``(block, chars_used)``.
    ``budget=None`` is unbounded; ``("", 0)`` if nothing beyond the header fits."""
    if not lines:
        return "", 0
    out, used = [header], len(header)
    for line in lines:
        need = len(line) + 1                       # + newline
        if budget is not None and used + need > budget:
            break
        out.append(line)
        used += need
    return ("\n".join(out), used) if len(out) > 1 else ("", 0)


def assemble_context(identity: str, facts: list, themes: list, max_facts: int = 8,
                     max_themes: int = 4, max_chars=None) -> str:
    """B6: assemble a session's context from the **three memory tiers** — identity (DNA),
    the activated facts (``recall`` — the epigenetic tier), and the relevant consolidated
    themes (B5 ``MemoryConcept``s — the attractor tier). Pure + **fail-soft**: any tier may
    be empty; returns ``""`` when nothing is available (never blocks a session).

    With ``max_chars`` set, fit within an approximate budget (~4 chars/token): **identity**
    first (truncated if it alone overflows), then **facts** (the majority share — the primary
    signal), then **themes** (whatever remains). Graceful truncation, facts prioritized over
    themes; ``max_chars=None`` keeps the prior count-only behavior."""
    facts = list(facts)[:max_facts]
    themes = list(themes)[:max_themes]
    fact_lines = [f"- {f['subject']} {f['predicate']} {f['object']}  ({f.get('session_id', '?')})"
                  for f in facts]
    theme_lines = [f"- **{t.get('label', '')}**: {(t.get('summary') or '').strip()}" for t in themes]

    blocks: list = []
    remaining = None if max_chars is None else max(0, int(max_chars))

    ident = (identity or "").strip()
    if ident:
        block = "## Who I am\n" + ident
        if remaining is not None and len(block) > remaining:
            block = block[:remaining].rstrip()     # identity is small + always useful → keep, truncate
        if block.strip():
            blocks.append(block)
            if remaining is not None:
                remaining = max(0, remaining - len(block) - 2)   # -2 ≈ the blank-line separator

    fact_budget = None if remaining is None else int(remaining * _FACT_BUDGET_SHARE)
    fact_block, fact_used = _fit_section("## What I remember (most relevant)", fact_lines, fact_budget)
    if fact_block:
        blocks.append(fact_block)
        if remaining is not None:
            remaining = max(0, remaining - fact_used - 2)

    theme_block, _ = _fit_section("## Themes across my memory", theme_lines, remaining)
    if theme_block:
        blocks.append(theme_block)

    return "\n\n".join(blocks)
