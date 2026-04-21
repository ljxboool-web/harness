"""Ensemble Judge 测试 — 并行 3 个 judge，取中位数."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import analyzer
import metrics
from schemas import AbilityReport, JudgeResult, SkillScore, TraitScore


@pytest.fixture(autouse=True)
def _isolate_metrics(tmp_path, monkeypatch):
    """避免测试污染真实 logs/metrics.jsonl."""
    monkeypatch.setattr(metrics, "METRICS_LOG", tmp_path / "metrics.jsonl")
    monkeypatch.delenv("DASHSCOPE_JUDGE_MODE", raising=False)


def _make_report(handle: str = "test") -> AbilityReport:
    return AbilityReport(
        handle=handle, generated_at=0,
        skills=[SkillScore(
            dimension="dp", score=80.0, solved=50, attempted=60,
            max_rating=2400, confidence="high",
        )],
        traits=[TraitScore(
            dimension="speed", score=70.0, evidence="test",
        )],
        overall_rating=2300, overall_max_rating=2500,
        narrative="【强项】... 【弱项】... 【建议】...",
    )


def _patch_judge_calls(monkeypatch, return_by_name: dict):
    """Return JudgeResult based on judge_name param."""
    def fake(report, system_prompt, judge_name, include_data=False):
        return return_by_name[judge_name]
    monkeypatch.setattr(analyzer, "_judge_with_prompt", fake)


# ---- 中位数计算：不同分数取中间 ----

def test_median_of_three_different(monkeypatch):
    _patch_judge_calls(monkeypatch, {
        "strict": JudgeResult(score=2, reason="s", judge_name="strict"),
        "lenient": JudgeResult(score=5, reason="l", judge_name="lenient"),
        "data": JudgeResult(score=4, reason="d", judge_name="data"),
    })
    result = analyzer.judge_report_ensemble(_make_report())
    assert result.median_score == 4
    assert len(result.individual) == 3


def test_median_all_same(monkeypatch):
    _patch_judge_calls(monkeypatch, {
        "strict": JudgeResult(score=5, reason="s", judge_name="strict"),
        "lenient": JudgeResult(score=5, reason="l", judge_name="lenient"),
        "data": JudgeResult(score=5, reason="d", judge_name="data"),
    })
    result = analyzer.judge_report_ensemble(_make_report())
    assert result.median_score == 5


# ---- 单个 judge 抛异常：fallback 为 3，中位数仍可算 ----

def test_one_judge_error_fallback(monkeypatch):
    def fake(report, system_prompt, judge_name, include_data=False):
        if judge_name == "strict":
            raise RuntimeError("boom")
        return JudgeResult(score=5, reason=judge_name, judge_name=judge_name)
    monkeypatch.setattr(analyzer, "_judge_with_prompt", fake)

    result = analyzer.judge_report_ensemble(_make_report())
    # strict → fallback 3；其他两个返回 5 → 中位数 = 5
    assert result.median_score == 5
    strict = next(r for r in result.individual if r.judge_name == "strict")
    assert strict.score == 3
    assert "error" in strict.reason


# ---- combined_reason 包含所有三个 judge 的分数和 reason ----

def test_combined_reason_format(monkeypatch):
    _patch_judge_calls(monkeypatch, {
        "strict": JudgeResult(score=3, reason="不够具体", judge_name="strict"),
        "lenient": JudgeResult(score=5, reason="可读性好", judge_name="lenient"),
        "data": JudgeResult(score=4, reason="数字准确", judge_name="data"),
    })
    result = analyzer.judge_report_ensemble(_make_report())
    assert "strict" in result.combined_reason
    assert "lenient" in result.combined_reason
    assert "data" in result.combined_reason
    assert "不够具体" in result.combined_reason


# ---- 并行：单个 judge sleep 0.1s，总耗时应 < 3x（串行） ----

def test_parallel_execution(monkeypatch):
    def slow(report, system_prompt, judge_name, include_data=False):
        time.sleep(0.1)
        return JudgeResult(score=5, reason=judge_name, judge_name=judge_name)
    monkeypatch.setattr(analyzer, "_judge_with_prompt", slow)

    t0 = time.time()
    analyzer.judge_report_ensemble(_make_report())
    elapsed = time.time() - t0
    # 串行为 0.3s；并行应接近 0.1s。给足余量到 0.25s 仍足以区分
    assert elapsed < 0.25, f"ensemble 执行耗时 {elapsed:.3f}s，疑似串行"


# ---- feedback rewrite 链路：combined_reason 出现在下一轮 feedback ----

def test_combined_reason_in_rewrite_feedback(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-key")
    calls: list = []

    def fake_gen(r, feedback=None):
        calls.append(feedback)
        return "narrative"
    monkeypatch.setattr(analyzer, "generate_narrative", fake_gen)

    from schemas import JudgeEnsembleResult
    scores = iter([
        JudgeEnsembleResult(
            median_score=2,
            individual=[
                JudgeResult(score=2, reason="严格不过", judge_name="strict"),
                JudgeResult(score=3, reason="一般", judge_name="lenient"),
                JudgeResult(score=2, reason="数字错", judge_name="data"),
            ],
            combined_reason="[strict/2]严格不过 | [lenient/3]一般 | [data/2]数字错",
        ),
        JudgeEnsembleResult(
            median_score=5,
            individual=[
                JudgeResult(score=5, reason="ok", judge_name="strict"),
                JudgeResult(score=5, reason="ok", judge_name="lenient"),
                JudgeResult(score=5, reason="ok", judge_name="data"),
            ],
            combined_reason="[strict/5]ok | [lenient/5]ok | [data/5]ok",
        ),
    ])
    monkeypatch.setattr(analyzer, "judge_report_ensemble",
                        lambda r: next(scores))

    analyzer.generate_narrative_with_judge(_make_report(), max_retries=2)
    # 第二次调用 generate_narrative 的 feedback 里应包含 combined_reason 碎片
    assert calls[1] is not None
    assert "严格不过" in calls[1] or "数字错" in calls[1]
