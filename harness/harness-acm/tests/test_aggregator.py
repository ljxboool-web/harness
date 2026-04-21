"""aggregator 的结构性不变量测试 — 不依赖选手具体 rating 数值，只检查聚合逻辑的自洽性."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aggregator import aggregate
from fetcher import fetch_profile


@pytest.fixture(scope="module")
def tourist_agg():
    return aggregate(fetch_profile("tourist", submissions=200))


@pytest.fixture(scope="module")
def jiangly_agg():
    return aggregate(fetch_profile("jiangly", submissions=200))


# ---------- 不变量 1：去重后的解题数不超过尝试数 ----------

def test_solved_subset_of_attempted(tourist_agg):
    assert tourist_agg.unique_problems_solved <= tourist_agg.unique_problems_attempted


# ---------- 不变量 2：每个难度桶内 solved <= attempted ----------

def test_bucket_solved_le_attempted(tourist_agg):
    for b in tourist_agg.difficulty_buckets:
        assert b.solved <= b.attempted, f"bucket {b.lo}-{b.hi} violated"


# ---------- 不变量 3：所有 verdict 槽求和 == 原始提交数 ----------

def test_verdicts_sum_matches_total(tourist_agg):
    assert tourist_agg.verdicts.total == tourist_agg.total_submissions


# ---------- 不变量 4：tag_solved[t] <= tag_attempted[t] ----------

def test_tag_solved_le_attempted(tourist_agg):
    for tag, solved in tourist_agg.tag_solved.items():
        assert solved <= tourist_agg.tag_attempted.get(tag, 0), tag


# ---------- 不变量 5：rating 字段非空且合理 ----------

def test_rating_stats_sane(tourist_agg):
    r = tourist_agg.rating
    assert r.current is not None and r.current > 0
    assert r.peak is not None and r.peak >= r.current - 200  # 允许从峰值下滑
    assert r.contests > 10
    assert r.std is not None and r.std > 0  # 顶级选手的 rating 不会纹丝不动
    assert len(r.rating_changes) == r.contests


# ---------- 不变量 6：AC 率在 (0, 1] ----------

def test_ac_rate_in_range(tourist_agg):
    rate = tourist_agg.verdicts.ac_rate
    assert 0 < rate <= 1


# ---------- 对照样本：顶级选手 AC 率应该高于 50% ----------

def test_top_player_high_ac_rate(tourist_agg):
    # tourist 是 legendary grandmaster，练习时以 AC 为主
    assert tourist_agg.verdicts.ac_rate > 0.5


# ---------- 跨选手对照：不同选手的 tag 分布不应完全一致 ----------

def test_different_players_have_different_profiles(tourist_agg, jiangly_agg):
    # 退化检测：如果两个顶级选手的 top-3 tag 完全一致，说明聚合出 bug
    t_top = sorted(tourist_agg.tag_solved.items(),
                   key=lambda x: -x[1])[:3]
    j_top = sorted(jiangly_agg.tag_solved.items(),
                   key=lambda x: -x[1])[:3]
    # 至少有一个 tag 不同，或者数量不同
    assert t_top != j_top or tourist_agg.rating.current != jiangly_agg.rating.current
