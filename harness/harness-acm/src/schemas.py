"""Pydantic 数据模型 — 所有 API 输入 / 分析输出必须过这里."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------- Codeforces API 原始结构 ----------

class CFUserInfo(BaseModel):
    handle: str
    rating: Optional[int] = None
    maxRating: Optional[int] = None
    rank: Optional[str] = None
    maxRank: Optional[str] = None
    contribution: int = 0
    registrationTimeSeconds: int


class CFProblem(BaseModel):
    contestId: Optional[int] = None
    index: str
    name: str
    rating: Optional[int] = None  # 题目难度
    tags: list[str] = Field(default_factory=list)
    solved_count: int = 0  # problemset.problemStatistics.solvedCount


class CFAuthor(BaseModel):
    participantType: str = "UNKNOWN"  # CONTESTANT / VIRTUAL / PRACTICE / OUT_OF_COMPETITION / MANAGER


class CFSubmission(BaseModel):
    id: int
    contestId: Optional[int] = None
    creationTimeSeconds: int
    problem: CFProblem
    author: Optional[CFAuthor] = None
    verdict: Optional[str] = None  # "OK" / "WRONG_ANSWER" / ...
    programmingLanguage: str = ""
    relativeTimeSeconds: int = 0  # 比赛开始后多少秒提交


class CFRatingChange(BaseModel):
    contestId: int
    contestName: str
    handle: str
    rank: int
    oldRating: int
    newRating: int
    ratingUpdateTimeSeconds: int


# ---------- 组合档案 ----------

class Profile(BaseModel):
    """单个选手的完整原始档案."""
    user: CFUserInfo
    submissions: list[CFSubmission]
    rating_history: list[CFRatingChange]


# ---------- 聚合中间层（纯统计，无打分） ----------

class DifficultyBucket(BaseModel):
    lo: int  # 含
    hi: int  # 含
    solved: int
    attempted: int


class VerdictCounts(BaseModel):
    ok: int = 0
    wrong_answer: int = 0
    time_limit_exceeded: int = 0
    memory_limit_exceeded: int = 0
    runtime_error: int = 0
    compilation_error: int = 0
    other: int = 0

    @property
    def total(self) -> int:
        return (self.ok + self.wrong_answer + self.time_limit_exceeded
                + self.memory_limit_exceeded + self.runtime_error
                + self.compilation_error + self.other)

    @property
    def ac_rate(self) -> float:
        return self.ok / self.total if self.total else 0.0


class RatingStats(BaseModel):
    current: Optional[int]
    peak: Optional[int]
    mean: Optional[float]
    std: Optional[float]  # rating 历史的标准差（稳定性）
    contests: int
    recent_trend: Optional[float] = None  # 近 10 场平均 delta
    rating_changes: list[int] = Field(default_factory=list)  # 按时间序的 delta


class AggregatedStats(BaseModel):
    handle: str
    total_submissions: int
    unique_problems_attempted: int
    unique_problems_solved: int
    difficulty_buckets: list[DifficultyBucket]
    verdicts: VerdictCounts
    tag_solved: dict[str, int]
    tag_attempted: dict[str, int]
    tag_max_rating: dict[str, int] = Field(default_factory=dict)  # 每个 tag 上 AC 过的最高难度
    attempted_problem_keys: list[str] = Field(default_factory=list)
    solved_problem_keys: list[str] = Field(default_factory=list)
    rating: RatingStats
    daily_submission_count: dict[str, int]  # YYYY-MM-DD -> count
    # 个性特征要用的额外字段
    mean_ac_contest_minutes: Optional[float] = None  # 比赛内 AC 的平均分钟数（越小越快）
    rated_ac_rate: Optional[float] = None  # CONTESTANT 提交的 AC 率
    practice_ac_rate: Optional[float] = None  # PRACTICE 提交的 AC 率
    breakthrough_ac_rate: Optional[float] = None  # 超 (current_rating+200) 的题的 AC 率


# ---------- 分析输出（评分层） ----------

SkillDimension = Literal[
    "dp", "graph", "math", "greedy", "data_structure",
    "string", "search", "geometry",
]

TraitDimension = Literal[
    "stability", "speed", "pressure", "breakthrough", "activity",
]

Confidence = Literal["low", "medium", "high"]


class SkillScore(BaseModel):
    dimension: SkillDimension
    score: float = Field(ge=0, le=100)
    solved: int                # 该维度 AC 题数
    attempted: int             # 该维度尝试题数
    max_rating: Optional[int]  # 该维度最高 AC 难度
    confidence: Confidence


class TraitScore(BaseModel):
    dimension: TraitDimension
    score: float = Field(ge=0, le=100)
    evidence: str  # 一句话解释分数怎么来的


class AbilityReport(BaseModel):
    handle: str
    generated_at: int  # unix timestamp
    skills: list[SkillScore]
    traits: list[TraitScore]
    overall_rating: Optional[int]
    overall_max_rating: Optional[int]
    narrative: Optional[str] = None  # AI 生成评语，初始为空

    @property
    def skill_radar(self) -> dict[str, float]:
        return {s.dimension: s.score for s in self.skills}


class RecommendedProblem(BaseModel):
    contest_id: int
    index: str
    name: str
    rating: int
    tags: list[str]
    solved_count: int = 0
    target_skill: SkillDimension
    reason: str
    url: str


class PracticePlan(BaseModel):
    """根据选手 rating 和薄弱技能生成的训练题单."""
    handle: str
    rating: Optional[int]
    target_rating_min: int
    target_rating_max: int
    weak_skills: list[SkillDimension]
    summary: str
    problems: list[RecommendedProblem]
    source: str = "heuristic"


class JudgeResult(BaseModel):
    score: int = Field(ge=1, le=5)
    reason: str
    judge_name: str = "default"  # ensemble 中用来区分三个 judge；单 judge 保持 default


class JudgeEnsembleResult(BaseModel):
    """3 个 judge 并行打分 → 中位数。"""
    median_score: int = Field(ge=1, le=5)
    individual: list[JudgeResult]  # 长度应为 3
    combined_reason: str  # 拼接所有 judge 的 reason，用于 rewrite feedback


class Drift(BaseModel):
    """Baseline 与当前报告在某维度上的偏差."""
    dimension: str
    old: float
    new: float
    delta: float  # new - old
