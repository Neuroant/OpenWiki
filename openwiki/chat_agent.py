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

from .llm import Message
from .tools import WikiTools

SYSTEM_PROMPT = (
    "You are OpenWiki's assistant for a documentation wiki. You can search, read, "
    "and edit wiki pages with the provided tools.\n"
    "Guidelines:\n"
    "- Answer in the user's language.\n"
    "- To answer a question: search first, read the most relevant page(s), then "
    "reply concisely and cite the page slug(s) you used.\n"
    "- To edit: read the page first so you use exact text, then call edit_page, "
    "append_section, or create_page. Make only the changes the user asked for.\n"
    "- After editing, state briefly what you changed (page slug + what/why).\n"
    "- If a tool returns an ERROR, adjust your arguments and try again."
)

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


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
                 max_iterations: int = 6) -> None:
        self.chat = chat
        self.tools = tools
        self.max_iterations = max_iterations
        self.messages: list[Message] = [{"role": "system", "content": system_prompt}]

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
