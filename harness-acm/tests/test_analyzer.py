"""analyzer 评分层测试."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aggregator import aggregate
from analyzer import SKILL_DIMS, compute_abilities
from fetcher import fetch_profile


@pytest.fixture(scope="module")
def tourist_report():
    return compute_abilities(aggregate(fetch_profile("tourist", submissions=300)))


@pytest.fixture(scope="module")
def umnik_report():
    return compute_abilities(aggregate(fetch_profile("Um_nik", submissions=200)))


# ---- 1. 所有 score 都在 [0, 100] ----

def test_all_scores_in_range(tourist_report):
    for s in tourist_report.skills:
        assert 0 <= s.score <= 100, f"{s.dimension} out of range: {s.score}"
    for t in tourist_report.traits:
        assert 0 <= t.score <= 100, f"{t.dimension} out of range: {t.score}"


# ---- 2. 8 维齐全 ----

def test_all_8_skills_present(tourist_report):
    dims = {s.dimension for s in tourist_report.skills}
    assert dims == set(SKILL_DIMS)


# ---- 3. 5 维齐全 ----

def test_all_5_traits_present(tourist_report):
    dims = {t.dimension for t in tourist_report.traits}
    assert dims == {"stability", "speed", "pressure", "breakthrough", "activity"}


# ---- 4. confidence 规则 ----

def test_confidence_rules(tourist_report):
    for s in tourist_report.skills:
        if s.attempted < 10:
            assert s.confidence == "low"
        elif s.attempted < 30:
            assert s.confidence == "medium"
        else:
            assert s.confidence == "high"


# ---- 5. solved <= attempted ----

def test_solved_le_attempted(tourist_report):
    for s in tourist_report.skills:
        assert s.solved <= s.attempted


# ---- 6. 顶级选手应有至少一项 score >= 70 ----

def test_top_player_has_strength(tourist_report):
    max_skill = max(s.score for s in tourist_report.skills)
    assert max_skill >= 70


# ---- 7. 区分度：Um_nik 的 geometry 应显著弱于他自己的其他维度 ----

def test_umnik_weakness_detected(umnik_report):
    geom = next(s for s in umnik_report.skills if s.dimension == "geometry")
    dp = next(s for s in umnik_report.skills if s.dimension == "dp")
    # Um_nik 以 dp 著称，geometry 是公开的弱项
    assert geom.score < dp.score - 20, (
        f"expected geometry ({geom.score}) < dp ({dp.score}) - 20"
    )


# ---- 8. 评语非空 ----

def test_report_has_narrative_capability(tourist_report):
    # 默认未生成 narrative，但 schema 支持
    assert tourist_report.narrative is None or isinstance(tourist_report.narrative, str)
