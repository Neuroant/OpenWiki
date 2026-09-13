"""Tests for the observability layer (openwiki.metrics). Pure — no Ollama."""

from __future__ import annotations

from openwiki.metrics import MetricsCollector, parse_ollama_stats


# -- parse_ollama_stats: extract Ollama's counters into ms + tok/s -------------

def test_parse_ollama_stats_full():
    data = {
        "message": {"content": "hi"},
        "total_duration": 2_400_000_000, "load_duration": 100_000_000,
        "prompt_eval_count": 512, "prompt_eval_duration": 300_000_000,
        "eval_count": 210, "eval_duration": 1_000_000_000,
    }
    s = parse_ollama_stats(data)
    assert s["prompt_tokens"] == 512 and s["eval_tokens"] == 210
    assert s["tokens_per_sec"] == 210.0            # 210 tok / 1.0 s
    assert s["total_ms"] == 2400.0 and s["eval_ms"] == 1000.0 and s["load_ms"] == 100.0


def test_parse_ollama_stats_absent_or_bad():
    assert parse_ollama_stats({}) == {}
    assert parse_ollama_stats({"message": {"content": "x"}}) == {}   # no counters (older Ollama)
    assert parse_ollama_stats("not a dict") == {}
    # eval_duration 0 → no division-by-zero, no rate
    assert "tokens_per_sec" not in parse_ollama_stats({"eval_count": 5, "eval_duration": 0})


# -- MetricsCollector ----------------------------------------------------------

def test_record_and_snapshot_summary():
    c = MetricsCollector()
    c.record("chat", "ollama:x", duration_ms=1000, prompt_tokens=512, eval_tokens=210, tokens_per_sec=210.0)
    c.record("chat", "ollama:x", duration_ms=500, eval_tokens=100)
    c.record("embed", "ollama:e", duration_ms=30, prompt_tokens=8)
    snap = c.snapshot()
    chat = snap["summary"]["chat"]
    assert chat["count"] == 2
    assert chat["p50_ms"] == 500.0 and chat["p95_ms"] == 1000.0     # sorted [500, 1000]
    assert chat["total_ms"] == 1500.0
    assert chat["prompt_tokens"] == 512 and chat["eval_tokens"] == 310
    assert snap["summary"]["embed"]["count"] == 1
    assert snap["total_events"] == 3
    assert snap["events"][0]["kind"] == "embed"                     # newest first


def test_since_scopes_a_turn():
    c = MetricsCollector()
    c.record("chat", "m", duration_ms=100)
    marker = c.seq
    c.record("chat", "m", duration_ms=200, eval_tokens=50)
    c.record("embed", "e", duration_ms=5)
    after = c.since(marker)
    assert [e.kind for e in after] == ["chat", "embed"]             # only events after the marker
    assert after[0].eval_tokens == 50


def test_ring_buffer_is_bounded():
    c = MetricsCollector(maxlen=10)
    for i in range(25):
        c.record("chat", f"m{i}", duration_ms=1)
    snap = c.snapshot(limit=100)
    assert snap["summary"]["chat"]["count"] == 10                   # only the last 10 kept
    assert c.seq == 25                                              # seq keeps counting


def test_empty_snapshot():
    c = MetricsCollector()
    snap = c.snapshot()
    assert snap["events"] == [] and snap["summary"] == {} and snap["total_events"] == 0
