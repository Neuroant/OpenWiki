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
import hashlib
import json
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
    accepted_at: Optional[int] = None  # a law held in the graph: when a person accepted it (a co-change law watches on)

    @property
    def described(self) -> str:
        """The check as shown and stored: the expression, or a co-change rule in words."""
        if self.kind == "co-change":
            return (f"{' | '.join(self.when)} changed ⇒ {' | '.join(self.then)} changed within "
                    f"{self.window} commit{'' if self.window == 1 else 's'}")
        return self.check

    def spec(self) -> str:
        """What a law held in the graph needs to be checked again (v0.127): its fixtures and a co-change rule."""
        spec = {"violation": self.violation, "example": self.example, "when": list(self.when),
                "then": list(self.then), "window": self.window, "environment": self.environment}
        if self.accepted_at:
            spec["accepted_at"] = int(self.accepted_at)
        return json.dumps(spec, ensure_ascii=False, sort_keys=True, default=str)

    def row(self) -> dict:
        """The ``Law`` node's fields (``GraphStore.upsert_laws``)."""
        return {"id": self.id, "text": self.text, "kind": self.kind, "check": self.described, "origin": self.origin,
                "status": self.status, "health": self.health, "spec": self.spec()}


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
        lines.append(f"  {mark} {r['id']:<21} {r.get('last_result') or UNKNOWN:<9} {r.get('check') or ''}"
                     + (f"   [{status} — `owiki world accept {r['id']}`]" if status in ("proposed", "changed") else ""))
        if r.get("health") and r["health"] != OK:
            lines.append(f"    not trusted: {r['health']}")
        elif r.get("detail"):
            lines.append(f"    {r['detail']}")
        if r.get("text"):
            lines.append(f"    {r['text']}")
    return "\n".join(lines)


# -- laws held in the graph: decisions' preconditions, proposals (C2.4, C2.5) ------------------------------------

def law_from_row(row: dict) -> Law:
    """A law held in the graph (a decision's precondition, a proposal from a document or the co-change history) as a
    ``Law`` again — its check parsed, its fixtures from ``spec``, its health checked anew."""
    try:
        spec = json.loads(row.get("spec") or "{}")
    except ValueError:
        spec = {}
    law = Law(id=str(row["id"]), text=str(row.get("text") or ""), kind=str(row.get("kind") or "equality"),
              check="" if row.get("kind") == "co-change" else str(row.get("check") or ""),
              origin=str(row.get("origin") or "graph"), status=str(row.get("status") or "proposed"),
              violation=dict(spec.get("violation") or {}), example=dict(spec.get("example") or {}),
              when=_globs(spec.get("when")), then=_globs(spec.get("then")), window=int(spec.get("window") or 1),
              environment=str(spec.get("environment") or ""), accepted_at=spec.get("accepted_at"))
    try:
        if law.kind != "co-change":
            law.tree, law.names = parse_check(law.check)
        law.health = law_health(law)
    except (CheckError, ValueError) as exc:
        law.health = f"check: {exc}"
    return law


def _changed(value):
    """Some other value of the same type — a decision's planted violation."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    if isinstance(value, (list, tuple)):
        return list(value) + ["·changed"]
    return f"{value}·changed" if value is not None else "·changed"


def precondition_law(decision: str, values: dict, fact_ids=()) -> Optional[Law]:
    """Path C (C2.5): a decision recorded with the state it was made in — a ``precondition`` law that holds while
    each variable keeps the value observed when the decision was recorded (``values``: ``{name: value}``). Its planted
    violation changes the first one. ``None`` without values."""
    values = {str(k): v for k, v in (values or {}).items()}
    if not values:
        return None
    names = {k: k.replace("-", "_") for k in values}
    check = " and ".join(f"{names[k]} == {v!r}" for k, v in values.items())
    first = next(iter(values))
    key = hashlib.sha1((decision + "|" + "|".join(sorted(fact_ids)) + "|" + check).encode("utf-8")).hexdigest()[:10]
    law = Law(id=f"decision:{key}", text=f"{decision} — made while " + ", ".join(
                  f"{k} = {v!r}" for k, v in values.items()),
              kind="precondition", check=check, origin="decision", status="accepted",
              violation={names[first]: _changed(values[first]), **{names[k]: v for k, v in values.items()
                                                                   if k != first}},
              example={names[k]: v for k, v in values.items()})
    try:
        law.tree, law.names = parse_check(check)
        law.health = law_health(law)
    except (CheckError, ValueError) as exc:
        law.health = f"check: {exc}"
    return law if law.health == OK else None


RULE_WORDS = re.compile(
    r"\b(?:must|never|only if|only when|at least|at most|no more than|above|below|requires?|always|exactly|"
    r"muss|müssen|nie|niemals|nur wenn|mindestens|höchstens|immer|darf nicht|dürfen nicht|genau)\b", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n{2,}|\n(?=[-*#|])")
EXTRACT_SYSTEM = (
    "You turn rules stated in a document into checks over measured variables. You get sentences from one page and "
    "a list of variables — each with its name, what it measures and its value now. For each sentence that states a "
    "rule about these variables (a value, a limit, an equality, an implication), write one JSON object: "
    '{"text": the rule in one sentence, "kind": "equality" | "comparison" | "implication" | "convention", '
    '"check": a Python-like expression over the variable names only (==, !=, <, <=, >, >=, and, or, not, '
    "implies(p, q), ver(x) to compare versions, len(x)), "
    '"violation": {name: value, …} values that break the rule, "example": {name: value, …} values that keep it}. '
    "Use only the given names. Skip sentences that state no checkable rule about them. "
    "Answer with a JSON array only — [] when there is none.")


def rule_sentences(text: str, terms=(), limit: int = 12) -> list:
    """The sentences of ``text`` that read like rules (``RULE_WORDS``) and — when ``terms`` are given — mention one of
    them (a variable's name, subject, label word or file), at most ``limit``."""
    out = []
    pats = [re.compile(r"(?<![\w-])" + re.escape(t) + r"(?![\w-])", re.I) for t in terms if len(t) >= 3]
    for sent in _SENTENCE.split(str(text or "")):
        sent = " ".join(sent.split())
        if 20 <= len(sent) <= 400 and RULE_WORDS.search(sent) and (not pats or any(p.search(sent) for p in pats)):
            out.append(sent)
            if len(out) >= limit:
                break
    return out


def variable_terms(variables) -> dict:
    """``{term: variable name}`` — the words a sentence may name a variable by: its id (spaced too), its subject, the
    file it reads. Not the single words of its label — "build", "code", "current", "file" matched nearly every
    sentence of this repository's CLAUDE.md."""
    out: dict = {}
    for v in variables:
        name = str(v["name"])
        for term in [name, name.replace("-", " "), name.replace("_", " "), str(v.get("subject") or "")] + [
                str(p).rsplit("/", 1)[-1] for p in v.get("paths") or ()]:
            term = term.strip()
            if len(term) >= 3 and term.lower() not in ("the", "has", "is", "openwiki"):
                out.setdefault(term, name)
    return out


def _json_array(text: str) -> list:
    """The objects of the first JSON array in a model's answer — one by one when the array as a whole doesn't parse,
    so a candidate written in Python (``("a", "b")``) loses only itself, not the page."""
    text = re.sub(r"<think>.*?</think>", "", str(text or ""), flags=re.S)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        got = json.loads(text[start:end + 1])
        return [g for g in got if isinstance(g, dict)] if isinstance(got, list) else []
    except ValueError:
        pass
    out, decoder, i = [], json.JSONDecoder(), start + 1
    while True:
        i = text.find("{", i, end)
        if i < 0:
            return out
        try:
            obj, i = decoder.raw_decode(text, i)
        except ValueError:
            i = _skip_object(text, i)
            continue
        if isinstance(obj, dict):
            out.append(obj)


def _skip_object(text: str, i: int) -> int:
    """The index after the ``{…}`` that starts at ``i`` (braces in strings ignored) — past a malformed candidate."""
    depth, quoted, escaped = 0, False, False
    for j in range(i, len(text)):
        c = text[j]
        if quoted:
            escaped, quoted = (not escaped and c == "\\"), quoted and (escaped or c != '"')
        elif c == '"':
            quoted = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1
    return len(text)


def _variables(node) -> set:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} - set(FUNCTIONS)


def tests_constants(tree) -> bool:
    """Whether a check compares constants with each other somewhere (``'session' in ['session']``) — a part that
    doesn't depend on the world, true or false whatever is read."""
    return any(isinstance(n, ast.Compare) and not _variables(n) for n in ast.walk(tree))


def check_key(tree) -> str:
    """A canonical form of a check — operands of ``==`` / ``!=`` and of ``and`` / ``or`` in a fixed order — so the
    same law found on two pages, or already declared (``code_version == version`` = ``version == code_version``),
    is proposed once."""
    def key(node) -> str:
        if isinstance(node, ast.Expression):
            return key(node.body)
        if isinstance(node, ast.BoolOp):
            return f"{type(node.op).__name__}({','.join(sorted(key(v) for v in node.values))})"
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
            sides = sorted((key(node.left), key(node.comparators[0])))
            return f"{type(node.ops[0]).__name__}({sides[0]},{sides[1]})"
        if isinstance(node, ast.Compare):
            return (f"Compare({key(node.left)},"
                    + ",".join(f"{type(o).__name__}:{key(c)}" for o, c in zip(node.ops, node.comparators)) + ")")
        if isinstance(node, (ast.Name, ast.Constant)):
            return ast.dump(node, annotate_fields=False)
        return f"{type(node).__name__}({','.join(key(c) for c in ast.iter_child_nodes(node))})"
    return key(tree)


def pins_constants(tree) -> bool:
    """Whether a check only equates variables with constants (``version == '0.52'``, ``len(stages) == 5 and …``) —
    it restates readings, which a binding's fact already holds, and breaks at the next change: no law."""
    node = getattr(tree, "body", tree)
    if isinstance(node, ast.BoolOp):
        return all(pins_constants(v) for v in node.values)
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
        names = [_variables(x) for x in (node.left, node.comparators[0])]
        return bool(names[0]) != bool(names[1])          # a variable on one side, a literal on the other
    return False


def extract_laws(chat, page: str, sentences, variables, known: set, report: Optional[dict] = None,
                 seen: Optional[set] = None) -> list:
    """Path C (C2.4): one model call over a page's rule-like ``sentences`` → candidate laws — *proposed*, origin
    ``document``, each validated like a manifest law (its check in the language, only ``known`` names, failing on its
    planted violation); dropped besides: a check that only pins variables to constants (``pins_constants``) and one
    the variables' current values violate — a misread sentence or a stale document, not a law to propose (measured
    on this repository's CLAUDE.md: "B6 (v0.52) …" became ``version == 'v0.52'``) —, one comparing constants
    (``tests_constants``) and one whose ``check_key`` is in ``seen`` (laws already held or proposed; extended with
    each one kept), one the current values can't evaluate (``probes <= 3`` over a file pattern's ``'3'``), and one
    whose text doesn't name a variable its check reads (the model paired a sentence about recall with a check of the
    build's stages). ``report`` counts what was dropped (``invalid`` / ``pinned`` / ``constant`` / ``unrelated`` /
    ``violated_now`` / ``unknown_now`` / ``duplicate``). Fail-soft: a failed call gives ``[]``."""
    if not sentences:
        return []
    report = report if report is not None else {}
    now = {v["name"]: v.get("value") for v in variables if v.get("value") is not None}
    words: dict = {}                                # variable → the words that name it, as the prefilter matches them
    for term, name in variable_terms(variables).items():
        words.setdefault(name, []).append(re.compile(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", re.I))

    def named(text: str, name: str) -> bool:
        return name not in words or any(w.search(text) for w in words[name])
    listing = "\n".join(f"- {v['name']}: {v.get('label') or v.get('subject') or ''} "
                         f"(now {json.dumps(v.get('value'), ensure_ascii=False, default=str)})" for v in variables)
    try:
        reply = chat.chat([{"role": "system", "content": EXTRACT_SYSTEM},
                           {"role": "user", "content": f"Variables:\n{listing}\n\nPage: {page}\nSentences:\n"
                                                       + "\n".join(f"- {x}" for x in sentences)}])
    except Exception:
        return []
    out = []
    for cand in _json_array(reply if isinstance(reply, str) else getattr(reply, "content", "")):
        text = str(cand.get("text") or "").strip()
        if not text:
            continue
        key = hashlib.sha1(f"{page}|{text}".encode("utf-8")).hexdigest()[:10]
        laws, problems = load_laws([{"id": f"doc:{key}", "text": text, "kind": cand.get("kind") or "equality",
                                     "check": cand.get("check") or "", "violation": cand.get("violation") or {},
                                     "example": cand.get("example") or {}}], known)
        law = laws[0] if laws else None
        if law is None or law.health != OK or problems or law.kind == "co-change":
            report["invalid"] = report.get("invalid", 0) + 1
            continue
        result = evaluate(law.tree, now)[0] if all(n in now for n in law.names) else None
        why = ("pinned" if pins_constants(law.tree) else "constant" if tests_constants(law.tree)
               else "unrelated" if not all(named(text, n) for n in law.names)
               else "violated_now" if result == VIOLATED else "unknown_now" if result == UNKNOWN
               else "duplicate" if seen is not None and check_key(law.tree) in seen else "")
        if why:
            report[why] = report.get(why, 0) + 1
            continue
        if seen is not None:
            seen.add(check_key(law.tree))
        law.origin, law.status = "document", "proposed"
        out.append(law)
    return out


# As ``graph.cochange``'s MAX_COMMIT_FILES / UBIQUITOUS_SHARE / UBIQUITOUS_MIN (ADR-48) — repeated here so this module
# needs no graph import (``openwiki.graph`` loads Kuzu); a test keeps them equal.
SWEEP_FILES = 40
UBIQUITOUS_SHARE = 0.5
UBIQUITOUS_MIN = 10


def cochange_candidates(commits, min_changes: int = 8, share: float = 0.9, window: int = 3, lift: float = 1.5,
                        limit: int = 10, environment: str = "", skip=()) -> list:
    """Path C (C2.4): co-change laws learned from the git history (the log ``CO_CHANGED`` comes from) — a file changed
    in at least ``min_changes`` commits, followed within ``window`` commits by a change to another file in at least
    ``share`` of them, ``lift`` times as often as that file changes at all. As for ``CO_CHANGED`` (ADR-48), a commit
    touching more than ``SWEEP_FILES`` files is a sweep, not a coupling, and a file changed in ``UBIQUITOUS_SHARE`` of
    the commits pairs only with files as ubiquitous: where nearly every commit is a release, the version files follow
    every file (measured on this repository: "a change to cli.py comes with a version bump", 100 of 100). *Proposed*,
    origin ``co-change``; the strongest ``limit`` not in ``skip`` (ids, or ``(when, then)`` pairs — laws already held
    or declared). Commits newest first, ``[(epoch, [paths])]``."""
    rows = [list(c[1]) if isinstance(c, tuple) else list(c) for c in commits or ()]
    rows = [files if len(set(files)) <= SWEEP_FILES else [] for files in rows]    # a sweep still counts as a commit
    total = len(rows)
    if not total:
        return []
    base: dict = {}
    for files in rows:
        for f in set(files):
            base[f] = base.get(f, 0) + 1
    changed = sum(1 for files in rows if files)
    ubiquitous = {f for f, n in base.items() if changed >= UBIQUITOUS_MIN and n >= UBIQUITOUS_SHARE * changed}
    scored = []
    for a, n_a in base.items():
        if n_a < min_changes:
            continue
        follows: dict = {}
        for i, files in enumerate(rows):
            if a not in files or i < window - 1:
                continue
            seen = set().union(*[set(f) for f in rows[max(0, i - window + 1): i + 1]]) - {a}
            for b in seen:
                follows[b] = follows.get(b, 0) + 1
        counted = sum(1 for i, files in enumerate(rows) if a in files and i >= window - 1)
        for b, n in follows.items():
            if (a in ubiquitous) != (b in ubiquitous):
                continue
            p_b = base[b] / total
            if counted and n / counted >= share and (n / counted) >= lift * p_b:
                scored.append((counted * (n / counted) / max(p_b, 1e-9), a, b, n, counted))
    out = []
    skip = set(skip or ())
    for _, a, b, n, counted in sorted(scored, reverse=True):
        if len(out) >= limit:
            break
        if f"cochange:{a}=>{b}" in skip or ((a,), (b,)) in skip:
            continue
        law = Law(id=f"cochange:{a}=>{b}", text=f"A change to {a} comes with a change to {b} within {window} commits "
                                                f"({n} of {counted} in the history).",
                  kind="co-change", origin="co-change", status="proposed", when=(a,), then=(b,), window=window,
                  environment=environment, violation={"commits": [["(another file)"]] * (window - 1) + [[a]]})
        law.health = law_health(law)
        if law.health == OK:
            out.append(law)
    return out
