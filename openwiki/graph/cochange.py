"""Git co-changes as a use signal for code corpora (v0.112) — files that change together in commits.

The graph's one learning-from-use signal, ``REINFORCES``, comes from GraphRAG expansion, which a coding workflow
never runs. How code is worked on is recorded anyway, in git: two files changed in the same commits are coupled in a
way their text need not show — a page script and its stylesheet, a module and the document that describes it, a
module and its test. Measured on this repository's history (``docs/path-b-memory.md`` §13.29): learned only from the
commits before each test commit, co-change ranked the files a commit changed together above content similarity and
popularity — best by raw count, with the files that change in most commits set aside. Edges are ``CO_CHANGED {count,
weight, last}`` between the pages of a code corpus (a page's title is its repo-relative path); a file's partners are
its most frequent co-changes; ``weight`` = count / √(changes of a · changes of b) is the association for display. Files
that change in at least half of the commits (the version files, here) are connected only to each other — next to an
ordinary file they say nothing specific.

Pure apart from one ``git log`` call; no Kuzu.
"""

from __future__ import annotations

import math
import subprocess
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional

MAX_COMMIT_FILES = 40     # a commit touching more files is a sweep (rename, reformat, vendoring), not a coupling
MIN_COUNT = 2             # a pair seen once is noise
TOP_K = 8                 # co-change neighbours kept per file
MAX_COMMITS = 2000        # history read, newest first
UBIQUITOUS_SHARE = 0.5    # a file changed in at least this share of the commits is "ubiquitous" …
UBIQUITOUS_MIN = 10       # … once the history has this many commits


def git_commits(repo, max_commits: int = MAX_COMMITS) -> list:
    """``[(epoch, [repo-relative paths]), …]``, newest first, from ``git log`` (renames followed by name: a renamed
    file's history before the rename stays with the old path). ``[]`` when ``repo`` is not a git repository or git is
    missing."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "log", f"-n{int(max_commits)}", "--name-only",
                              "--no-merges", "--format=@@%ct"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    commits, cur = [], None
    for line in out.stdout.splitlines():
        if line.startswith("@@"):
            cur = (int(line[2:].strip() or 0), [])
            commits.append(cur)
        elif line.strip() and cur is not None:
            cur[1].append(line.strip().replace("\\", "/"))
    return commits


def cochange_pairs(commits: Iterable, files: Optional[set] = None, max_files: int = MAX_COMMIT_FILES,
                   min_count: int = MIN_COUNT, top_k: int = TOP_K) -> list:
    """Co-change pairs of ``commits`` (``(epoch, paths)``), restricted to ``files`` when given:
    ``[(a, b, count, weight, last)]`` with ``a < b``, ``weight`` = count / √(n_a · n_b), ``last`` = the epoch of the
    latest commit that changed both. Commits touching more than ``max_files`` of the files are skipped; a pair is kept
    when it co-changed at least ``min_count`` times and is among the ``top_k`` most frequent partners of either file.
    Ubiquitous files (changed in ``UBIQUITOUS_SHARE`` of ``UBIQUITOUS_MIN``+ commits) pair only with each other."""
    n, pair, last = Counter(), Counter(), {}
    counted = 0
    for when, paths in commits:
        fs = sorted({p for p in paths if files is None or p in files})
        if not fs or len(fs) > max_files:
            continue
        counted += 1
        n.update(fs)
        for i, a in enumerate(fs):
            for b in fs[i + 1:]:
                pair[(a, b)] += 1
                last[(a, b)] = max(last.get((a, b), 0), int(when))
    ubiquitous = {f for f, c in n.items() if counted >= UBIQUITOUS_MIN and c >= UBIQUITOUS_SHARE * counted}
    weighted = {(a, b): c / math.sqrt(n[a] * n[b]) for (a, b), c in pair.items()
                if c >= min_count and (a in ubiquitous) == (b in ubiquitous)}
    best: dict = {}
    for (a, b) in weighted:
        best.setdefault(a, []).append((pair[(a, b)], b))
        best.setdefault(b, []).append((pair[(a, b)], a))
    keep = set()
    for f, partners in best.items():
        for _c, g in sorted(partners, key=lambda x: (-x[0], x[1]))[:top_k]:
            keep.add((f, g) if f < g else (g, f))
    return sorted(((a, b, pair[(a, b)], round(weighted[(a, b)], 4), last[(a, b)]) for a, b in keep),
                  key=lambda e: (-e[2], -e[3], e[0], e[1]))


def cochange_edges(pages: Iterable, repo, max_commits: int = MAX_COMMITS) -> list:
    """``CO_CHANGED`` edges for the pages of a code corpus: ``pages`` are ``(slug, title)`` with the title a
    repo-relative path (as ``CodeParser`` names them); titles that occur more than once (several repositories merged)
    are left out. → ``[(slug_a, slug_b, count, weight, last)]``."""
    titles: dict = {}
    for slug, title in pages:
        titles.setdefault(str(title).replace("\\", "/"), []).append(slug)
    by_path = {t: s[0] for t, s in titles.items() if len(s) == 1}
    root = Path(repo)
    commits = git_commits(root, max_commits=max_commits)
    if not commits:
        return []
    return [(by_path[a], by_path[b], c, w, t) for a, b, c, w, t in cochange_pairs(commits, set(by_path))]
