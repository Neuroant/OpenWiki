"""Built-in sensors — what the world model reads (Path C, C1.1; ``docs/path-c-world-model.md`` §6.1, §6.6).

Each sensor reads one value of an environment and returns a :class:`Reading`: the value, when it was read, its quality
(``complete`` / ``partial`` / ``failed``) and — where the source knows it — when the value last changed (a commit date,
``git blame`` on the line). Built-in types only: a manifest configures a sensor but never adds a command. File paths
stay inside the environment's root, HTTP goes to localhost only, subprocesses run fixed argument lists (git,
nvidia-smi, an interpreter's pytest), never a shell, and a sensor writes nothing (pytest runs without its cache and
bytecode). String values pass credential redaction and the P0 policy like any other input (ADR-30, ADR-37). No Kuzu,
no NumPy."""

from __future__ import annotations

import ast
import glob as _glob
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

from ..handoff import _pid_alive, git as _git, ollama_state
from ..policy import is_unsafe_text, redact_secrets

try:
    import tomllib as _toml
except ModuleNotFoundError:  # Python 3.10
    import tomli as _toml  # type: ignore[no-redef]

QUALITIES = ("complete", "partial", "failed")
TYPES = ("git", "file-pattern", "py-constant", "toml-key", "count", "ollama", "http-json", "process", "gpu",
         "pytest-collect")
LATER_TYPES = {"opcua": "C4", "twin": "C2"}      # §6.1 types that arrive with a later phase
SLOW_TYPES = frozenset({"pytest-collect"})
GIT_READS = ("branch", "head", "latest-tag", "ahead", "behind", "dirty")
OLLAMA_READS = ("version", "pulled", "loaded")
GPU_READS = ("total", "free", "used")
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
HTTP_TIMEOUT = 2.0
PORT_TIMEOUT = 0.5
PYTEST_TIMEOUT = 300.0
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_VERSION_TAG = re.compile(r"v?\d+(?:\.\d+)+")
_FLAGS = {"m": re.M, "s": re.S, "i": re.I, "x": re.X}


class SensorError(Exception):
    """A sensor could not read its value — becomes the failed reading's ``detail``."""


@dataclass
class Reading:
    sensor: str
    value: object = None
    time: int = 0
    quality: str = "complete"
    changed_at: Optional[int] = None    # when the value last changed, where the source knows (commit, blame)
    source: str = ""                    # what was read: "pyproject.toml:3", "git tags", "tcp 127.0.0.1:8137"
    detail: str = ""                    # why it is partial or failed, or what screening changed
    ms: int = 0

    @property
    def ok(self) -> bool:
        return self.quality != "failed"

    def to_dict(self) -> dict:
        out = asdict(self)
        out["rendered"] = render(self.value)
        return out


def render(value) -> str:
    """A reading's value as text — what a fact's object template receives as ``{value}``."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(render(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}={render(v)}" for k, v in value.items())
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def regex_flags(spec: str = "m") -> int:
    """``"ms"`` → ``re.M | re.S`` — the flags a manifest may set on a pattern."""
    out = 0
    for ch in str(spec or ""):
        if ch not in _FLAGS:
            raise ValueError(f"unknown regex flag {ch!r} (use m, s, i, x)")
        out |= _FLAGS[ch]
    return out


def inside(root, rel) -> Path:
    """``rel`` resolved under ``root``; a :class:`SensorError` when it leaves it — ``..``, an absolute path, a link
    pointing out."""
    base = Path(root).resolve()
    path = (base / str(rel)).resolve()
    if path != base and base not in path.parents:
        raise SensorError(f"{rel}: outside the environment ({base})")
    return path


def local_url(url: str) -> str:
    """``url`` when it is http(s) to localhost — the only HTTP a sensor makes (§6.6)."""
    parts = urllib.parse.urlsplit(str(url or ""))
    if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() not in LOCAL_HOSTS:
        raise SensorError(f"{url}: sensors read http(s) on localhost only")
    return str(url).rstrip("/")


def blame_time(root, rel, line: int) -> Optional[int]:
    """The committer time of the commit that last changed line ``line`` (1-based) of ``rel`` — ``None`` when the line
    is not committed yet or git can't tell."""
    out = _git(root, "blame", "--porcelain", "-L", f"{int(line)},{int(line)}", "--", str(rel).replace("\\", "/"))
    if not out:
        return None
    head = out.split(None, 1)[0] if out.strip() else ""
    if head and set(head) == {"0"}:                      # "Not Committed Yet"
        return None
    m = re.search(r"^committer-time (\d+)$", out, re.M)
    return int(m.group(1)) if m else None


def _plain(value):
    """A literal as a reading holds it: lists as tuples, sets sorted, dicts kept."""
    if isinstance(value, (list, tuple)):
        return tuple(_plain(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(_plain(v) for v in value))
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return value


def _get_json(url: str):
    with urllib.request.urlopen(local_url(url), timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_command(args, cwd=None, timeout: float = 30.0) -> tuple:
    """``(returncode, output)`` of a fixed argument list — no shell; writes no bytecode."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        res = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, env=env,
                             creationflags=_NO_WINDOW)
    except FileNotFoundError:
        raise SensorError(f"{args[0]}: not found")
    except subprocess.TimeoutExpired:
        raise SensorError(f"{Path(str(args[0])).name}: no answer within {timeout:g} s")
    return res.returncode, (res.stdout or "") + (res.stderr or "")


# -- the readers ----------------------------------------------------------------------------------
# Each takes (root, params, ctx) and returns {"value", "changed_at", "source"} (+ "quality", "detail"), or raises
# SensorError.

def _read_git(root, params, ctx) -> dict:
    read = params.get("read", "head")
    top = _git(root, "rev-parse", "--show-toplevel")
    if not top:
        raise SensorError("not a git repository")
    if read == "branch":
        return {"value": _git(top, "rev-parse", "--abbrev-ref", "HEAD") or "", "source": "git branch"}
    if read == "head":
        stamp = _git(top, "log", "-1", "--format=%ct")
        return {"value": _git(top, "rev-parse", "--short", "HEAD") or "", "source": "git HEAD",
                "changed_at": int(stamp) if stamp and stamp.isdigit() else None}
    if read == "latest-tag":
        tags = [t.strip() for t in (_git(top, "tag", "--list", "--sort=-v:refname") or "").splitlines()]
        versions = [t for t in tags if _VERSION_TAG.fullmatch(t)]
        if not versions:
            raise SensorError("no version tag")
        stamp = _git(top, "for-each-ref", f"refs/tags/{versions[0]}", "--format=%(creatordate:unix)")
        return {"value": versions[0], "source": "git tags",
                "changed_at": int(stamp) if stamp and stamp.isdigit() else None}
    if read in ("ahead", "behind"):
        upstream = _git(top, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        if not upstream:
            raise SensorError("no upstream branch")
        counts = (_git(top, "rev-list", "--left-right", "--count", f"{upstream}...HEAD") or "").split()
        if len(counts) != 2 or not all(c.isdigit() for c in counts):
            raise SensorError(f"git can't compare with {upstream}")
        return {"value": int(counts[1] if read == "ahead" else counts[0]), "source": f"git {upstream}"}
    if read == "dirty":
        out = _git(top, "status", "--porcelain")
        if out is None:
            raise SensorError("git status failed")
        return {"value": sum(1 for line in out.splitlines() if line.strip()), "source": "git status"}
    raise SensorError(f"unknown git read {read!r} (use {', '.join(GIT_READS)})")


def _read_file_pattern(root, params, ctx) -> dict:
    rel = params["path"]
    text = inside(root, rel).read_text(encoding="utf-8", errors="replace")
    rx = re.compile(params["pattern"], regex_flags(params.get("flags", "m")))
    m = rx.search(text)
    if not m:
        raise SensorError(f"{rel}: the pattern matched nothing")
    group = "value" if "value" in rx.groupindex else (1 if rx.groups else 0)
    line = text.count("\n", 0, m.start(group)) + 1
    return {"value": m.group(group).strip(), "changed_at": blame_time(root, rel, line), "source": f"{rel}:{line}"}


def _read_py_constant(root, params, ctx) -> dict:
    rel, name = params["path"], params["name"]
    tree = ast.parse(inside(root, rel).read_text(encoding="utf-8"), filename=str(rel))
    found = None
    for node in tree.body:                               # module level only; the last assignment wins
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            found = node
    if found is None:
        raise SensorError(f"{rel}: no module-level {name}")
    try:
        value = ast.literal_eval(found.value)
    except ValueError:
        raise SensorError(f"{rel}: {name} is not a literal")
    return {"value": _plain(value), "changed_at": blame_time(root, rel, found.lineno),
            "source": f"{rel}:{found.lineno}"}


def _read_toml_key(root, params, ctx) -> dict:
    rel, key = params["path"], str(params["key"])
    text = inside(root, rel).read_text(encoding="utf-8")
    cur = _toml.loads(text)
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise SensorError(f"{rel}: no key {key}")
        cur = cur[part]
    last = re.escape(key.split(".")[-1])
    m = re.search(rf"^\s*[\"']?{last}[\"']?\s*=", text, re.M)
    line = text.count("\n", 0, m.start()) + 1 if m else None
    return {"value": _plain(cur), "changed_at": blame_time(root, rel, line) if line else None,
            "source": f"{rel}:{line}" if line else str(rel)}


def _read_count(root, params, ctx) -> dict:
    patterns = params.get("paths") or params.get("path")
    patterns = [patterns] if isinstance(patterns, str) else list(patterns or [])
    rx = re.compile(params["pattern"], regex_flags(params.get("flags", "m")))
    base = Path(root).resolve()
    seen, total = set(), 0
    for pat in patterns:
        inside(base, pat.split("*")[0] or ".")          # the fixed part of the glob stays inside
        for name in sorted(_glob.glob(str(base / pat), recursive=True)):
            path = Path(name).resolve()
            if path in seen or not path.is_file() or (path != base and base not in path.parents):
                continue
            seen.add(path)
            total += len(rx.findall(path.read_text(encoding="utf-8", errors="replace")))
    if not seen:
        raise SensorError(f"{', '.join(patterns)}: no files")
    return {"value": total, "source": f"{len(seen)} file(s)"}


def _read_ollama(root, params, ctx) -> dict:
    read = params.get("read", "version")
    host = local_url(params.get("host") or ctx.get("host") or "http://localhost:11434")
    model = str(params.get("model") or "")
    if read == "version":
        return {"value": str(_get_json(host + "/api/version").get("version") or ""), "source": "ollama /api/version"}
    if read == "pulled" and model:                     # one probe implementation, shared with the handoff
        state = ollama_state(host, [model], timeout=HTTP_TIMEOUT)
        if not state.get("reachable"):
            raise SensorError(f"ollama at {host}: {state.get('error') or 'not reachable'}")
        return {"value": bool(state["models"].get(model)), "source": "ollama /api/tags"}
    if read in ("pulled", "loaded"):
        endpoint = "/api/tags" if read == "pulled" else "/api/ps"
        names = sorted({str(m.get("name") or m.get("model") or "")
                        for m in (_get_json(host + endpoint).get("models") or []) if isinstance(m, dict)} - {""})
        if model:
            return {"value": any(n == model or n == f"{model}:latest" for n in names), "source": f"ollama {endpoint}"}
        return {"value": tuple(names), "source": f"ollama {endpoint}"}
    raise SensorError(f"unknown ollama read {read!r} (use {', '.join(OLLAMA_READS)})")


def _read_http_json(root, params, ctx) -> dict:
    url = local_url(params["url"])
    cur = _get_json(url)
    for part in [p for p in str(params.get("key") or "").split(".") if p]:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise SensorError(f"{url}: no {params.get('key')}")
    return {"value": _plain(cur), "source": url}


def _read_process(root, params, ctx) -> dict:
    if params.get("port") is not None:
        host = str(params.get("host") or "127.0.0.1")
        if host.lower() not in LOCAL_HOSTS:
            raise SensorError(f"{host}: a port is checked on localhost only")
        port = int(params["port"])
        try:
            with socket.create_connection((host, port), timeout=PORT_TIMEOUT):
                listening = True
        except OSError:
            listening = False
        return {"value": listening, "source": f"tcp {host}:{port}"}
    if params.get("lock"):
        base = Path(root).resolve()
        inside(base, str(params["lock"]).split("*")[0] or ".")
        alive = 0
        files = [Path(n) for n in _glob.glob(str(base / params["lock"]))]
        for path in files:
            try:
                m = re.search(r"\d+", path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            alive += bool(m) and _pid_alive(int(m.group(0))) is not False     # can't tell → alive, as the handoff
        return {"value": alive > 0, "source": f"{len(files)} lock file(s), {alive} alive"}
    raise SensorError("a process sensor needs a port or a lock file")


def _read_gpu(root, params, ctx) -> dict:
    read = params.get("read", "total")
    if read not in GPU_READS:
        raise SensorError(f"unknown gpu read {read!r} (use {', '.join(GPU_READS)})")
    code, out = ctx["runner"](["nvidia-smi", "--query-gpu=memory.total,memory.free,memory.used",
                               "--format=csv,noheader,nounits"], timeout=10.0)
    lines = [line for line in out.splitlines() if line.strip()]
    if code != 0 or not lines:
        raise SensorError(f"nvidia-smi: {out.strip()[:120] or f'exit {code}'}")
    total, free, used = (int(float(x)) for x in lines[0].split(",")[:3])
    return {"value": {"total": total, "free": free, "used": used}[read],
            "source": "nvidia-smi (GPU 0, MiB)" if len(lines) == 1 else f"nvidia-smi (GPU 0 of {len(lines)}, MiB)"}


def default_python(root) -> str:
    """The environment's own interpreter (``.venv``), else this one."""
    base = Path(root)
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python", "venv/Scripts/python.exe", "venv/bin/python"):
        if (base / rel).is_file():
            return str(base / rel)
    return sys.executable


def _read_pytest_collect(root, params, ctx) -> dict:
    """Opt-in (§6.6): it runs the environment's test setup, which on a foreign repository is code execution — so it
    is never a default, only a sensor a manifest declares."""
    python = params.get("python")
    python = str(inside(root, python)) if python and not Path(str(python)).is_absolute() else (python or
                                                                                              default_python(root))
    paths = [str(inside(root, p)) for p in (params.get("paths") or [])]
    code, out = ctx["runner"]([python, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *paths],
                              cwd=str(Path(root).resolve()), timeout=float(params.get("timeout") or PYTEST_TIMEOUT))
    summary = [line for line in out.splitlines() if re.search(r"\d+ tests? collected|collected \d+ items?", line)]
    if not summary:
        raise SensorError(f"pytest: {out.strip().splitlines()[-1][:160] if out.strip() else f'exit {code}'}")
    m = re.search(r"(\d+) tests? collected", summary[-1]) or re.search(r"collected (\d+) items?", summary[-1])
    errors = re.search(r"(\d+) errors?\b", summary[-1])        # "831 tests collected, 2 errors in 3.1s"
    if errors and int(errors.group(1)):
        return {"value": int(m.group(1)), "source": "pytest --collect-only", "quality": "partial",
                "detail": f"{errors.group(1)} collection error(s)"}
    return {"value": int(m.group(1)), "source": "pytest --collect-only"}


READERS: dict = {
    "git": _read_git, "file-pattern": _read_file_pattern, "py-constant": _read_py_constant,
    "toml-key": _read_toml_key, "count": _read_count, "ollama": _read_ollama, "http-json": _read_http_json,
    "process": _read_process, "gpu": _read_gpu, "pytest-collect": _read_pytest_collect,
}


def _screen(value) -> tuple:
    """Credential redaction and the P0 policy on a value's strings → ``(value, note, withheld)``."""
    found = []

    def clean(v):
        if isinstance(v, str):
            text, kinds = redact_secrets(v)
            found.extend(kinds)
            return text
        if isinstance(v, tuple):
            return tuple(clean(x) for x in v)
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        return v
    value = clean(value)
    if is_unsafe_text(render(value)):
        return None, "withheld by the memory policy", True
    return value, ("credentials redacted" if found else ""), False


def read(sensor: str, type_: str, params: dict, root=None, now=None, host: Optional[str] = None,
         runner: Optional[Callable] = None) -> Reading:
    """Read one sensor → a :class:`Reading` (never raises: a failure is a ``failed`` reading with its reason)."""
    t0 = time.perf_counter()
    stamp = int(time.time()) if now is None else int(now)
    ctx = {"host": host, "runner": runner or run_command}

    def done(**kw) -> Reading:
        return Reading(sensor=sensor, time=stamp, ms=round((time.perf_counter() - t0) * 1000), **kw)
    reader = READERS.get(type_)
    if reader is None:
        return done(quality="failed", detail=f"unknown sensor type {type_!r}")
    try:
        got = reader(Path(root) if root is not None else Path.cwd(), dict(params or {}), ctx)
    except SensorError as exc:
        return done(quality="failed", detail=str(exc)[:240])
    except Exception as exc:          # a sensor never raises — a hook must not fail on one (§6.5, later)
        return done(quality="failed", detail=f"{type(exc).__name__}: {exc}"[:240])
    value, note, withheld = _screen(got.get("value"))
    if withheld:
        return done(quality="failed", detail=note, source=got.get("source", ""))
    detail = "; ".join(x for x in (got.get("detail", ""), note) if x)
    return done(value=value, quality=got.get("quality", "complete"), changed_at=got.get("changed_at"),
                source=got.get("source", ""), detail=detail)
