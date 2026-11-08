"""Built-in variables of a project's environment (Path C, C2.14; ``docs/path-c-world-model.md`` §2.3).

The git state, Ollama, the wiki's freshness and the capture workers — read without a person's acceptance, since they
write no fact. The session handoff renders them, and a law may read them (``git_dirty == 0``,
``wiki_commits_behind < 20``). Each comes from the probe the handoff always used (one implementation, looked up at
call time): ``handoff.git_state`` / ``ollama_state`` / ``wiki_freshness`` / ``capture_workers``. A reading is
``{"value", "time", "quality", "source", "raw"}`` — ``raw`` the probe's full result, for the handoff. Never raises.
"""

from __future__ import annotations

import time
from typing import Optional

BUILTIN = {
    "git_branch": "the checked-out branch",
    "git_head": "the HEAD commit",
    "git_dirty": "uncommitted files",
    "git_ahead": "commits ahead of the upstream",
    "git_behind": "commits behind the upstream",
    "ollama_reachable": "whether the Ollama server answers",
    "ollama_models": "the project's models Ollama has pulled",
    "wiki_commits_behind": "commits since the wiki's index was built",
    "capture_workers": "capture workers running now",
}
_GIT = {"git_branch", "git_head", "git_dirty", "git_ahead", "git_behind"}


def _reading(value, source: str, raw=None, quality: str = "complete", now: Optional[int] = None) -> dict:
    return {"value": value, "time": int(time.time() if now is None else now), "quality": quality, "source": source,
            "raw": raw}


def _failed(source: str, detail: str, now: Optional[int] = None) -> dict:
    return dict(_reading(None, source, quality="failed", now=now), detail=detail[:240])


def read_builtin(project, repo_root=None, host: Optional[str] = None, models=(), names=None,
                 now: Optional[int] = None) -> dict:
    """``{name: reading}`` for the built-in variables (``names`` = a subset; default all). ``repo_root`` = the
    repository the git state and the wiki's freshness are about (none → those fail)."""
    from .. import handoff
    want = [n for n in (names or BUILTIN) if n in BUILTIN]
    out: dict = {}
    if _GIT & set(want):
        try:
            state = handoff.git_state(repo_root) if repo_root else None
        except Exception as exc:          # a probe never costs the caller
            state, error = None, f"{type(exc).__name__}: {exc}"
        else:
            error = "no repository" if not repo_root else ""
        for name in _GIT & set(want):
            if not state or state.get("error"):
                out[name] = _failed("git", error or (state or {}).get("error") or "not a git repository", now)
                continue
            value = {"git_branch": state.get("branch"), "git_head": state.get("head"),
                     "git_dirty": state.get("dirty_count"),
                     "git_ahead": state.get("ahead"), "git_behind": state.get("behind")}[name]
            out[name] = _reading(value, "git", state, "complete" if value is not None else "failed", now)
    if {"ollama_reachable", "ollama_models"} & set(want):
        try:
            state = handoff.ollama_state(host or "http://localhost:11434", [m for m in models if m])
        except Exception as exc:
            state = {"reachable": False, "error": f"{type(exc).__name__}: {exc}", "models": {}}
        if "ollama_reachable" in want:
            out["ollama_reachable"] = _reading(bool(state.get("reachable")), "ollama /api/tags", state, now=now)
        if "ollama_models" in want:
            pulled = sorted(m for m, ok in (state.get("models") or {}).items() if ok)
            out["ollama_models"] = _reading(pulled, "ollama /api/tags", state,
                                            "complete" if state.get("reachable") else "failed", now)
    if "wiki_commits_behind" in want:
        try:
            fresh = handoff.wiki_freshness(project, repo_root)
        except Exception as exc:
            fresh, error = None, f"{type(exc).__name__}: {exc}"
        else:
            error = "the wiki isn't built from this repository"
        out["wiki_commits_behind"] = (_reading(int(fresh.get("commits_since") or 0), "the wiki's index", fresh,
                                               now=now) if fresh else _failed("the wiki's index", error, now))
    if "capture_workers" in want:
        try:
            workers = handoff.capture_workers(project.state_dir)
            out["capture_workers"] = _reading(len(workers), "capture locks", workers, now=now)
        except Exception as exc:
            out["capture_workers"] = _failed("capture locks", f"{type(exc).__name__}: {exc}", now)
    return out
