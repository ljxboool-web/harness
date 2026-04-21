"""Baseline 回归：固定选手的分数快照 + drift 检测.

存储：baselines/{handle}.json（git 跟踪）。只存数字维度，不存 narrative
（AI 生成每次必变，不适合 baseline）。

CLI:
  python src/baseline.py update <handle>    # 重新抓数据 + 写 baseline
  python src/baseline.py check  <handle>    # 比对，打印 drift 表
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from metrics import emit_metric
from schemas import AbilityReport, Drift

ROOT = Path(__file__).resolve().parent.parent
BASELINE_DIR = ROOT / "baselines"
DEFAULT_THRESHOLD = 5.0


def _baseline_path(handle: str) -> Path:
    return BASELINE_DIR / f"{handle}.json"


def save_baseline(report: AbilityReport) -> Path:
    """把 report 的数字维度落盘。"""
    BASELINE_DIR.mkdir(exist_ok=True)
    record = {
        "handle": report.handle,
        "snapshot_at": report.generated_at,
        "skills": {s.dimension: s.score for s in report.skills},
        "traits": {t.dimension: t.score for t in report.traits},
        "rating": report.overall_rating,
        "peak": report.overall_max_rating,
    }
    path = _baseline_path(report.handle)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def load_baseline(handle: str) -> Optional[dict]:
    path = _baseline_path(handle)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def diff_baseline(
    report: AbilityReport,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[Drift]:
    """返回所有 |new - old| > threshold 的维度 drift 列表（按 |delta| 降序）。"""
    baseline = load_baseline(report.handle)
    if baseline is None:
        return []

    drifts: list[Drift] = []

    def _check(scope: str, old_map: dict, current_pairs: list[tuple[str, float]]) -> None:
        for name, new_score in current_pairs:
            if name not in old_map:
                continue
            old_score = float(old_map[name])
            delta = new_score - old_score
            if abs(delta) > threshold:
                dim = f"{scope}.{name}"
                drifts.append(Drift(
                    dimension=dim, old=old_score,
                    new=new_score, delta=delta,
                ))
                emit_metric("baseline_diff", handle=report.handle,
                            dimension=dim, delta=delta)

    _check("skill", baseline.get("skills", {}),
           [(s.dimension, s.score) for s in report.skills])
    _check("trait", baseline.get("traits", {}),
           [(t.dimension, t.score) for t in report.traits])

    drifts.sort(key=lambda d: -abs(d.delta))
    return drifts


def format_drift_table(drifts: list[Drift]) -> str:
    if not drifts:
        return "baseline 对比: 0 drift"
    lines = ["baseline drift:"]
    lines.append(f"  {'dimension':<24}{'old':>8}{'new':>8}{'Δ':>8}")
    for d in drifts:
        sign = "+" if d.delta >= 0 else ""
        lines.append(
            f"  {d.dimension:<24}{d.old:>8.1f}{d.new:>8.1f}"
            f"  {sign}{d.delta:>+6.1f}"
        )
    return "\n".join(lines)


def _cmd_update(handle: str, submissions: int) -> int:
    from aggregator import aggregate
    from analyzer import compute_abilities
    from fetcher import fetch_profile

    profile = fetch_profile(handle, submissions=submissions)
    report = compute_abilities(aggregate(profile))
    path = save_baseline(report)
    print(f"baseline 已写入 {path.relative_to(ROOT)}")
    print(f"  skills: {len(report.skills)}, traits: {len(report.traits)}")
    return 0


def _cmd_check(
    handle: str, submissions: int, threshold: float, strict: bool,
) -> int:
    from aggregator import aggregate
    from analyzer import compute_abilities
    from fetcher import fetch_profile

    if load_baseline(handle) is None:
        print(f"尚无 baseline。先跑: python src/baseline.py update {handle}",
              file=sys.stderr)
        return 2

    profile = fetch_profile(handle, submissions=submissions)
    report = compute_abilities(aggregate(profile))
    drifts = diff_baseline(report, threshold=threshold)
    print(format_drift_table(drifts))
    if drifts and strict:
        print(f"\n[--strict] 有 {len(drifts)} 处超阈值 drift → exit 1",
              file=sys.stderr)
        return 1
    return 0


def _main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="baseline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_up = sub.add_parser("update", help="重新抓数据并覆盖 baseline")
    p_up.add_argument("handle")
    p_up.add_argument("--submissions", type=int, default=300)

    p_ck = sub.add_parser("check", help="比对当前报告与 baseline")
    p_ck.add_argument("handle")
    p_ck.add_argument("--submissions", type=int, default=300)
    p_ck.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    p_ck.add_argument("--strict", action="store_true",
                      help="有 drift 即 exit 1")

    args = parser.parse_args()
    if args.cmd == "update":
        return _cmd_update(args.handle, args.submissions)
    if args.cmd == "check":
        return _cmd_check(args.handle, args.submissions,
                          args.threshold, args.strict)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
