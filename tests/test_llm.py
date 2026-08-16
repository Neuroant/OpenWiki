"""Offline tests for OllamaChat option plumbing (no server; urlopen is faked)."""

from __future__ import annotations

import json
import urllib.request

from openwiki.llm import OllamaChat


class _FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture(monkeypatch, reply="ok"):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResp(json.dumps({"message": {"role": "assistant", "content": reply}}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


def test_constructor_options_merge_into_every_request(monkeypatch):
    captured = _capture(monkeypatch)
    chat = OllamaChat(model="m", host="http://h", temperature=0.0, options={"seed": 0})
    assert chat.chat([{"role": "user", "content": "hi"}]) == "ok"
    opts = captured["body"]["options"]
    assert opts["temperature"] == 0.0 and opts["seed"] == 0


def test_per_call_options_take_precedence(monkeypatch):
    captured = _capture(monkeypatch)
    chat = OllamaChat(model="m", options={"seed": 0})
    chat.chat_raw([{"role": "user", "content": "hi"}], options={"seed": 42})
    assert captured["body"]["options"]["seed"] == 42


def test_entity_chat_is_deterministic():
    from openwiki.cli import _entity_chat

    chat = _entity_chat("some-model", "http://localhost:11434")
    assert chat.temperature == 0.0          # greedy decoding
    assert chat.options.get("seed") == 0    # reproducible
