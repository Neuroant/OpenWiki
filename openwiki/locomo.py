"""LoCoMo — the public long-conversation memory benchmark (snap-research/locomo), for Path B.

LoCoMo (Maharana et al., 2024) is 10 very long two-person conversations — 19–32 dated sessions each, ~300 turns —
with ~2,000 questions in five categories (numbering as in LoCoMo's own evaluation code): **1 multi-hop**,
**2 temporal**, **3 open-domain** (commonsense on top of the conversation), **4 single-hop**, **5 adversarial**
(asks about something that never happened — the right answer is "not mentioned"). It is the benchmark memory
systems (Mem0, Zep, LangMem, A-Mem, …) report on, so it gives OpenWiki its first externally comparable number.

The run mirrors how the memory tier is used: every session is **captured** into facts (``capture_session``,
resolved against the session's date) and **remembered** into a per-conversation graph (B7 valid time + B9 + the
coexistence check, as in production); each question is then answered from the **assembled** recall
(``recall(k)`` → ``assemble_context``) by the chat model and scored with token **F1** and an **LLM judge** (the
"J" score of the Mem0 paper; category 5 by the "not mentioned" pattern). The dataset is not bundled (its
license is not an open one) — pass the downloaded ``locomo10.json``.

Pure + backend-agnostic: the graph (``open_graph(path)``), embedder and chat model are injected, so the runner is
unit-tested with fakes. **Resumable + time-budgeted**: per conversation, the graph and the answers persist in a
work directory; a run stops cleanly when ``budget_s`` is spent and the next run continues where it stopped.

**Phased, to avoid model swaps.** On a 12 GB GPU the 30B chat model and the embedder don't fit together, so every
embed call between two chat calls reloads a model (measured: ~58 s per session, ~26 s per question). The runner
therefore groups the calls — capture every session (chat), embed all captured facts in one batch, remember them
(chat: coexistence / attribute checks), embed all questions in one batch, answer + judge (chat) — through a
:class:`CachingEmbedder` that serves the store's later embed calls from memory.
"""

from __future__ import annotations

import json
import re
import string
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .embeddings import CachingEmbedder  # noqa: F401  (re-exported: the harness's embed-ahead cache)

CATEGORIES = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop", 5: "adversarial"}
NOT_MENTIONED = "Not mentioned"
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], 1)}
_DATE = re.compile(r"(\d{1,2}):(\d{2})\s*(am|pm)\s+on\s+(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})", re.I)
_ABSTAIN = re.compile(r"\b(not mentioned|no information|not (specified|stated|discussed|known)|"
                      r"(don'?t|do not) know|unknown|no mention|cannot (be )?determine|not in the)", re.I)


@dataclass
class LocomoSession:
    sid: str
    date: Optional[int]      # epoch seconds (UTC) of the session's stated date + time
    text: str                # "Session of …\nCaroline: …\nMelanie: …"
    turns: list = field(default_factory=list)    # [(speaker, text)] in order — for session search (v0.108)


@dataclass
class LocomoQA:
    question: str
    answer: str
    category: int
    evidence: list = field(default_factory=list)


@dataclass
class LocomoConversation:
    sample_id: str
    speakers: tuple
    sessions: list
    qa: list

    @property
    def present(self) -> Optional[int]:
        """The conversation's "now": the day after its last session (recall's recency reference)."""
        dates = [s.date for s in self.sessions if s.date is not None]
        return max(dates) + 86400 if dates else None


def parse_locomo_date(text) -> Optional[int]:
    """``"1:56 pm on 8 May, 2023"`` → epoch seconds (UTC); ``None`` if it doesn't parse."""
    m = _DATE.search(str(text or ""))
    if not m:
        return None
    hour, minute, ampm, day, month, year = m.groups()
    mon = _MONTHS.get(month.lower())
    if mon is None:
        return None
    h = int(hour) % 12 + (12 if ampm.lower() == "pm" else 0)
    return int(datetime(int(year), mon, int(day), h, int(minute), tzinfo=timezone.utc).timestamp())


def _turn_body(turn: dict) -> str:
    text = str(turn.get("text") or "").strip()
    if turn.get("blip_caption"):                     # a shared photo, described in the data
        text = f"{text} [shares a photo: {turn['blip_caption']}]".strip()
    return text


def _turn_text(turn: dict) -> str:
    return f"{turn.get('speaker', '?')}: {_turn_body(turn)}"


def load_locomo(path) -> list:
    """Read ``locomo10.json`` into :class:`LocomoConversation` s (sessions in order, with dates; the QA list —
    an adversarial question's gold answer is :data:`NOT_MENTIONED`)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for item in data:
        conv = item.get("conversation", {})
        nums = sorted(int(k.split("_")[1]) for k in conv
                      if re.fullmatch(r"session_\d+", k) and isinstance(conv[k], list))
        sessions = []
        for n in nums:
            when = conv.get(f"session_{n}_date_time", "")
            body = "\n".join(_turn_text(t) for t in conv[f"session_{n}"])
            sessions.append(LocomoSession(f"{item.get('sample_id', 'conv')}-s{n}", parse_locomo_date(when),
                                          f"Session of {when}.\n{body}",
                                          [(str(t.get("speaker", "?")), _turn_body(t)) for t in conv[f"session_{n}"]]))
        qa = []
        for q in item.get("qa", []):
            cat = int(q.get("category", 0))
            gold = NOT_MENTIONED if cat == 5 else str(q.get("answer", "")).strip()
            qa.append(LocomoQA(str(q.get("question", "")).strip(), gold, cat, list(q.get("evidence") or [])))
        out.append(LocomoConversation(str(item.get("sample_id", f"conv-{len(out)}")),
                                      (conv.get("speaker_a"), conv.get("speaker_b")), sessions, qa))
    return out


# -- scoring -------------------------------------------------------------------------

def normalize_answer(text) -> str:
    """LoCoMo/SQuAD-style normalization: lowercase, drop punctuation and articles, fold whitespace."""
    t = str(text or "").lower()
    t = "".join(ch for ch in t if ch not in set(string.punctuation))
    t = re.sub(r"\b(a|an|the|and)\b", " ", t)
    return " ".join(t.split())


def f1(prediction, gold) -> float:
    """Token F1 between a prediction and the gold answer (after :func:`normalize_answer`)."""
    p, g = normalize_answer(prediction).split(), normalize_answer(gold).split()
    if not p or not g:
        return float(p == g)
    common = sum((Counter(p) & Counter(g)).values())
    if common == 0:
        return 0.0
    precision, recall = common / len(p), common / len(g)
    return 2 * precision * recall / (precision + recall)


def abstains(prediction) -> bool:
    """Does the answer say the conversation doesn't contain it? (Category 5 is correct iff it does.)"""
    return bool(_ABSTAIN.search(str(prediction or "")))


ANSWER_SYSTEM = (
    "You answer questions about a long conversation between two people, from your memory of it: facts "
    "remembered from its sessions, each with the date it held. Answer with a short phrase — a few words, no "
    "explanation. For 'when' questions give the date, month or year from the memories (resolve relative "
    "expressions like 'last week' against the date shown). If the memories do not contain the answer, reply "
    f"exactly: {NOT_MENTIONED}.")

# "infer" (the default): the strict prompt answered "Not mentioned" to 57 % of open-domain questions ("what would
# Caroline likely pursue?"). Measured paired on the same graphs: overall J 50.5 → 55.0 % (+45 single-hop, +10
# multi-hop, +8 temporal, +7 open-domain) for −19 adversarial (path-b-memory.md §13.9).
ANSWER_SYSTEM_INFER = (
    "You answer questions about a long conversation between two people, from your memory of it: facts "
    "remembered from its sessions, each with the date it held. Answer with a short phrase — a few words, no "
    "explanation. For 'when' questions give the date, month or year from the memories (resolve relative "
    "expressions like 'last week' against the date shown). When the question asks what someone would likely "
    "do, prefer, think or be — or the answer follows from the remembered facts without being stated — give "
    "your best inference from them. Reply exactly "
    f"{NOT_MENTIONED} only when nothing remembered bears on the question, or when it asks about something the "
    "memories attribute to someone else or never mention happening.")
ANSWER_STYLES = {"strict": ANSWER_SYSTEM, "infer": ANSWER_SYSTEM_INFER}

JUDGE_SYSTEM = (
    "You label a generated answer to a question about a conversation as CORRECT or WRONG, given the gold answer. "
    "Be generous: if the generated answer captures the same fact as the gold answer — even phrased differently, "
    "longer, or more specific — it is CORRECT. For time questions the same date or period in any format counts "
    "(\"May 7th\" = \"7 May 2023\"). An answer saying it is not mentioned is WRONG unless the gold says so. "
    "Reply with one word: CORRECT or WRONG.")


def build_answer_messages(question: str, context: str, style: str = "infer") -> list:
    user = (f"{context}\n\nQuestion: {question}" if context.strip()
            else f"(Nothing is remembered about this.)\n\nQuestion: {question}")
    return [{"role": "system", "content": ANSWER_STYLES[style]}, {"role": "user", "content": user}]


def judge_answer(chat, question: str, gold: str, prediction: str) -> bool:
    """The J score of one answer (an LLM judge, Mem0-style generous matching)."""
    raw = chat.chat([{"role": "system", "content": JUDGE_SYSTEM},
                     {"role": "user", "content": f"Question: {question}\nGold answer: {gold}\n"
                                                 f"Generated answer: {prediction}"}]) or ""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip().upper()
    return raw.startswith("CORRECT")


def summarize(records) -> dict:
    """Per-category and overall F1 / J. ``overall`` covers categories 1–4 (what memory papers report);
    adversarial (5) is reported on its own."""
    by: dict = {}
    for r in records:
        b = by.setdefault(r["category"], {"n": 0, "f1": 0.0, "j": 0})
        b["n"] += 1
        b["f1"] += r["f1"]
        b["j"] += 1 if r["j"] else 0
    table = {CATEGORIES.get(c, str(c)): {"n": b["n"], "f1": round(b["f1"] / b["n"], 3),
                                         "j": round(b["j"] / b["n"], 3)} for c, b in sorted(by.items())}
    main = [r for r in records if r["category"] in (1, 2, 3, 4)]
    overall = ({"n": len(main), "f1": round(sum(r["f1"] for r in main) / len(main), 3),
                "j": round(sum(1 for r in main if r["j"]) / len(main), 3)} if main else {"n": 0})
    return {"by_category": table, "overall": overall}


# -- the resumable runner ---------------------------------------------------------------

def _retry(fn, pause: float = 20.0):
    """One retry after a pause: a long local run meets transient model-server failures (measured: an
    Ollama "CUDA error: an illegal memory access" mid-run; the next call, after a reload, worked)."""
    try:
        return fn()
    except Exception:
        time.sleep(pause)
        return fn()


def _read_jsonl(path: Path) -> list:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_locomo(conversations, work_dir, open_graph: Callable, embedder, chat, judge=None, recall_k: int = 10,
               categories=None, budget_s: Optional[float] = None, coexist=None, resolve=None,
               on_progress: Optional[Callable] = None, now_mode: str = "present",
               answer_style: str = "infer", capture_style: str = "durable", lexical: float = 0.0,
               temporal: float = 0.0, reuse_base: bool = False, merge: str = "checks", episodes: int = 0,
               excerpts: int = 0, excerpt_window: int = 1) -> dict:
    """Capture + remember each conversation (once — resumable per session), then answer + score its questions
    (resumable per question). ``open_graph(path)`` returns a **writable** memory graph at ``path`` (created if
    absent). Stops when ``budget_s`` seconds are spent (``complete: False``); the next call continues.
    ``now_mode``: recall's reference time — ``"present"`` (the day after the conversation's last session) or
    ``"today"`` (the wall clock: every session equally old, recency neutral). ``lexical``: recall's BM25 weight
    (``GraphStore.recall``; 0 = dense only); ``temporal``: the question's time-window weight. ``reuse_base``: where
    the variant recalls exactly the list of the variant one step back (``temporal`` → without it; ``lexical``
    alone → dense), that variant's answer is copied (``"reused": true``) instead of asked again — the prompt would
    be identical, so a paired comparison gains no noise where nothing changed. ``merge``: ``checks`` (production —
    ``coexist`` / ``resolve`` as given), ``tags`` (neither LLM check) or ``add-only`` (every fact ``"many"``, no checks:
    nothing superseded) — an ablation; use a separate ``work_dir`` per mode. ``episodes`` (v0.106): one dated
    narrative per session (``memory.narrate_session``, kept in ``episodes.jsonl``, written once — resumable like the
    capture) and the ``episodes`` most similar to each question shown next to its facts, in date order.
    ``excerpts`` (v0.108): session search — the ``excerpts`` turns of the conversation that best match the question
    by full text (``sessions.SessionIndex``, BM25), each with ``excerpt_window`` turns on either side, shown verbatim
    and dated next to its facts (the sessions need their ``turns``). Returns ``{"complete", "records", "summary"}``
    over every answered question in ``work_dir``."""
    from .graph.memory import MemoryFact, assemble_context, capture_session, narrate_session
    from .graph.temporal import format_date
    from .sessions import SessionIndex, Turn

    t0 = time.time()
    work = Path(work_dir)
    cats = set(categories) if categories else set(CATEGORIES)
    over = (lambda: budget_s is not None and time.time() - t0 > budget_s)
    emb = embedder if isinstance(embedder, CachingEmbedder) else CachingEmbedder(embedder)
    records, complete = [], True
    for conv in conversations:
        cdir = work / conv.sample_id
        cdir.mkdir(parents=True, exist_ok=True)
        done_path, cap_path = cdir / "sessions.json", cdir / "captured.jsonl"
        core = "".join(f"-{t}" for t in (now_mode if now_mode != "present" else "", answer_style,
                                         f"k{recall_k}" if recall_k != 10 else "") if t)
        lex_tag = f"-lex{lexical:g}" if lexical else ""
        tag = (core + lex_tag + (f"-tw{temporal:g}" if temporal else "") + (f"-ep{episodes}" if episodes else "")
               + (f"-ex{excerpts}" + (f"w{excerpt_window}" if excerpt_window != 1 else "") if excerpts else ""))
        turn_index = SessionIndex(Turn(s.sid, n, format_date(s.date), speaker, text)
                                  for s in conv.sessions for n, (speaker, text) in enumerate(s.turns)) \
            if excerpts else None
        ans_path = cdir / f"answers{tag}.jsonl"             # each variant keeps its own answers
        base_tag, base_kw = ((core + lex_tag, {"lexical": lexical}) if temporal else (core, {}))
        base_answers = ({r["i"]: r for r in _read_jsonl(cdir / f"answers{base_tag}.jsonl")}  # never with more
                        if reuse_base and (lexical or temporal) and not (episodes or excerpts) else {})  # context
        done = json.loads(done_path.read_text(encoding="utf-8")) if done_path.is_file() else []
        captured = {r["sid"]: r["facts"] for r in _read_jsonl(cap_path)}
        answers = {r["i"]: r for r in _read_jsonl(ans_path)}
        todo_q = [(i, q) for i, q in enumerate(conv.qa) if q.category in cats and i not in answers]
        todo_s = [s for s in conv.sessions if s.sid not in done]
        for s in todo_s:                                               # 1. capture (chat only)
            if s.sid in captured or over():
                continue
            facts = _retry(lambda: capture_session(chat, s.text, session_date=s.date, style=capture_style))
            captured[s.sid] = [[f.subject, f.predicate, f.object, f.valid_from, f.cardinality, f.source]
                               for f in facts]
            with cap_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"sid": s.sid, "facts": captured[s.sid]}, ensure_ascii=False) + "\n")
            if on_progress:
                on_progress(f"{conv.sample_id}: captured {len(captured)}/{len(conv.sessions)} sessions")
        ep_path = cdir / "episodes.jsonl"
        narrated = {r["sid"]: r for r in _read_jsonl(ep_path)} if episodes else {}
        for s in (conv.sessions if episodes and todo_q else []):          # 1b. episodes (chat only)
            if s.sid in narrated or over():
                continue
            text = _retry(lambda: narrate_session(chat, s.text, session_date=s.date))
            narrated[s.sid] = {"sid": s.sid, "date": s.date, "text": text}
            with ep_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(narrated[s.sid], ensure_ascii=False) + "\n")
        ep_list = [narrated[s.sid] for s in conv.sessions if s.sid in narrated] if episodes else []
        ready = []                                   # remember in order: the captured prefix only
        for s in todo_s:
            if s.sid not in captured:
                break
            ready.append(s)
        if (ready or (todo_q and len(done) == len(conv.sessions))) and not over():
            graph = open_graph(cdir / "graph")
            try:
                as_facts = {sid: [MemoryFact(t[0], t[1], t[2], valid_from=t[3],
                                             cardinality="many" if merge == "add-only" else (t[4] or "one"),
                                             source=t[5]) for t in rows] for sid, rows in captured.items()}
                if merge != "checks":                                   # the ablation: no LLM checks in the merge
                    coexist = resolve = None
                emb.warm(docs=[f.text() for s in ready for f in as_facts[s.sid]])      # 2. one embed batch
                for s in ready:                                                   # 3. remember (chat only)
                    if over():
                        break
                    graph.remember(s.sid, as_facts[s.sid], emb, session_date=s.date,
                                   coexist=coexist, resolve=resolve)
                    done.append(s.sid)
                    done_path.write_text(json.dumps(done), encoding="utf-8")
                    if on_progress:
                        on_progress(f"{conv.sample_id}: remembered {len(done)}/{len(conv.sessions)} sessions")
                if len(done) == len(conv.sessions) and todo_q and not over() and \
                        (not episodes or len(ep_list) == len(conv.sessions)):
                    emb.warm(queries=[q.question for _, q in todo_q])                 # 4. one embed batch
                    ep_vecs = None
                    if ep_list:
                        import numpy as np
                        ep_vecs = np.asarray(emb.embed_documents([e["text"] for e in ep_list]), dtype=np.float32)
                        ep_vecs /= np.where(np.linalg.norm(ep_vecs, axis=1, keepdims=True) == 0, 1.0,
                                            np.linalg.norm(ep_vecs, axis=1, keepdims=True))
                    now = conv.present if now_mode == "present" else None
                    for i, q in todo_q:                                              # 5. answer + judge
                        if over():
                            break
                        hits = graph.recall(q.question, emb, k=recall_k, now=now, lexical=lexical,
                                            temporal=temporal)
                        base = base_answers.get(i)
                        if base is not None and [h["id"] for h in hits] == \
                                [h["id"] for h in graph.recall(q.question, emb, k=recall_k, now=now, **base_kw)]:
                            rec = dict(base, reused=True)                # the same prompt: the same answer
                        else:
                            shown = []
                            if ep_vecs is not None:
                                qv = np.asarray(emb.embed_query(q.question), dtype=np.float32)
                                qv /= np.linalg.norm(qv) or 1.0
                                best = sorted(np.argsort(-(ep_vecs @ qv))[:episodes])
                                shown = sorted((ep_list[n] for n in best), key=lambda e: e.get("date") or 0)
                            found = []
                            if turn_index is not None:
                                found = [f"[{t['ts'] or 'undated'}] {t['speaker']}: {t['text']}"
                                         for e in turn_index.search(q.question, k=excerpts, context=excerpt_window)
                                         for t in e["turns"]]
                            ctx = assemble_context("", hits, [], max_facts=recall_k, episodes=shown,
                                                   excerpts=found)
                            raw = _retry(lambda: chat.chat(build_answer_messages(q.question, ctx,
                                                                                 answer_style))) or ""
                            pred = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
                            ok = (abstains(pred) if q.category == 5
                                  else _retry(lambda: judge_answer(judge or chat, q.question, q.answer, pred)))
                            rec = {"i": i, "category": q.category, "question": q.question, "gold": q.answer,
                                   "prediction": pred, "f1": round(f1(pred, q.answer), 4), "j": bool(ok),
                                   "recalled": len(hits)}
                        answers[i] = rec
                        with ans_path.open("a", encoding="utf-8") as fh:
                            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        if on_progress and len(answers) % 10 == 0:
                            on_progress(f"{conv.sample_id}: answered {len(answers)} questions")
            finally:
                graph.close()
        left_s = len(conv.sessions) - len(done)
        left_q = sum(1 for i, q in enumerate(conv.qa) if q.category in cats and i not in answers)
        complete = complete and not left_s and not left_q
        records += [r for r in answers.values() if r["category"] in cats]
    return {"complete": complete, "records": records, "summary": summarize(records),
            "seconds": round(time.time() - t0)}


# -- the LoCoMo guard (Path C, M.2) -----------------------------------------------------
# LoCoMo has no sensors, so a Path C change must not move what any of its questions gets to see
# (path-c-roadmap.md §4). The guard freezes what is slow and noisy — the capture, the merge's model checks,
# the embeddings — and rebuilds the memory from it with the current code on every run: a context that differs
# from the recorded baseline is a change in the code, never noise. Answers are kept per (question, context),
# so J is paired by construction — an unchanged context keeps its answer.

class RecordedChecks:
    """The merge's model checks (``coexist`` / ``resolve``) with every answer kept in a JSONL file: a later run
    asks the model only what no run asked before (``asked`` counts those), so the same captures and the same
    code rebuild the same memory."""

    def __init__(self, path, coexist=None, resolve=None) -> None:
        self.path = Path(path)
        self._checks = {"coexist": coexist, "resolve": resolve}
        self.answers: dict = {}
        self.asked = 0
        for row in _read_jsonl(self.path):
            self.answers[(row["kind"], json.dumps(row["args"], ensure_ascii=False))] = row["answer"]

    def _ask(self, kind: str, args: list, call: Callable):
        key = (kind, json.dumps(args, ensure_ascii=False))
        if key not in self.answers:
            answer = call()                       # a failed call raises — nothing is recorded
            self.answers[key] = answer
            self.asked += 1
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"kind": kind, "args": args, "answer": answer}, ensure_ascii=False) + "\n")
        return self.answers[key]

    @property
    def coexist(self):
        check = self._checks["coexist"]
        if check is None:
            return None
        return lambda older, newer, subjects=None: self._ask(
            "coexist", [older, newer, list(subjects) if subjects else None],
            lambda: bool(check(older, newer, subjects=subjects)))

    @property
    def resolve(self):
        check = self._checks["resolve"]
        if check is None:
            return None
        return lambda fact, labels: self._ask("resolve", [fact, list(labels)], lambda: check(fact, labels))


def _write_jsonl(path: Path, rows) -> None:
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(path)


def _context_hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _guard_row(check: str, now: str, expected: str, status: str, misses=()) -> dict:
    return {"guard": "locomo", "check": check, "now": now, "expected": expected, "status": status,
            "misses": list(misses)}


def guard_locomo(conversations, work_dir, open_graph: Callable, embedder, chat, judge=None, coexist=None,
                 resolve=None, recall_k: int = 20, lexical: float = 0.0, temporal: float = 0.0,
                 answer_style: str = "infer", budget_s: Optional[float] = None, record: bool = False,
                 code: str = "", on_progress: Optional[Callable] = None) -> dict:
    """The LoCoMo guard. Per conversation, in ``work_dir/<sample_id>/``: capture each session once
    (``captured.jsonl`` — chat only, frozen from then on), rebuild the memory from those captures into a fresh graph
    (``open_graph(path)`` returns it writable and empty) with the merge's model checks answered from
    ``checks.jsonl`` where asked before (``RecordedChecks``) and the vectors from ``embeddings.npz``, assemble every
    question's context as ``run_locomo`` does and compare it with the baseline — ``baseline.jsonl``, written by the
    first complete run or with ``record`` (the one it replaces kept under its date). Then each question whose
    context has no answer yet is answered and judged (``answers.jsonl``, keyed by question, context, model and
    answer style) while ``budget_s`` lasts. Returns ``{"rows", "complete", "seconds"}`` — rows for
    ``eval.format_guards``."""
    from .graph.memory import MemoryFact, assemble_context, capture_session

    t0 = time.time()
    over = (lambda: budget_s is not None and time.time() - t0 > budget_s)
    model = getattr(chat, "name", "")
    rows, complete = [], True
    for conv in conversations:
        cdir = Path(work_dir) / conv.sample_id
        cdir.mkdir(parents=True, exist_ok=True)
        label = f"contexts unchanged ({conv.sample_id})"
        cap_path = cdir / "captured.jsonl"
        captured = {r["sid"]: r["facts"] for r in _read_jsonl(cap_path)}
        for s in conv.sessions:                                            # 1. capture, once (chat only)
            if s.sid in captured or over():
                continue
            facts = _retry(lambda: capture_session(chat, s.text, session_date=s.date))
            captured[s.sid] = [[f.subject, f.predicate, f.object, f.valid_from, f.cardinality, f.source]
                               for f in facts]
            with cap_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"sid": s.sid, "facts": captured[s.sid]}, ensure_ascii=False) + "\n")
            if on_progress:
                on_progress(f"{conv.sample_id}: captured {len(captured)}/{len(conv.sessions)} sessions")
        n_sessions = len(conv.sessions)
        left = sum(1 for s in conv.sessions if s.sid not in captured)
        if left or over():
            rows.append(_guard_row(label, f"{n_sessions - left}/{n_sessions} captured", "all", "incomplete"))
            complete = False
            continue
        emb = CachingEmbedder(embedder)
        emb.load(cdir / "embeddings.npz")
        checks = RecordedChecks(cdir / "checks.jsonl", coexist, resolve)
        as_facts = {sid: [MemoryFact(t[0], t[1], t[2], valid_from=t[3], cardinality=t[4] or "one", source=t[5])
                          for t in held] for sid, held in captured.items()}
        contexts, rebuilt = [], True
        graph = open_graph(cdir / "guard-graph")                           # 2. rebuild with this code
        try:
            emb.warm(docs=[f.text() for s in conv.sessions for f in as_facts[s.sid]])
            for n, s in enumerate(conv.sessions, 1):
                if over():
                    rebuilt = False
                    break
                graph.remember(s.sid, as_facts[s.sid], emb, session_date=s.date, coexist=checks.coexist,
                               resolve=checks.resolve)
                if on_progress and (n % 5 == 0 or n == n_sessions):
                    on_progress(f"{conv.sample_id}: rebuilt {n}/{n_sessions} sessions "
                                f"({checks.asked} model check(s) asked anew)")
            if rebuilt:                                                    # 3. every question's context
                emb.warm(queries=[q.question for q in conv.qa])
                for q in conv.qa:
                    hits = graph.recall(q.question, emb, k=recall_k, now=conv.present, lexical=lexical,
                                        temporal=temporal)
                    contexts.append(assemble_context("", hits, [], max_facts=recall_k))
        finally:
            graph.close()
            emb.save(cdir / "embeddings.npz")
        if not rebuilt:
            rows.append(_guard_row(label, "rebuild stopped", "all", "incomplete"))
            complete = False
            continue
        hashes = [_context_hash(c) for c in contexts]
        _write_jsonl(cdir / "contexts.jsonl", [{"i": i, "ctx": h, "context": c}
                                               for i, (h, c) in enumerate(zip(hashes, contexts))])
        base_path = cdir / "baseline.jsonl"
        base = _read_jsonl(base_path)
        recorded = record or not base
        if recorded:                                                       # 4. the baseline: first run, --record
            if base:
                stamp = re.sub(r"[^0-9TZ]", "", str(base[0].get("recorded") or "")) or "old"
                base_path.replace(cdir / f"baseline-{stamp}.jsonl")
            head = {"recorded": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "code": code,
                    "settings": {"recall_k": recall_k, "lexical": lexical, "temporal": temporal,
                                 "embedder": emb.name, "model": model}, "questions": len(conv.qa)}
            base = [head] + [{"i": i, "category": q.category, "ctx": h, "context": c}
                             for i, (q, h, c) in enumerate(zip(conv.qa, hashes, contexts))]
            _write_jsonl(base_path, base)
        base_rows = {r["i"]: r for r in base[1:]}
        changed = [i for i, h in enumerate(hashes) if (base_rows.get(i) or {}).get("ctx") != h]
        ans_path = cdir / "answers.jsonl"                                  # 5. answers, per (question, context)
        answers = {(r["i"], r["ctx"]): r for r in _read_jsonl(ans_path)
                   if r.get("model") == model and r.get("style") == answer_style}
        todo = [(i, hashes[i], contexts[i]) for i in range(len(conv.qa))]
        todo += [(i, base_rows[i]["ctx"], base_rows[i].get("context") or "") for i in changed if i in base_rows]
        for i, h, ctx in todo:
            if (i, h) in answers:
                continue
            if over():
                break
            q = conv.qa[i]
            raw = _retry(lambda: chat.chat(build_answer_messages(q.question, ctx, answer_style))) or ""
            pred = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
            ok = (abstains(pred) if q.category == 5
                  else _retry(lambda: judge_answer(judge or chat, q.question, q.answer, pred)))
            answers[(i, h)] = {"i": i, "ctx": h, "model": model, "style": answer_style, "category": q.category,
                               "prediction": pred, "f1": round(f1(pred, q.answer), 4), "j": bool(ok)}
            with ans_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(answers[(i, h)], ensure_ascii=False) + "\n")
            if on_progress and len(answers) % 20 == 0:
                on_progress(f"{conv.sample_id}: {len(answers)} answer(s) kept")
        main = [i for i, q in enumerate(conv.qa) if q.category in (1, 2, 3, 4)]

        def j_rate(keys):
            got = [answers[k] for k in keys if k in answers]
            return (sum(1 for r in got if r["j"]) / len(got) if got and len(got) == len(keys) else None), len(got)

        j_now, n_now = j_rate([(i, hashes[i]) for i in main])
        j_base, _ = j_rate([(i, base_rows[i]["ctx"]) for i in main if i in base_rows])
        status = "recorded" if recorded else ("fail" if changed else "pass")
        rows.append(_guard_row(label, f"{len(conv.qa) - len(changed)}/{len(conv.qa)}", "all", status,
                               [f"q{i}" for i in changed]))
        rows.append(_guard_row(f"merge checks asked anew ({conv.sample_id})", str(checks.asked), "—", "info"))
        rows.append(_guard_row(f"J, categories 1-4 ({conv.sample_id})",
                               f"{j_now:.1%}" if j_now is not None else f"{n_now}/{len(main)} answered",
                               f"baseline {j_base:.1%}" if j_base is not None else "—", "info"))
        complete = complete and j_now is not None and j_base is not None
    return {"rows": rows, "complete": complete, "seconds": round(time.time() - t0)}
