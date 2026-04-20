"""结构化 metrics — 写 JSON Lines 到 logs/metrics.jsonl，只用 stdlib。

所有业务模块 (fetcher / analyzer / baseline) 通过 emit_metric() 上报事件；
summarize() 聚合指定时间窗内的事件，输出 cache 命中率 / API 延迟 / Judge 分布。
严格单向依赖：本模块不 import 任何业务代码。
"""
from __future__ import annotations

import json
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
METRICS_LOG = ROOT / "logs" / "metrics.jsonl"


def emit_metric(event: str, **fields: Any) -> None:
    """Append one JSON line to logs/metrics.jsonl.

    `ts` (unix float seconds) is always attached. Never raises on I/O errors —
    observability must not break business logic.
    """
    record = {"ts": time.time(), "event": event, **fields}
    try:
        METRICS_LOG.parent.mkdir(exist_ok=True)
        with METRICS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _iter_records(path: Path | None = None) -> Iterable[dict]:
    path = path if path is not None else METRICS_LOG
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def summarize(since_hours: float | None = None, path: Path | None = None) -> dict:
    """Aggregate events into a summary dict.

    since_hours: only consider records newer than (now - since_hours*3600).
                 None = all records.
    path: defaults to current METRICS_LOG (looked up lazily so monkeypatches work).
    """
    effective_path = path if path is not None else METRICS_LOG
    cutoff = time.time() - since_hours * 3600 if since_hours else 0.0
    records = [r for r in _iter_records(effective_path) if r.get("ts", 0) >= cutoff]

    by_event: Counter = Counter(r.get("event", "?") for r in records)

    cache_hit = by_event.get("cache_hit", 0)
    cache_miss = by_event.get("cache_miss", 0)
    cache_total = cache_hit + cache_miss
    cache_hit_rate = (cache_hit / cache_total) if cache_total > 0 else None

    latencies = [r["latency_ms"] for r in records
                 if r.get("event") == "api_call_done" and "latency_ms" in r]
    api_stats = {}
    if latencies:
        api_stats["count"] = len(latencies)
        api_stats["avg_ms"] = round(statistics.mean(latencies), 1)
        api_stats["p95_ms"] = round(_percentile(latencies, 95), 1)

    judge_scores: dict[str, list[int]] = defaultdict(list)
    for r in records:
        if r.get("event") == "judge_run" and "score" in r:
            judge_scores[r.get("judge_name", "default")].append(r["score"])
    judge_stats = {
        name: {
            "count": len(scores),
            "median": statistics.median(scores),
            "mean": round(statistics.mean(scores), 2),
        }
        for name, scores in judge_scores.items()
    }

    loop_records = [r for r in records if r.get("event") == "judge_loop_done"]
    loop_stats = {}
    if loop_records:
        attempts_list = [r.get("attempts", 1) for r in loop_records]
        first_pass = sum(1 for a in attempts_list if a == 1)
        loop_stats = {
            "count": len(loop_records),
            "avg_attempts": round(statistics.mean(attempts_list), 2),
            "first_pass_rate": round(first_pass / len(loop_records), 2),
        }

    drift_count = by_event.get("baseline_diff", 0)

    return {
        "total_records": len(records),
        "since_hours": since_hours,
        "cache": {
            "hit": cache_hit, "miss": cache_miss,
            "hit_rate": round(cache_hit_rate, 3) if cache_hit_rate is not None else None,
        },
        "api": api_stats,
        "judges": judge_stats,
        "loops": loop_stats,
        "baseline_drift_count": drift_count,
    }


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _format_summary(s: dict) -> str:
    lines = [
        f"metrics.jsonl · records={s['total_records']}"
        f" · window={s['since_hours']}h" if s["since_hours"] else
        f"metrics.jsonl · records={s['total_records']} · window=all",
        "",
        "── Cache ──",
        f"  hit={s['cache']['hit']}  miss={s['cache']['miss']}"
        f"  hit_rate={s['cache']['hit_rate']}",
    ]
    if s["api"]:
        lines += [
            "",
            "── API ──",
            f"  calls={s['api']['count']}"
            f"  avg={s['api']['avg_ms']}ms"
            f"  p95={s['api']['p95_ms']}ms",
        ]
    if s["judges"]:
        lines.append("")
        lines.append("── Judges ──")
        for name, js in sorted(s["judges"].items()):
            lines.append(f"  {name:<10} count={js['count']}"
                         f"  median={js['median']}  mean={js['mean']}")
    if s["loops"]:
        lines += [
            "",
            "── Judge loops ──",
            f"  count={s['loops']['count']}"
            f"  avg_attempts={s['loops']['avg_attempts']}"
            f"  first_pass_rate={s['loops']['first_pass_rate']}",
        ]
    lines.append("")
    lines.append(f"baseline_drift events: {s['baseline_drift_count']}")
    return "\n".join(lines)


def _main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="metrics")
    sub = parser.add_subparsers(dest="cmd", required=True)
    stats = sub.add_parser("stats", help="print summary of logs/metrics.jsonl")
    stats.add_argument("--since", type=float, default=None,
                       help="only last N hours (default: all)")
    args = parser.parse_args()
    if args.cmd == "stats":
        print(_format_summary(summarize(since_hours=args.since)))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
