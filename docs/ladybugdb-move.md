# Moving the graph from Kuzu to LadybugDB — scope

*Status: scoped 2026-10-09, not started. Risk: arc42 §11 **R10**; earlier spike: `path-b-memory.md` §13.17, ADR-39.*

## Why, and how urgent

Kùzu Inc. archived Kuzu on 2025-10-10. OpenWiki runs pinned on Kuzu 0.11, which works on Python 3.10–3.13 and will
never get a 3.14 wheel (TC2). [LadybugDB](https://pypi.org/project/ladybug/) is the maintained fork: MIT, wheels for
Python 3.10–3.14 on Windows, Linux and macOS, a release every few weeks (0.19.0 at the v0.102 spike, 0.21.2 on
2026-10-01, daily 0.22 dev builds).

Since v0.102 the memory no longer depends on Kuzu's file format: `memory export --full` restores losslessly into any
engine, and the document tier is a rebuildable mirror. So the move is not urgent while 0.11 installs. It becomes
urgent when one of these happens:
- OpenWiki needs Python 3.14 (a dependency drops 3.13, or a platform ships without it);
- Kuzu 0.11 stops installing on a supported platform, or a data or security issue turns up in it;
- or the opposite — LadybugDB closes the gaps below upstream, and the move becomes mechanical.

## Where LadybugDB stands — re-checked 2026-10-09 with 0.21.2

A raw probe (`ladybug` without OpenWiki) and OpenWiki's suite through a `kuzu` compatibility shim:

| gap | 0.19.0 (v0.102 spike) | 0.21.2 |
|---|---|---|
| Windows wheel bundles OpenSSL | no — DLL workaround needed | **no** — without it the import falls back to a C-API backend that fails (`lbug_get_last_error not found`) |
| vector extension (HNSW) | `INSTALL VECTOR` (download) + `LOAD` per database | **same**; create + query work after `INSTALL` |
| prepared statements after DDL | cached by query text, never invalidated | **same** — a cached query still returns a column `ALTER … DROP` removed |
| a reader keeps a writer out | no — the reader keeps a stale view | **no**, same |
| a writer keeps a reader out | yes | **no** — a reader now opens under a writer (in-process and across processes) |
| two writers | refused | refused |
| OpenWiki suite through the shim | 643 / 645 | **710 / 714** — 3 lock tests (`LazyGraph`, two-phase writes) + 1 test whose subprocess doesn't see the shim |

The gaps persist and the locking one widened: LadybugDB now lets readers and a writer open the same file together,
which its own documentation forbids (a reader's stale cache can corrupt data). OpenWiki's concurrency design (ADR-19,
ADR-38) depends on the engine refusing that combination.

## Work packages

1. **Engine adapter** (small). One module, `openwiki/graph/engine.py`, becomes the only importer of the driver
   (TC5 today: `builder.py` and `store.py`). It applies the Windows OpenSSL workaround, loads the vector extension
   (and installs it on first use, with a clear error when offline), and gives a fresh statement cache after schema
   changes — by opening a new connection after OpenWiki's DDL (the builder's schema, `_ensure_memory_schema`, the
   in-place migrations) rather than clearing the driver's private cache as the spike shim did.
2. **An OpenWiki reader/writer lock** (medium — the risky part). A lock file next to the graph: shared for readers
   (a read-only `GraphStore`, `LazyGraph` per call), exclusive for writers (a writable `GraphStore`, `GraphBuilder`).
   OS file locks — `fcntl.flock` on POSIX, `LockFileEx` through `ctypes` on Windows — so a crashed process releases
   its lock. A refused lock raises the error the engine raises today, so `LazyGraph`'s 15-s wait, `_open_writer`'s
   60-s wait and the journal fallback keep their meaning unchanged. The three failing lock tests become its tests.
3. **Migrating existing graphs** (medium). `openwiki graph-migrate` detects a Kuzu file and converts it:
   - *route A* — Kuzu `EXPORT DATABASE` → LadybugDB `IMPORT DATABASE` (Kuzu installed for this step only). It keeps
     every layer, entities and communities included; on the dev graph in v0.102 it took 0.5 s + 2.8 s;
   - *route B* — `memory export --full` → `graph-build` → `memory import`. It needs no Kuzu, but re-runs whatever the
     build computes, entity extraction included (slow on informatik).

   Either way: a fingerprint check (table counts, the facts, recall for fixed queries), and the old file kept as a
   backup.
4. **Packaging and platforms** (small). Pin `ladybug` exactly — behaviour changed between minor versions. Allow
   Python 3.14 if the Windows OpenSSL workaround holds there (CPython 3.14's DLL names). Add 3.14 to the CI matrix,
   and run `INSTALL VECTOR` when the Docker image is built.
5. **Docs** (small). A new ADR; R10 closed; TC2 / TC4 / TC5 / TC8 / LC2. Re-verify the Kuzu notes in `CLAUDE.md`
   (vector API, `find_path` syntax, single-file database, the `DROP_VECTOR_INDEX` bug) and the deployment view.

## Gates — checks before the switch

- **G1** The suite passes natively on Windows and Linux, the lock tests against the OpenWiki lock.
- **G2** The dev graph migrates by route A with an identical fingerprint, apart from approximate vector search (top-1
  identical, top-5 overlap ≥ 95 %).
- **G3** Memory behaviour unchanged: the temporal, poisoning and cue-trigger sets, and recall for 20 fixed queries.
- **G4** Concurrency under load: a live MCP session, a capture worker and `sleep` at once — no refused write lost, no
  corruption (fingerprint after).
- **G5** Offline: with the extension installed, no network access at runtime; on first use without network, a clear
  message.

## Risks

- **A moving target.** Four weeks took 0.19 → 0.21.2, and the lock behaviour changed in between. Pin exactly, and
  re-run the probe and the suite before every upgrade.
- **A runtime download.** `INSTALL VECTOR` fetches a binary from extension.ladybugdb.com. Do it at setup or image
  build, and consider pinning its checksum or vendoring it per platform (local-first, Q1).
- **The Windows OpenSSL workaround** depends on CPython's DLL names.
- **Storage format across LadybugDB versions** is undocumented. Take a `memory export --full` before each engine
  upgrade.
- **Approximate vector search** returns slightly different neighbours after the HNSW index is rebuilt.

## Recommendation

Not now — prepare, and let a trigger decide. Three low-regret steps:
1. Report the four gaps upstream: the statement cache after DDL, a reader and a writer opening together, OpenSSL
   missing from the Windows wheels, the vector extension not bundled. Fixes there shrink packages 1–2 to almost nothing.
2. Keep a lossless memory backup: a periodic `memory export --full`, for example weekly next to `sleep`.
3. Re-run the probe (raw `ladybug`, the three gaps) and the suite through the shim before any decision.

When a trigger fires, the order is packages 1 → 2 → 3 → 4 → 5, through gates G1–G5.
