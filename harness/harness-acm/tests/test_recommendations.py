"""训练题单推荐测试."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from analyzer import SKILL_DIMS, generate_practice_plan
from schemas import (
    AbilityReport,
    AggregatedStats,
    CFProblem,
    DifficultyBucket,
    RatingStats,
    SkillScore,
    TraitScore,
    VerdictCounts,
)


def _stats() -> AggregatedStats:
    return AggregatedStats(
        handle="alice",
        total_submissions=20,
        unique_problems_attempted=2,
        unique_problems_solved=1,
        difficulty_buckets=[DifficultyBucket(lo=800, hi=999, solved=1, attempted=1)],
        verdicts=VerdictCounts(ok=1, wrong_answer=1),
        tag_solved={"dp": 1},
        tag_attempted={"dp": 2},
        tag_max_rating={"dp": 1200},
        attempted_problem_keys=["c:1000:A"],
        solved_problem_keys=["c:1000:A"],
        rating=RatingStats(
            current=1500,
            peak=1600,
            mean=1450,
            std=80,
            contests=5,
            rating_changes=[10, -20, 30, -10, 40],
        ),
        daily_submission_count={},
    )


def _report() -> AbilityReport:
    skills = [
        SkillScore(
            dimension=dim,
            score=25.0 if dim == "dp" else 75.0,
            solved=1 if dim == "dp" else 20,
            attempted=3 if dim == "dp" else 25,
            max_rating=1200 if dim == "dp" else 1800,
            confidence="low" if dim == "dp" else "medium",
        )
        for dim in SKILL_DIMS
    ]
    traits = [
        TraitScore(dimension="stability", score=60, evidence="ok"),
        TraitScore(dimension="speed", score=60, evidence="ok"),
        TraitScore(dimension="pressure", score=60, evidence="ok"),
        TraitScore(dimension="breakthrough", score=60, evidence="ok"),
        TraitScore(dimension="activity", score=60, evidence="ok"),
    ]
    return AbilityReport(
        handle="alice",
        generated_at=1,
        skills=skills,
        traits=traits,
        overall_rating=1500,
        overall_max_rating=1600,
    )


def test_practice_plan_uses_weak_skill_and_skips_attempted():
    problems = [
        CFProblem(
            contestId=1000, index="A", name="Already Tried",
            rating=1200, tags=["dp"], solved_count=10_000,
        ),
        CFProblem(
            contestId=2000, index="B", name="Good DP",
            rating=1200, tags=["dp", "math"], solved_count=5000,
        ),
        CFProblem(
            contestId=2001, index="C", name="Graph Only",
            rating=1500, tags=["graphs"], solved_count=4000,
        ),
    ]

    plan = generate_practice_plan(
        _stats(), _report(), problems, max_problems=2, use_ai=False,
    )

    assert plan.handle == "alice"
    assert plan.weak_skills[0] == "dp"
    assert len(plan.problems) == 1
    assert plan.problems[0].name == "Good DP"
    assert plan.problems[0].target_skill == "dp"
    assert plan.problems[0].url == "https://codeforces.com/problemset/problem/2000/B"
    assert "Already Tried" not in {p.name for p in plan.problems}
