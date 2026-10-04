"""Episodes (v0.106) — one dated narrative per session, shown next to the facts (offline, pure)."""

from __future__ import annotations

from openwiki.graph.memory import (
    EPISODE_SYSTEM, assemble_context, build_episode_messages, narrate_session,
)
from openwiki.graph.temporal import parse_date


class _Chat:
    def __init__(self, reply):
        self.reply, self.seen = reply, []

    def chat(self, messages):
        self.seen.append(messages)
        return self.reply


def test_the_episode_prompt_carries_the_session_date():
    msgs = build_episode_messages("Ann: I adopted a cat yesterday.", parse_date("2023-05-08"))
    assert msgs[0]["content"] == EPISODE_SYSTEM
    assert msgs[1]["content"].startswith("Session date: 8 May 2023\n\nAnn: I adopted")
    assert build_episode_messages("x")[1]["content"] == "x"                     # no date known: just the text


def test_narrate_session_cleans_up_and_never_shows_a_credential():
    key = "".join(("gh", "p_", "Ab12" * 9))
    chat = _Chat("<think>hm</think>  On 8 May 2023,\n Ann adopted   a cat. ")
    text = narrate_session(chat, f"Ann: my token is {key}. I adopted a cat yesterday.", parse_date("2023-05-08"))
    assert text == "On 8 May 2023, Ann adopted a cat."
    assert key not in chat.seen[0][1]["content"]                                # redacted before the model
    assert narrate_session(_Chat(None), "x") == ""


def _fact(n):
    return {"subject": f"s{n}", "predicate": "p", "object": f"o{n}", "session_id": "s1"}


def test_episodes_sit_between_the_facts_and_the_themes():
    ctx = assemble_context("I am X.", [_fact(1)], [{"label": "T", "summary": "a theme"}],
                           episodes=[{"text": "On 8 May 2023, Ann adopted a cat."}, {"text": "  "}])
    parts = [p.split("\n")[0] for p in ctx.split("\n\n")]
    assert parts == ["## Who I am", "## What I remember (most relevant)",
                     "## Episodes — what happened in a session", "## Themes across my memory"]
    assert ctx.count("\n- On 8 May 2023") == 1                                  # empty episodes are dropped
    assert "## Episodes" not in assemble_context("", [_fact(1)], [])           # none given: no block


def test_under_a_budget_facts_come_first_then_episodes():
    facts = [_fact(n) for n in range(6)]
    episodes = [{"text": "On 8 May 2023, " + "word " * 60}]
    ctx = assemble_context("", facts, [], max_facts=6, max_chars=260, episodes=episodes)
    assert "s0 p o0" in ctx and "## Episodes" not in ctx                        # no room left: episodes go first
    roomy = assemble_context("", facts, [], max_facts=6, max_chars=2000, episodes=episodes)
    assert "## Episodes" in roomy
