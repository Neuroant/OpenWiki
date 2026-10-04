"""Session handoff — carry a working session's state into the next one (``owiki handoff``).

A long coding session ends with context only it has: what was decided, what is half done, what comes
next. ``prepare`` writes that down — the agent's own note (Next, Summary, Decisions, Open threads,
Ready-to-use prompts) merged with the state OpenWiki can derive itself: the repository (branch, HEAD,
tag, sync with upstream, uncommitted files, commits this session), the memory (facts captured from the
session or recorded by the agent, facts closed, writes still queued, turns not yet captured) and the
environment (Ollama and its models, the graph, capture workers, hook-log problems), plus the memory
the next session will see for its first task. ``resume`` reads the latest handoff back, re-checks that
state and reports what changed since — new commits, new or closed facts. The ``SessionStart`` hook
(``owiki hook resume``) injects that brief into every new session; the MCP tool ``wiki_handoff`` gives
agents both modes without a shell.

The handoff lives in the OpenWiki project (``handoff/HANDOFF.md`` + ``handoff.json`` + an archive),
next to the memory it complements: the handoff carries the narrative and the next steps, the memory
the facts. The note is screened by the P0 policy (``policy.is_unsafe_text``) before it is stored — it
is injected into later sessions just like memory. Git via subprocess, Ollama via stdlib urllib; the
graph, embedder and index are passed in, so this module imports neither Kuzu nor NumPy.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .policy import is_unsafe_text, redact_secrets

HANDOFF_DIR = "handoff"                     # in the OpenWiki project, next to its memory
HANDOFF_MD = "HANDOFF.md"
HANDOFF_JSON = "handoff.json"
CAPTURE_STATE_FILE = "capture-state.json"   # the hook capture's per-session watermarks (.openwiki/)
HOOK_BRIEF_CHARS = 6000                     # the SessionStart brief: ~1,500 tokens, once per session
NEXT_MEMORY_CHARS = 1600                    # memory for the next task, inside the brief
MAX_WINDOW_DAYS = 7                         # "this session" reaches back at most a week (sessions can run for months)

NOTE_SECTIONS = (("next", "Next (start here)"), ("summary", "Summary"), ("decisions", "Decisions"),
                 ("threads", "Open threads"), ("prompts", "Ready-to-use prompts"))
_TITLES = dict(NOTE_SECTIONS)
_CARRY = ("next", "threads", "prompts")     # kept from the last handoff when no new note is given

HOOK_HEADER = ("Session handoff (OpenWiki) — written by the previous session for this one; context, not "
               "the user's message. Check it against the current state before acting on it, and start "
               "from \"Next\" when the user wants to continue.\n\n")


@dataclass
class HandoffEnv:
    """What a handoff reads, opened by the caller (CLI, MCP server, hook): the session's working
    directory, the OpenWiki project that holds the handoff and the memory, and — optional — the
    project's graph (read-only is enough), embedder and index."""
    repo: Path
    project: Any
    graph: Any = None
    embedder: Any = None
    index: Any = None
    graph_note: str = ""        # why there is no graph ("locked by a writer", "not built yet")
    chat_model: str = ""
    embed_model: str = ""
    host: str = ""


# -- time ----------------------------------------------------------------------

def _epoch(ts) -> Optional[int]:
    """An ISO timestamp (``2026-10-03T14:05:00.123Z``) → epoch seconds (``None`` if unparseable)."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _when(epoch) -> str:
    """Epoch seconds → local ``2026-10-03 14:05`` (``?`` if unknown)."""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(epoch))) if epoch else "?"


def _ago(epoch, now) -> str:
    s = max(0, int(now) - int(epoch or 0))
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{round(s / 60)} min ago"
    if s < 36 * 3600:
        return f"{round(s / 3600)} h ago"
    return f"{round(s / 86400)} days ago"


# -- git -----------------------------------------------------------------------

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # no console flash from a hook on Windows


def git(repo, *args, timeout: float = 10.0) -> Optional[str]:
    """``git -C repo args…`` → its output (trailing whitespace stripped), or ``None`` (not a
    repository, no git, an error)."""
    try:
        res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=timeout,
                             stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return res.stdout.rstrip() if res.returncode == 0 else None


def git_state(repo, max_dirty: int = 20) -> dict:
    """Branch, HEAD, tag, sync with upstream and uncommitted files of the repository containing
    ``repo`` (``{}`` outside one)."""
    top = git(repo, "rev-parse", "--show-toplevel")
    if not top:
        return {}
    st = {"root": str(Path(top)),
          "branch": git(top, "rev-parse", "--abbrev-ref", "HEAD") or "",
          "head": git(top, "rev-parse", "HEAD") or "",
          "subject": git(top, "log", "-1", "--format=%s") or "",
          "describe": git(top, "describe", "--tags", "--always") or "",
          "tag": git(top, "describe", "--tags", "--abbrev=0") or ""}
    upstream = git(top, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream:
        counts = (git(top, "rev-list", "--left-right", "--count", f"{upstream}...HEAD") or "").split()
        if len(counts) == 2 and all(c.isdigit() for c in counts):
            st.update(upstream=upstream, behind=int(counts[0]), ahead=int(counts[1]))
    dirty = [line for line in (git(top, "status", "--porcelain") or "").splitlines() if line.strip()]
    st["dirty"], st["dirty_count"] = dirty[:max_dirty], len(dirty)
    return st


def git_commits(repo, since: Optional[int] = None, rev_range: Optional[str] = None,
                limit: int = 15) -> tuple:
    """``(["<short> <subject>", …], total)`` — the commits after ``since`` (epoch seconds, committer
    date) and/or in ``rev_range`` (``"<sha>..HEAD"``), newest first; at most ``limit`` listed."""
    flt = [f"--since={time.strftime('%Y-%m-%d %H:%M:%S +0000', time.gmtime(int(since)))}"] if since else []
    revs = [rev_range or "HEAD"]
    log = git(repo, "log", f"-{int(limit)}", "--format=%h %s", *flt, *revs)
    lines = [line for line in (log or "").splitlines() if line.strip()]
    total = git(repo, "rev-list", "--count", *flt, *revs)
    return lines, int(total) if total and total.isdigit() else len(lines)


def git_is_ancestor(repo, rev: str) -> bool:
    return bool(rev) and git(repo, "merge-base", "--is-ancestor", rev, "HEAD") is not None


# -- the Claude Code transcript ------------------------------------------------

def claude_home() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env) if env else Path.home() / ".claude"


def claude_slug(path) -> str:
    """Claude Code's folder name for a working directory under ``~/.claude/projects/`` — every
    character but ASCII letters and digits becomes ``-`` (``G:\\Claude\\OpenWiki`` →
    ``G--Claude-OpenWiki``)."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(path).resolve()))


def find_transcript(repo, session_id: Optional[str] = None, home=None) -> Optional[Path]:
    """The Claude Code transcript (JSONL) of session ``session_id`` started in ``repo`` — or, without
    an id, the most recently written one (the session running now, when called from inside it)."""
    base = (Path(home) if home else claude_home()) / "projects"
    slug = claude_slug(repo)
    folder = base / slug
    if not folder.is_dir():          # Windows paths are case-insensitive; the folder name is not
        try:
            folder = next((d for d in base.iterdir() if d.is_dir() and d.name.lower() == slug.lower()),
                          folder)
        except OSError:
            return None
    if session_id:
        path = folder / f"{session_id}.jsonl"
        return path if path.is_file() else None
    try:
        files = [p for p in folder.glob("*.jsonl") if p.is_file()]
    except OSError:
        return None
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def transcript_stats(text: str, after: str = "") -> dict:
    """First / last turn timestamps, turn counts and compactions of a Claude Code transcript, and
    ``after`` = how many turns are dated after the given timestamp (the capture watermark)."""
    from .claude_code_template import iter_claude_turns
    first = last = ""
    users = assistants = pending = 0
    for ts, turn in iter_claude_turns(text):
        if ts:
            first = min(first, ts) if first else ts
            last = max(last, ts)
            pending += ts > after
        users += turn.startswith("User:")
        assistants += not turn.startswith("User:")
    compactions = 0
    for line in text.splitlines():
        if "isCompactSummary" in line:
            try:
                compactions += bool(json.loads(line).get("isCompactSummary"))
            except (ValueError, AttributeError):
                pass
    return {"first_ts": first, "last_ts": last, "user_turns": users, "assistant_turns": assistants,
            "compactions": compactions, "after": pending}


def capture_watermark(state_dir, session_id: str) -> str:
    """The timestamp of the last turn of ``session_id`` the hook capture has taken (``""`` = none)."""
    try:
        state = json.loads((Path(state_dir) / CAPTURE_STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(state.get(session_id, "")) if isinstance(state, dict) else ""


# -- environment ---------------------------------------------------------------

def ollama_state(host: str, models=(), timeout: float = 2.0) -> dict:
    """Is the Ollama server at ``host`` reachable, and are ``models`` pulled?"""
    host = str(host or "http://localhost:11434").rstrip("/")
    try:
        with urllib.request.urlopen(host + "/api/tags", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"host": host, "reachable": False, "error": str(exc)[:160]}
    names = {str(m.get("name") or m.get("model") or "")
             for m in (data.get("models") or []) if isinstance(m, dict)}

    def pulled(model: str) -> bool:
        return (model in names or f"{model}:latest" in names
                or (model.endswith(":latest") and model[:-7] in names))
    return {"host": host, "reachable": True, "models": {m: pulled(m) for m in models if m}}


def _pid_alive(pid: int) -> Optional[bool]:
    """Is process ``pid`` running? (``None`` = can't tell.) Never signals it — on Windows
    ``os.kill(pid, 0)`` would *terminate* the process."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return ctypes.get_last_error() == 5              # access denied → it exists
            code = ctypes.c_ulong()
            ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
            k32.CloseHandle(handle)
            return bool(ok) and code.value == 259               # STILL_ACTIVE
        except Exception:
            return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None


def capture_workers(state_dir, now: Optional[float] = None, stale_s: float = 6 * 3600) -> list:
    """The hook-capture workers still running — a live process holding a session's
    ``capture-<sid>.lock`` → ``[{"session", "since", "pid"}]``."""
    now = time.time() if now is None else now
    out = []
    try:
        locks = sorted(Path(state_dir).glob("capture-*.lock"))
    except OSError:
        return out
    for lock in locks:
        try:
            started = lock.stat().st_mtime
            pid = int(lock.read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            continue
        if now - started < stale_s and _pid_alive(pid) is not False:
            out.append({"session": lock.stem[len("capture-"):], "since": int(started), "pid": pid})
    return out


# a capture that failed, a hook that raised, a worker that could not start — not a locked graph
# ("graph opened read-only: … Could not set lock"): that one only queues the writes to the journal
_LOG_PROBLEM = re.compile(r"\bfailed\b|traceback|could not spawn|^openwiki hook '", re.IGNORECASE)


def hook_log_problems(path, offset: Optional[int] = None, limit: int = 5) -> tuple:
    """``(problems, size)`` — the last ``limit`` lines of the hook log after byte ``offset`` that
    report a failure (``offset=None``: its last 64 KB), and the log's size now (the next offset)."""
    p = Path(path)
    try:
        size = p.stat().st_size
        start = max(0, size - 65536) if offset is None or offset > size else max(0, int(offset))
        with p.open("rb") as fh:
            fh.seek(max(start, size - 262144))
            data = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return [], 0
    problems = [line.strip() for line in data.splitlines()
                if _LOG_PROBLEM.search(line.strip()) and "graph opened read-only" not in line]
    return problems[-limit:], size


def wiki_freshness(project, repo_root) -> Optional[dict]:
    """When the project's wiki is built from this repository (a code source): when its index was
    built and how many commits came since — a stale index answers from the old code and docs."""
    if not repo_root:
        return None
    try:
        built = int((Path(project.index_dir) / "index.json").stat().st_mtime)
        sources = [str(src) for src in project.source_paths()]
    except (OSError, AttributeError):
        return None
    root = os.path.normcase(os.path.abspath(str(repo_root))).rstrip("\\/")
    if not any(os.path.normcase(os.path.abspath(src)).rstrip("\\/") == root
               for src in sources if "://" not in src):
        return None
    return {"built": built, "commits_since": git_commits(repo_root, since=built, limit=1)[1]}


def environment(env: HandoffEnv, log_offset: Optional[int] = None, repo_root=None) -> dict:
    from . import __version__
    state_dir = Path(env.project.state_dir)
    problems, size = hook_log_problems(state_dir / "hook.log", log_offset)
    if env.graph is not None:
        graph = "readable"
    elif Path(env.project.graph_path).exists():
        graph = f"not readable — {env.graph_note or 'unavailable'}"
    else:
        graph = "not built yet"
    return {"ollama": ollama_state(env.host, [env.chat_model, env.embed_model]), "graph": graph,
            "wiki": wiki_freshness(env.project, repo_root), "workers": capture_workers(state_dir),
            "hook_log": {"offset": size, "since": log_offset, "problems": problems},
            "openwiki": __version__, "python": platform.python_version()}


# -- where the handoff belongs -------------------------------------------------

def bound_project(repo) -> Optional[Path]:
    """The OpenWiki project a repository's Claude Code hooks are bound to — the ``--project "…"`` of
    an OpenWiki hook command in ``.claude/settings.local.json`` / ``settings.json`` (written by
    ``owiki claude-code --hooks --into``): where a code repository keeps its memory and handoff."""
    for name in ("settings.local.json", "settings.json"):
        try:
            data = json.loads((Path(repo) / ".claude" / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        hooks = data.get("hooks") if isinstance(data, dict) else None
        for blocks in (hooks.values() if isinstance(hooks, dict) else []):
            for block in (blocks if isinstance(blocks, list) else []):
                for hook in ((block.get("hooks") or []) if isinstance(block, dict) else []):
                    cmd = str(hook.get("command") or "") if isinstance(hook, dict) else ""
                    if " hook " not in cmd or ("openwiki" not in cmd and "owiki" not in cmd):
                        continue
                    m = re.search(r'--project\s+(?:"([^"]+)"|(\S+))', cmd)
                    if m:
                        return Path(m.group(1) or m.group(2))
    return None


def same_repo(h: dict, cwd) -> bool:
    """Was handoff ``h`` written for the repository ``cwd`` is in? (A handoff without one: yes.)"""
    root = (h.get("repo") or {}).get("root") or h.get("cwd")
    if not root:
        return True
    a = os.path.normcase(os.path.abspath(str(root))).rstrip("\\/")
    b = os.path.normcase(os.path.abspath(str(cwd))).rstrip("\\/")
    return b == a or b.startswith(a + os.sep)


def handoff_dir(project, out=None) -> Path:
    return Path(out) if out else Path(project.root) / HANDOFF_DIR


def load_handoff(folder) -> Optional[dict]:
    try:
        data = json.loads((Path(folder) / HANDOFF_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save_handoff(folder, data: dict) -> Path:
    """Write ``HANDOFF.md`` + ``handoff.json`` (+ a dated copy under ``archive/``) → the ``.md`` path."""
    folder = Path(folder)
    (folder / "archive").mkdir(parents=True, exist_ok=True)
    md = render_handoff(data)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(int(data["created_at"])))
    (folder / "archive" / f"HANDOFF-{stamp}.md").write_text(md, encoding="utf-8")
    _write_atomic(folder / HANDOFF_MD, md)
    _write_atomic(folder / HANDOFF_JSON, json.dumps(data, ensure_ascii=False, indent=1))
    return folder / HANDOFF_MD


# -- the agent's note ----------------------------------------------------------

def note_key(title: str) -> str:
    """A note section title → its canonical key (``next`` / ``summary`` / ``decisions`` /
    ``threads`` / ``prompts``), else a slug of the title."""
    t = str(title).strip().lower()
    if t.startswith("next") or "start here" in t:
        return "next"
    if "prompt" in t:
        return "prompts"
    if "decision" in t:
        return "decisions"
    if "thread" in t or t.startswith("open"):
        return "threads"
    if "summary" in t:
        return "summary"
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-") or "notes"


def parse_note(text: str) -> dict:
    """The agent's handoff note (Markdown) → ``{key: {"title", "body"}}`` by its ``## `` sections
    (see :func:`note_key`; other sections keep their own title). Text before the first section
    counts as the summary; a leading ``# `` title line is dropped; fenced code is left intact."""
    sections: dict = {}
    key, title, buf, fence = "summary", "Summary", [], False

    def flush():
        body = "\n".join(buf).strip()
        if body:
            if key in sections:
                sections[key]["body"] += "\n\n" + body
            else:
                sections[key] = {"title": _TITLES.get(key, title), "body": body}

    for line in (text or "").splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        m = None if fence else re.match(r"^##\s+(.+?)\s*#*\s*$", line)
        if m:
            flush()
            title, buf = m.group(1).strip(), []
            key = note_key(title)
        elif not fence and re.match(r"^#\s+\S", line) and not sections and not "".join(buf).strip():
            continue
        else:
            buf.append(line)
    flush()
    return sections


def screen_note(sections: dict) -> tuple:
    """Apply the P0 memory policy to the note line by line — it is injected into later sessions —
    → ``(sections, dropped lines, credentials redacted)``: instruction-like and security-sensitive
    lines are dropped, credentials redacted (in the dropped lines too, which are reported)."""
    out, dropped, redacted = {}, [], 0
    for key, sec in sections.items():
        keep = []
        for line in sec["body"].splitlines():
            clean, kinds = redact_secrets(line)
            redacted += len(kinds)
            if is_unsafe_text(line):
                dropped.append(clean.strip())
            else:
                keep.append(clean)
        body = "\n".join(keep).strip()
        if body:
            out[key] = {"title": sec["title"], "body": body}
    return out, dropped, redacted


_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)$")


def note_items(body: str) -> list:
    """The top-level list items of a note section, continuation lines joined (a paragraph before
    the list counts as an item)."""
    items: list = []
    for line in (body or "").splitlines():
        if not line.strip():
            continue
        m = _ITEM.match(line)
        if m and len(line) - len(line.lstrip()) < 2:
            items.append(m.group(1).strip())
        elif items:
            items[-1] += " " + line.strip()
        else:
            items.append(line.strip())
    return items


def next_query(sections: dict) -> str:
    """The first "Next" item — the task the next session starts with (the memory / page query)."""
    items = note_items((sections.get("next") or {}).get("body", ""))
    return items[0][:400] if items else ""


# -- memory --------------------------------------------------------------------

def memory_changes(graph, since: int, session_id: str = "", limit: int = 10) -> dict:
    """What the memory learned and dropped since ``since`` (read-only): the facts recorded since —
    counted by origin: the session's own captures, the agent's ``wiki_remember`` writes, other
    sessions — and the facts closed or retracted since, each newest first."""
    from .graph.memory import fact_line
    recs = graph.list_assertions(limit=10 ** 9, include_superseded=True)
    new = sorted((r for r in recs if (r.get("created_at") or 0) >= since and r.get("status") != "forgotten"),
                 key=lambda r: -(r.get("created_at") or 0))

    def ended(r) -> int:
        return max(r.get("valid_to") or 0, r.get("expired_at") or 0)
    closed = sorted((r for r in recs if r.get("status") in ("past", "retracted") and ended(r) >= since),
                    key=lambda r: -ended(r))
    groups = {"session": 0, "agent": 0, "other": 0}
    for r in new:
        sid = str(r.get("session_id") or "")
        groups["session" if session_id and sid == session_id
               else "agent" if sid.startswith("agent-") else "other"] += 1
    mark = {"past": "  [superseded]", "retracted": "  [retracted]"}
    return {"new_count": len(new), "groups": groups, "new": [fact_line(r) for r in new[:limit]],
            "closed_count": len(closed),
            "closed": [fact_line(r, mark.get(r.get("status"), "")) for r in closed[:limit]]}


def queued_writes(graph_path, limit: int = 10) -> dict:
    """The memory writes waiting in the journal for the next write pass (session end, ``sleep``):
    ops and facts by origin (capture / the agent's ``wiki_remember``), facts they close, and the
    agent's facts — its recorded decisions and new states."""
    try:
        from .graph.journal import journal_path, read_journal
        recs = read_journal(journal_path(graph_path))
    except Exception:
        return {"ops": 0}
    out = {"ops": len(recs), "facts": 0, "retire": 0, "agent_ops": 0, "capture_ops": 0,
           "reindex": 0, "agent_facts": []}
    for r in recs:
        if r.get("op") != "remember":
            out["reindex"] += 1
            continue
        facts = [f for f in (r.get("facts") or []) if isinstance(f, list) and len(f) >= 3]
        out["facts"] += len(facts)
        out["retire"] += len(r.get("retire") or [])
        if r.get("agent"):
            out["agent_ops"] += 1
            out["agent_facts"] += [f"- {f[0]} {f[1]} {f[2]}" for f in facts]
        else:
            out["capture_ops"] += 1
    out["agent_facts"] = out["agent_facts"][-limit:]
    return out


def next_memory(graph, embedder, query: str, k: int = 8, max_chars: int = NEXT_MEMORY_CHARS) -> str:
    """The remembered facts (+ themes) relevant to the next task — what ``wiki_memory`` would load
    for it — without the identity block (fail-soft: ``""``)."""
    if graph is None or embedder is None or not str(query).strip():
        return ""
    try:
        ctx = graph.context_for(query, embedder, identity="", k=k, max_chars=max_chars) or ""
    except Exception:
        return ""
    lines = []
    for line in ctx.splitlines():
        if line.startswith("## What I remember"):
            continue
        lines.append(line[3:].rstrip() + ":" if line.startswith("## ") else line)
    return "\n".join(lines).strip()


def relevant_pages(index, query: str, k: int = 6) -> list:
    """The wiki pages (for a code corpus: the files) most relevant to the next task (fail-soft)."""
    if index is None or not str(query).strip():
        return []
    try:
        hits = index.search(query, k=k * 4)
    except Exception:
        return []
    seen, out = set(), []
    for h in hits:
        if h.page_slug not in seen:
            seen.add(h.page_slug)
            out.append(h.page_title or h.page_slug)
    return out[:k]


# -- prepare / resume ----------------------------------------------------------

def prepare(env: HandoffEnv, note: Optional[str] = None, *, transcript=None,
            session_id: Optional[str] = None, out=None, now: Optional[int] = None) -> dict:
    """Gather a handoff (not yet written — :func:`save_handoff`): the agent's ``note`` (screened by the
    P0 policy; without one the last handoff's Next / Open threads / prompts carry over) plus the
    repository, memory and environment state since the session started or the last handoff, whichever
    is later, and the memory + pages for the first "Next" item."""
    now = int(time.time() if now is None else now)
    project = env.project
    prev = load_handoff(handoff_dir(project, out))
    tpath = Path(transcript) if transcript else find_transcript(env.repo, session_id)
    if tpath is not None and not tpath.is_file():
        tpath = None
    sid = session_id or (tpath.stem if tpath else "")
    mark = capture_watermark(project.state_dir, sid) if sid else ""
    stats = transcript_stats(tpath.read_text(encoding="utf-8", errors="ignore"), after=mark) if tpath else {}
    started = _epoch(stats.get("first_ts"))
    prev_at = int(prev.get("created_at") or 0) if prev else 0
    floor = now - MAX_WINDOW_DAYS * 86400
    if prev_at and prev_at >= floor and (not started or prev_at > started):
        since, since_label = prev_at, f"since the last handoff ({_when(prev_at)})"
    elif started and started >= floor:
        since, since_label = started, f"since the session start ({_when(started)})"
    else:
        since, since_label = floor, f"in the last {MAX_WINDOW_DAYS} days"

    repo = git_state(env.repo)
    commits, total = git_commits(repo["root"], since=since) if repo else ([], 0)
    sections, dropped, redacted, carried_from = {}, [], 0, None
    if note and note.strip():
        sections, dropped, redacted = screen_note(parse_note(note))
    elif prev and prev.get("note"):
        sections = {k: v for k, v in prev["note"].items() if k in _CARRY}
        carried_from = prev_at or None
    query = next_query(sections)

    memory = None
    if project.memory_enabled:
        memory = {"queued": queued_writes(project.graph_path),
                  "capture": {"watermark": mark, "last_turn": stats.get("last_ts", ""),
                              "pending_turns": stats.get("after", 0)}}
        if env.graph is not None:
            try:
                memory["changes"] = memory_changes(env.graph, since, sid)
            except Exception as exc:          # an old graph without the memory tables
                memory["error"] = str(exc)[:160]
    log_offset = ((((prev or {}).get("env") or {}).get("hook_log")) or {}).get("offset")
    return {
        "version": 1, "created_at": now, "project": project.name, "project_root": str(project.root),
        "cwd": str(Path(env.repo).resolve()),
        "session": {"id": sid, "transcript": str(tpath) if tpath else "", **stats},
        "since": since, "since_label": since_label,
        "repo": repo, "commits": commits, "commits_total": total,
        "note": sections, "dropped": dropped, "redacted": redacted, "carried_from": carried_from,
        "memory": memory, "env": environment(env, log_offset, repo.get("root")),
        "next_query": query,
        "next_memory": next_memory(env.graph, env.embedder, query) if memory is not None else "",
        "pages": relevant_pages(env.index, query),
        "resumed": [],
    }


def resume(env: HandoffEnv, *, out=None, now: Optional[int] = None, max_chars: Optional[int] = None,
           record_session: Optional[str] = None) -> str:
    """The resume brief for the latest handoff (``""`` if there is none): its Next, Summary, Decisions,
    Open threads and prompts, what changed since in the repository, the memory and the environment, and
    — computed fresh — the memory and pages for the first "Next" item. ``record_session`` (the hook's
    new session) is noted in ``handoff.json``, so a later session sees the handoff was already picked up."""
    folder = handoff_dir(env.project, out)
    h = load_handoff(folder)
    if h is None:
        return ""
    now = int(time.time() if now is None else now)
    created = int(h.get("created_at") or 0)
    repo = git_state(env.repo)
    head0 = (h.get("repo") or {}).get("head") or ""
    commits, total, diverged = [], 0, False
    if repo and head0 and repo.get("head") != head0:
        if git_is_ancestor(repo["root"], head0):
            commits, total = git_commits(repo["root"], rev_range=f"{head0}..HEAD")
        else:
            diverged = True
            commits, total = git_commits(repo["root"], since=created)
    memory = None
    if env.project.memory_enabled:
        memory = {"queued": queued_writes(env.project.graph_path)}
        if env.graph is not None:
            try:
                memory["changes"] = memory_changes(env.graph, created, (h.get("session") or {}).get("id", ""))
            except Exception as exc:
                memory["error"] = str(exc)[:160]
    query = h.get("next_query") or ""
    state = {"now": now, "repo": repo, "commits": commits, "commits_total": total, "diverged": diverged,
             "memory": memory,
             "env": environment(env, ((h.get("env") or {}).get("hook_log") or {}).get("offset"),
                                (repo or {}).get("root")),
             "next_memory": next_memory(env.graph, env.embedder, query) if memory is not None else "",
             "pages": relevant_pages(env.index, query)}
    others = [r for r in (h.get("resumed") or []) if r.get("session") and r.get("session") != record_session]
    brief = render_brief(h, state, others=others, path=folder / HANDOFF_MD, max_chars=max_chars)
    if record_session:
        h["resumed"] = (others + [{"session": record_session, "at": now}])[-10:]
        try:
            _write_atomic(folder / HANDOFF_JSON, json.dumps(h, ensure_ascii=False, indent=1))
        except OSError:
            pass
    return brief


# -- rendering -----------------------------------------------------------------

def _sync_text(repo: dict) -> str:
    up = repo.get("upstream")
    if not up:
        return "no upstream branch"
    ahead, behind = repo.get("ahead", 0), repo.get("behind", 0)
    if not ahead and not behind:
        return f"in sync with {up}"
    parts = [f"{ahead} commit(s) not pushed to {up}"] if ahead else []
    if behind:
        parts.append(f"{behind} behind {up}")
    return ", ".join(parts)


def _dirty_text(repo: dict) -> str:
    n = repo.get("dirty_count", 0)
    return "working tree clean" if not n else f"{n} uncommitted file(s)"


def _origin_text(groups: dict, own: str) -> str:
    parts = [f"{groups['session']} captured from {own}" if groups.get("session") else "",
             f"{groups['agent']} recorded by the agent" if groups.get("agent") else "",
             f"{groups['other']} from other sessions" if groups.get("other") else ""]
    return ", ".join(p for p in parts if p)


def _queued_text(q: dict) -> str:
    if not q.get("ops"):
        return "no writes queued"
    parts = [f"{q.get('facts', 0)} fact(s)"]
    if q.get("retire"):
        parts.append(f"closing {q['retire']}")
    return (f"{q['ops']} write(s) queued ({', '.join(parts)}) — they land at the next write pass "
            "(session end or `owiki sleep`)")


def _env_summary(env: dict) -> str:
    o = env.get("ollama") or {}
    if not o.get("reachable"):
        parts = [f"Ollama NOT reachable at {o.get('host')}"]
    else:
        missing = [m for m, ok in (o.get("models") or {}).items() if not ok]
        parts = ["Ollama ok" + (f" but not pulled: {', '.join(missing)}" if missing else "")]
    parts.append(f"graph {env.get('graph')}")
    wiki = env.get("wiki") or {}
    if wiki.get("commits_since"):
        parts.append(f"wiki index {wiki['commits_since']} commit(s) behind the repository (`owiki build`)")
    workers = env.get("workers") or []
    if workers:
        parts.append("capture worker running (session " + ", ".join(w["session"][:8] for w in workers)
                     + ") — its facts are still landing")
    problems = (env.get("hook_log") or {}).get("problems") or []
    parts.append(f"hook log: {len(problems)} problem(s)" if problems else "hook log: no problems")
    return " · ".join(parts)


def _repo_lines(repo: dict, commits: list, total: int, since_label: str) -> list:
    if not repo:
        return ["- not a git repository"]
    out = [f"- `{repo.get('branch')}` at `{str(repo.get('head'))[:7]}` — {repo.get('subject')}"
           + (f" (`{repo['describe']}`)" if repo.get("describe") else ""),
           f"- {_sync_text(repo)}; {_dirty_text(repo)}"]
    out += [f"  - `{line}`" for line in repo.get("dirty") or []]
    if total:
        out.append(f"- {total} commit(s) {since_label}:")
        out += [f"  - {c}" for c in commits]
        if total > len(commits):
            out.append(f"  - … and {total - len(commits)} more")
    else:
        out.append(f"- no commits {since_label}")
    return out


def _memory_lines(h: dict) -> list:
    mem = h.get("memory") or {}
    sid = (h.get("session") or {}).get("id", "")
    out = []
    ch = mem.get("changes")
    if ch is not None:
        origin = _origin_text(ch["groups"], "this session")
        out.append(f"- Learned {h.get('since_label')}: {ch['new_count']} fact(s)"
                   + (f" — {origin}" if origin else "") + (", newest first:" if ch["new"] else ""))
        out += [f"  {line}" for line in ch["new"]]
        if ch["closed_count"]:
            out.append(f"- Closed or retracted since then: {ch['closed_count']}")
            out += [f"  {line}" for line in ch["closed"]]
    elif mem.get("error"):
        out.append(f"- Memory not readable: {mem['error']}")
    q = mem.get("queued") or {}
    queued = _queued_text(q)
    out.append("- " + queued[0].upper() + queued[1:])
    out += [f"  {line}" for line in q.get("agent_facts") or []]
    cap = mem.get("capture") or {}
    if sid:
        pending = cap.get("pending_turns", 0)
        last = _when(_epoch(cap.get("watermark"))) if cap.get("watermark") else "none yet"
        out.append(f"- Capture of this session: last captured turn {last}; "
                   + (f"{pending} turn(s) since — captured at session end" if pending
                      else "every turn captured"))
    return out


def _env_lines(env: dict) -> list:
    o = env.get("ollama") or {}
    if o.get("reachable"):
        models = ", ".join(f"{m} ({'pulled' if ok else 'NOT pulled'})" for m, ok in (o.get("models") or {}).items())
        out = [f"- Ollama at {o.get('host')}: reachable" + (f" — {models}" if models else "")]
    else:
        out = [f"- Ollama at {o.get('host')}: NOT reachable ({o.get('error', '')})"]
    out.append(f"- Graph: {env.get('graph')}")
    wiki = env.get("wiki")
    if wiki:
        behind = wiki.get("commits_since", 0)
        out.append(f"- Wiki index (built from this repository): {_when(wiki.get('built'))} — "
                   + (f"{behind} commit(s) since; `owiki build` refreshes it" if behind else "up to date"))
    workers = env.get("workers") or []
    out.append("- Capture workers: " + (", ".join(f"session {w['session'][:8]} since {_when(w['since'])}"
                                                  for w in workers) if workers else "none running"))
    problems = (env.get("hook_log") or {}).get("problems") or []
    out.append("- Hook log: " + ("problems:" if problems else "no problems"))
    out += [f"  - {line}" for line in problems]
    out.append(f"- openwiki {env.get('openwiki')} · Python {env.get('python')}")
    return out


def render_handoff(h: dict) -> str:
    """The handoff as Markdown (``HANDOFF.md``): the agent's note, then the derived state."""
    s, repo = h.get("session") or {}, h.get("repo") or {}
    meta = []
    if s.get("id"):
        span = (f" — {_when(_epoch(s.get('first_ts')))} → {_when(_epoch(s.get('last_ts')))}, "
                f"{s.get('user_turns', 0)} user turns, {s.get('compactions', 0)} compactions"
                if s.get("first_ts") else "")
        meta.append(f"session `{s['id'][:8]}`{span}")
    if repo:
        meta.append(f"`{repo.get('branch')}` at `{str(repo.get('head'))[:7]}`"
                    + (f" (`{repo['describe']}`)" if repo.get("describe") else ""))
    meta.append(f"project `{h.get('project')}`")
    lines = [f"# Session handoff — {_when(h.get('created_at'))}", "", " · ".join(meta), ""]
    if h.get("carried_from"):
        lines += [f"*No new note — Next, Open threads and prompts carried over from the handoff of "
                  f"{_when(h['carried_from'])}.*", ""]
    note = h.get("note") or {}
    for key, title in NOTE_SECTIONS:
        if key in note:
            lines += [f"## {title}", "", note[key]["body"], ""]
    for key, sec in note.items():
        if key not in _TITLES:
            lines += [f"## {sec['title']}", "", sec["body"], ""]
    if h.get("dropped"):
        lines += [f"*{len(h['dropped'])} line(s) of the note left out by the memory policy "
                  "(security-sensitive — restate them in the session).*", ""]
    if h.get("redacted"):
        lines += [f"*{h['redacted']} credential(s) redacted from the note.*", ""]
    lines += ["## Repository", ""] + _repo_lines(repo, h.get("commits") or [], h.get("commits_total", 0),
                                                 h.get("since_label", "")) + [""]
    if h.get("memory") is not None:
        lines += ["## Memory", ""] + _memory_lines(h) + [""]
    lines += ["## Environment", ""] + _env_lines(h.get("env") or {}) + [""]
    if h.get("next_memory"):
        lines += ["## Memory for the next task", "", h["next_memory"], ""]
    if h.get("pages"):
        lines += ["## Relevant files / pages", ""] + [f"- {p}" for p in h["pages"]] + [""]
    return redact_secrets("\n".join(lines).rstrip() + "\n")[0]     # a last pass over the derived parts too


def _since_lines(h: dict, state: dict) -> list:
    out = []
    repo, r0 = state.get("repo") or {}, h.get("repo") or {}
    if repo:
        head0, head = str(r0.get("head") or "")[:7], str(repo.get("head") or "")[:7]
        if state.get("diverged"):
            first = f"HEAD moved off the handoff's {head0} (another branch or rewritten history) — now {repo.get('branch')} {head}"
        elif not head0 or head0 == head:
            first = f"no new commits ({repo.get('branch')} {head})"
        else:
            first = f"{state.get('commits_total', 0)} new commit(s), {repo.get('branch')} {head0} → {head}"
        parts = [first, _dirty_text(repo), _sync_text(repo)] + ([repo["describe"]] if repo.get("describe") else [])
        out.append("- Repository: " + " · ".join(parts))
        out += [f"  - {c}" for c in (state.get("commits") or [])[:8]]
    mem = state.get("memory")
    if mem is not None:
        ch = mem.get("changes")
        if ch is None:
            line = f"- Memory: not readable now ({mem.get('error') or state['env'].get('graph')})"
        else:
            origin = _origin_text(ch["groups"], "the handoff's session")
            line = (f"- Memory: {ch['new_count']} fact(s) learned" + (f" ({origin})" if origin else "")
                    + f", {ch['closed_count']} closed")
        out.append(line + " · " + _queued_text(mem.get("queued") or {}))
    out.append("- Environment: " + _env_summary(state.get("env") or {}))
    out += [f"  - {p}" for p in ((state.get("env") or {}).get("hook_log") or {}).get("problems") or []]
    return out


def _cut(text: str, n: int) -> str:
    """``text`` cut to at most ``n`` characters at a line (else word) boundary, marked with ``…``."""
    if len(text) <= n:
        return text
    if n <= 1:
        return ""
    head = text[: n - 1]
    for sep in ("\n", " "):
        at = head.rfind(sep)
        if at > n // 2:
            head = head[:at]
            break
    return head.rstrip() + "…"


def _fit(blocks: list, tail: str = "", max_chars: Optional[int] = None) -> str:
    """Join ``blocks`` (in priority order) + ``tail`` within ``max_chars``: blocks that don't fit are
    cut or left out; the first two (header and Next) are always kept, cut if need be."""
    if max_chars is None:
        return "\n\n".join(b for b in blocks + [tail] if b)
    budget = max(0, int(max_chars) - (len(tail) + 2 if tail else 0))
    out, used = [], 0
    for i, block in enumerate(blocks):
        sep = 2 if out else 0
        if used + sep + len(block) <= budget:
            out.append(block)
            used += sep + len(block)
        elif i < 2 or budget - used - sep >= 200:
            cut = _cut(block, max(0, budget - used - sep))
            if cut:
                out.append(cut)
                used += sep + len(cut)
    return "\n\n".join(out + ([tail] if tail else []))


def render_brief(h: dict, state: dict, others=(), path=None, max_chars: Optional[int] = None) -> str:
    """The resume brief: header, Next, what changed since the handoff, then (as room allows) Summary,
    Decisions, Open threads, prompts, relevant files and the memory for the next task."""
    now, created = state.get("now") or int(time.time()), int(h.get("created_at") or 0)
    sid, r0 = (h.get("session") or {}).get("id", ""), h.get("repo") or {}
    header = (f"Handoff from {_when(created)} ({_ago(created, now)})"
              + (f", session {sid[:8]}" if sid else "")
              + (f", {r0.get('branch')} at {str(r0.get('head'))[:7]}" if r0 else "") + ".")
    if h.get("carried_from"):
        header += f" Its Next was carried over from the handoff of {_when(h['carried_from'])}."
    if others:
        header += (f" Already resumed by {len(others)} session(s) since (last {_when(others[-1].get('at'))})"
                   " — some of Next may be done.")
    note = h.get("note") or {}
    blocks = [header,
              "Next (start here):\n" + note["next"]["body"] if "next" in note
              else "Next: the handoff names no next step.",
              "Since the handoff:\n" + "\n".join(_since_lines(h, state))]
    for key, label, cap in (("summary", "Summary", 900), ("decisions", "Decisions", 700),
                            ("threads", "Open threads", None), ("prompts", "Ready-to-use prompts", None)):
        if key in note:
            body = note[key]["body"]
            blocks.append(f"{label}:\n" + (_cut(body, cap) if cap else body))
    if state.get("pages"):
        blocks.append("Relevant files: " + " · ".join(state["pages"]))
    if state.get("next_memory"):
        blocks.append("Memory for the next task:\n" + state["next_memory"])
    blocks = [redact_secrets(b)[0] for b in blocks]       # a last pass over the derived parts too
    return _fit(blocks, f"Full handoff: {path}" if path else "", max_chars)
