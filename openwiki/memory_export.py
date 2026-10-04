"""Portable memory — the remembered tier as a COGX archive and as a readable Markdown view; COGX back in.

Since B0 the graph is the only store of remembered content, and the graph engine (Kuzu) is archived upstream (arc42
R10). Two exports make the memory independent of it:

- **COGX** (the Cognee eXchange format, v0.1): a directory — ``manifest.json`` plus one JSONL file per record kind —
  or a ``.cogx.tar.gz`` of it, readable by Cognee and, through its importers, by Mem0, Zep / Graphiti, Letta and
  LangMem. Each assertion becomes a **fact** (``subject_ref`` / ``predicate`` / ``object_ref``, ``valid_at`` /
  ``invalid_at`` = B7 valid time, ``confidence``, the session as provenance; transaction times, cardinality, the
  attribute key, source, forgotten marks, the ``SUPERSEDES`` provenance and — optionally — the embedding go into
  ``metadata["openwiki"]``), each session an **episode** (no turns: transcripts stay outside memory), each theme a
  **memory**, the identity a **memory block**. An OpenWiki archive restores **losslessly** into an empty remembered
  tier (``GraphStore.restore_memory``); facts from other systems go through ``remember`` — the merge, the P0 policy
  and credential redaction — like any capture, tagged as discussed material.
- **A Markdown view**: ``README.md`` (identity, counts, themes, an index of subjects) plus one file per subject —
  its current facts and their history — rendered deterministically, so a git diff shows what the memory learned.
  ``sleep`` rewrites it; ``owiki memory export`` writes either on demand.

Pure apart from file I/O: works on a ``GraphStore.memory_snapshot`` dict, no Kuzu import.
"""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
import tempfile
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .policy import redact_secrets

COGX_VERSION = "0.1"
SYSTEM = "openwiki"
RECORD_FILES = {"document": "documents.jsonl", "episode": "episodes.jsonl", "entity": "entities.jsonl",
                "fact": "facts.jsonl", "memory": "memories.jsonl", "memory_block": "memory_blocks.jsonl"}
MANIFEST = "manifest.json"
TAR_SUFFIX = ".cogx.tar.gz"
# unpacking limits (Cognee's): a decompression bomb is refused before it fills the disk
MAX_MEMBERS, MAX_MEMBER_BYTES, MAX_TOTAL_BYTES = 100_000, 512 * 2**20, 2 * 2**30
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
# assertion row fields — the order of GraphStore.memory_snapshot / GraphBuilder._snapshot_memory
FIELDS = ("id", "subject", "predicate", "object", "session_id", "created_at", "emb", "confidence", "last_seen",
          "valid_from", "valid_to", "expired_at", "cardinality", "attr", "source", "forgotten_at", "forgotten")


def iso(epoch) -> Optional[str]:
    """Epoch seconds → ISO 8601 UTC (``2026-09-27T07:48:41Z``); negative epochs too (a stated "since 1969")."""
    if epoch is None:
        return None
    return (_EPOCH + timedelta(seconds=int(epoch))).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch(value) -> Optional[int]:
    """ISO 8601 (or epoch seconds) → epoch seconds, ``None`` if absent or unreadable."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int((dt - _EPOCH).total_seconds())


def _clean(record: dict) -> dict:
    """Drop ``None`` values (COGX writes ``exclude_none``), recursively for metadata."""
    out = {}
    for k, v in record.items():
        if isinstance(v, dict):
            v = _clean(v)
        if v is not None and v != {}:
            out[k] = v
    return out


def _scrub(value):
    """Credential redaction over every string of a record (defense in depth: facts are redacted when stored) —
    per value, so a pattern can never reach across JSON syntax."""
    if isinstance(value, str):
        return redact_secrets(value)[0]
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


# -- snapshot → COGX ------------------------------------------------------------------------------------

def to_cogx(snap: dict, themes=(), theme_members=None, identity: str = "",
            with_embeddings: bool = False) -> list:
    """COGX records (dicts) for a memory snapshot, its themes (``memory_concepts``) and the identity."""
    supersedes: dict = {}
    for new_id, old_id in snap.get("supersedes", []):
        supersedes.setdefault(new_id, []).append(old_id)
    records = []
    for sid, created, sdate in snap.get("sessions", []):
        records.append(_clean({
            "kind": "episode", "external_system": SYSTEM, "external_id": sid, "scope": {"session_id": sid},
            "created_at": iso(created), "title": sid, "turns": [],
            "metadata": {SYSTEM: {"session_date": sdate}}}))
    for row in snap.get("assertions", []):
        a = dict(zip(FIELDS, row))
        meta = {"expired_at": iso(a["expired_at"]), "cardinality": a["cardinality"], "attr": a["attr"],
                "source": a["source"], "last_seen": a["last_seen"], "forgotten_at": iso(a["forgotten_at"]),
                "forgotten": a["forgotten"], "supersedes": sorted(supersedes.get(a["id"], [])) or None}
        if with_embeddings and a["emb"] is not None:
            meta["embedding"] = [float(x) for x in a["emb"]]
        records.append(_clean({
            "kind": "fact", "external_system": SYSTEM, "external_id": a["id"],
            "scope": {"session_id": a["session_id"]}, "created_at": iso(a["created_at"]),
            "updated_at": iso(a["last_seen"]) if a["last_seen"] else None,
            "subject_ref": a["subject"], "predicate": a["predicate"], "object_ref": a["object"],
            "fact_text": f"{a['subject']} {a['predicate']} {a['object']}",
            "valid_at": iso(a["valid_from"]), "invalid_at": iso(a["valid_to"]),
            "confidence": a["confidence"], "provenance": [a["session_id"]] if a["session_id"] else None,
            "metadata": {SYSTEM: meta}}))
    members = theme_members or {}
    for t in themes:
        records.append(_clean({
            "kind": "memory", "external_system": SYSTEM, "external_id": f"theme-{t['id']}",
            "content": t.get("summary") or "", "categories": [t["label"]] if t.get("label") else [],
            "metadata": {SYSTEM: {"theme_id": t["id"], "size": t.get("size"),
                                  "members": sorted(members.get(t["id"], [])) or None}}}))
    if identity:
        records.append({"kind": "memory_block", "external_system": SYSTEM, "external_id": "identity",
                        "label": "identity", "value": identity})
    return records


def write_cogx(records: list, out, embedding_model: Optional[str] = None, notes=()) -> Path:
    """Write a COGX archive: a directory (owned — earlier record files and manifest are replaced) or, for a path
    ending in ``.cogx.tar.gz``, a packed one. Every string passes the credential redaction once more."""
    out = Path(out)
    if str(out).endswith(TAR_SUFFIX):
        with tempfile.TemporaryDirectory() as tmp:
            write_cogx(records, Path(tmp) / "archive", embedding_model, notes)
            out.parent.mkdir(parents=True, exist_ok=True)
            with tarfile.open(out, "w:gz") as tar:
                for path in sorted((Path(tmp) / "archive").iterdir()):
                    tar.add(path, arcname=path.name)
        return out
    out.mkdir(parents=True, exist_ok=True)
    for name in (*RECORD_FILES.values(), MANIFEST):
        (out / name).unlink(missing_ok=True)
    counts: dict = {}
    handles: dict = {}
    try:
        for rec in records:
            name = RECORD_FILES[rec["kind"]]
            if name not in handles:
                handles[name] = (out / name).open("w", encoding="utf-8", newline="\n")
            line = json.dumps(_scrub(rec), ensure_ascii=False)
            handles[name].write(line + "\n")
            counts[rec["kind"]] = counts.get(rec["kind"], 0) + 1
    finally:
        for h in handles.values():
            h.close()
    manifest = _clean({"cogx_version": COGX_VERSION, "source_system": SYSTEM,
                       "exported_at": iso(int(datetime.now(timezone.utc).timestamp())), "counts": counts,
                       "embedding_model": embedding_model, "notes": list(notes) or None})
    (out / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def _unpack(path: Path, dest: Path) -> Path:
    """Extract a packed archive member by member — a member outside ``dest``, a link or device, or one past the
    size / count limits is refused — and return the directory holding the manifest (the root, or its only
    subdirectory)."""
    count = total = 0
    root = dest.resolve()
    with tarfile.open(path, "r:*") as tar:
        for m in tar:
            count += 1
            name = Path(m.name)
            if (name.is_absolute() or ".." in name.parts or not (dest / name).resolve().is_relative_to(root)
                    or not (m.isfile() or m.isdir())):
                raise ValueError(f"unsafe member in COGX archive: {m.name}")
            total += m.size
            if count > MAX_MEMBERS or m.size > MAX_MEMBER_BYTES or total > MAX_TOTAL_BYTES:
                raise ValueError("COGX archive exceeds the unpacking limits (members / bytes)")
            try:
                tar.extract(m, dest, filter="data")
            except TypeError:                                  # Python without extraction filters
                tar.extract(m, dest)                           # (the member was checked above)
    if (dest / MANIFEST).exists():
        return dest
    subs = [d for d in dest.iterdir() if d.is_dir()]
    if len(subs) == 1 and (subs[0] / MANIFEST).exists():      # packed with a top-level folder
        return subs[0]
    raise ValueError(f"no {MANIFEST} in the COGX archive")


def read_cogx(path) -> tuple:
    """``(manifest, records)`` from a COGX directory or a packed ``.cogx.tar.gz`` (see ``_unpack``)."""
    path = Path(path)
    if path.is_file():
        with tempfile.TemporaryDirectory() as tmp:
            return read_cogx(_unpack(path, Path(tmp)))
    if not path.is_dir():
        raise FileNotFoundError(f"no COGX archive at {path}")
    manifest = json.loads((path / MANIFEST).read_text(encoding="utf-8")) if (path / MANIFEST).exists() else {}
    major = str(manifest.get("cogx_version", COGX_VERSION)).split(".")[0]
    if major.isdigit() and int(major) > int(COGX_VERSION.split(".")[0]):
        raise ValueError(f"COGX version {manifest['cogx_version']} is newer than this reader ({COGX_VERSION})")
    records = []
    for name in RECORD_FILES.values():
        f = path / name
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    records.append(json.loads(line))
    return manifest, records


# -- COGX → snapshot / facts ----------------------------------------------------------------------------

def from_cogx(records: list) -> tuple:
    """``(snapshot, foreign)``: the OpenWiki facts and sessions of an archive as a restorable snapshot
    (``GraphStore.restore_memory``), and the facts of other systems as dicts (``session_id``, ``subject``,
    ``predicate``, ``object``, ``valid_from``, ``closed``, ``source``) for ``remember`` — subject / object
    references resolved through the archive's entities. OpenWiki themes come back as ``snapshot["themes"]``
    (label, summary, members) — restoring them spares the next ``sleep`` the summaries; the identity is
    configured, not restored."""
    entities = {r["external_id"]: r.get("name") or r["external_id"] for r in records if r.get("kind") == "entity"}
    sessions: dict = {}
    for r in records:
        if r.get("kind") == "episode" and r.get("external_system") == SYSTEM:
            meta = (r.get("metadata") or {}).get(SYSTEM) or {}
            sessions[r["external_id"]] = [r["external_id"], epoch(r.get("created_at")), meta.get("session_date")]
    assertions, asserts, supersedes, foreign = [], [], [], []
    for r in records:
        if r.get("kind") != "fact":
            continue
        sid = (r.get("scope") or {}).get("session_id")
        meta = (r.get("metadata") or {}).get(SYSTEM)
        if r.get("external_system") != SYSTEM or meta is None:
            foreign.append({"session_id": sid or f"cogx-{r.get('external_system') or 'import'}",
                            "subject": entities.get(r["subject_ref"], r["subject_ref"]),
                            "predicate": r["predicate"],
                            "object": entities.get(r["object_ref"], r["object_ref"]),
                            "valid_from": epoch(r.get("valid_at")), "closed": r.get("invalid_at") is not None,
                            "source": "material"})          # another system's claim, not this user's decision
            continue
        created = epoch(r.get("created_at")) or 0
        confidence = r.get("confidence")
        assertions.append([r["external_id"], r["subject_ref"], r["predicate"], r["object_ref"], sid, created,
                           meta.get("embedding"), float(confidence if confidence is not None else 1.0),
                           int(meta.get("last_seen") or 0), epoch(r.get("valid_at")), epoch(r.get("invalid_at")),
                           epoch(meta.get("expired_at")), meta.get("cardinality") or "one", meta.get("attr"),
                           meta.get("source"), epoch(meta.get("forgotten_at")), meta.get("forgotten")])
        if sid:
            asserts.append([sid, r["external_id"]])
            sessions.setdefault(sid, [sid, created, None])
        for old in meta.get("supersedes") or []:
            supersedes.append([r["external_id"], old])
    themes = []
    for r in records:
        meta = (r.get("metadata") or {}).get(SYSTEM) or {}
        if r.get("kind") == "memory" and r.get("external_system") == SYSTEM and "theme_id" in meta:
            themes.append({"id": int(meta["theme_id"]), "label": (r.get("categories") or [""])[0],
                           "summary": r.get("content") or "", "members": list(meta.get("members") or [])})
    snap = {"sessions": sorted(sessions.values()), "assertions": assertions, "asserts": asserts,
            "supersedes": supersedes, "themes": sorted(themes, key=lambda t: t["id"])}
    return snap, foreign


def believed_only(snap: dict) -> dict:
    """The part of a snapshot OpenWiki believes: retracted (``expired_at``) and forgotten facts left out —
    what another system should import, since COGX has no notion of either. Current, past (a closed
    interval — COGX ``invalid_at``) and planned facts stay."""
    keep = [a for a in snap.get("assertions", []) if a[11] is None and a[15] is None]
    ids = {a[0] for a in keep}
    used = {a[4] for a in keep}
    return {"sessions": [s for s in snap.get("sessions", []) if s[0] in used],
            "assertions": keep,
            "asserts": [e for e in snap.get("asserts", []) if e[1] in ids],
            "supersedes": [e for e in snap.get("supersedes", []) if e[0] in ids and e[1] in ids],
            "themes": [dict(t, members=[m for m in t.get("members", []) if m in ids])
                       for t in snap.get("themes", [])]}


def current_facts(snap: dict) -> tuple:
    """``(facts, skipped)`` for a merge import (``--merge``): the snapshot's facts that hold — open interval,
    neither retracted nor forgotten — as ``from_cogx`` foreign dicts that keep their own source; ``skipped`` =
    the rest (history, retracted, forgotten), which a merge into another memory's timeline can't place."""
    facts, skipped = [], 0
    for a in snap.get("assertions", []):
        if a[10] is not None or a[11] is not None or a[15] is not None:
            skipped += 1
            continue
        facts.append({"session_id": a[4] or "cogx-openwiki", "subject": a[1], "predicate": a[2], "object": a[3],
                      "valid_from": a[9], "closed": False, "source": a[14], "cardinality": a[12]})
    return facts, skipped


def foreign_facts(entries: list) -> tuple:
    """``({session_id: [MemoryFact]}, closed)``: the entries that still hold, grouped by session for
    ``remember``; ``closed`` = how many no longer hold (``invalid_at`` set) and were skipped."""
    from .graph.memory import MemoryFact            # lazy: the graph package loads Kuzu
    groups: dict = {}
    closed = 0
    for e in entries:
        if e.get("closed"):
            closed += 1
            continue
        if not (str(e.get("subject") or "").strip() and str(e.get("predicate") or "").strip()
                and str(e.get("object") or "").strip()):
            continue
        groups.setdefault(e["session_id"], []).append(MemoryFact(
            subject=str(e["subject"]).strip(), predicate=str(e["predicate"]).strip(),
            object=str(e["object"]).strip(), valid_from=e.get("valid_from"),
            cardinality=e.get("cardinality") or "one", source=e.get("source")))
    return groups, closed


# -- the Markdown view ----------------------------------------------------------------------------------

def _day(t) -> str:
    return iso(t)[:10] if t is not None else "?"


def _text(value) -> str:
    """One line, credentials redacted — every value that reaches the view."""
    return redact_secrets(" ".join(str(value or "").split()))[0]


_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"})
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def _fold(text: str) -> str:
    """German umlauts spelled out, accents dropped from Latin letters (``café`` → ``cafe``); other scripts kept
    (``й`` stays ``й``)."""
    out = []
    for ch in unicodedata.normalize("NFKD", text.translate(_FOLD)):
        if unicodedata.combining(ch) and out and out[-1].isascii():
            continue
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out))


def _slug(key: str, taken: dict) -> str:
    """A file name for a subject: lower-case letters and digits joined by hyphens (``_fold``ed), never a Windows
    device name, unique — a second key on the same slug gets a stable hash suffix."""
    base = re.sub(r"[\W_]+", "-", _fold(key).lower()).strip("-")[:60].strip("-") or "subject"
    if base in _RESERVED:
        base += "-subject"
    slug = base
    if taken.get(slug, key) != key:
        slug = f"{base}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:6]}"
    taken[slug] = key
    return slug


def render_markdown(snap: dict, themes=(), identity: str = "") -> dict:
    """``{relative path: text}`` for the Markdown view: ``README.md`` (identity, counts, themes, the subjects) and
    one ``subjects/<slug>.md`` per subject — spellings that differ only in case or separators (space, hyphen,
    underscore, dot, slash) share one, titled by the most frequent — with its current facts and its history.
    Deterministic (no export time, stable order), so the files change only when the memory does. Forgotten facts
    are left out; retracted ones stay in the history, marked."""
    rows = [dict(zip(FIELDS, row)) for row in snap.get("assertions", [])]
    by_key: dict = {}
    for r in rows:
        if r["forgotten_at"] is None:
            subject = _text(r["subject"])
            key = re.sub(r"[\s._/-]+", " ", subject.lower()).strip() or subject.lower()
            by_key.setdefault(key, []).append(dict(r, subject=subject))
    taken: dict = {}
    files: dict = {}
    index = []
    for key in sorted(by_key):
        facts = by_key[key]
        spellings: dict = {}
        for r in facts:
            spellings[r["subject"]] = spellings.get(r["subject"], 0) + 1
        title = min(spellings, key=lambda sp: (-spellings[sp], sp))
        slug = _slug(key, taken)
        current = sorted((r for r in facts if r["valid_to"] is None and r["expired_at"] is None),
                         key=lambda r: (_text(r["predicate"]).lower(), _text(r["object"]).lower(),
                                        r["valid_from"] or 0, r["id"]))
        history = sorted((r for r in facts if r["valid_to"] is not None or r["expired_at"] is not None),
                         key=lambda r: (_text(r["predicate"]).lower(), r["valid_from"] or 0,
                                        _text(r["object"]).lower(), r["id"]))
        lines = [f"# {title}", ""]
        if current:
            lines += ["## Current", ""]
            for r in current:
                material = " · from discussed material" if r["source"] == "material" else ""
                lines.append(f"- **{_text(r['predicate'])}** {_text(r['object'])} — since {_day(r['valid_from'])} "
                             f"(`{_text(r['session_id'])}`{material})")
            lines.append("")
        if history:
            lines += ["## History", ""]
            for r in history:
                end = (f"retracted {_day(r['expired_at'])}" if r["expired_at"] is not None
                       else f"until {_day(r['valid_to'])}")
                lines.append(f"- ~~{_text(r['predicate'])} {_text(r['object'])}~~ — {_day(r['valid_from'])}, {end} "
                             f"(`{_text(r['session_id'])}`)")
            lines.append("")
        files[f"subjects/{slug}.md"] = "\n".join(lines).rstrip() + "\n"
        index.append((title, slug, len(current), len(history)))
    n_current = sum(c for _, _, c, _ in index)
    n_history = sum(h for _, _, _, h in index)
    readme = ["# Memory", "",
              "*Generated by OpenWiki (`owiki memory export` / `sleep`) — edits here are overwritten.*", ""]
    if identity:
        readme += ["## Identity", "", _text(identity), ""]
    readme += [f"**{n_current}** current facts and **{n_history}** in history, about **{len(index)}** subjects.", ""]
    if themes:
        readme += ["## Themes", ""]
        for t in themes:
            readme.append(f"- **{_text(t['label'])}** ({t.get('size') or 0} facts) — {_text(t.get('summary'))}")
        readme.append("")
    readme += ["## Subjects", ""]
    for title, slug, c, h in index:
        link = title.replace("[", "\\[").replace("]", "\\]")
        readme.append(f"- [{link}](subjects/{slug}.md) — {c} current" + (f", {h} past" if h else ""))
    files["README.md"] = "\n".join(readme).rstrip() + "\n"
    return files


def write_markdown(files: dict, out) -> tuple:
    """Write the view into ``out`` (owned: ``README.md`` and ``subjects/``; files nobody regenerated are removed,
    unchanged ones aren't touched) → ``(written, removed)``."""
    out = Path(out)
    (out / "subjects").mkdir(parents=True, exist_ok=True)
    written = removed = 0
    for rel, text in files.items():
        target = out / rel
        if not target.exists() or target.read_text(encoding="utf-8") != text:
            target.write_text(text, encoding="utf-8", newline="\n")
            written += 1
    keep = {(out / rel).resolve() for rel in files}
    for stale in (out / "subjects").glob("*.md"):
        if stale.resolve() not in keep:
            stale.unlink()
            removed += 1
    return written, removed
