"""AggregatedStats → AbilityReport (评分层) + DashScope 评语 + Ensemble Judge 循环.

评分公式参考 AGENTS.md 的项目约定.
Judge 循环: 生成 → 3-judge ensemble 并行审阅 → 中位数 <4 分携带 feedback 重写.
"""
from __future__ import annotations

import json
import logging
import math
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from codex_api import (
    CodexAPIConfigError,
    generate_json,
    generate_text,
    has_api_key,
)
from metrics import emit_metric
from schemas import (
    AbilityReport,
    AggregatedStats,
    CFProblem,
    Confidence,
    JudgeEnsembleResult,
    JudgeResult,
    PracticePlan,
    RecommendedProblem,
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


# ---------- 训练题单推荐 ----------

def _problem_key_from_problem(problem: CFProblem) -> str:
    if problem.contestId:
        return f"c:{problem.contestId}:{problem.index}"
    return f"g:{problem.name}"


def _problem_url(problem: CFProblem) -> str:
    return f"https://codeforces.com/problemset/problem/{problem.contestId}/{problem.index}"


def _round_cf_rating(value: float) -> int:
    return int(_clamp(round(value / 100) * 100, 800, 3500))


def _target_rating_band(skill: SkillScore, base_rating: int) -> tuple[int, int]:
    if skill.score < 40:
        lo, hi = base_rating - 400, base_rating + 100
    elif skill.score < 70:
        lo, hi = base_rating - 200, base_rating + 200
    else:
        lo, hi = base_rating, base_rating + 300
    if skill.confidence == "low":
        lo -= 200
        hi -= 100
    lo_i = _round_cf_rating(lo)
    hi_i = _round_cf_rating(max(hi, lo_i))
    return lo_i, max(lo_i, hi_i)


def _weak_skills(report: AbilityReport, limit: int = 3) -> list[SkillScore]:
    weak = [s for s in report.skills if s.score < 70]
    if not weak:
        weak = list(report.skills)
    weak.sort(key=lambda s: (s.score, s.attempted))
    return weak[:limit]


def _candidate_problems(
    *,
    skill: SkillScore,
    problems: list[CFProblem],
    attempted_keys: set[str],
    picked_keys: set[str],
    lo: int,
    hi: int,
) -> list[CFProblem]:
    tags = set(TAG_CATEGORIES[skill.dimension])
    candidates: list[CFProblem] = []
    for problem in problems:
        if problem.contestId is None or problem.rating is None:
            continue
        key = _problem_key_from_problem(problem)
        if key in attempted_keys or key in picked_keys:
            continue
        if not tags.intersection(problem.tags):
            continue
        if lo <= problem.rating <= hi:
            candidates.append(problem)
    target = (lo + hi) / 2
    candidates.sort(key=lambda p: (
        abs((p.rating or target) - target),
        -p.solved_count,
        -(p.contestId or 0),
        p.index,
    ))
    return candidates


def _problem_reason(skill: SkillScore, problem: CFProblem, lo: int, hi: int) -> str:
    matched = [
        t for t in problem.tags
        if t in TAG_CATEGORIES[skill.dimension]
    ][:3]
    tag_part = "/".join(matched) if matched else skill.dimension
    return (
        f"{skill.dimension} 当前 {skill.score:.1f}，推荐先做 "
        f"{lo}-{hi} 分段；本题 rating {problem.rating}，匹配 {tag_part}。"
    )


def _refine_plan_with_ai(plan: PracticePlan) -> PracticePlan:
    """用 DashScope 对题单摘要和理由做轻量润色；失败时保留确定性题单."""
    if not has_api_key() or not plan.problems:
        return plan

    payload = {
        "handle": plan.handle,
        "rating": plan.rating,
        "weak_skills": plan.weak_skills,
        "rating_range": [plan.target_rating_min, plan.target_rating_max],
        "problems": [p.model_dump() for p in plan.problems],
    }
    system = """你是 Codeforces 训练教练。只基于输入 problems 生成题单说明。
不要新增、删除或改写题目 contest_id/index。只输出 JSON:
{"summary":"<=80字中文总结","items":[{"contest_id":整数,"index":"原 index","reason":"<=50字中文推荐理由"}]}"""
    try:
        text = generate_json(
            system_prompt=system,
            user_content=json.dumps(payload, ensure_ascii=False),
            schema_name="practice_plan_refine",
            schema={
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "contest_id": {"type": "integer"},
                                "index": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            "required": ["contest_id", "index", "reason"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["summary", "items"],
                "additionalProperties": False,
            },
            max_output_tokens=500,
        )
        parsed = json.loads(text)
    except Exception:
        return plan

    by_key = {(p.contest_id, p.index): p for p in plan.problems}
    refined: list[RecommendedProblem] = []
    for item in parsed.get("items", []):
        key = (item.get("contest_id"), item.get("index"))
        if key not in by_key:
            continue
        problem = by_key[key]
        refined.append(problem.model_copy(update={
            "reason": str(item.get("reason", problem.reason))[:80],
        }))
    if not refined:
        refined = plan.problems
    return plan.model_copy(update={
        "summary": str(parsed.get("summary", plan.summary))[:120],
        "problems": refined,
        "source": "dashscope",
    })


def generate_practice_plan(
    stats: AggregatedStats,
    report: AbilityReport,
    problems: list[CFProblem],
    max_problems: int = 9,
    use_ai: bool = True,
) -> PracticePlan:
    """根据 rating、薄弱技能和 CF problemset 推荐未尝试题目."""
    max_problems = max(1, min(max_problems, 30))
    base_rating = report.overall_rating or stats.rating.current or 1200
    base_rating = int(_clamp(base_rating, 800, 3500))
    weak = _weak_skills(report)
    attempted_keys = set(stats.attempted_problem_keys)
    picked_keys: set[str] = set()
    picked: list[RecommendedProblem] = []
    bands = {s.dimension: _target_rating_band(s, base_rating) for s in weak}
    per_skill = max(1, math.ceil(max_problems / max(1, len(weak))))

    for skill in weak:
        lo, hi = bands[skill.dimension]
        candidates = _candidate_problems(
            skill=skill, problems=problems, attempted_keys=attempted_keys,
            picked_keys=picked_keys, lo=lo, hi=hi,
        )
        if not candidates:
            lo = _round_cf_rating(lo - 300)
            hi = _round_cf_rating(hi + 300)
            candidates = _candidate_problems(
                skill=skill, problems=problems, attempted_keys=attempted_keys,
                picked_keys=picked_keys, lo=lo, hi=hi,
            )
            bands[skill.dimension] = (lo, hi)

        for problem in candidates[:per_skill]:
            if len(picked) >= max_problems:
                break
            key = _problem_key_from_problem(problem)
            picked_keys.add(key)
            picked.append(RecommendedProblem(
                contest_id=int(problem.contestId or 0),
                index=problem.index,
                name=problem.name,
                rating=int(problem.rating or base_rating),
                tags=problem.tags,
                solved_count=problem.solved_count,
                target_skill=skill.dimension,
                reason=_problem_reason(skill, problem, *bands[skill.dimension]),
                url=_problem_url(problem),
            ))

    if len(picked) < max_problems:
        for skill in weak:
            lo, hi = bands[skill.dimension]
            candidates = _candidate_problems(
                skill=skill, problems=problems, attempted_keys=attempted_keys,
                picked_keys=picked_keys, lo=_round_cf_rating(lo - 400),
                hi=_round_cf_rating(hi + 400),
            )
            for problem in candidates:
                if len(picked) >= max_problems:
                    break
                key = _problem_key_from_problem(problem)
                picked_keys.add(key)
                picked.append(RecommendedProblem(
                    contest_id=int(problem.contestId or 0),
                    index=problem.index,
                    name=problem.name,
                    rating=int(problem.rating or base_rating),
                    tags=problem.tags,
                    solved_count=problem.solved_count,
                    target_skill=skill.dimension,
                    reason=_problem_reason(skill, problem, lo, hi),
                    url=_problem_url(problem),
                ))
            if len(picked) >= max_problems:
                break

    if bands:
        target_min = min(lo for lo, _ in bands.values())
        target_max = max(hi for _, hi in bands.values())
    else:
        target_min = _round_cf_rating(base_rating - 200)
        target_max = _round_cf_rating(base_rating + 200)

    weak_names = "、".join(s.dimension for s in weak)
    summary = (
        f"围绕 {weak_names} 补短板，难度集中在 {target_min}-{target_max}；"
        f"建议每题完成后复盘错误原因和可复用模板。"
    )
    plan = PracticePlan(
        handle=report.handle,
        rating=report.overall_rating,
        target_rating_min=target_min,
        target_rating_max=target_max,
        weak_skills=[s.dimension for s in weak],
        summary=summary,
        problems=picked,
    )
    return _refine_plan_with_ai(plan) if use_ai else plan


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
    """调阿里云百炼 Chat Completions API 生成评语；无 key 时降级到模板."""
    if not has_api_key():
        return "[模板评语，未检测到 DASHSCOPE_API_KEY]\n" + _template_narrative(report)

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

    try:
        return generate_text(
            system_prompt=_NARRATIVE_SYSTEM,
            user_content=user_content,
            max_output_tokens=600,
        )
    except CodexAPIConfigError:
        return "[模板评语，OpenAI SDK 未安装]\n" + _template_narrative(report)


# ---------- LLM-as-Judge: 3 个不同风格的 prompt ----------

_JUDGE_SYSTEM = """你是竞赛评语审阅员（严苛）. 按以下标准打 1-5 分：
5: 三段齐 + 数字/tag 引用准确 + 建议具体到 难度段+tag+题量
4: 以上都满足但措辞稍空
3: 至少 1 条建议不够具体，或 1 项数据引用模糊
2: 缺段 / 引用了数据之外的内容 / 建议空泛
1: 完全不可用

只输出 JSON: {"score": 1-5整数, "reason": "<=30字一句话解释"}
不要 markdown 代码块、不要其它文字。"""


_JUDGE_STRICT = """你是竞赛评语严格审阅员。重点查格式与完整性：
- 必须有 【强项】【弱项】【建议】 三段，缺一段立即 1 分
- 含"非常/极其/十分/特别"等空泛措辞 → 扣 1 分
- 任一段落引用的维度不在 data 里 → 扣 2 分
- 三段齐全、无空泛措辞、无编造 → 5 分

只输出 JSON: {"score": 1-5整数, "reason": "<=30字解释"}
不要 markdown 代码块、不要其它文字。"""


_JUDGE_LENIENT = """你是竞赛评语宽松审阅员。重点看有无实用信息：
- 只要三段齐、能让选手看到自己的长短板，默认 4 分
- 建议明确到 tag 或难度段 → 加 1 分 → 5
- 完全没有可操作性才打 3
- 只有一段（或无格式）才 2
- 完全胡编才 1

只输出 JSON: {"score": 1-5整数, "reason": "<=30字解释"}
不要 markdown 代码块、不要其它文字。"""


_JUDGE_DATA = """你是竞赛评语数字审阅员（只看数据保真度）。
规则：narrative 括号里写出的 score 数字必须与 data 中同维度数值相符（允许 ±1 误差）。
- 所有引用数字都对 → 5
- 1 处偏差 2-5 分 → 3
- 1 处偏差 >5 分，或引用了 data 中不存在的维度 → 2
- 多处数据错误 → 1

输入包含 data JSON 与 narrative 文本，你必须交叉比对。
只输出 JSON: {"score": 1-5整数, "reason": "<=30字解释, 必要时点名具体维度"}
不要 markdown 代码块、不要其它文字。"""


def _judge_with_prompt(
    report: AbilityReport,
    system_prompt: str,
    judge_name: str,
    include_data: bool = False,
) -> JudgeResult:
    """单个 judge 的核心调用；无 key / SDK 缺失 → 中性 3 分."""
    if not report.narrative:
        return JudgeResult(score=1, reason="narrative 为空", judge_name=judge_name)
    if not has_api_key():
        return JudgeResult(score=3, reason="无 API key，跳过评审",
                           judge_name=judge_name)
    # data judge 需要完整 data 做交叉比对；其他 judge 只看 narrative
    if include_data:
        data_block = {
            "skills": [s.model_dump() for s in report.skills],
            "traits": [t.model_dump() for t in report.traits],
        }
        user_content = (f"data:\n{json.dumps(data_block, ensure_ascii=False)}"
                        f"\n\nnarrative:\n{report.narrative}")
    else:
        user_content = report.narrative

    try:
        text = generate_json(
            system_prompt=system_prompt,
            user_content=user_content,
            schema_name="judge_result",
            schema={
                "type": "object",
                "properties": {
                    "score": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                    },
                    "reason": {
                        "type": "string",
                    },
                },
                "required": ["score", "reason"],
                "additionalProperties": False,
            },
            max_output_tokens=200,
        )
    except CodexAPIConfigError:
        return JudgeResult(score=3, reason="OpenAI SDK 未安装",
                           judge_name=judge_name)
    except Exception as e:
        return JudgeResult(score=3, reason=f"API error: {str(e)[:40]}",
                           judge_name=judge_name)

    try:
        parsed = json.loads(text)
        return JudgeResult(
            score=int(parsed["score"]),
            reason=str(parsed.get("reason", ""))[:60],
            judge_name=judge_name,
        )
    except Exception:
        return JudgeResult(score=3, reason=f"JSON 解析失败: {text[:40]}",
                           judge_name=judge_name)


def judge_report(report: AbilityReport) -> JudgeResult:
    """向后兼容的单 judge 入口（仍用原 _JUDGE_SYSTEM prompt）."""
    return _judge_with_prompt(report, _JUDGE_SYSTEM, judge_name="default")


# ---------- Ensemble Judge (3 个并行，取中位数) ----------

_ENSEMBLE_JUDGES: tuple[tuple[str, str, bool], ...] = (
    ("strict", _JUDGE_STRICT, False),
    ("lenient", _JUDGE_LENIENT, False),
    ("data", _JUDGE_DATA, True),
)


def _selected_ensemble_judges() -> tuple[tuple[str, str, bool], ...]:
    mode = os.environ.get("DASHSCOPE_JUDGE_MODE", "ensemble").strip().lower()
    if mode in {"fast", "single", "lenient"}:
        return (("lenient", _JUDGE_LENIENT, False),)
    if mode == "data":
        return (("data", _JUDGE_DATA, True),)
    return _ENSEMBLE_JUDGES


def judge_report_ensemble(report: AbilityReport) -> JudgeEnsembleResult:
    """Configured judges run in parallel; default is 3-judge median."""
    judges = _selected_ensemble_judges()
    with ThreadPoolExecutor(max_workers=len(judges)) as pool:
        futures = [
            pool.submit(_judge_with_prompt, report, prompt, name, include_data)
            for name, prompt, include_data in judges
        ]
        results: list[JudgeResult] = []
        for name, _, _ in judges:
            pass  # keep names in order
        for i, fut in enumerate(futures):
            name = judges[i][0]
            try:
                results.append(fut.result(timeout=30))
            except Exception as e:
                results.append(JudgeResult(
                    score=3, reason=f"judge error: {str(e)[:40]}",
                    judge_name=name,
                ))

    scores = [r.score for r in results]
    median = int(statistics.median(scores))
    combined = " | ".join(f"[{r.judge_name}/{r.score}]{r.reason}" for r in results)

    for r in results:
        emit_metric("judge_run", handle=report.handle,
                    judge_name=r.judge_name, score=r.score, reason=r.reason)

    return JudgeEnsembleResult(
        median_score=median, individual=results, combined_reason=combined,
    )


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
    on_attempt: Optional[Callable[[int, JudgeEnsembleResult], None]] = None,
) -> tuple[str, JudgeEnsembleResult, list[dict]]:
    """核心反馈循环：生成 → ensemble 审阅 → 中位数<4 时携带 combined feedback 重写.

    返回 (最终评语, 最终 JudgeEnsembleResult, 所有尝试的 trace).
    每次尝试都落盘 logs/judge.log + logs/metrics.jsonl 作为观测证据.
    """
    trace: list[dict] = []
    has_key = has_api_key()

    # 无 key：跳过循环，走一次模板生成
    if not has_key:
        narrative = generate_narrative(report)
        report.narrative = narrative
        fallback_individual = [
            JudgeResult(score=3, reason="无 API key", judge_name=name)
            for name, _, _ in _selected_ensemble_judges()
        ]
        judge = JudgeEnsembleResult(
            median_score=3,
            individual=fallback_individual,
            combined_reason="无 API key，跳过 Judge 循环",
        )
        if on_attempt:
            on_attempt(1, judge)
        entry = {"attempt": 1, "handle": report.handle,
                 "score": judge.median_score, "reason": judge.combined_reason,
                 "individual_scores": [r.score for r in judge.individual],
                 "narrative_preview": narrative[:120]}
        _judge_logger.info(json.dumps(entry, ensure_ascii=False))
        trace.append(entry)
        emit_metric("judge_loop_done", handle=report.handle,
                    attempts=1, final_score=3,
                    individual_scores=[r.score for r in judge.individual])
        return narrative, judge, trace

    # 有 key：最多 max_retries+1 次尝试
    feedback: Optional[str] = None
    narrative = ""
    judge = JudgeEnsembleResult(
        median_score=1, individual=[], combined_reason="未执行",
    )
    for attempt in range(max_retries + 1):
        narrative = generate_narrative(report, feedback=feedback)
        report.narrative = narrative
        judge = judge_report_ensemble(report)
        entry = {
            "attempt": attempt + 1,
            "handle": report.handle,
            "score": judge.median_score,
            "reason": judge.combined_reason,
            "individual_scores": [r.score for r in judge.individual],
            "narrative_preview": narrative[:120],
        }
        _judge_logger.info(json.dumps(entry, ensure_ascii=False))
        trace.append(entry)
        if on_attempt:
            on_attempt(attempt + 1, judge)
        if judge.median_score >= 4:
            break
        feedback = (f"上一版评语:\n{narrative}\n\n"
                    f"审阅反馈 (中位数 {judge.median_score}/5): "
                    f"{judge.combined_reason}")

    emit_metric("judge_loop_done", handle=report.handle,
                attempts=len(trace), final_score=judge.median_score,
                individual_scores=[r.score for r in judge.individual])
    return narrative, judge, trace


if __name__ == "__main__":
    import sys
    from fetcher import fetch_profile
    from aggregator import aggregate

    handle = sys.argv[1] if len(sys.argv) > 1 else "tourist"
    agg = aggregate(fetch_profile(handle, submissions=300))
    report = compute_abilities(agg)

    def _progress(n: int, j: JudgeEnsembleResult) -> None:
        mark = "✓" if j.median_score >= 4 else "✗"
        indiv = " ".join(f"{r.judge_name}={r.score}" for r in j.individual)
        print(f"  [{mark}] 第 {n} 次: median={j.median_score}/5 · {indiv}")

    narrative, judge, trace = generate_narrative_with_judge(
        report, max_retries=2, on_attempt=_progress,
    )
    report.narrative = narrative
    print(report.model_dump_json(indent=2))
    print("\n=== Judge trace ===")
    for t in trace:
        print(json.dumps(t, ensure_ascii=False))
