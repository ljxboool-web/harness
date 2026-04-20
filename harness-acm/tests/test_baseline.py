"""baseline.py 测试 — 快照保存/读取/diff."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import baseline
import metrics
from schemas import AbilityReport, SkillScore, TraitScore


@pytest.fixture(autouse=True)
def _isolate_metrics(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics, "METRICS_LOG", tmp_path / "metrics.jsonl")


def _make_report(
    handle: str = "sample",
    dp_score: float = 80.0,
    speed_score: float = 70.0,
) -> AbilityReport:
    return AbilityReport(
        handle=handle, generated_at=0,
        skills=[
            SkillScore(dimension="dp", score=dp_score,
                       solved=50, attempted=60,
                       max_rating=2400, confidence="high"),
            SkillScore(dimension="graph", score=60.0,
                       solved=30, attempted=40,
                       max_rating=2200, confidence="high"),
        ],
        traits=[
            TraitScore(dimension="speed", score=speed_score, evidence="x"),
            TraitScore(dimension="stability", score=55.0, evidence="y"),
        ],
        overall_rating=2300, overall_max_rating=2500,
    )


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(baseline, "BASELINE_DIR", tmp_path)
    report = _make_report()
    path = baseline.save_baseline(report)
    assert path.exists()

    loaded = baseline.load_baseline("sample")
    assert loaded is not None
    assert loaded["handle"] == "sample"
    assert loaded["skills"]["dp"] == 80.0
    assert loaded["traits"]["speed"] == 70.0
    assert loaded["rating"] == 2300


def test_no_baseline_returns_empty_diff(tmp_path, monkeypatch):
    monkeypatch.setattr(baseline, "BASELINE_DIR", tmp_path)
    # 没 save 过，应当返回空列表
    drifts = baseline.diff_baseline(_make_report(handle="new_guy"))
    assert drifts == []


def test_identical_report_zero_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(baseline, "BASELINE_DIR", tmp_path)
    r = _make_report()
    baseline.save_baseline(r)
    drifts = baseline.diff_baseline(_make_report())
    assert drifts == []


def test_drift_above_threshold_detected(tmp_path, monkeypatch):
    monkeypatch.setattr(baseline, "BASELINE_DIR", tmp_path)
    baseline.save_baseline(_make_report(dp_score=80.0))
    # 当前版本 dp 降到 68 = drift -12
    drifts = baseline.diff_baseline(_make_report(dp_score=68.0), threshold=5.0)
    assert len(drifts) == 1
    assert drifts[0].dimension == "skill.dp"
    assert drifts[0].old == 80.0
    assert drifts[0].new == 68.0
    assert drifts[0].delta == -12.0


def test_drift_below_threshold_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(baseline, "BASELINE_DIR", tmp_path)
    baseline.save_baseline(_make_report(dp_score=80.0))
    # drift 为 3，阈值 5 → 不报警
    drifts = baseline.diff_baseline(_make_report(dp_score=77.0), threshold=5.0)
    assert drifts == []


def test_trait_drift_also_tracked(tmp_path, monkeypatch):
    monkeypatch.setattr(baseline, "BASELINE_DIR", tmp_path)
    baseline.save_baseline(_make_report(speed_score=70.0))
    drifts = baseline.diff_baseline(
        _make_report(speed_score=50.0), threshold=5.0,
    )
    assert any(d.dimension == "trait.speed" for d in drifts)


def test_drift_sorted_by_magnitude(tmp_path, monkeypatch):
    monkeypatch.setattr(baseline, "BASELINE_DIR", tmp_path)
    baseline.save_baseline(_make_report(dp_score=80.0, speed_score=70.0))
    # dp 变 -15，speed 变 -8
    drifts = baseline.diff_baseline(
        _make_report(dp_score=65.0, speed_score=62.0), threshold=5.0,
    )
    assert len(drifts) == 2
    assert abs(drifts[0].delta) >= abs(drifts[1].delta)


def test_format_drift_table_empty():
    out = baseline.format_drift_table([])
    assert "0 drift" in out


def test_format_drift_table_nonempty(tmp_path, monkeypatch):
    monkeypatch.setattr(baseline, "BASELINE_DIR", tmp_path)
    baseline.save_baseline(_make_report(dp_score=80.0))
    drifts = baseline.diff_baseline(_make_report(dp_score=68.0), threshold=5.0)
    out = baseline.format_drift_table(drifts)
    assert "skill.dp" in out
    assert "80.0" in out
    assert "68.0" in out
