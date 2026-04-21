"""metrics.py 测试 — emit_metric 落盘 + summarize 聚合."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import metrics


def test_emit_metric_writes_jsonl(tmp_path, monkeypatch):
    log = tmp_path / "m.jsonl"
    monkeypatch.setattr(metrics, "METRICS_LOG", log)
    metrics.emit_metric("test_event", foo=1, bar="x")
    metrics.emit_metric("test_event", foo=2, bar="y")

    lines = log.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["event"] == "test_event"
    assert rec["foo"] == 1
    assert rec["bar"] == "x"
    assert "ts" in rec


def test_summarize_cache_hit_rate(tmp_path, monkeypatch):
    log = tmp_path / "m.jsonl"
    monkeypatch.setattr(metrics, "METRICS_LOG", log)
    for _ in range(7):
        metrics.emit_metric("cache_hit", method="user.info", key_hash="x")
    for _ in range(3):
        metrics.emit_metric("cache_miss", method="user.info", key_hash="x")

    s = metrics.summarize()
    assert s["cache"]["hit"] == 7
    assert s["cache"]["miss"] == 3
    assert s["cache"]["hit_rate"] == 0.7


def test_summarize_since_filter(tmp_path, monkeypatch):
    log = tmp_path / "m.jsonl"
    # 手动写入两条，一条老 3 小时，一条新 10 秒
    old_ts = time.time() - 3 * 3600
    new_ts = time.time() - 10
    log.write_text(
        json.dumps({"ts": old_ts, "event": "cache_hit", "method": "x"}) + "\n"
        + json.dumps({"ts": new_ts, "event": "cache_hit", "method": "x"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(metrics, "METRICS_LOG", log)

    # since=1h 应只看到 1 条
    s1 = metrics.summarize(since_hours=1)
    assert s1["cache"]["hit"] == 1

    # since=4h 看到两条
    s2 = metrics.summarize(since_hours=4)
    assert s2["cache"]["hit"] == 2


def test_summarize_judges(tmp_path, monkeypatch):
    log = tmp_path / "m.jsonl"
    monkeypatch.setattr(metrics, "METRICS_LOG", log)
    for s in (5, 5, 4):
        metrics.emit_metric("judge_run", handle="h", judge_name="strict",
                            score=s, reason="ok")
    for s in (4, 4):
        metrics.emit_metric("judge_run", handle="h", judge_name="lenient",
                            score=s, reason="ok")

    result = metrics.summarize()
    assert "strict" in result["judges"]
    assert result["judges"]["strict"]["count"] == 3
    assert result["judges"]["strict"]["median"] == 5
    assert result["judges"]["lenient"]["count"] == 2


def test_summarize_api_latency(tmp_path, monkeypatch):
    log = tmp_path / "m.jsonl"
    monkeypatch.setattr(metrics, "METRICS_LOG", log)
    for lat in (100, 200, 300, 400, 500):
        metrics.emit_metric("api_call_done", method="x", attempt=0,
                            latency_ms=lat, ok=True)
    s = metrics.summarize()
    assert s["api"]["count"] == 5
    assert s["api"]["avg_ms"] == 300.0


def test_emit_metric_swallows_io_error(tmp_path, monkeypatch):
    # 让 METRICS_LOG 指向一个不可写路径 (文件占据的目录名)
    bad = tmp_path / "blocker"
    bad.write_text("not a dir", encoding="utf-8")
    fake_log = bad / "m.jsonl"
    monkeypatch.setattr(metrics, "METRICS_LOG", fake_log)
    # 应当不抛异常
    metrics.emit_metric("will_fail", x=1)
