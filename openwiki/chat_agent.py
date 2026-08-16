"""A multi-turn, tool-using agent that can read and edit the wiki.

Unlike :class:`~openwiki.agent.RAGAgent` (one-shot Q&A), this keeps a running
conversation and lets the model call the :class:`~openwiki.tools.WikiTools` tools
in a loop: model → tool calls → results → model → ... until it produces a plain
reply. History persists across :meth:`WikiAgent.send` calls.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .llm import Message
from .tools import WikiTools

SYSTEM_PROMPT = (
    "You are OpenWiki's assistant for a documentation wiki. You can search, read, "
    "and edit wiki pages with the provided tools.\n"
    "Guidelines:\n"
    "- Answer in the user's language.\n"
    "- Always search the wiki before answering any question about its content — "
    "including broad ones like \"what is this wiki about?\". Read the most relevant "
    "page(s), then reply concisely and cite the page slug(s) you used. Never answer "
    "a content question from prior knowledge; if the tools find nothing, say so.\n"
    "- To edit: read the page first so you use exact text, then call edit_page, "
    "append_section, or create_page. Make only the changes the user asked for.\n"
    "- After editing, state briefly what you changed (page slug + what/why).\n"
    "- If a tool returns an ERROR, adjust your arguments and try again."
)

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def summarize_wiki(wiki_dir) -> str:
    """A one-line identity for the wiki (title + top-level section titles), read
    from ``wiki.json``. Injected into the agent's system prompt so it knows which
    wiki it is serving and won't answer content questions from prior knowledge of
    some other document. Returns ``""`` when no manifest is present."""
    manifest = Path(wiki_dir) / "wiki.json"
    if not manifest.is_file():
        return ""
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return ""
    title = (data.get("title") or Path(wiki_dir).name).strip()
    sections = [p.get("title", "").strip() for p in data.get("pages", []) if not p.get("parent")]
    sections = [s for s in sections if s]
    summary = f'This wiki is titled "{title}".'
    if sections:
        shown = "; ".join(sections[:12])
        if len(sections) > 12:
            shown += "; …"
        summary += f" Its main sections are: {shown}."
    return summary


@dataclass
class ToolCall:
    name: str
    arguments: dict
    result: str


@dataclass
class AgentTurn:
    reply: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class WikiAgent:
    def __init__(self, chat, tools: WikiTools, system_prompt: str = SYSTEM_PROMPT,
                 wiki_summary: str = "", max_iterations: int = 6) -> None:
        self.chat = chat
        self.tools = tools
        self.max_iterations = max_iterations
        prompt = system_prompt
        if wiki_summary:
            prompt += ("\n\nAbout this wiki: " + wiki_summary +
                       " Answer every content question from THIS wiki via the tools; "
                       "do not draw on outside knowledge of other products or manuals.")
        self.messages: list[Message] = [{"role": "system", "content": prompt}]

    def send(self, user_message: str) -> AgentTurn:
        """Run one user turn to completion, executing any tool calls in between."""
        self.messages.append({"role": "user", "content": user_message})
        made: list[ToolCall] = []

        for _ in range(self.max_iterations):
            message = self.chat.chat_raw(self.messages, tools=self.tools.schemas())
            message.setdefault("role", "assistant")
            self.messages.append(message)

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                reply = _THINK.sub("", message.get("content") or "").strip()
                return AgentTurn(reply=reply, tool_calls=made)

            for call in tool_calls:
                function = call.get("function", {})
                name = function.get("name", "")
                arguments = function.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                result = self.tools.dispatch(name, arguments)
                made.append(ToolCall(name=name, arguments=arguments, result=result))
                self.messages.append({"role": "tool", "content": result, "tool_name": name})

        return AgentTurn(
            reply="(Stopped: reached the tool-call limit without a final answer.)",
            tool_calls=made,
        )
