"""Build orchestration state for ``openwiki build`` (roadmap Phase 2).

Pure, testable helpers: a per-stage **fingerprint chain** plus the
``.openwiki/state.json`` lockfile. Together they make ``build`` incremental (skip a
stage whose inputs + params are unchanged) and let ``status`` report staleness. The
actual stage *execution* lives in the CLI, which wires PDFParser / WikiBuilder /
SemanticIndex / GraphBuilder — this module stays dependency-light and unit-testable.

The fingerprints form a chain so an upstream change invalidates everything below it:

    ingest  = f(sources, tables)
    wiki    = f(ingest, split_level, tables)
    index   = f(ingest, split_level, chunk_size, overlap, embed_model)
    graph   = f(index, split_level, similar_k, references, entities[, entity_model])
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Iterable, Optional

from .project import Project

STAGES = ("ingest", "wiki", "index", "graph")
STATE_FILE = "state.json"


def _hash(*parts) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(repr(part).encode("utf-8"))
    return digest.hexdigest()[:16]


def file_sig(path: Path) -> Optional[list]:
    """A cheap content signature: (name, size, mtime_ns). ``None`` if missing."""
    try:
        st = path.stat()
    except OSError:
        return None
    return [path.name, st.st_size, st.st_mtime_ns]


def sources_signature(paths: Iterable[Path]) -> str:
    return _hash([file_sig(Path(p)) for p in paths])


def compute_fingerprints(project: Project, sources: Iterable[Path]) -> dict:
    """The current fingerprint of each stage from the manifest + source signature."""
    build = project.section("build")
    models = project.section("models")
    graph = project.section("graph")
    split = build.get("split_level", 2)
    tables = build.get("tables", True)
    entities = graph.get("entities", False)

    src = sources_signature(sources)
    ingest = _hash("ingest", src, tables, build.get("synthesize_outline", True))
    wiki = _hash("wiki", ingest, split, tables)
    index = _hash(
        "index", ingest, split,
        build.get("chunk_size", 180), build.get("overlap", 30),
        models.get("embed", "bge-m3"),
    )
    graph_fp = _hash(
        "graph", index, split,
        graph.get("similar_k", 6), graph.get("references", True), entities,
        (models.get("chat", ""), graph.get("entity_types"), graph.get("entity_max_chars"))
        if entities else "",
    )
    return {"ingest": ingest, "wiki": wiki, "index": index, "graph": graph_fp}


class BuildState:
    """The ``.openwiki/state.json`` lockfile: per-stage fingerprint + stats."""

    def __init__(self, path: Path, data: Optional[dict] = None) -> None:
        self.path = path
        self.data = data or {"version": 1, "stages": {}}

    @classmethod
    def load(cls, project: Project) -> "BuildState":
        path = project.state_dir / STATE_FILE
        if path.is_file():
            try:
                return cls(path, json.loads(path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                pass
        return cls(path)

    def get(self, stage: str) -> dict:
        return self.data.get("stages", {}).get(stage, {})

    def fingerprint(self, stage: str) -> Optional[str]:
        return self.get(stage).get("fingerprint")

    def record(self, stage: str, fingerprint: str, output, stats: Optional[dict] = None) -> None:
        self.data.setdefault("stages", {})[stage] = {
            "fingerprint": fingerprint,
            "output": str(output),
            "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "stats": stats or {},
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def stale_stages(
    state: BuildState,
    fingerprints: dict,
    outputs_exist: dict,
    only: Optional[set] = None,
    force: bool = False,
) -> list:
    """Stages that must run: fingerprint changed, output missing, or ``force``."""
    result = []
    for stage in STAGES:
        if only is not None and stage not in only:
            continue
        if (force
                or state.fingerprint(stage) != fingerprints.get(stage)
                or not outputs_exist.get(stage, False)):
            result.append(stage)
    return result
