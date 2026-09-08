"""Tests for the retrieval eval core (pure metrics + evaluate driver)."""

from __future__ import annotations

from openwiki.agent import RAGAnswer, Source
from openwiki.eval import (
    EvalItem, cited_page_slugs, community_grounding, evaluate, grounding, hit_at_k,
    judge_pairwise, load_eval_set, recall_at_k, reciprocal_rank, run_global_eval,
)

RANKED = ["a", "b", "c", "d"]


def _src(marker, slug):
    return Source(marker=marker, page_slug=slug, page_title=slug.upper(),
                  pdf_page_start=marker, pdf_page_end=marker, chunk_id=f"c{marker}",
                  score=1.0 / marker, text="")


def test_answer_grounding_uses_citations():
    ans = RAGAnswer(question="q", answer="Because of X [1] and Y.",
                    sources=[_src(1, "a"), _src(2, "b")], model="m")
    assert cited_page_slugs(ans) == {"a"}                     # only [1] is cited
    g = grounding(ans, ["a", "c"])
    assert g["cite_hit"] is True and g["expected_recall"] == 0.5
    assert grounding(ans, ["c"])["cite_hit"] is False         # cited page isn't expected


class _Judge:
    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages):
        return self.reply


def test_judge_pairwise_parses_verdict():
    assert judge_pairwise(_Judge("A"), "q", "x", "y") == "a"
    assert judge_pairwise(_Judge("The better one is B."), "q", "x", "y") == "b"
    assert judge_pairwise(_Judge("<think>weighing…</think>tie"), "q", "x", "y") == "tie"
    assert judge_pairwise(_Judge("(no clear token)"), "q", "x", "y") == "tie"   # fallback


class _FakeResult:
    def __init__(self, slug, marker):
        self.page_slug, self.page_title = slug, slug.upper()
        self.pdf_page_start = self.pdf_page_end = marker
        self.chunk_id, self.score, self.text = f"c{marker}", 1.0 / marker, f"text {slug}"


class _FakeIndex:
    def __init__(self, slugs):
        self.slugs = slugs

    def search(self, query, k):
        return [_FakeResult(s, i + 1) for i, s in enumerate(self.slugs[:k])]

    def best_chunk_per_page(self, query, slugs):
        return [_FakeResult(s, i + 1) for i, s in enumerate(slugs)]


class _FakeChat:
    name = "fake"

    def chat(self, messages):
        return "The answer relies on [1]."          # always cites the top source


def test_run_answer_eval_aggregates(monkeypatch):
    from openwiki import eval as ev
    items = [ev.EvalItem("q1", ["a"]), ev.EvalItem("q2", ["b"])]
    index = _FakeIndex(["a", "b", "c"])
    # graph=None → RAG and GraphRAG both retrieve seeds; the top source is 'a'
    result = ev.run_answer_eval(items, index, graph=None, chat=_FakeChat(),
                                top_k=3, expand_k=0, judge=_Judge("tie"))
    assert result["questions"] == 2 and result["judged"] is True
    # both answers cite 'a': q1 (expected 'a') hits, q2 (expected 'b') doesn't → 50%
    assert result["grounding"]["RAG"]["cite_hit"] == 0.5
    assert result["tally"]["tie"] == 2


# -- global (thematic) eval ----------------------------------------------------

class _GlobalChat:
    name = "fake:global"

    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages):
        return self.reply


def test_community_grounding_precision_and_recall():
    assert community_grounding({1, 2}, {2, 3}) == {"cite_hit": True, "recall": 0.5, "precision": 0.5}
    assert community_grounding({1}, set())["cite_hit"] is False          # no relevant themes exist
    assert community_grounding(set(), {1})["precision"] == 0.0           # cited nothing


def test_run_global_eval_grounding_only():
    items = [EvalItem("q1", ["a1"]), EvalItem("q2", ["b1", "c1"])]
    communities = [("A", "sa"), ("B", "sb"), ("C", "sc")]                # markers 1,2,3
    members = {1: {"a1", "a2"}, 2: {"b1"}, 3: {"c1"}}
    chat = _GlobalChat("Antwort [1] [2].")                              # cites themes 1 & 2 every time
    r = run_global_eval(items, communities, members, chat)
    assert r["questions"] == 2 and r["judged"] is False
    assert r["grounding"]["cite_hit"] == 1.0                            # each hits a relevant theme
    assert r["grounding"]["recall"] == 0.75                             # q1 1/1, q2 1/2
    assert r["grounding"]["precision"] == 0.5                           # 1 of 2 cited themes relevant


def test_run_global_eval_judges_global_vs_rag():
    items = [EvalItem("q1", ["a"]), EvalItem("q2", ["b"])]
    communities = [("A", "sa"), ("B", "sb")]
    members = {1: {"a"}, 2: {"b"}}
    index = _FakeIndex(["a", "b", "c"])
    r = run_global_eval(items, communities, members, _FakeChat(), index=index, judge=_Judge("tie"))
    assert r["judged"] is True and r["tally"]["tie"] == 2


def test_reciprocal_rank():
    assert reciprocal_rank(RANKED, ["c"]) == 1 / 3       # first hit at position 3
    assert reciprocal_rank(RANKED, ["a", "d"]) == 1.0    # earliest hit wins
    assert reciprocal_rank(RANKED, ["x"]) == 0.0


def test_hit_and_recall_at_k():
    assert hit_at_k(RANKED, ["c"], 3) == 1.0
    assert hit_at_k(RANKED, ["c"], 2) == 0.0             # c is at rank 3, outside top-2
    assert recall_at_k(RANKED, ["a", "d"], 3) == 0.5     # only a is in top-3
    assert recall_at_k(RANKED, ["a", "b"], 3) == 1.0
    assert recall_at_k(RANKED, [], 3) == 0.0


def test_evaluate_aggregates_and_lists_misses():
    items = [EvalItem("q1", ["a"]), EvalItem("q2", ["z"])]
    ranked = {"q1": ["a", "b"], "q2": ["b", "c"]}
    report = evaluate(items, lambda q: ranked[q], k=2)
    assert report.hit_rate == 0.5                        # q1 hits, q2 misses
    assert abs(report.mrr - 0.5) < 1e-9                  # rr 1.0 and 0.0
    assert [m.question for m in report.misses] == ["q2"]


# -- cross-session memory eval (Path B) ----------------------------------------

class _FakeMemGraph:
    """Minimal stand-in for GraphStore's memory tier (offline driver test)."""

    def __init__(self):
        self.stored = []            # (session_id, MemoryFact)
        self.forgot = 0

    def forget_all(self):
        self.stored = []
        self.forgot += 1

    def remember(self, session_id, facts, embedder, now=None):
        self.stored.extend((session_id, f) for f in facts)
        return {"facts": len(facts), "added": len(facts), "duplicates": 0}

    def recall(self, query, embedder, k=5, **kw):
        return [{"subject": f.subject, "predicate": f.predicate, "object": f.object,
                 "session_id": sid, "cos": 1.0, "score": 1.0} for sid, f in self.stored][:k]


class _XChat:
    """Capture request → a fixed fact; probe request → echo the memory context back."""
    name = "fake:x"

    def chat(self, messages):
        if "JSON array" in messages[0]["content"]:       # CAPTURE_SYSTEM
            return '[{"subject":"the port","predicate":"is","object":"8137"}]'
        return messages[-1]["content"]                    # probe → echo the user content


def test_task_success_substring_all():
    from openwiki import eval as ev
    assert ev.task_success("The port is 8137.", ["8137"]) is True
    assert ev.task_success("Kuzu, a single-file DB", ["Kuzu", "single"]) is True
    assert ev.task_success("I don't know the port.", ["8137"]) is False
    assert ev.task_success("anything", []) is False           # no expected → not a success


def test_build_probe_messages_cold_vs_warm():
    from openwiki import eval as ev
    cold = ev.build_probe_messages("q?", "")
    assert cold[0]["role"] == "system" and "No remembered context" in cold[1]["content"]
    warm = ev.build_probe_messages("q?", "MEMBLOCK")
    assert "MEMBLOCK" in warm[1]["content"] and "q?" in warm[1]["content"]


def test_load_cross_session_set(tmp_path):
    from openwiki import eval as ev
    path = tmp_path / "x.jsonl"
    path.write_text(
        '# comment\n'
        '{"name":"n1","setup":"s only","question":"q1","expected":"tok"}\n'
        '\n'
        '{"setup":["a","b"],"question":"q2","answer":["x","y"]}\n',   # setup list; answer alias; no name
        encoding="utf-8")
    items = ev.load_cross_session_set(path)
    assert [i.name for i in items] == ["n1", "scenario-4"]            # 2nd JSON is file line 4
    assert items[0].setup == ["s only"] and items[0].expected == ["tok"]
    assert items[1].setup == ["a", "b"] and items[1].expected == ["x", "y"]


def test_run_cross_session_eval_conditions():
    from openwiki import eval as ev
    items = [ev.CrossSessionItem(name="port", setup=["We always run on port 8137."],
                                 question="Which port?", expected=["8137"])]
    graph = _FakeMemGraph()
    r = ev.run_cross_session_eval(items, graph, embedder=None, chat=_XChat())
    assert graph.forgot == 1                                  # scenario isolated
    assert r["success"]["cold"] == 0.0                        # no memory → can't know
    assert r["success"]["raw-log"] == 1.0                     # transcript holds the fact
    assert r["success"]["assembled"] == 1.0                   # recall surfaces the fact
    assert r["scenarios"] == 1 and r["judged"] is False


def test_run_cross_session_eval_judge_balances_position():
    from openwiki import eval as ev
    items = [ev.CrossSessionItem("a", ["x runs on 8137"], "q1", ["8137"]),
             ev.CrossSessionItem("b", ["y runs on 8137"], "q2", ["8137"])]
    r = ev.run_cross_session_eval(items, _FakeMemGraph(), None, _XChat(), judge=_Judge("A"))
    # judge always answers "A": i=0 A=assembled→assembled; i=1 A=raw-log→raw-log (position flipped)
    assert r["judged"] is True
    assert r["tally"] == {"assembled": 1, "raw-log": 1, "tie": 0}


def test_load_eval_set(tmp_path):
    path = tmp_path / "eval.jsonl"
    path.write_text(
        '# a comment line\n'
        '{"question": "q1", "pages": ["a", "b"]}\n'
        '\n'
        '{"question": "q2", "expected": "c"}\n',   # alias + scalar coerced to list
        encoding="utf-8",
    )
    items = load_eval_set(path)
    assert [i.question for i in items] == ["q1", "q2"]
    assert items[0].expected == ["a", "b"]
    assert items[1].expected == ["c"]
