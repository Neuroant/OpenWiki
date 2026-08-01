"""Chat-model backends for the RAG agent.

Mirrors :mod:`openwiki.embeddings`: a small :class:`ChatModel` protocol plus an
Ollama implementation (``/api/chat`` via stdlib ``urllib``, no API key). Keeping
this behind a protocol means the agent never depends on a specific provider.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Protocol, Sequence, runtime_checkable

Message = dict  # {"role": "system" | "user" | "assistant", "content": str}


@runtime_checkable
class ChatModel(Protocol):
    @property
    def name(self) -> str: ...

    def chat(self, messages: Sequence[Message]) -> str: ...

    def chat_raw(self, messages: Sequence[Message], tools=None) -> Message: ...


class OllamaChat:
    """Chat completion via a local Ollama server's ``/api/chat`` endpoint."""

    def __init__(
        self,
        model: str = "qwen2.5:14b-instruct-q4_K_M",
        host: str = "http://localhost:11434",
        temperature: float = 0.2,
        timeout: float = 300.0,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    def chat(self, messages: Sequence[Message]) -> str:
        """Convenience wrapper returning just the assistant's text content."""
        return self.chat_raw(messages).get("content", "")

    def chat_raw(self, messages: Sequence[Message], tools=None, options=None) -> Message:
        """Return the full assistant message dict (may include ``tool_calls``)."""
        body = {
            "model": self.model,
            "messages": list(messages),
            "stream": False,
            "options": {"temperature": self.temperature, **(options or {})},
        }
        if tools:
            body["tools"] = tools
        payload = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self.host}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(
                f"Ollama chat failed ({exc.code}) for model '{self.model}': {detail}. "
                f"Is it pulled? Try `ollama pull {self.model}`."
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Could not reach Ollama at {self.host} (is it running?): {exc}"
            ) from exc
        return data.get("message", {}) or {}
