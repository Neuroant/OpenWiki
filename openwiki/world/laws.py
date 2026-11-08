"""Laws — rules over the world model's variables (Path C, C2.3; ``docs/path-c-world-model.md`` §7.2).

Declarative first (ADR-51): a law is data, and its check a small expression a generic checker evaluates — parsed
with ``ast`` against a whitelist of node types, interpreted node by node, never ``eval``'d. A law counts only after
its check has failed on a planted violation (§2.2): one that can't fail is reported, never trusted. Laws only report —
a violation changes no fact and remedies nothing (decision 5); C2.6 turns it into an event.

A law in the manifest::

    [[world.laws]]
    id = "version-in-code"
    text = "The version in pyproject.toml equals openwiki.__version__."
    kind = "equality"                    # equality · comparison · implication · convention · co-change · precondition
    check = "version == code_version"
    violation = { version = "0.2.0", code_version = "0.1.0" }    # planted: the check must fail on it
    example = { version = "0.2.0", code_version = "0.2.0" }      # optional: a case it must hold on

A name in a check is a sensor's id (write ``-`` as ``_``) or a built-in variable of the environment
(``builtin.BUILTIN``: ``git_dirty``, ``wiki_commits_behind``, …). Functions: ``implies(p, q)``, ``age(x)`` (seconds
since x's value changed), ``ver(x)`` (a comparable version), ``num(x)``, ``lower(x)``, ``len(x)``, ``abs``, ``min``,
``max``, ``minutes(n)`` / ``hours(n)`` / ``days(n)``. In a fixture a value may be ``{ value = …, age = "2d" }`` for
``age()``. A **co-change** law checks the git history instead: a commit that changes a ``when`` file is followed —
within ``window`` commits, itself included — by one that changes a ``then`` file. Until a person accepts it, it
reports over the recent history (its dry run); once accepted, it watches the commits after the acceptance. Its planted
violation is a list of commits, newest first (``violation = { commits = [["x.md"], ["y.md"], ["cli.py"]] }``).
Pure apart from the git log a co-change check reads.
"""

from __future__ import annotations

import ast
import fnmatch
import re
from dataclasses import dataclass, field
from typing import Optional

KINDS = ("equality", "comparison", "implication", "convention", "co-change", "precondition")
HOLDS, VIOLATED, UNKNOWN = "holds", "violated", "unknown"
OK = "ok"
MAX_CHECK = 400                        # characters in a check
FUNCTIONS = {"implies": 2, "age": 1, "ver": 1, "num": 1, "lower": 1, "len": 1, "abs": 1, "min": None, "max": None,
             "minutes": 1, "hours": 1, "days": 1}
_NODES = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd, ast.Compare,
          ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.BinOp, ast.Add, ast.Sub,
          ast.Mult, ast.Div, ast.Mod, ast.Name, ast.Load, ast.Constant, ast.Call, ast.List, ast.Tuple)
_VERSION = re.compile(r"v?(\d+(?:\.\d+)*)")
_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$")


class CheckError(ValueError):
    """A check that is not in the language."""


class Unknown(Exception):
    """A value the check needs is not known (no reading, no change time)."""


@dataclass
class Law:
    id: str
    text: str = ""
    kind: str = "equality"
    check: str = ""
    origin: str = "manifest"
    status: str = "accepted"
    violation: dict = field(default_factory=dict)
    example: dict = field(default_factory=dict)
    when: tuple = ()                   # co-change: file globs
    then: tuple = ()
    window: int = 1
    environment: str = ""
    tree: Optional[ast.Expression] = None
    names: tuple = ()                  # the variables the check reads
    health: str = OK                   # "ok", else why it can't be trusted

    @property
    def described(self) -> str:
        """The check as shown and stored: the expression, or a co-change rule in words."""
        if self.kind == "co-change":
            return (f"{' | '.join(self.when)} changed ⇒ {' | '.join(self.then)} changed within "
                    f"{self.window} commit{'' if self.window == 1 else 's'}")
        return self.check

    def row(self) -> dict:
        """The ``Law`` node's fields (``GraphStore.upsert_laws``)."""
        return {"id": self.id, "text": self.text, "kind": self.kind, "check": self.described, "origin": self.origin,
                "status": self.status, "health": self.health}


# -- the expression language ---------------------------------------------------------------------------------------

def parse_check(text: str) -> tuple:
    """``(tree, names)`` — or ``CheckError`` for anything outside the whitelist: attributes, subscripts, lambdas,
    comprehensions, keyword arguments, unknown functions, more than ``MAX_CHECK`` characters."""
    text = str(text or "").strip()
    if not text:
        raise CheckError("an empty check")
    if len(text) > MAX_CHECK:
        raise CheckError(f"longer than {MAX_CHECK} characters")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise CheckError(f"not an expression ({exc.msg})") from None
    funcs = set()
    for node in ast.walk(tree):
        if not isinstance(node, _NODES):
            raise CheckError(f"{type(node).__name__} is not allowed in a check")
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name not in FUNCTIONS:
                raise CheckError(f"unknown function {ast.unparse(node.func)!r} (known: {', '.join(sorted(FUNCTIONS))})")
            if node.keywords:
                raise CheckError(f"{name}() takes no keyword arguments")
            if FUNCTIONS[name] is not None and len(node.args) != FUNCTIONS[name]:
                raise CheckError(f"{name}() takes {FUNCTIONS[name]} argument(s)")
            if name == "age" and not isinstance(node.args[0], ast.Name):
                raise CheckError("age() takes a variable's name")
            funcs.add(id(node.func))
        if isinstance(node, ast.Constant) and not isinstance(node.value, (str, int, float, bool, type(None))):
            raise CheckError(f"a {type(node.value).__name__} constant is not allowed")
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and id(node) not in funcs and node.id not in names:
            names.append(node.id)
    return tree, tuple(names)


def version(value) -> tuple:
    """``"v0.125.0"`` → ``(0, 125, 0)`` — padded to three parts, so 0.125 == 0.125.0."""
    m = _VERSION.search(str(value))
    if not m:
        raise Unknown(f"{value!r} is not a version")
    parts = [int(x) for x in m.group(1).split(".")]
    return tuple(parts + [0] * (3 - len(parts))) if len(parts) < 3 else tuple(parts)


def duration(value) -> float:
    """``"2d"`` / ``"36h"`` / ``"90m"`` / ``"30s"`` / ``120`` → seconds."""
    if isinstance(value, bool):
        raise ValueError(f"{value!r} is not a duration")
    if isinstance(value, (int, float)):
        return float(value)
    m = _DURATION.match(str(value))
    if not m:
        raise ValueError(f"{value!r} is not a duration (use 30s / 5m / 2h / 1d)")
    return float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def _plain(value):
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


class _Env:
    def __init__(self, values: dict, ages: dict) -> None:
        self.values, self.ages = values, ages

    def _key(self, name: str, table: dict) -> str:
        for key in (name, name.replace("_", "-")):
            if key in table:
                return key
        raise Unknown(f"no reading of {name}")

    def value(self, name: str):
        return _plain(self.values[self._key(name, self.values)])

    def age(self, name: str) -> float:
        try:
            return float(self.ages[self._key(name, self.ages)])
        except Unknown:
            raise Unknown(f"no change time for {name}") from None


def _truth(value) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{value!r} is not true or false")
    return value


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{value!r} is not a number")
    return value


_COMPARE = {ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b, ast.Lt: lambda a, b: a < b,
            ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
            ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}
_ARITH = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
          ast.Div: lambda a, b: a / b, ast.Mod: lambda a, b: a % b}


def _eval(node, env: _Env):
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return env.value(node.id)
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, env) for e in node.elts]
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, env)
        if isinstance(node.op, ast.Not):
            return not _truth(value)
        return -_number(value) if isinstance(node.op, ast.USub) else +_number(value)
    if isinstance(node, ast.BoolOp):
        want = isinstance(node.op, ast.Or)
        for e in node.values:
            if _truth(_eval(e, env)) == want:
                return want
        return not want
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, env)
            if not _COMPARE[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.BinOp):
        return _ARITH[type(node.op)](_number(_eval(node.left, env)), _number(_eval(node.right, env)))
    if isinstance(node, ast.Call):
        name, args = node.func.id, node.args
        if name == "implies":
            return True if not _truth(_eval(args[0], env)) else _truth(_eval(args[1], env))
        if name == "age":
            return env.age(args[0].id)
        values = [_eval(a, env) for a in args]
        if name == "ver":
            return version(values[0])
        if name == "num":
            if isinstance(values[0], bool):
                raise TypeError("a boolean is not a number")
            return float(values[0])
        if name == "lower":
            return str(values[0]).lower()
        if name in ("minutes", "hours", "days"):
            return _number(values[0]) * {"minutes": 60, "hours": 3600, "days": 86400}[name]
        if name == "len":
            return len(values[0])
        if name == "abs":
            return abs(_number(values[0]))
        items = values[0] if len(values) == 1 and isinstance(values[0], list) else values
        if not items:
            raise ValueError(f"{name}() of nothing")
        return min(items) if name == "min" else max(items)
    raise CheckError(f"{type(node).__name__} is not allowed")      # unreachable after parse_check


def evaluate(tree, values: dict, ages: Optional[dict] = None) -> tuple:
    """``(holds | violated | unknown, detail)`` — the check over ``values`` (``{variable: value}``) and ``ages``
    (``{variable: seconds since its value changed}``). A missing value, a type mismatch or a non-boolean result is
    *unknown*, never a violation."""
    try:
        result = _eval(tree, _Env(values or {}, ages or {}))
    except Unknown as exc:
        return UNKNOWN, str(exc)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
        return UNKNOWN, f"can't evaluate: {exc}"
    if not isinstance(result, bool):
        return UNKNOWN, f"the check gives {result!r}, not true or false"
    return (HOLDS if result else VIOLATED), ""


# -- co-change laws ------------------------------------------------------------------------------------------------

def _touches(files, globs) -> bool:
    return any(fnmatch.fnmatch(str(f).replace("\\", "/"), g) for f in files for g in globs)


def check_cochange(commits, when, then, window: int = 1, since: Optional[int] = None) -> tuple:
    """``(holds | violated, detail)`` over ``commits`` (``[(epoch, [paths])]`` or ``[[paths]]``, newest first): every
    commit after ``since`` that changes a ``when`` file is followed — itself or one of the ``window - 1`` newer commits —
    by a change to a ``then`` file. A commit whose window isn't complete yet is pending, not a violation."""
    rows = [(c[0], list(c[1])) if isinstance(c, tuple) else (None, list(c)) for c in commits or ()]
    window = max(1, int(window))
    missed, pending, checked = [], 0, 0
    for i, (at, files) in enumerate(rows):
        if since is not None and at is not None and at < since:
            continue
        if not _touches(files, when):
            continue
        checked += 1
        span = rows[max(0, i - window + 1): i + 1]                    # this commit and the newer ones after it
        if any(_touches(f, then) for _, f in span):
            continue
        if i < window - 1:                                             # its window isn't complete yet
            pending += 1
            continue
        missed.append(at)
    if missed:
        return VIOLATED, f"{len(missed)} of {checked} change(s) to {' | '.join(when)} without {' | '.join(then)}"
    if not checked:
        return HOLDS, "no change to check yet"
    return HOLDS, f"{checked} change(s) checked" + (f", {pending} pending" if pending else "")


# -- planted violations and the manifest -----------------------------------------------------------------------------

def fixture(entries: dict) -> tuple:
    """A fixture table → ``(values, ages)``: each entry a value, or ``{ value = …, age = "2d" }``."""
    values, ages = {}, {}
    for name, entry in (entries or {}).items():
        if isinstance(entry, dict):
            if "value" in entry:
                values[name] = entry["value"]
            if "age" in entry:
                ages[name] = duration(entry["age"])
        else:
            values[name] = entry
    return values, ages


def run_fixture(law: Law, entries: dict) -> tuple:
    """The law's result on a fixture (``violation`` / ``example``)."""
    if law.kind == "co-change":
        return check_cochange((entries or {}).get("commits") or [], law.when, law.then, law.window)
    values, ages = fixture(entries)
    return evaluate(law.tree, values, ages)


def law_health(law: Law) -> str:
    """``ok``, or why the law can't be trusted: no planted violation, it doesn't fail on it, it fails its example."""
    if not law.violation:
        return "no planted violation — never trusted"
    result, detail = run_fixture(law, law.violation)
    if result != VIOLATED:
        return f"its planted violation gives {result}{f' ({detail})' if detail else ''} — the check can't fail"
    if law.example:
        result, detail = run_fixture(law, law.example)
        if result != HOLDS:
            return f"its example gives {result}{f' ({detail})' if detail else ''}"
    return OK


def _globs(value) -> tuple:
    value = [value] if isinstance(value, str) else list(value or ())
    return tuple(str(v).replace("\\", "/") for v in value if str(v).strip())


def load_laws(raw: list, known: set, environments=()) -> tuple:
    """``(laws, problems)`` from the manifest's ``[[world.laws]]`` tables. ``known`` = the names a check may read
    (sensor ids, ``-`` as ``_`` too, and the built-in variables). Every law is kept — one with a problem carries it as
    its ``health`` and is never evaluated."""
    laws, problems, seen = [], [], set()
    names = set(known) | {k.replace("-", "_") for k in known}
    envs = [getattr(e, "name", e) for e in environments]
    for i, entry in enumerate(raw or ()):
        if not isinstance(entry, dict):
            problems.append(f"law #{i + 1}: not a table")
            continue
        lid = str(entry.get("id") or "").strip()
        where = f"law {lid or '#' + str(i + 1)}"
        if not lid:
            problems.append(f"{where}: no id")
            continue
        if lid in seen:
            problems.append(f"{where}: declared twice")
            continue
        seen.add(lid)
        kind = str(entry.get("kind") or "equality")
        law = Law(id=lid, text=str(entry.get("text") or ""), kind=kind, check=str(entry.get("check") or ""),
                  violation=dict(entry.get("violation") or {}), example=dict(entry.get("example") or {}),
                  when=_globs(entry.get("when")), then=_globs(entry.get("then")),
                  environment=str(entry.get("environment") or (envs[0] if len(envs) == 1 else "")))
        issue = ""
        if kind not in KINDS:
            issue = f"unknown kind {kind!r} (use {', '.join(KINDS)})"
        elif kind == "co-change":
            try:
                law.window = int(entry.get("window", 1))
            except (TypeError, ValueError):
                issue = "window must be a number of commits"
            if not issue and (not law.when or not law.then):
                issue = "a co-change law needs when and then (file globs)"
            if not issue and law.environment not in envs:
                issue = f"unknown environment {law.environment!r}" if law.environment else "which environment?"
        else:
            try:
                law.tree, law.names = parse_check(law.check)
            except CheckError as exc:
                issue = f"check: {exc}"
            missing = [n for n in law.names if n not in names] if not issue else []
            if missing:
                issue = f"check reads unknown variable(s) {', '.join(missing)}"
        if not issue:
            try:
                health = law_health(law)
            except ValueError as exc:
                health = f"fixture: {exc}"
            issue = "" if health == OK else health
        if issue:
            law.health = issue
            problems.append(f"{where}: {issue}")
        laws.append(law)
    return laws, problems


def check_laws(laws, values: dict, ages: Optional[dict] = None, commits: Optional[dict] = None,
               since: Optional[dict] = None) -> dict:
    """``{law id: (result, detail)}`` for the trusted laws: expressions over ``values`` / ``ages``, co-change laws over
    ``commits`` (``{environment: commits}``) after ``since[law id]`` (its acceptance). An untrusted law gives
    ``unknown``."""
    out = {}
    for law in laws:
        if law.health != OK:
            out[law.id] = (UNKNOWN, f"not trusted: {law.health}")
        elif law.kind == "co-change":
            history = (commits or {}).get(law.environment)
            out[law.id] = (UNKNOWN, "no git history") if history is None else check_cochange(
                history, law.when, law.then, law.window, (since or {}).get(law.id))
        else:
            out[law.id] = evaluate(law.tree, values, ages)
    return out


def format_laws(rows, project: str = "") -> str:
    """The laws as text — each one's result now (``✓`` holds, ``✗`` violated, ``?`` unknown), its check, the detail,
    and why a law isn't trusted."""
    rows = list(rows or ())
    lines = [f"Laws{' — ' + project if project else ''}   {len(rows)} declared"]
    if not rows:
        lines.append("  none — declare [[world.laws]] in openwiki.toml (each with a planted violation)")
    for r in rows:
        mark = {HOLDS: "✓", VIOLATED: "✗"}.get(r.get("last_result"), "?")
        status = r.get("status") or ""
        lines.append(f"  {mark} {r['id']:<22}{r.get('last_result') or UNKNOWN:<10}{r.get('check') or ''}"
                     + (f"   [{status} — `owiki world accept {r['id']}`]" if status in ("proposed", "changed") else ""))
        if r.get("health") and r["health"] != OK:
            lines.append(f"    not trusted: {r['health']}")
        elif r.get("detail"):
            lines.append(f"    {r['detail']}")
        if r.get("text"):
            lines.append(f"    {r['text']}")
    return "\n".join(lines)
