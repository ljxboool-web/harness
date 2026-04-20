# CF-Profiler · Harness Engineering 重设计草案 v2

> 面向作业目标"让 AI 稳定输出 + 自动感知与纠正"，把三支柱（Context / Tools / Feedback）升到四支柱，并把四层扁平架构拆成可插拔的领域包。

---

## 0. 诊断：v1 架构的四个裂缝

| 裂缝 | 现状 | 代价 |
|------|------|------|
| **Context 只是文档** | `AGENTS.md` / `.codex/project.md` 写了"分层边界"，但没有任何机制在运行时/CI 时校验它 | `analyzer.py` 已经 407 行，混了评分公式 + OpenAI SDK + Judge 循环三件事，下次加一维很容易误跨层 |
| **工具硬编码** | `fetcher.py` 直接 import `requests` + `sqlite3` + CF 域名；`analyzer.py` 直接 import 具体 LLM SDK | 换数据源（AtCoder）、换模型（Sonnet / 本地 / Codex）、换缓存（Redis）都要改业务代码 |
| **Feedback 只有一条回路** | Judge 循环只审"评语文本"，不审"评分数字"；pytest 是事后批跑不是运行时闭环；没有回归基线 | tourist 的 dp 分数从 99 降到 85 不会报警；prompt 改坏了，要肉眼比 logs |
| **Agent 单薄** | 单个 `cf-analyzer` skill，流程固定 5 步，输出是 TUI 字符串 | 没法做"对比两位选手""推荐 4 周训练计划"之类的组合任务；Agent 不能自己决定再抓 200 条 |

---

## 1. 新 Harness：四支柱 + 可执行化

```
┌──────────────────────────────────────────────────────────────┐
│                    CF-Profiler Harness v2                    │
├────────────┬────────────┬────────────────┬──────────────────┤
│  Context   │   Tools    │    Feedback    │  Observability   │
│ (规则即代码) │ (全部接口化) │ (5 道闸 + 基线) │   (指标 + 回归)    │
├────────────┼────────────┼────────────────┼──────────────────┤
│ AGENTS.md  │ DataSource │ 1 Schema       │ metrics.jsonl    │
│ SKILL.md×3 │ LLMClient  │ 2 Contract     │ baseline diff    │
│ Contract   │ Cache      │ 3 pytest       │ judge stats      │
│ Tests      │ Rubric     │ 4 Property     │ cache hit rate   │
│            │   (YAML)   │ 5 Ensemble     │ stats CLI        │
│            │            │   + Baseline   │                  │
└────────────┴────────────┴────────────────┴──────────────────┘
```

四支柱每一项都要"可执行" — 不是写在 README 里的口号。

### 1.1 Context 支柱：规则即代码

- `AGENTS.md` 保留为项目宪法，但**每条硬规则必须有一个 test 对应**。例：
  - 规则 "analyzer 不得访问 Profile" → `tests/contract/test_layer_boundaries.py` 用 AST 扫描 `scoring/*.py` 的 import，命中 `domain.profile` 立即 fail
  - 规则 "评语 ≤300 字" → `tests/unit/test_narrative_length.py` mock LLM 返回超长文本，断言循环拒绝并重写
- 每个 SKILL.md 前 matter 里声明**结构化 args**（Pydantic 模型名），skill 正文只写流程与禁忌

### 1.2 Tools 支柱：全部 Protocol 化

所有外部依赖都走 `typing.Protocol`，业务代码只对抽象编程：

```python
# sources/base.py
class DataSource(Protocol):
    def fetch_user(self, handle: str) -> UserInfo: ...
    def fetch_submissions(self, handle: str, limit: int) -> list[Submission]: ...
    def fetch_ratings(self, handle: str) -> list[RatingChange]: ...

# narrator/llm.py
class LLMClient(Protocol):
    def complete(self, sys: str, user: str, cache: bool = True) -> str: ...

# sources/cache.py
class Cache(Protocol):
    def get(self, key: str) -> Any | None: ...
    def put(self, key: str, value: Any, ttl: int) -> None: ...
```

好处：测试用 `FakeDataSource` / `FakeLLMClient` 不再 monkeypatch（`test_judge_loop.py` 现在 170 行一半是 monkeypatch，新写法 30 行）；未来加 AtCoder 只要写一个新 adapter。

### 1.3 Feedback 支柱：5 道闸

| # | 闸门 | 何时触发 | 状态 |
|---|------|----------|------|
| 1 | **Pydantic Schema** | 数据流入流出每一层 | 已有，迁移 |
| 2 | **Contract Tests** | CI + 每次保存文件 | **新增**：分层边界（AST 扫 import）+ prompt 不变量（必须含`【强项】`）+ 配置一致性（`TAG_CATEGORIES` 覆盖全 8 维） |
| 3 | **pytest + 固定样本** | CI | 已有 24 个，按 `tests/unit,contract,property,e2e` 重组 |
| 4 | **Property-Based** | CI | **新增**：hypothesis 随机生成 Profile，断言 aggregator 的不变量（`solved ≤ attempted`、score 随 solved 单调不减等） |
| 5 | **Ensemble Judge + Baseline** | 运行时 + daily CI | **升级**：单 judge → 3 个不同 prompt 的 judge 取中位数；tourist/jiangly/Um_nik 每日跑回归，指标漂移 >5% 自动报警 |

闸 5 是关键升级：现在 judge 只有 1 个，同一个 prompt 可能系统性偏好某种风格；改成 3 个风格不同的 judge（严格 / 宽松 / 数据导向）取中位数，偏差自动抵消。

### 1.4 Observability 支柱（v1 没有）

- `logs/metrics.jsonl` 统一记录：每次 API 调用、每次 LLM token、每次 judge 分、每次 cache hit/miss
- `cf-profiler stats` 子命令 → 看近 7 天的 judge 分布、平均重试次数、token 成本、缓存命中率
- `baselines/<handle>.json` 存金标准 report；每次 CI 跑 `pytest tests/e2e` 对比，漂移 >5% 就 fail
- 所有数据结构化，将来可接 Grafana/Loki

---

## 2. 新目录结构

```
harness-acm/
├── AGENTS.md                       # 宪法（每条规则 ↔ 一个 test）
├── README.md
├── pyproject.toml                  # 从 requirements.txt 升级
├── configs/                        # 外置配置（可切换）
│   ├── rubric.default.yaml         # 默认 8+5 维权重
│   ├── rubric.acm_icpc.yaml        # ACM 偏向
│   └── tags.default.yaml           # tag → 技能维度映射
│
├── prompts/                        # 提示词版本化外置
│   ├── narrative_v2.md
│   ├── judge_strict_v1.md
│   ├── judge_lenient_v1.md
│   └── judge_data_v1.md
│
├── baselines/                      # e2e 金标准
│   ├── tourist.json
│   ├── jiangly.json
│   └── Um_nik.json
│
├── src/cfprof/                     # 可安装包
│   ├── __init__.py
│   ├── config.py                   # 统一配置中心（读 env + YAML）
│   │
│   ├── domain/                     # 纯数据，无任何外部依赖
│   │   ├── profile.py              # Profile / CFSubmission / CFUserInfo
│   │   ├── aggregated.py           # AggregatedStats / DifficultyBucket
│   │   └── report.py               # AbilityReport / SkillScore / NarrativeBundle
│   │
│   ├── sources/                    # 外部数据接入（Protocol 化）
│   │   ├── base.py                 # DataSource / Cache Protocol
│   │   ├── codeforces.py           # CF 适配器（现 fetcher.py 的 API 部分）
│   │   ├── cache_sqlite.py         # SQLite 缓存实现
│   │   └── cache_memory.py         # 测试用 in-memory 缓存
│   │
│   ├── aggregation/                # 纯统计层（不评分）
│   │   ├── aggregate.py            # 主编排
│   │   ├── difficulty.py           # 难度分桶策略
│   │   ├── verdict.py              # Verdict 归并
│   │   └── signals.py              # 速度/抗压/攻坚信号
│   │
│   ├── scoring/                    # 评分层（不调 LLM）
│   │   ├── rubric.py               # Rubric 加载 + 权重
│   │   ├── skills.py               # 8 维评分
│   │   ├── traits.py               # 5 维评分
│   │   └── tag_map.py              # tag → 维度映射
│   │
│   ├── narrator/                   # LLM 生成层（只管写字）
│   │   ├── llm.py                  # LLMClient Protocol + OpenAI/Codex 实现
│   │   ├── prompts.py              # 从 prompts/ 加载 + 版本号校验
│   │   └── generate.py             # 一次生成调用
│   │
│   ├── loop/                       # 反馈循环编排（Harness 核心）
│   │   ├── single_judge.py         # 单 judge 循环（v1 兼容）
│   │   ├── ensemble_judge.py       # 多 judge 中位数（新）
│   │   └── trace.py                # 循环 trace 统一落盘
│   │
│   ├── observability/              # 观测性（新）
│   │   ├── metrics.py              # 统一计量
│   │   ├── baseline.py             # 基线加载 + diff
│   │   └── stats.py                # stats CLI 子命令实现
│   │
│   ├── agent/                      # Agent 交互层（新）
│   │   ├── skills/
│   │   │   ├── profile_args.py     # Pydantic: ProfileArgs
│   │   │   ├── compare_args.py     # Pydantic: CompareArgs
│   │   │   └── coach_args.py       # Pydantic: CoachArgs
│   │   └── tools.py                # Agent tool schema 导出
│   │
│   └── cli/
│       ├── main.py                 # argparse 入口（子命令 dispatcher）
│       ├── commands/
│       │   ├── profile.py          # 默认：单人画像
│       │   ├── compare.py          # 新：两人对比
│       │   ├── plan.py             # 新：4 周训练计划
│       │   ├── growth.py           # 新：成长轨迹
│       │   └── stats.py            # 新：观测面板
│       └── render.py               # TUI 渲染（保留现在的 ANSI 风格）
│
├── tests/
│   ├── contract/                   # 新：规则即代码
│   │   ├── test_layer_boundaries.py     # AST 扫 import
│   │   ├── test_prompts.py              # prompt 不变量
│   │   └── test_rubric_coverage.py      # tag 映射覆盖全维度
│   ├── property/                   # 新：hypothesis
│   │   ├── test_aggregator_invariants.py
│   │   └── test_scoring_monotone.py
│   ├── unit/                       # 迁移现有 24 个
│   │   ├── test_sources.py
│   │   ├── test_aggregation.py
│   │   ├── test_scoring.py
│   │   └── test_loop.py
│   ├── e2e/                        # 新：回归基线
│   │   └── test_golden_baselines.py
│   └── conftest.py                 # FakeDataSource / FakeLLMClient 统一夹具
│
├── cache/                          # gitignore
├── logs/
│   ├── fetcher.log
│   ├── judge.log
│   └── metrics.jsonl               # 新：观测数据
│
└── .codex/
    └── project.md                  # Codex 项目说明
```

拆分尺度：没有超过 150 行的文件；`analyzer.py` 407 行被拆成 `scoring/{skills,traits,rubric}.py` + `narrator/{llm,generate}.py` + `loop/{single_judge,ensemble_judge}.py`，每件事一个文件。

---

## 3. Agent / Skill 升级

### 3.1 多 skill 分角色

| Skill | 触发 | 输入 | 输出 |
|-------|------|------|------|
| `cf-profile` | "分析 tourist" | `ProfileArgs(handle, submissions=500, rubric="default")` | `AbilityReport` JSON + TUI |
| `cf-compare` | "对比 tourist 和 jiangly" | `CompareArgs(handles=[...], focus=[dp,graph])` | 差距雷达 + 胜率估计 |
| `cf-coach` | "我该怎么练 dp" | `CoachArgs(handle, question, context_turns)` | 多轮对话 + 题单推荐 |

每个 skill 的 `args` 是 Pydantic 模型，由 SKILL.md 的 front matter 声明。Agent 调用前参数自动校验。

### 3.2 Tool Use 开放

`agent/tools.py` 把下列函数导出为 agent tool schema：

- `fetch_profile(handle, submissions)`
- `compute_abilities(stats, rubric)`
- `recommend_problems(dim, rating_lo, rating_hi, n)` ← 新
- `compare_players(a, b)` ← 新
- `generate_training_plan(handle, target_rating, weeks)` ← 新

Agent 在 `cf-coach` 模式里可以自由串联。例：选手问"为啥我 dp 弱" → agent 调 `compute_abilities` 取数 → 调 `recommend_problems(dim=dp, rating_lo=user.rating, n=20)` 给题单 → 调 `generate_narrative` 解释。

### 3.3 输出契约双轨

- **给人看**：TUI（ANSI 渲染，保持 v1 视觉）
- **给 agent 吃**：JSON（NarrativeBundle + JudgeResult + BaselineDiff），schema 固定，用 `--json` 切换

这样 agent 可以把 `cf-profile` 的输出作为 `cf-compare` 的输入，实现组合。

---

## 4. 产品深度：4 个新命令

| 命令 | 一句话 | 依赖 |
|------|--------|------|
| `profile <handle>` | v1 保留：8+5 维画像 + Judge 循环 | 无 |
| `compare <a> <b>` | 两人差距雷达，高亮 diff>15 的维度，估算 rated 对战胜率 | 多个 report + 简单 Elo 换算 |
| `plan <handle> --target 2400 --weeks 4` | 基于弱项生成 4 周计划：每周 5 题，带具体 contestId+index | `recommend_problems` + 弱项排序 |
| `growth <handle> --since 2024-01` | 月度 skill score 曲线 + 兴趣漂移检测 | Profile 按时间切片跑 aggregate |
| `stats` | 观测面板：近 7 天 judge 分布 / 成本 / 缓存命中率 | `logs/metrics.jsonl` |

这些命令反推 Harness 要做的改造：
- `compare` 需要 **Rubric 可配置**（不同目标场景换权重）
- `plan` 需要 **Tool Use**（agent 拿到 report 后主动调推荐）
- `growth` 需要 **aggregator 支持时间窗切片**
- `stats` 需要 **Observability 支柱**

---

## 5. 关键接口草图

```python
# domain/report.py
class NarrativeBundle(BaseModel):
    text: str
    prompt_version: str             # "narrative_v2"
    judge_results: list[JudgeResult]  # 多 judge 全记录
    median_score: int
    attempts: int

class AbilityReport(BaseModel):
    handle: str
    generated_at: int
    rubric_name: str                # 新：标明用哪套权重
    skills: list[SkillScore]
    traits: list[TraitScore]
    narrative: Optional[NarrativeBundle] = None


# scoring/rubric.py
class Rubric(BaseModel):
    name: str
    skill_weights: dict[str, float]   # {"qty": 0.4, "qual": 0.5, "succ": 0.1}
    tag_map: dict[str, list[str]]
    trait_formulas: dict[str, str]    # 维度 → 公式名（可切换）

    @classmethod
    def load(cls, name: str = "default") -> "Rubric":
        return cls.model_validate(yaml.safe_load(
            (CONFIG_DIR / f"rubric.{name}.yaml").read_text()
        ))


# loop/ensemble_judge.py
def generate_with_ensemble(
    report: AbilityReport,
    llm: LLMClient,
    judges: list[Judge],              # 3 个不同 prompt 的 judge
    max_retries: int = 2,
) -> NarrativeBundle:
    """
    每轮：生成 → N 个 judge 并发打分 → 取中位数
    中位数 ≥4 → 通过；否则拼接全体反馈继续。
    全程 trace 写 logs/judge.log + metrics.jsonl。
    """


# observability/baseline.py
class BaselineDrift(BaseModel):
    handle: str
    dimension: str
    baseline_score: float
    current_score: float
    delta_pct: float

def diff_against_baseline(
    handle: str, current: AbilityReport, threshold: float = 0.05,
) -> list[BaselineDrift]: ...


# agent/skills/profile_args.py
class ProfileArgs(BaseModel):
    handle: str = Field(min_length=1)
    submissions: int = Field(default=500, ge=50, le=10000)
    rubric: str = Field(default="default")
    format: Literal["tui", "json"] = "tui"
```

---

## 6. 迁移路径（6 步，每步都能跑 pytest）

| 阶段 | 工作 | 不变量 |
|------|------|--------|
| **P1** | 拆 `domain/` + `sources/` + `aggregation/`，保留原函数签名当 facade | 现有 24 tests 全绿 |
| **P2** | prompts 外置到 `prompts/`，LLMClient Protocol 化 | 现有 + 新增 prompt contract tests 绿 |
| **P3** | contract tests（AST）+ property tests（hypothesis） | 加一维技能时 tag_map 漏配会立即 fail |
| **P4** | baseline + metrics 观测性 | golden regression 跑通 |
| **P5** | ensemble judge（3 个 judge 中位数）+ trace 扩展 | 单 judge 结果仍可复现 |
| **P6** | 新命令（compare/plan/growth/stats）+ 新 skill | Agent 可组合调用 |

每一步都独立可发布；出问题回滚单步即可。

---

## 7. 最关键的 3 个"取舍"

1. **评分公式配置化 vs 硬编码的取舍**
   - 配置化：灵活，可做 ACM/IOI/CF 三套预设
   - 代价：YAML 里的魔法数字没有 docstring，反而更难 review
   - **决定**：配置化，但每份 rubric 顶上必须写"为什么这么设"的注释段（也走 contract test 校验）

2. **Ensemble Judge 成本 vs 稳定性的取舍**
   - 3 个 judge = 3× token 消耗
   - 但轻量 judge 模型的单次调用成本通常很低
   - **决定**：默认开 ensemble，`--single-judge` 降级

3. **Agent Tool Use vs 纯脚本的取舍**
   - Tool Use 让 coach 模式自然，但引入了 agent 不可控的调用链
   - **决定**：profile/compare/plan/growth 四个命令是**确定性脚本**（agent 可选调用但不必须），只有 `coach` 才开 Tool Use；所有 tool 调用统一走 `logs/tool_calls.jsonl` 可审计

---

## 8. 验收清单

- [ ] 每条 AGENTS.md 硬规则都有一个测试
- [ ] `src/cfprof/` 任何文件 ≤150 行
- [ ] `analyzer.py`（407 行）拆为 ≥5 个文件，每个单职责
- [ ] 外部依赖（CF API / OpenAI / SQLite）全部 Protocol 化，测试不需 monkeypatch
- [ ] Ensemble judge 3 个 prompt 版本化入库
- [ ] tourist/jiangly/Um_nik 三条 baseline 入库，CI 每次跑 diff
- [ ] 至少 3 个新命令：compare / plan / stats
- [ ] 3 个 skill（profile / compare / coach）每个有 Pydantic args + 触发 contract test

24 tests 全绿 + 这 8 条验收过 = 重设计完成。
