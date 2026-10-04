"""No memory for chore prompts (v0.101) — the classifier, the session check and the inject hook (offline)."""

from __future__ import annotations

import json

import pytest

from openwiki import cli
from openwiki.project import Project


@pytest.mark.parametrize("prompt,kind", [
    ("push", "git"), ("Push.", "git"), ("push it", "git"), ("commit and push", "git"), ("git push", "git"),
    ("push and tag v0.98.0", "git"), ("push it and tag v0.8.0", "git"), ("tag v1.2.3", "git"),
    ("/compact", "command"), ("/session-restart prepare", "command"),
    ("yes", "ack"), ("Continue!", "ack"), ("go ahead", "ack"), ("danke", "ack"),
    ("push and proceed with credential redaction", None), ("push and continue with Nemori", None),
    ("yes, run it in the foreground", None), ("proceed with writes landing during a session", None),
    ("sync arc42 docs", None), ("commit the redaction fix and explain why", None), ("", None),
])
def test_chore_kind(prompt, kind):
    assert cli.chore_kind(prompt) == kind


def test_project_patterns_and_settings(tmp_path):
    assert cli.chore_kind("Sync arc42 docs.", ["sync arc42 docs"]) == "custom"
    assert cli.chore_kind("sync arc42 docs now", ["sync arc42 docs"]) is None      # whole prompt only
    assert cli.chore_kind("x", ["(unbalanced"]) is None                             # a bad pattern is ignored
    root = tmp_path / "p"
    root.mkdir()
    (root / "openwiki.toml").write_text('[project]\nname = "p"\n', encoding="utf-8")
    assert Project.load(root).skip_chores is True and Project.load(root).skip_prompts == []
    (root / "openwiki.toml").write_text('[project]\nname = "p"\n\n[memory]\nskip_chores = false\n'
                                        'skip_prompts = ["sync arc42 docs"]\n', encoding="utf-8")
    assert Project.load(root).skip_chores is False and Project.load(root).skip_prompts == ["sync arc42 docs"]


def _transcript(path, with_answer):
    lines = [{"type": "user", "message": {"role": "user", "content": "hello"}}]
    if with_answer:
        lines.append({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}})
    path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return path


def test_session_has_turns(tmp_path):
    assert cli._session_has_turns(_transcript(tmp_path / "a.jsonl", True))
    assert not cli._session_has_turns(_transcript(tmp_path / "b.jsonl", False))
    assert not cli._session_has_turns(tmp_path / "missing.jsonl") and not cli._session_has_turns(None)


def test_inject_skips_chores_but_not_an_opening_ack(tmp_path, monkeypatch, capsys):
    root = tmp_path / "brain"
    root.mkdir()
    (root / "openwiki.toml").write_text('[project]\nname = "brain"\n\n[memory]\nenabled = true\n', encoding="utf-8")
    project = Project.load(root)
    project.graph_path.parent.mkdir(parents=True, exist_ok=True)
    project.graph_path.write_text("", encoding="utf-8")
    loaded = []

    class _Graph:
        def context_for(self, prompt, embedder, **kw):
            return f"CTX for {prompt}"

        def close(self):
            pass
    monkeypatch.setattr(cli, "_hook_embedder", lambda p: loaded.append(1) or object())
    monkeypatch.setattr(cli, "_open_reader", lambda *a, **kw: _Graph())
    fresh, going = _transcript(tmp_path / "fresh.jsonl", False), _transcript(tmp_path / "going.jsonl", True)

    def inject(prompt, transcript, proj=project):
        cli._hook_inject(proj, {"prompt": prompt, "transcript_path": str(transcript)})
        return capsys.readouterr().out

    assert inject("push and tag v1.0.0", going) == "" and loaded == []    # a chore costs nothing
    assert inject("/compact", going) == ""
    assert inject("continue", going) == ""                                # a later acknowledgement
    assert "CTX for continue" in inject("continue", fresh)                # but it opens the session
    assert "CTX for what is our release process?" in inject("what is our release process?", going)
    (root / "openwiki.toml").write_text('[project]\nname = "brain"\n\n[memory]\nenabled = true\nskip_chores = false\n',
                                        encoding="utf-8")
    assert "CTX for push" in inject("push", going, Project.load(root))      # the gate can be switched off
