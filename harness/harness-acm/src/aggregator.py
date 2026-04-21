"""Profile → AggregatedStats（纯统计层，不做 0-100 评分）.

分层职责:
  fetcher   : Codeforces API → Profile (原始)
  aggregator: Profile → AggregatedStats (去重 / 计数 / 分桶)
  analyzer  : AggregatedStats → AbilityReport (评分 / 画像)
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Iterable

from schemas import (
    AggregatedStats,
    CFSubmission,
    DifficultyBucket,
    Profile,
    RatingStats,
    VerdictCounts,
)


# 难度分桶: CF 题目 rating 从 800 起步，每 200 一段；3000 以上合并
DIFFICULTY_BUCKETS: list[tuple[int, int]] = [
    (800, 999),   (1000, 1199), (1200, 1399), (1400, 1599),
    (1600, 1799), (1800, 1999), (2000, 2199), (2200, 2399),
    (2400, 2599), (2600, 2799), (2800, 2999), (3000, 3500),
]

# 把 CF 的 verdict 字符串归到我们的 7 个槽位
_VERDICT_SLOTS = {
    "OK": "ok",
    "WRONG_ANSWER": "wrong_answer",
    "TIME_LIMIT_EXCEEDED": "time_limit_exceeded",
    "MEMORY_LIMIT_EXCEEDED": "memory_limit_exceeded",
    "RUNTIME_ERROR": "runtime_error",
    "COMPILATION_ERROR": "compilation_error",
}


def _problem_key(sub: CFSubmission) -> tuple:
    """同一题的唯一标识：(contestId, index)；gym / 无 contestId 用题目名 fallback."""
    if sub.contestId:
        return ("c", sub.contestId, sub.problem.index)
    return ("g", sub.problem.name)


def _std(values: Iterable[float]) -> float | None:
    vs = list(values)
    if len(vs) < 2:
        return None
    mean = sum(vs) / len(vs)
    var = sum((v - mean) ** 2 for v in vs) / (len(vs) - 1)
    return var ** 0.5


def aggregate(profile: Profile) -> AggregatedStats:
    """从 Profile 计算聚合统计，纯函数，无副作用."""
    submissions = profile.submissions

    # 1. 按题目去重
    attempted_info: dict[tuple, tuple[int | None, list[str]]] = {}
    solved_info: dict[tuple, tuple[int | None, list[str]]] = {}

    for sub in submissions:
        key = _problem_key(sub)
        meta = (sub.problem.rating, sub.problem.tags)
        attempted_info.setdefault(key, meta)
        if sub.verdict == "OK":
            solved_info.setdefault(key, meta)

    # 2. 难度分桶
    buckets: list[DifficultyBucket] = []
    for lo, hi in DIFFICULTY_BUCKETS:
        s = sum(1 for r, _ in solved_info.values()
                if r is not None and lo <= r <= hi)
        a = sum(1 for r, _ in attempted_info.values()
                if r is not None and lo <= r <= hi)
        buckets.append(DifficultyBucket(lo=lo, hi=hi, solved=s, attempted=a))

    # 3. Verdict 聚合
    slot_counter: Counter = Counter()
    for sub in submissions:
        slot = _VERDICT_SLOTS.get(sub.verdict or "", "other")
        slot_counter[slot] += 1

    verdicts = VerdictCounts(
        ok=slot_counter.get("ok", 0),
        wrong_answer=slot_counter.get("wrong_answer", 0),
        time_limit_exceeded=slot_counter.get("time_limit_exceeded", 0),
        memory_limit_exceeded=slot_counter.get("memory_limit_exceeded", 0),
        runtime_error=slot_counter.get("runtime_error", 0),
        compilation_error=slot_counter.get("compilation_error", 0),
        other=slot_counter.get("other", 0),
    )

    # 4. tag 聚合 + 每个 tag 的最高 AC 难度
    tag_solved: Counter = Counter()
    tag_attempted: Counter = Counter()
    tag_max_rating: dict[str, int] = {}
    for (r, tags) in solved_info.values():
        for t in tags:
            tag_solved[t] += 1
            if r is not None:
                tag_max_rating[t] = max(tag_max_rating.get(t, 0), r)
    for (_, tags) in attempted_info.values():
        for t in tags:
            tag_attempted[t] += 1

    # 5. Rating 统计
    rh_sorted = sorted(profile.rating_history,
                       key=lambda r: r.ratingUpdateTimeSeconds)
    new_ratings = [r.newRating for r in rh_sorted]
    deltas = [r.newRating - r.oldRating for r in rh_sorted]
    recent_trend = (sum(deltas[-10:]) / len(deltas[-10:])) if deltas else None

    rating_stats = RatingStats(
        current=profile.user.rating,
        peak=profile.user.maxRating,
        mean=(sum(new_ratings) / len(new_ratings)) if new_ratings else None,
        std=_std(new_ratings),
        contests=len(new_ratings),
        recent_trend=recent_trend,
        rating_changes=deltas,
    )

    # 6. 活跃度
    daily: Counter = Counter()
    for sub in submissions:
        day = datetime.fromtimestamp(
            sub.creationTimeSeconds, tz=timezone.utc
        ).strftime("%Y-%m-%d")
        daily[day] += 1

    # 7. 比赛内 AC 的平均分钟（速度）
    contest_ac_times = [
        sub.relativeTimeSeconds / 60.0
        for sub in submissions
        if sub.verdict == "OK"
        and sub.author is not None
        and sub.author.participantType in ("CONTESTANT", "VIRTUAL")
        and 0 < sub.relativeTimeSeconds < 10_800  # 3h 以内
    ]
    mean_ac_contest_minutes = (sum(contest_ac_times) / len(contest_ac_times)
                               if contest_ac_times else None)

    # 8. 抗压：rated vs practice AC 率
    rated_total = rated_ok = 0
    practice_total = practice_ok = 0
    for sub in submissions:
        if not sub.author:
            continue
        if sub.author.participantType == "CONTESTANT":
            rated_total += 1
            if sub.verdict == "OK":
                rated_ok += 1
        elif sub.author.participantType == "PRACTICE":
            practice_total += 1
            if sub.verdict == "OK":
                practice_ok += 1
    rated_ac_rate = (rated_ok / rated_total) if rated_total > 0 else None
    practice_ac_rate = (practice_ok / practice_total) if practice_total > 0 else None

    # 9. 攻坚：高于 (current_rating + 200) 的题的 AC 率
    user_r = profile.user.rating or 0
    threshold = user_r + 200
    hard_attempted = hard_solved = 0
    if user_r > 0:
        for key, (r, _) in attempted_info.items():
            if r is not None and r >= threshold:
                hard_attempted += 1
                if key in solved_info:
                    hard_solved += 1
    breakthrough_ac_rate = (hard_solved / hard_attempted
                            if hard_attempted > 0 else None)

    return AggregatedStats(
        handle=profile.user.handle,
        total_submissions=len(submissions),
        unique_problems_attempted=len(attempted_info),
        unique_problems_solved=len(solved_info),
        difficulty_buckets=buckets,
        verdicts=verdicts,
        tag_solved=dict(tag_solved),
        tag_attempted=dict(tag_attempted),
        tag_max_rating=tag_max_rating,
        rating=rating_stats,
        daily_submission_count=dict(daily),
        mean_ac_contest_minutes=mean_ac_contest_minutes,
        rated_ac_rate=rated_ac_rate,
        practice_ac_rate=practice_ac_rate,
        breakthrough_ac_rate=breakthrough_ac_rate,
    )


if __name__ == "__main__":
    import sys
    from fetcher import fetch_profile

    handle = sys.argv[1] if len(sys.argv) > 1 else "tourist"
    prof = fetch_profile(handle, submissions=200)
    agg = aggregate(prof)
    print(f"{agg.handle}: unique AC {agg.unique_problems_solved}"
          f" / attempted {agg.unique_problems_attempted}"
          f" / AC rate {agg.verdicts.ac_rate:.1%}")
    print(f"rating std={agg.rating.std:.1f}" if agg.rating.std else "rating std=N/A")
    print("top tags:", Counter(agg.tag_solved).most_common(5))
