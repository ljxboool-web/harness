"""TUI 入口. 阶段 3：完整 8+5 维渲染 + AI 评语."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aggregator import aggregate
from analyzer import compute_abilities, generate_narrative_with_judge
from fetcher import fetch_profile
from schemas import JudgeResult


# ANSI 颜色
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def _color_by_score(score: float) -> str:
    if score >= 70:
        return GREEN
    if score >= 40:
        return YELLOW
    return RED


def _render_bar(value: float, maximum: float = 100.0, width: int = 24) -> str:
    if maximum <= 0:
        return ""
    filled = round(width * value / maximum)
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def _section(title: str) -> str:
    return f"\n{BOLD}{CYAN}── {title} ──{RESET}"


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="cf-profiler",
        description="生成 Codeforces 选手实力画像",
    )
    parser.add_argument("handle", help="CF handle, 例如 tourist")
    parser.add_argument("--submissions", type=int, default=500,
                        help="抓取最近 N 条提交 (默认 500)")
    parser.add_argument("--no-ai", action="store_true",
                        help="跳过 AI 评语生成")
    parser.add_argument("--max-retries", type=int, default=2,
                        help="Judge <4 分时的最大重写次数（默认 2）")
    args = parser.parse_args()

    profile = fetch_profile(args.handle, submissions=args.submissions)
    agg = aggregate(profile)
    report = compute_abilities(agg)

    judge: JudgeResult | None = None
    trace: list[dict] = []
    if not args.no_ai:
        print(_section("评语生成 · Judge 审阅循环"))

        def _on_attempt(n: int, j: JudgeResult) -> None:
            color = GREEN if j.score >= 4 else (YELLOW if j.score >= 3 else RED)
            mark = "✓" if j.score >= 4 else "✗"
            print(f"  {color}[{mark}] 第 {n} 次 · {j.score}/5 · "
                  f"{j.reason}{RESET}")

        narrative, judge, trace = generate_narrative_with_judge(
            report, max_retries=args.max_retries, on_attempt=_on_attempt,
        )
        report.narrative = narrative

    # --- Header ---
    print(f"\n{BOLD}Codeforces 选手画像 · {report.handle}{RESET}")
    print(f"{DIM}{'─' * 60}{RESET}")
    print(f"Rating   : {BOLD}{report.overall_rating}{RESET}"
          f"    Peak : {report.overall_max_rating}"
          f"    Contests : {agg.rating.contests}"
          f"    AC Rate : {agg.verdicts.ac_rate:.1%}")

    # --- 8 维技能雷达 ---
    print(_section("8 维算法技能"))
    for s in report.skills:
        color = _color_by_score(s.score)
        bar = _render_bar(s.score)
        conf_mark = {"high": "●", "medium": "◐", "low": "○"}[s.confidence]
        print(f"  {s.dimension:<15} {color}{bar}{RESET} "
              f"{color}{s.score:>5.1f}{RESET}  "
              f"{DIM}{conf_mark} {s.solved:>3} AC / peak {s.max_rating or '—'}{RESET}")

    # --- 5 维个性特征 ---
    print(_section("5 维个性特征"))
    for t in report.traits:
        color = _color_by_score(t.score)
        bar = _render_bar(t.score)
        print(f"  {t.dimension:<15} {color}{bar}{RESET} "
              f"{color}{t.score:>5.1f}{RESET}  "
              f"{DIM}{t.evidence}{RESET}")

    # --- 难度分布小图 ---
    print(_section("难度分布 (solved)"))
    max_s = max((b.solved for b in agg.difficulty_buckets), default=1) or 1
    for b in agg.difficulty_buckets:
        if b.attempted == 0:
            continue
        bar = _render_bar(b.solved, max_s, width=32)
        print(f"  {b.lo:>4}-{b.hi:<4} {bar} {b.solved:>3}")

    # --- AI 评语 ---
    print(_section("教练评语"))
    if report.narrative:
        print(report.narrative)
        if judge is not None:
            final_color = GREEN if judge.score >= 4 else YELLOW
            print(f"\n{DIM}— 最终 Judge 评分：{final_color}{judge.score}/5{RESET}"
                  f"{DIM} · 共 {len(trace)} 轮 · trace → logs/judge.log{RESET}")
    else:
        print(f"{DIM}(已跳过 AI 评语){RESET}")

    print(f"\n{DIM}{'─' * 60}{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
