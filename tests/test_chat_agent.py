"""Tests for the multi-turn editing agent (offline via a scripted chat model)."""

from __future__ import annotations

import json

import pytest

from openwiki.chat_agent import WikiAgent
from openwiki.tools import WikiTools

PAGE = """# Doc

body old text here.

---

nav
"""


class ScriptedChat:
    """Returns pre-scripted assistant messages and records what it was sent."""

    name = "scripted"

    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def chat_raw(self, messages, tools=None):
        self.seen.append(list(messages))
        return self.script.pop(0)


def _tool_msg(name, arguments):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}


@pytest.fixture
def tools(tmp_path) -> WikiTools:
    pages = tmp_path / "wiki" / "pages"
    pages.mkdir(parents=True)
    (pages / "001-doc.md").write_text(PAGE, encoding="utf-8")
    return WikiTools(tmp_path / "wiki")


def test_agent_executes_tool_then_answers(tools):
    chat = ScriptedChat([
        _tool_msg("edit_page", {"slug": "001-doc", "old_text": "old text", "new_text": "new text"}),
        {"role": "assistant", "content": "Ich habe die Seite 001-doc aktualisiert."},
    ])
    turn = WikiAgent(chat, tools).send("Ändere 'old text' zu 'new text'.")

    assert turn.reply == "Ich habe die Seite 001-doc aktualisiert."
    assert [c.name for c in turn.tool_calls] == ["edit_page"]
    assert turn.tool_calls[0].result.startswith("OK")
    assert "new text" in tools.read_page("001-doc")  # the file was actually edited


def test_agent_parses_string_arguments(tools):
    chat = ScriptedChat([
        _tool_msg("read_page", json.dumps({"slug": "001-doc"})),  # args as JSON string
        {"role": "assistant", "content": "gelesen"},
    ])
    turn = WikiAgent(chat, tools).send("lies die Seite")
    assert turn.tool_calls[0].name == "read_page"
    assert "body old text" in turn.tool_calls[0].result


def test_multi_turn_history_persists(tools):
    chat = ScriptedChat([
        {"role": "assistant", "content": "Hallo!"},
        {"role": "assistant", "content": "Wie besprochen."},
    ])
    agent = WikiAgent(chat, tools)
    agent.send("Hi")
    agent.send("Weiter")
    # the 2nd call must have seen the 1st turn's assistant reply in history
    assert any(m.get("content") == "Hallo!" for m in chat.seen[-1])
    assert agent.messages[0]["role"] == "system"


def test_agent_stops_at_iteration_limit(tools):
    loop = [_tool_msg("list_pages", {}) for _ in range(10)]  # never yields a final answer
    turn = WikiAgent(ScriptedChat(loop), tools, max_iterations=3).send("loop")
    assert "limit" in turn.reply.lower()
    assert len(turn.tool_calls) == 3


def test_think_tags_stripped(tools):
    chat = ScriptedChat([{"role": "assistant", "content": "<think>secret</think>Antwort."}])
    assert WikiAgent(chat, tools).send("frage").reply == "Antwort."
