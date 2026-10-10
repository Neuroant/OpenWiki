"""Write-ahead journal for deferred memory ops (Path B — B1 concurrency).

Kuzu 0.11 is **reader-XOR-writer**: a writable connection is exclusive (it blocks all
readers, *and* readers block a writer — empirically verified). So while a read-only
``serve`` (or any reader) holds the graph, a would-be *writer* — ``remember``, a host
``capture`` hook, or a chat-agent edit's graph re-sync — can't open it writable. Rather
than fail or drop the write, it **appends** the op to a small JSONL sidecar next to the
graph, and the next *writable* pass (``serve``/``chat`` start-or-shutdown fold,
``openwiki decay``, or the next ``remember``) drains it (``GraphStore.fold_journal``).
Writes are never lost; they land when a writer next runs.

This complements :mod:`openwiki.graph.usage` (the reinforce-pair slice of the same
write-ahead idea); together they are OpenWiki's lock-free deferred-write log. Records
are **self-contained** (a ``remember`` carries its triples, a ``reindex`` its page text),
so a fold needs only an embedder — no wiki directory or session lookup. Pure +
dependency-free (no Kuzu), mirroring ``usage.py``/``decay.py``; the Kuzu side (fold)
lives in ``store.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from ..policy import REDACTED, redact_secrets


def journal_path(db_path) -> Path:
    """The op-journal sidecar for a graph DB file. Named off the DB file so it travels
    with the graph and survives a rebuild (``GraphBuilder._remove_existing`` leaves it)."""
    p = Path(db_path)
    return p.with_name(p.name + ".journal.jsonl")


def _now(now: Optional[int]) -> int:
    return int(now if now is not None else time.time())


def _append(path, obj: dict) -> None:
    """Write one JSON record per line so concurrent appends never interleave mid-record."""
    line = json.dumps(obj, ensure_ascii=False)
    with Path(path).open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def append_remember(path, session_id, facts, now: Optional[int] = None,
                    session_date: Optional[int] = None, correct: bool = False,
                    retire=None, agent: bool = False) -> int:
    """Queue a ``remember`` op — see :func:`_remember_record`. Returns the number of triples written
    (0 → nothing queued)."""
    rec = _remember_record(session_id, facts, now, session_date, correct, retire, agent)
    if rec is None:
        return 0
    _append(path, rec)
    return len(rec["facts"])


def _remember_record(session_id, facts, now: Optional[int] = None, session_date: Optional[int] = None,
                     correct: bool = False, retire=None, agent: bool = False) -> Optional[dict]:
    """Queue a ``remember`` op — (subject, predicate, object) triples for one session.
    ``facts`` may be ``MemoryFact``-likes (``.subject``/``.predicate``/``.object``) or
    ``(s, p, o)`` tuples. Returns the number of triples written (0 → nothing queued).

    B7: ``t`` is the record (transaction) time the fold honors; a fact with a stated
    ``valid_from`` or a ``"many"`` cardinality is written as ``[s, p, o, valid_from,
    cardinality]`` (plain triples otherwise, so older readers still parse them), and the
    record carries the ``session_date`` / ``correct`` flag when set.

    ``retire`` (``wiki_remember``'s ``replaces``): ids of remembered facts this op makes outdated —
    the fold closes them at the op's time (valid time ends: the world changed). A record may carry
    only ``retire`` (the new state already remembered, or none needed). ``agent`` marks an op
    written by the host agent (``wiki_remember``): it names its replacements itself, so the fold
    skips the model-based attribute resolution (B9) for its facts. ``None`` when there is nothing to
    write."""
    triples = []
    for f in facts:
        if hasattr(f, "subject"):
            s, p, o = f.subject, f.predicate, f.object
            vf, card = getattr(f, "valid_from", None), getattr(f, "cardinality", "one")
            src = getattr(f, "source", None)
        else:
            s, p, o = f[:3]
            vf, card = (f[3], f[4]) if len(f) >= 5 else (None, "one")
            src = f[5] if len(f) >= 6 else None
        s, p, o = str(s).strip(), str(p).strip(), str(o).strip()
        # P0: a credential never reaches the journal file (it stays on disk until the next fold); a
        # fact that was nothing but a credential is dropped
        s, p, o = (redact_secrets(x)[0] for x in (s, p, o))
        if REDACTED in (s, o):
            continue
        if s and p and o:
            if src is not None:                   # P0 provenance travels with the fact
                triples.append([s, p, o, None if vf is None else int(vf), card or "one", src])
            elif vf is None and card == "one":
                triples.append([s, p, o])
            else:
                triples.append([s, p, o, None if vf is None else int(vf), card or "one"])
    retire = [str(i) for i in (retire or []) if str(i).strip()]
    if not triples and not retire:
        return None
    rec = {"op": "remember", "t": _now(now), "session": str(session_id), "facts": triples}
    if retire:
        rec["retire"] = retire
    if agent:
        rec["agent"] = True
    if session_date is not None:
        rec["session_date"] = int(session_date)
    if correct:
        rec["correct"] = True
    return rec


ID_OPS = ("confirm", "forget")      # ops on facts named by id — the Memory tab's maintenance panel (M7)


def append_ids(path, op, ids, now: Optional[int] = None, reason: Optional[str] = None) -> int:
    """Queue an op on facts named by id: ``confirm`` (a person checked the fact still holds — the fold
    re-affirms it, as if it were said again) or ``forget`` (archive it with ``reason``). Pinned by id like a
    ``remember`` record's ``retire``: the fold touches exactly the facts the reviewer saw, and a fact's content
    never changes. Returns how many ids were queued (0 → nothing)."""
    if op not in ID_OPS:
        raise ValueError(f"unknown op {op!r} (one of {', '.join(ID_OPS)})")
    ids = [str(i).strip() for i in (ids or []) if str(i).strip()]
    if not ids:
        return 0
    rec = {"op": op, "t": _now(now), "ids": ids}
    if reason:
        rec["reason"] = str(reason)
    _append(path, rec)
    return len(ids)


def append_reindex(path, slug, text, now: Optional[int] = None) -> int:
    """Queue a ``reindex`` op — re-sync one page into the graph. The record carries the
    page text so the fold is self-contained. Returns 1 (queued) or 0 (empty slug)."""
    slug = str(slug).strip()
    if not slug:
        return 0
    _append(path, {"op": "reindex", "t": _now(now), "slug": slug, "text": str(text or "")})
    return 1


def read_journal(path) -> list:
    """Read all pending op records. Lenient — skips blank/corrupt lines and unknown ops;
    ``[]`` if the journal is absent."""
    p = Path(path)
    if not p.is_file():
        return []
    records = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("op") in ("remember", "reindex") + ID_OPS:
            records.append(obj)
    return records


def queued_ids(path) -> dict:
    """The fact ids pending in the journal per action — ``close`` (a ``remember`` record's ``retire``),
    ``confirm``, ``forget`` — until a fold applies them (the maintenance panel marks them queued)."""
    out: dict = {"close": [], "confirm": [], "forget": []}
    for rec in read_journal(path):
        if rec.get("op") == "remember":
            out["close"].extend(rec.get("retire") or [])
        elif rec.get("op") in ID_OPS:
            out[rec["op"]].extend(rec.get("ids") or [])
    return out


def clear_journal(path) -> None:
    """Drop the journal (after folding it into the graph)."""
    Path(path).unlink(missing_ok=True)


def pending_journal(path) -> int:
    """How many op records are queued (0 if none)."""
    return len(read_journal(path))


# -- staging (v0.110): agent writes held for a person's approval ([memory] approve_writes) -----------
# The same records as the journal, plus an ``id``, in a second sidecar. Approving moves a record into the
# journal — the next fold applies it —, valid from when the agent staged it and recorded when it was
# approved (B7: until then memory did not believe it). Rejecting moves it to an audit log. A staged
# replacement names its facts by id, and a fact's content never changes (a new value is a new fact), so an
# approval closes exactly the fact the reviewer saw — or nothing, if it was closed meanwhile.

def staged_path(db_path) -> Path:
    """The staging sidecar for a graph DB file: ``wiki_remember`` writes awaiting approval."""
    p = Path(db_path)
    return p.with_name(p.name + ".staged.jsonl")


def rejected_path(db_path) -> Path:
    """The audit log of rejected staged writes."""
    p = Path(db_path)
    return p.with_name(p.name + ".rejected.jsonl")


@contextmanager
def _locked(path, wait: float = 5.0, stale: float = 60.0):
    """An advisory lock around a staging file's read-modify-write (the agent stages while a person
    approves): a ``.lock`` file created exclusively; one older than ``stale`` seconds is taken over."""
    lock = Path(str(path) + ".lock")
    deadline = time.time() + wait
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > stale:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.time() > deadline:
                raise TimeoutError(f"staging file busy: {lock}")
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def stage_remember(path, session_id, facts, now: Optional[int] = None, session_date: Optional[int] = None,
                   retire=None, agent: bool = True) -> tuple:
    """Stage a ``remember`` op for approval instead of queueing it → ``(triples staged, id)`` (``(0, "")``
    when there is nothing to stage)."""
    rec = _remember_record(session_id, facts, now, session_date, False, retire, agent)
    if rec is None:
        return 0, ""
    rec["id"] = hashlib.sha1(json.dumps(rec, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:8]
    with _locked(path):
        _append(path, rec)
    return len(rec["facts"]), rec["id"]


def read_staged(path) -> list:
    """The staged writes, oldest first."""
    return [r for r in read_journal(path) if r.get("op") == "remember" and r.get("id")]


def _take_staged(path, ids=None) -> list:
    """Remove the staged records with these ids (all when ``ids`` is None) and return them."""
    want = None if ids is None else {str(i).strip() for i in ids if str(i).strip()}
    with _locked(path):
        recs = read_staged(path)
        taken = [r for r in recs if want is None or r["id"] in want]
        keep = [r for r in recs if not (want is None or r["id"] in want)]
        p = Path(path)
        if keep:
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep), encoding="utf-8")
            os.replace(tmp, p)
        else:
            p.unlink(missing_ok=True)
    return taken


def _valid_from(fact, at: int) -> list:
    """A journal fact with an explicit valid-from (``at`` unless it states one)."""
    f = list(fact)
    s, p, o = f[:3]
    vf = f[3] if len(f) >= 4 and f[3] is not None else at
    card = f[4] if len(f) >= 5 and f[4] else "one"
    return [s, p, o, int(vf), card] + ([f[5]] if len(f) >= 6 else [])


def approve_staged(path, journal, ids=None, now: Optional[int] = None) -> list:
    """Approve staged writes (all when ``ids`` is None): each moves into the journal — the next fold applies
    it —, valid from when it was staged (its facts, and the closing of what it replaces: ``valid_at``),
    recorded now. Returns the approved records."""
    at = _now(now)
    taken = _take_staged(path, ids)
    for rec in taken:
        staged = int(rec.get("t") or at)
        out = {k: v for k, v in rec.items() if k != "id"}
        out["facts"] = [_valid_from(f, staged) for f in rec.get("facts", [])]
        out.update({"t": at, "valid_at": staged, "approved": rec["id"]})
        _append(journal, out)
    return taken


def reject_staged(path, log, ids=None, now: Optional[int] = None) -> list:
    """Reject staged writes (all when ``ids`` is None): moved to the audit ``log``, never applied."""
    at = _now(now)
    taken = _take_staged(path, ids)
    for rec in taken:
        _append(log, dict(rec, rejected_at=at))
    return taken
