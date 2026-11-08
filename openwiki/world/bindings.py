"""Environments and bindings — what a project observes, and which remembered facts each observation governs (Path C,
C1.2; ``docs/path-c-world-model.md`` §2.3, §6.2).

Parsed from the manifest's ``[world]``, ``[[environments]]`` and ``[[world.sensors]]``, plus a small default set for
code sources — the version file when one is found, the latest version tag — and validated: an unknown type or
environment, a path outside the environment, a non-localhost URL or a broken pattern is reported, never run. A binding
governs facts by a **pattern over their wording** (``"subject | predicate | object"``) with exclusions — C0.4 measured
attribute keys and found they reach almost nothing. Before a binding may close a fact (v0.124) a person accepts the
list of facts it governs, which ``owiki world status`` shows. Pure apart from looking for a version file and tags."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..handoff import git as _git
from .sensors import (GIT_READS, GPU_READS, LATER_TYPES, LOCAL_HOSTS, OLLAMA_READS, SLOW_TYPES, TYPES, _VERSION_TAG,
                      _toml, regex_flags, render)

KINDS = ("repository", "twin")
DEFAULT_EVERY = 60                      # seconds — a fast sensor at most once a minute (§6.2's example)
VERSION = r"v?\d+\.\d+(?:\.\d+)?"
VERSION_PREDICATES = (r"is versioned|has version|has latest version|has release|current release is|latest release is"
                      r"|version is|latest (?:release )?tag(?: is)?")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_ENV_VAR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EVERY = re.compile(r"^(\d+(?:\.\d+)?)\s*([smhd])?$")
_UNITS = {None: 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
_NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())}
_BINDING_KEYS = frozenset({"id", "environment", "type", "fact", "governs", "excludes", "belief", "every"})
_REQUIRED = {"file-pattern": ("path", "pattern"), "py-constant": ("path", "name"), "toml-key": ("path", "key"),
             "count": ("pattern",), "http-json": ("url",)}


@dataclass(frozen=True)
class Environment:
    name: str
    kind: str = "repository"            # repository | twin
    root: Optional[Path] = None         # a repository: the code source it reads
    url: str = ""                       # a twin: its MCP endpoint (localhost only, ADR-54)
    token_env: str = ""                 # … and the variable that holds its token — never the manifest
    command: tuple = ()                 # only for a twin OpenWiki owns (a simulator a test starts)
    subject: str = ""                   # whom its facts are about (default: the folder's name)
    defaults: bool = True               # a repository's default sensors (version file, latest tag)

    def describe(self) -> str:
        return str(self.root) if self.kind == "repository" else (self.url or " ".join(self.command))


@dataclass
class Binding:
    id: str
    environment: str
    type: str
    params: dict = field(default_factory=dict)
    subject: str = ""
    predicate: str = ""
    object: str = "{value}"             # the fact's object; {value} = the reading, rendered
    governs: str = ""                   # a pattern over "subject | predicate | object" (case-insensitive)
    excludes: str = ""                  # … minus these: history, measurements, statements about documents
    belief: str = ""                    # what a governed fact claims: a pattern with a group "value"
    every: int = DEFAULT_EVERY          # seconds between readings; 0 = slow passes only
    default: bool = False               # a code source's default sensor

    @property
    def slow(self) -> bool:
        return self.type in SLOW_TYPES

    def fact(self, value) -> tuple:
        return (self.subject, self.predicate, self.object.format(value=render(value)))

    def target(self) -> str:
        p = self.params
        if self.type in ("file-pattern", "py-constant", "toml-key"):
            return " ".join(str(x) for x in (p.get("path"), p.get("name") or p.get("key") or "") if x)
        if self.type == "count":
            paths = p.get("paths") or p.get("path") or ""
            return ", ".join(paths) if isinstance(paths, list) else str(paths)
        if self.type in ("git", "gpu"):
            return str(p.get("read") or ("head" if self.type == "git" else "total"))
        if self.type == "ollama":
            return " ".join(str(x) for x in (p.get("read") or "version", p.get("model") or "") if x)
        if self.type == "http-json":
            return " ".join(str(x) for x in (p.get("url"), p.get("key") or "") if x)
        if self.type == "process":
            return f"port {p['port']}" if p.get("port") is not None else f"lock {p.get('lock')}"
        if self.type == "pytest-collect":
            return ", ".join(p.get("paths") or []) or "the test suite"
        return ""

    def variable(self) -> dict:
        """Path C (C2.2): the ``Variable`` this binding observes — its id is the attribute key the binding's
        observations carry (``sensor:<id>``), ``paths`` the files it reads (a code corpus has a page for each, its
        ``DESCRIBED_IN``). ``kind``: version, count, state or setting."""
        p = self.params
        raw = (p.get("paths") or p.get("path")) if self.type == "count" else p.get("path")
        paths = [raw] if isinstance(raw, str) else list(raw or [])
        paths = [q[2:] if q.startswith("./") else q for q in (str(x).replace("\\", "/") for x in paths)
                 if "*" not in q and "?" not in q]
        wording = f"{self.subject} {self.predicate} {self.object}".lower()
        named = str(p.get("name") or p.get("key") or "")
        if (self.type == "git" and p.get("read") == "latest-tag") or re.search(VERSION_PREDICATES, wording) or (
                self.type in ("py-constant", "toml-key") and "version" in named):
            kind = "version"
        elif self.type in ("count", "pytest-collect") or (self.type == "git" and p.get("read") in
                                                          ("dirty", "ahead", "behind")):
            kind = "count"
        elif self.type in ("py-constant", "toml-key", "file-pattern"):
            kind = "setting"
        else:
            kind = "state"
        return {"id": f"sensor:{self.id}", "label": f"{self.subject} {self.predicate}".strip(),
                "subject": self.subject, "predicate": self.predicate, "unit": "",
                "sensor": f"{self.type} {self.target()}".strip(), "kind": kind, "environment": self.environment,
                "paths": paths}

    def governs_text(self, text: str) -> bool:
        """Does this binding govern a fact worded ``text`` (``"subject | predicate | object"``)?"""
        if not self.governs or not re.search(self.governs, text, re.I):
            return False
        return not (self.excludes and re.search(self.excludes, text, re.I))


@dataclass
class World:
    enabled: bool = False
    memory: bool = False
    environments: list = field(default_factory=list)
    bindings: list = field(default_factory=list)
    laws: list = field(default_factory=list)          # Path C (C2.3): world.laws.Law, the manifest's rules
    problems: list = field(default_factory=list)      # validation errors — one line each

    def environment(self, name: str) -> Optional[Environment]:
        return next((e for e in self.environments if e.name == name), None)


def parse_every(value) -> int:
    """``"1m"`` / ``"30s"`` / ``"2h"`` / ``"1d"`` / ``0`` / ``90`` → seconds (``0`` = slow passes only)."""
    if isinstance(value, bool):
        raise ValueError(f"every = {value!r}: use seconds or 30s / 5m / 2h / 1d")
    if isinstance(value, (int, float)):
        if value < 0:
            raise ValueError(f"every = {value!r}: not negative")
        return int(value)
    m = _EVERY.match(str(value).strip().lower())
    if not m:
        raise ValueError(f"every = {value!r}: use seconds or 30s / 5m / 2h / 1d")
    return int(float(m.group(1)) * _UNITS[m.group(2)])


def is_local_url(url: str) -> bool:
    from urllib.parse import urlsplit
    parts = urlsplit(str(url or ""))
    return parts.scheme in ("http", "https") and (parts.hostname or "").lower() in LOCAL_HOSTS


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "-", str(name).lower()).strip("-") or "repository"


def _names(subject: str) -> str:
    """A regex alternation of the ways a subject is written: as given, lowercased, without separators."""
    forms = {subject, subject.lower(), re.sub(r"[\s_-]+", "", subject)}
    return "|".join(sorted((re.escape(f) for f in forms if f), key=len, reverse=True))


def _compiles(pattern: str, flags: int = 0) -> Optional[str]:
    try:
        re.compile(pattern, flags)
    except re.error as exc:
        return str(exc)
    return None


# -- environments ----------------------------------------------------------------------------------

def _environments(project, problems: list) -> list:
    code = [Path(p).resolve() for p in project.code_paths() if not str(p).lower().startswith(("http://", "https://"))]
    raw = project.environments
    if not raw:                           # no [[environments]]: each code source is a repository environment
        return [Environment(name=_slug(root.name), kind="repository", root=root, subject=root.name) for root in code]
    out, names = [], set()
    for n, entry in enumerate(raw, 1):
        name = str(entry.get("name") or "").strip()
        if not _ID.match(name):
            problems.append(f"[[environments]] #{n}: name {name!r} — letters, digits and - _ . : only")
            continue
        if name in names:
            problems.append(f"environment {name}: declared twice")
            continue
        names.add(name)
        kind = str(entry.get("kind") or "repository")
        if kind not in KINDS:
            problems.append(f"environment {name}: kind {kind!r} — use repository or twin")
            continue
        if "token" in entry:
            problems.append(f"environment {name}: a token never goes in the manifest — name the variable that holds "
                            f"it with token_env (§6.6)")
            continue
        if kind == "repository":
            if not entry.get("source"):
                problems.append(f"environment {name}: a repository needs source = the path of a code source")
                continue
            root = Path(project._resolve_path(str(entry["source"]))).resolve()
            if root not in code:
                problems.append(f"environment {name}: {entry['source']} is not one of the project's code sources "
                                f"([[sources]] with type = \"code\")")
                continue
            out.append(Environment(name=name, kind=kind, root=root, subject=str(entry.get("subject") or root.name),
                                   defaults=bool(entry.get("defaults", True))))
            continue
        url, token_env = str(entry.get("url") or ""), str(entry.get("token_env") or "")
        command = entry.get("command") or ()
        command = tuple(str(c) for c in command) if isinstance(command, (list, tuple)) else (str(command),)
        if not url and not command:
            problems.append(f"environment {name}: a twin needs url = its MCP endpoint on localhost (ADR-54)")
            continue
        if url and not is_local_url(url):
            problems.append(f"environment {name}: {url} — a twin is reached on localhost only (ADR-54)")
            continue
        if token_env and not _ENV_VAR.match(token_env):
            problems.append(f"environment {name}: token_env {token_env!r} is not a variable name")
            continue
        out.append(Environment(name=name, kind=kind, url=url, token_env=token_env, command=command,
                               subject=str(entry.get("subject") or name)))
    return out


# -- default sensors for a code source ----------------------------------------------------------------

def _version_file(root: Path) -> Optional[tuple]:
    """``(type, params)`` of the sensor that reads a repository's version, or ``None``."""
    for rel, keys in (("pyproject.toml", ("project.version", "tool.poetry.version")), ("Cargo.toml",
                                                                                     ("package.version",))):
        path = root / rel
        if not path.is_file():
            continue
        try:
            data = _toml.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for key in keys:
            cur = data
            for part in key.split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
            if isinstance(cur, str) and cur.strip():
                return "toml-key", {"path": rel, "key": key}
    if (root / "package.json").is_file():
        return "file-pattern", {"path": "package.json", "pattern": r'^\s*"version"\s*:\s*"(?P<value>[^"]+)"'}
    return None


def _has_version_tag(root: Path) -> bool:
    return any(_VERSION_TAG.fullmatch(t.strip()) for t in (_git(root, "tag", "--list") or "").splitlines())


def default_bindings(env: Environment, single: bool = True) -> list:
    """A repository's default sensor, ``release``: its current release — the latest version tag when the repository
    has one, else its version file (§6.2: "git state, a version file when one is found"). The tag first, because a
    version file runs ahead of the release while one is being prepared (v0.123: the bump came before the tag, and the
    file said 0.123.0 while 0.122.0 was still the release). Its governs pattern is scoped to the environment's
    subject."""
    if env.kind != "repository" or env.root is None or not env.defaults:
        return []
    prefix = "" if single else f"{env.name}-"
    subject = env.subject or env.name
    names = f"{_names(subject)}|project|package"
    common = {"id": f"{prefix}release", "environment": env.name, "subject": subject, "predicate": "current release is",
              "governs": rf"^(?:{names}) \| (?:{VERSION_PREDICATES})\b.*{VERSION}",
              "excludes": r"contains|was added|introduced", "belief": rf"(?P<value>{VERSION})", "default": True}
    if _has_version_tag(env.root):
        return [Binding(type="git", params={"read": "latest-tag"}, object="{value}", **common)]
    found = _version_file(env.root)
    if found:
        return [Binding(type=found[0], params=found[1], object="v{value}", **common)]
    return []


# -- declared sensors ---------------------------------------------------------------------------------

def _check_params(type_: str, params: dict, env: Environment) -> Optional[str]:
    """What is wrong with a sensor's type-specific settings (``None`` = nothing)."""
    for key in _REQUIRED.get(type_, ()):
        if not params.get(key):
            return f"type {type_} needs `{key}`"
    if type_ == "count" and not (params.get("paths") or params.get("path")):
        return "type count needs `paths` (a glob, or a list of them)"
    if type_ == "process" and params.get("port") is None and not params.get("lock"):
        return "type process needs `port` or `lock`"
    reads = {"git": (GIT_READS, "head"), "ollama": (OLLAMA_READS, "version"), "gpu": (GPU_READS, "total")}
    if type_ in reads and params.get("read", reads[type_][1]) not in reads[type_][0]:
        return f"read = {params.get('read')!r} — use {', '.join(reads[type_][0])}"
    if type_ == "process" and params.get("port") is not None:
        port = params["port"]
        if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
            return f"port = {port!r} — a number from 1 to 65535"
        if str(params.get("host") or "127.0.0.1").lower() not in LOCAL_HOSTS:
            return f"host = {params.get('host')!r} — ports are checked on localhost only"
    for key in ("url", "host"):
        if params.get(key) and type_ in ("http-json", "ollama") and not is_local_url(params[key]):
            return f"{key} = {params[key]!r} — sensors read http(s) on localhost only (§6.6)"
    try:
        flags = regex_flags(params.get("flags", "m"))
    except ValueError as exc:
        return str(exc)
    if params.get("pattern") and _compiles(str(params["pattern"]), flags):
        return f"pattern: {_compiles(str(params['pattern']), flags)}"
    for key in ("path", "paths", "lock", "python"):
        values = params.get(key)
        for rel in ([values] if isinstance(values, str) else list(values or []) if isinstance(values, list) else []):
            if key == "python" and Path(rel).is_absolute():
                continue                                 # an interpreter may live outside the repository
            fixed = str(rel).split("*")[0] or "."
            if Path(fixed).is_absolute() or ".." in Path(fixed).parts:
                return f"{key} = {rel!r} — paths are relative to the environment and stay inside it"
            if env.root is not None:
                base = env.root.resolve()
                resolved = (base / fixed).resolve()
                if resolved != base and base not in resolved.parents:
                    return f"{key} = {rel!r} — outside the environment ({base})"
    return None


def _declared(project, environments: list, problems: list) -> list:
    envs = {e.name: e for e in environments}
    out, ids = [], set()
    for n, entry in enumerate(project.world_sensors, 1):
        bid = str(entry.get("id") or "").strip()
        if not _ID.match(bid):
            problems.append(f"[[world.sensors]] #{n}: id {bid!r} — letters, digits and - _ . : only")
            continue
        if bid in ids:
            problems.append(f"sensor {bid}: declared twice")
            continue
        ids.add(bid)
        env_name = str(entry.get("environment") or (next(iter(envs)) if len(envs) == 1 else ""))
        env = envs.get(env_name)
        if env is None:
            problems.append(f"sensor {bid}: " + (f"unknown environment {env_name!r}" if env_name
                                                 else "name its environment (environment = …)"))
            continue
        if env.kind == "twin":
            problems.append(f"sensor {bid}: a twin's variables are read with the twin type, which arrives with C2")
            continue
        type_ = str(entry.get("type") or "")
        if type_ in LATER_TYPES:
            problems.append(f"sensor {bid}: type {type_} arrives with {LATER_TYPES[type_]}")
            continue
        if type_ not in TYPES:
            problems.append(f"sensor {bid}: unknown type {type_!r} — built-in types only: {', '.join(TYPES)}")
            continue
        params = {k: v for k, v in entry.items() if k not in _BINDING_KEYS}
        problem = _check_params(type_, params, env)
        if problem:
            problems.append(f"sensor {bid}: {problem}")
            continue
        fact = entry.get("fact")
        if not isinstance(fact, dict) or not all(isinstance(fact.get(k), str) and fact[k].strip()
                                                 for k in ("subject", "predicate", "object")):
            problems.append(f"sensor {bid}: fact = {{ subject = …, predicate = …, object = … }}, all three as text")
            continue
        try:
            fact["object"].format(value="x")
        except (KeyError, IndexError, ValueError):
            problems.append(f"sensor {bid}: the fact's object may use {{value}} only")
            continue
        broken = [f"{k}: {_compiles(str(entry[k]))}" for k in ("governs", "excludes", "belief")
                  if entry.get(k) and _compiles(str(entry[k]))]
        if broken:
            problems.append(f"sensor {bid}: " + "; ".join(broken))
            continue
        try:
            every = parse_every(entry.get("every", 0 if type_ in SLOW_TYPES else DEFAULT_EVERY))
        except ValueError as exc:
            problems.append(f"sensor {bid}: {exc}")
            continue
        out.append(Binding(id=bid, environment=env.name, type=type_, params=params, subject=fact["subject"].strip(),
                           predicate=fact["predicate"].strip(), object=fact["object"].strip(),
                           governs=str(entry.get("governs") or ""), excludes=str(entry.get("excludes") or ""),
                           belief=str(entry.get("belief") or ""), every=every))
    return out


def load_world(project) -> World:
    """The project's world model as configured: environments, bindings (declared, plus the defaults a declared
    binding of the same id replaces) and every validation problem."""
    world = World(enabled=project.world_enabled, memory=project.memory_enabled)
    if world.enabled and not world.memory:
        world.problems.append("[world] enabled needs [memory] enabled = true — observations are memory-tier facts "
                              "(§2.3)")
    world.environments = _environments(project, world.problems)
    declared = _declared(project, world.environments, world.problems)
    taken = {b.id for b in declared}
    repos = [e for e in world.environments if e.kind == "repository"]
    for env in world.environments:
        world.bindings += [b for b in default_bindings(env, single=len(repos) == 1) if b.id not in taken]
        world.bindings += [b for b in declared if b.environment == env.name]
    from .builtin import BUILTIN
    from .laws import load_laws
    world.laws, problems = load_laws(getattr(project, "world_laws", []), {b.id for b in world.bindings} | set(BUILTIN),
                                     world.environments)
    world.problems += problems
    return world


# -- what a governed fact claims -------------------------------------------------------------------------

def normalize(value) -> str:
    """A value as compared: lowercase, ``v`` before a version and thousands separators dropped, number words as
    digits, versions padded to three parts; lists as their words."""
    if isinstance(value, (list, tuple)):
        return ",".join(normalize(v) for v in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = re.sub(r"\s+", " ", str(value if value is not None else "").strip().lower())
    text = re.sub(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", lambda m: _NUMBER_WORDS[m.group(1)], text)
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    if re.fullmatch(r"v?\d+(?:\.\d+){1,3}", text):
        parts = text.lstrip("v").split(".")
        text = ".".join(parts + ["0"] * (3 - len(parts)))
    return text


def _words(text) -> list:
    return re.findall(r"[a-z0-9]+", str(text).lower())


def _listed(text) -> int:
    """How many items an object lists ("Wiki · Hilfe · Tutorial" → 3; prose → 0) — a count sensor reads such a list
    as the claim of its length, as C0.5's ground truth did (C1.10)."""
    parts = [p for p in re.split(r"\s*(?:·|,|;|/|\band\b)\s*", str(text or "").split(":", 1)[-1]) if p.strip()]
    return len(parts) if len(parts) > 1 and all(len(p.split()) <= 3 for p in parts) else 0


def compare(binding: Binding, fact: dict, value) -> str:
    """``agrees`` / ``differs`` / ``unclear`` — does the remembered ``fact`` claim the value its sensor read? Its
    claim is the binding's ``belief`` match (in the object first, else anywhere in the wording); with no belief
    pattern a fact agrees when it contains the value and is unclear otherwise — never "differs" on a guess."""
    obj = str(fact.get("object") or "")
    text = f"{fact.get('subject')} | {fact.get('predicate')} | {obj}"
    if not binding.belief:
        return "agrees" if normalize(value) and normalize(value) in normalize(text) else "unclear"
    m = re.search(binding.belief, obj, re.I) or re.search(binding.belief, text, re.I)
    if not m:
        listed = _listed(obj)
        if isinstance(value, int) and not isinstance(value, bool) and listed > 1:
            return "agrees" if listed == value else "differs"      # "tabs: Wiki · Hilfe · Tutorial" claims 3
        return "unclear"
    claimed = m.group("value") if "value" in m.re.groupindex else (m.group(1) if m.re.groups else m.group(0))
    if isinstance(value, (list, tuple)):
        return "agrees" if _words(normalize(claimed)) == _words(normalize(value)) else "differs"
    if normalize(claimed) == normalize(value):
        return "agrees"
    words = _words(normalize(value))           # the same list written another way: "a → b" / "a, b"
    return "agrees" if len(words) > 1 and _words(normalize(claimed)) == words else "differs"


def observed_claim(binding: Binding, text: str) -> str:
    """The value an observation's own wording claims — the binding's ``belief`` match in it, else the whole text —
    so a later belief can be compared with an observation the graph holds (rule 5) without its raw reading."""
    if binding.belief:
        m = re.search(binding.belief, str(text), re.I)
        if m:
            return m.group("value") if "value" in m.re.groupindex else (m.group(1) if m.re.groups else m.group(0))
    return str(text)
