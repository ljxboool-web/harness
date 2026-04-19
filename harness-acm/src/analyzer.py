"""AggregatedStats → AbilityReport (评分层) + Claude 评语 + Judge 循环.

评分公式参考 CLAUDE.md 阶段 3 设计.
Judge 循环: 生成 → 审阅 → <4 分携带 feedback 重写，最多 2 轮重写.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from schemas import (
    AbilityReport,
    AggregatedStats,
    Confidence,
    JudgeResult,
    Profile,
    SkillDimension,
    SkillScore,
    TraitScore,
)

# ---------- 常量：8 维技能的 CF tag 归类 ----------

TAG_CATEGORIES: dict[str, list[str]] = {
    "dp": ["dp", "dp on trees"],
    "graph": ["graphs", "dfs and similar", "dsu", "trees",
              "shortest paths", "flows"],
    "math": ["math", "number theory", "combinatorics",
             "probabilities", "matrices"],
    "greedy": ["greedy", "constructive algorithms"],
    "data_structure": ["data structures", "segment tree",
                       "fenwick", "sortings"],
    "string": ["strings", "hashing", "string suffix structures"],
    "search": ["binary search", "ternary search", "brute force",
               "meet-in-the-middle"],
    "geometry": ["geometry", "2-sat"],
}

SKILL_DIMS: tuple[SkillDimension, ...] = (
    "dp", "graph", "math", "greedy",
    "data_structure", "string", "search", "geometry",
)


# ---------- 评分工具函数 ----------

def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _confidence(attempted: int) -> Confidence:
    if attempted < 10:
        return "low"
    if attempted < 30:
        return "medium"
    return "high"


def _std(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    return (sum((v - m) ** 2 for v in values) / (len(values) - 1)) ** 0.5


# ---------- 8 维技能评分 ----------

def score_skill(dim: SkillDimension, stats: AggregatedStats) -> SkillScore:
    """skill_score = 0.40*量 + 0.50*质 + 0.10*成功率"""
    tags = TAG_CATEGORIES[dim]
    solved = sum(stats.tag_solved.get(t, 0) for t in tags)
    attempted = sum(stats.tag_attempted.get(t, 0) for t in tags)
    max_r_list = [stats.tag_max_rating[t] for t in tags if t in stats.tag_max_rating]
    max_r = max(max_r_list) if max_r_list else None

    # 量：log 缩放，100 题封顶
    qty = _clamp(25 * math.log2(solved + 1))
    # 质：难度线性映射 800→0, 3000→100
    qual = _clamp((max_r - 800) / 22) if max_r else 0.0
    # 成功率：solved/attempted
    succ = (100.0 * solved / attempted) if attempted > 0 else 0.0

    score = 0.40 * qty + 0.50 * qual + 0.10 * succ

    return SkillScore(
        dimension=dim,
        score=round(score, 1),
        solved=solved,
        attempted=attempted,
        max_rating=max_r,
        confidence=_confidence(attempted),
    )


# ---------- 5 维个性特征评分 ----------

def _score_stability(stats: AggregatedStats) -> TraitScore:
    deltas = stats.rating.rating_changes
    recent = deltas[-30:] if len(deltas) >= 5 else deltas
    std_delta = _std([float(d) for d in recent])
    if std_delta is None:
        return TraitScore(dimension="stability", score=50.0,
                          evidence="比赛场数不足（<5）")
    score = _clamp(100.0 - std_delta / 1.5)
    return TraitScore(
        dimension="stability",
        score=round(score, 1),
        evidence=f"近 {len(recent)} 场 rating 变化 std={std_delta:.0f}",
    )


def _score_speed(stats: AggregatedStats) -> TraitScore:
    m = stats.mean_ac_contest_minutes
    if m is None:
        return TraitScore(dimension="speed", score=50.0,
                          evidence="无比赛内 AC 数据")
    score = _clamp(100.0 - 1.5 * (m - 30))
    return TraitScore(
        dimension="speed",
        score=round(score, 1),
        evidence=f"比赛内 AC 平均 {m:.1f} 分钟",
    )


def _score_pressure(stats: AggregatedStats) -> TraitScore:
    r, p = stats.rated_ac_rate, stats.practice_ac_rate
    if r is None or p is None or p == 0:
        return TraitScore(dimension="pressure", score=50.0,
                          evidence="rated / practice 数据不足")
    ratio = r / p
    score = _clamp(100.0 * ratio) if ratio < 1 else 100.0
    return TraitScore(
        dimension="pressure",
        score=round(score, 1),
        evidence=f"rated AC {r:.0%} / practice AC {p:.0%}",
    )


def _score_breakthrough(stats: AggregatedStats) -> TraitScore:
    br = stats.breakthrough_ac_rate
    if br is None:
        return TraitScore(
            dimension="breakthrough", score=80.0,
            evidence="已接近难度上限，无可测样本",
        )
    score = _clamp(br * 100 * 3)  # 30% AC → 90 分
    return TraitScore(
        dimension="breakthrough",
        score=round(score, 1),
        evidence=f"超自身 rating+200 的题 AC 率 {br:.1%}",
    )


def _score_activity(stats: AggregatedStats) -> TraitScore:
    now = datetime.now(tz=timezone.utc).date()
    cutoff = now - timedelta(days=30)
    recent_subs = sum(
        n for day, n in stats.daily_submission_count.items()
        if datetime.strptime(day, "%Y-%m-%d").date() >= cutoff
    )
    avg_per_day = recent_subs / 30.0
    score = _clamp(avg_per_day * 10)
    return TraitScore(
        dimension="activity",
        score=round(score, 1),
        evidence=f"近 30 天共 {recent_subs} 次提交（日均 {avg_per_day:.1f}）",
    )


# ---------- 主入口 ----------

def compute_abilities(stats: AggregatedStats) -> AbilityReport:
    skills = [score_skill(d, stats) for d in SKILL_DIMS]
    traits = [
        _score_stability(stats),
        _score_speed(stats),
        _score_pressure(stats),
        _score_breakthrough(stats),
        _score_activity(stats),
    ]
    return AbilityReport(
        handle=stats.handle,
        generated_at=int(time.time()),
        skills=skills,
        traits=traits,
        overall_rating=stats.rating.current,
        overall_max_rating=stats.rating.peak,
    )


# ---------- AI 评语 ----------

_NARRATIVE_SYSTEM = """你是资深 Codeforces 竞赛教练。根据用户给出的选手画像数据，生成三段式评语。

格式要求（严格遵守）：
【强项】 ≤100 字，引用 score≥70 的技能/特征，说明该选手的长处
【弱项】 ≤100 字，引用 score≤40 的技能/特征，指出明显短板；若无明显弱项，说明哪些维度有提升空间
【建议】 ≤100 字，给出 3 条可执行建议（带具体难度段或 tag）

硬规则：
- 只引用数据中出现过的数字、tag、维度名
- 禁用"非常/极其/十分"等空泛形容词
- 不编造题号和比赛名
- 全文总长 ≤300 字"""


def _template_narrative(report: AbilityReport) -> str:
    top_skills = sorted(report.skills, key=lambda s: -s.score)[:3]
    weak_skills = sorted(report.skills, key=lambda s: s.score)[:2]
    top_traits = sorted(report.traits, key=lambda t: -t.score)[:2]
    weak_traits = sorted(report.traits, key=lambda t: t.score)[:1]
    return (
        f"【强项】 {report.handle} 在 "
        + "、".join(f"{s.dimension}({s.score})" for s in top_skills)
        + f" 三项技能突出；特征上 "
        + "、".join(f"{t.dimension}({t.score})" for t in top_traits)
        + " 表现亮眼。\n"
        f"【弱项】 相对薄弱的是 "
        + "、".join(f"{s.dimension}({s.score})" for s in weak_skills)
        + f"，以及 {weak_traits[0].dimension}({weak_traits[0].score})。\n"
        f"【建议】 针对 {weak_skills[0].dimension} 做 20 道该 tag 题；"
        f"每周 2 场 virtual 参赛提高 {weak_traits[0].dimension}；"
        f"保持 {top_skills[0].dimension} 的训练强度不掉队。"
    )


def generate_narrative(
    report: AbilityReport,
    feedback: Optional[str] = None,
) -> str:
    """调 Haiku 生成评语；无 key 时降级到模板. feedback 非空时要求改写前版."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "[模板评语，未检测到 ANTHROPIC_API_KEY]\n" + _template_narrative(report)

    try:
        from anthropic import Anthropic
    except ImportError:
        return "[模板评语，anthropic SDK 未安装]\n" + _template_narrative(report)

    client = Anthropic()

    data_block = {
        "handle": report.handle,
        "rating": report.overall_rating,
        "peak": report.overall_max_rating,
        "skills": [s.model_dump() for s in report.skills],
        "traits": [t.model_dump() for t in report.traits],
    }
    user_content = "选手画像数据：\n" + json.dumps(
        data_block, ensure_ascii=False, indent=2
    )
    if feedback:
        user_content += (
            f"\n\n上一版评语及审阅反馈：\n{feedback}\n"
            f"请针对反馈修正，保持格式规则不变。"
        )

    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=600,
        system=[
            {
                "type": "text",
                "text": _NARRATIVE_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
    )
    return resp.content[0].text.strip()


# ---------- LLM-as-Judge ----------

_JUDGE_SYSTEM = """你是竞赛评语审阅员（严苛）. 按以下标准打 1-5 分：
5: 三段齐 + 数字/tag 引用准确 + 建议具体到 难度段+tag+题量
4: 以上都满足但措辞稍空
3: 至少 1 条建议不够具体，或 1 项数据引用模糊
2: 缺段 / 引用了数据之外的内容 / 建议空泛
1: 完全不可用

只输出 JSON: {"score": 1-5整数, "reason": "<=30字一句话解释"}
不要 markdown 代码块、不要其它文字。"""


def judge_report(report: AbilityReport) -> JudgeResult:
    """用 Haiku 给 narrative 打分. 无 key 时返回默认 3 分."""
    if not report.narrative:
        return JudgeResult(score=1, reason="narrative 为空")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return JudgeResult(score=3, reason="无 API key，跳过评审")

    try:
        from anthropic import Anthropic
    except ImportError:
        return JudgeResult(score=3, reason="anthropic SDK 未安装")

    client = Anthropic()
    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        system=[{"type": "text", "text": _JUDGE_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": report.narrative}],
    )
    text = resp.content[0].text.strip()
    # 去掉可能的 markdown 代码块包裹
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        return JudgeResult.model_validate(json.loads(text))
    except Exception:
        return JudgeResult(score=3, reason=f"JSON 解析失败: {text[:80]}")


# ---------- Judge 重写循环（Feedback Loop 核心） ----------

_JUDGE_LOG = Path(__file__).resolve().parent.parent / "logs" / "judge.log"
_judge_logger = logging.getLogger("judge")
if not _judge_logger.handlers:
    _JUDGE_LOG.parent.mkdir(exist_ok=True)
    _judge_logger.setLevel(logging.INFO)
    _h = logging.FileHandler(_JUDGE_LOG)
    _h.setFormatter(logging.Formatter("%(message)s"))
    _judge_logger.addHandler(_h)


def generate_narrative_with_judge(
    report: AbilityReport,
    max_retries: int = 2,
    on_attempt: Optional[Callable[[int, JudgeResult], None]] = None,
) -> tuple[str, JudgeResult, list[dict]]:
    """核心反馈循环：生成 → 审阅 → <4 分携带反馈重写.

    返回 (最终评语, 最终 Judge, 所有尝试的 trace).
    每次尝试都落盘 logs/judge.log 作为观测证据.
    """
    trace: list[dict] = []
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))

    # 无 key：跳过循环，走一次模板生成
    if not has_key:
        narrative = generate_narrative(report)
        report.narrative = narrative
        judge = JudgeResult(score=3, reason="无 API key，跳过 Judge 循环")
        if on_attempt:
            on_attempt(1, judge)
        entry = {"attempt": 1, "handle": report.handle,
                 "score": judge.score, "reason": judge.reason,
                 "narrative_preview": narrative[:120]}
        _judge_logger.info(json.dumps(entry, ensure_ascii=False))
        trace.append(entry)
        return narrative, judge, trace

    # 有 key：最多 max_retries+1 次尝试
    feedback: Optional[str] = None
    narrative = ""
    judge = JudgeResult(score=1, reason="未执行")
    for attempt in range(max_retries + 1):
        narrative = generate_narrative(report, feedback=feedback)
        report.narrative = narrative
        judge = judge_report(report)
        entry = {
            "attempt": attempt + 1,
            "handle": report.handle,
            "score": judge.score,
            "reason": judge.reason,
            "narrative_preview": narrative[:120],
        }
        _judge_logger.info(json.dumps(entry, ensure_ascii=False))
        trace.append(entry)
        if on_attempt:
            on_attempt(attempt + 1, judge)
        if judge.score >= 4:
            break
        feedback = f"上一版评语:\n{narrative}\n\n审阅反馈 ({judge.score}/5): {judge.reason}"

    return narrative, judge, trace


if __name__ == "__main__":
    import sys
    from fetcher import fetch_profile
    from aggregator import aggregate

    handle = sys.argv[1] if len(sys.argv) > 1 else "tourist"
    agg = aggregate(fetch_profile(handle, submissions=300))
    report = compute_abilities(agg)

    def _progress(n: int, j: JudgeResult) -> None:
        mark = "✓" if j.score >= 4 else "✗"
        print(f"  [{mark}] 第 {n} 次: score={j.score}/5 · {j.reason}")

    narrative, judge, trace = generate_narrative_with_judge(
        report, max_retries=2, on_attempt=_progress,
    )
    report.narrative = narrative
    print(report.model_dump_json(indent=2))
    print("\n=== Judge trace ===")
    for t in trace:
        print(json.dumps(t, ensure_ascii=False))
