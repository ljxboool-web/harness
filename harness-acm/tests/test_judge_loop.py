"""Judge 重写循环测试 — 用 monkeypatch 模拟 Haiku 响应，验证重试逻辑."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import analyzer
from schemas import (
    AbilityReport,
    JudgeResult,
    SkillScore,
    TraitScore,
)


def _make_fake_report(handle: str = "test") -> AbilityReport:
    return AbilityReport(
        handle=handle,
        generated_at=0,
        skills=[SkillScore(
            dimension="dp", score=80.0, solved=50, attempted=60,
            max_rating=2400, confidence="high",
        )],
        traits=[TraitScore(
            dimension="speed", score=70.0, evidence="test",
        )],
        overall_rating=2300,
        overall_max_rating=2500,
    )


# ---- 无 key 场景：单次模板生成 ----

def test_no_key_single_attempt(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    report = _make_fake_report()
    narrative, judge, trace = analyzer.generate_narrative_with_judge(
        report, max_retries=2,
    )
    assert len(trace) == 1
    assert judge.score == 3
    assert "跳过" in judge.reason
    assert narrative  # 非空


# ---- 第一次就 5 分：不触发重试 ----

def test_first_pass_ok(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(analyzer, "generate_narrative",
                        lambda r, feedback=None: f"生成版本 v{int(bool(feedback))}")
    monkeypatch.setattr(analyzer, "judge_report",
                        lambda r: JudgeResult(score=5, reason="完美"))

    report = _make_fake_report()
    narrative, judge, trace = analyzer.generate_narrative_with_judge(
        report, max_retries=2,
    )
    assert len(trace) == 1
    assert judge.score == 5
    assert narrative == "生成版本 v0"  # 没带 feedback


# ---- 第一次 2 分第二次 5 分：触发一次重写 ----

def test_retry_then_pass(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    gen_calls: list = []

    def fake_gen(r, feedback=None):
        gen_calls.append(feedback)
        return f"attempt#{len(gen_calls)}"
    monkeypatch.setattr(analyzer, "generate_narrative", fake_gen)

    scores = iter([
        JudgeResult(score=2, reason="建议不具体"),
        JudgeResult(score=5, reason="修正后可"),
    ])
    monkeypatch.setattr(analyzer, "judge_report", lambda r: next(scores))

    report = _make_fake_report()
    narrative, judge, trace = analyzer.generate_narrative_with_judge(
        report, max_retries=2,
    )
    assert len(trace) == 2
    assert judge.score == 5
    assert narrative == "attempt#2"
    # 第二次调用 generate_narrative 时 feedback 非空
    assert gen_calls[0] is None
    assert gen_calls[1] is not None
    assert "2/5" in gen_calls[1] and "建议不具体" in gen_calls[1]


# ---- 三次都不及格：max_retries 生效，返回最后一版 ----

def test_exhaust_retries(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(analyzer, "generate_narrative",
                        lambda r, feedback=None: "bad")
    monkeypatch.setattr(analyzer, "judge_report",
                        lambda r: JudgeResult(score=2, reason="始终不及格"))

    report = _make_fake_report()
    narrative, judge, trace = analyzer.generate_narrative_with_judge(
        report, max_retries=2,
    )
    assert len(trace) == 3  # 初始 + 2 次重试
    assert judge.score == 2  # 仍然失败
    assert narrative == "bad"


# ---- Judge 必须收到带 narrative 的 report ----

def test_judge_receives_populated_narrative(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(analyzer, "generate_narrative",
                        lambda r, feedback=None: "生成内容")

    seen: list[str] = []

    def fake_judge(r):
        seen.append(r.narrative or "")
        return JudgeResult(score=5, reason="ok")
    monkeypatch.setattr(analyzer, "judge_report", fake_judge)

    report = _make_fake_report()
    analyzer.generate_narrative_with_judge(report, max_retries=1)
    assert seen == ["生成内容"]


# ---- 每次 trace 都落盘 logs/judge.log ----

def test_trace_logged(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(analyzer, "generate_narrative",
                        lambda r, feedback=None: "x")
    monkeypatch.setattr(analyzer, "judge_report",
                        lambda r: JudgeResult(score=5, reason="ok"))

    report = _make_fake_report(handle="trace-test")
    narrative, judge, trace = analyzer.generate_narrative_with_judge(
        report, max_retries=0,
    )
    # 读 logs/judge.log 最后一行，应该出现 trace-test
    log_path = Path(__file__).resolve().parent.parent / "logs" / "judge.log"
    assert log_path.exists()
    tail = log_path.read_text().strip().split("\n")[-1]
    assert "trace-test" in tail


# ---- on_attempt 回调每次尝试都被调用 ----

def test_on_attempt_called(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(analyzer, "generate_narrative",
                        lambda r, feedback=None: "x")
    scores = iter([
        JudgeResult(score=3, reason="a"),
        JudgeResult(score=5, reason="b"),
    ])
    monkeypatch.setattr(analyzer, "judge_report", lambda r: next(scores))

    calls: list = []
    analyzer.generate_narrative_with_judge(
        _make_fake_report(), max_retries=2,
        on_attempt=lambda n, j: calls.append((n, j.score)),
    )
    assert calls == [(1, 3), (2, 5)]
