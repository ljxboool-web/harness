# CF-Profiler · Codex Harness Project

> 一个面向 Codex / harness 工作流整理过的 Codeforces 选手实力画像项目：输入 handle，输出 **8 维算法技能 + 5 维个性特征 + AI 教练评语**，带 LLM-as-Judge 自动审阅 / 重写循环。

## Codex 入口

如果这个仓库是从 `harness` 工作区接入 Codex，先按这个顺序：

```bash
cd /home/ljxboool/harness/harness-acm
sed -n '1,220p' AGENTS.md
sed -n '1,220p' .codex/project.md
pytest -x
```

项目级规则以 `AGENTS.md` 和 `.codex/project.md` 为准；README 负责解释项目，不承担唯一规则源。

```
Codeforces 选手画像 · Um_nik
Rating   : 3376    Peak : 3663    Contests : 307    AC Rate : 86.0%

── 8 维算法技能 ──
  dp              ████████████████████████  99.6   ● 51 AC / peak 3500
  graph           ████████████████████████  98.9   ● 48 AC / peak 3500
  ...
  geometry        ██████░░░░░░░░░░░░░░░░░░  24.1   ○  1 AC / peak 1200    ← 自动识别弱项
```

---

## 解决什么痛点

算法竞赛选手（ACM 集训队 / Codeforces 活跃用户）每周都会遇到三个问题：

1. **"我到底哪个算法弱？"** —— 看 CF rating 只知道一个笼统数字（比如 1800），但 1800 选手里 DP 弱的和图论弱的训练方向完全不同
2. **"下一阶段该练什么？"** —— 没人帮你从 500+ 次提交里归纳规律
3. **"我和目标的差距在哪？"** —— 对比 tourist 这种顶尖选手，差距是具体的哪几个维度？

**CF-Profiler** 输入一个 handle，10 秒内输出：
- 8 维算法技能评分 + 置信度（DP / 图论 / 数学 / 贪心 / 数据结构 / 字符串 / 搜索 / 几何）
- 5 维个性特征（稳定性 / 速度 / 抗压 / 攻坚 / 活跃）
- AI 教练三段式评语（强项 / 弱项 / 建议），**经过 Codex/OpenAI Judge 审阅 + 自动重写**，确保建议具体可执行

关键点：所有数据都是客观算出的，没有任何硬编码。Um_nik 的 geometry 弱项是系统从他 200 次提交里发现的。

---

## 架构：Harness 三支柱如何落地

```
         Codeforces API
               │
               ▼
      ┌────────────────┐
      │   fetcher.py   │  抓取 + SQLite 缓存 + 结构化日志 → Profile
      └───────┬────────┘
              ▼
      ┌────────────────┐
      │ aggregator.py  │  去重 / 难度分桶 / tag 聚合 / 速度·抗压·攻坚信号 → AggregatedStats
      └───────┬────────┘       （纯统计，不评分）
              ▼
      ┌────────────────┐
      │  analyzer.py   │  8+5 维打分 → OpenAI/Codex 生成评语 → Judge → <4 分重写
      └───────┬────────┘
              ▼
      ┌────────────────┐
      │     cli.py     │  ANSI TUI 渲染 + 实时审阅进度
      └────────────────┘
```

**分层边界是强制的**：`analyzer` 不得直接访问 `Profile`，只能用 `AggregatedStats`；`fetcher` 不得做任何计数。违反边界 → pytest 会炸。

---

### 支柱 1 · 上下文管理 / Agent Skill

| 文件 | 承载的规则 |
|------|------------|
| [`AGENTS.md`](./AGENTS.md) | Codex 项目入口：角色定位、**分层职责边界**、数据获取规则、AI 评语硬约束、失败处理流程 |
| [`./.codex/project.md`](./.codex/project.md) | Codex 本地项目说明：启动命令、验证路径、运行时约束 |
| [`docs/USAGE.md`](./docs/USAGE.md) | 命令入口、baseline、metrics 与 Web UI 使用方式 |

关键设计：规则不是写给人看的励志口号，而是**可执行的动作**，比如：
- "AI 评语必须按【强项】/【弱项】/【建议】三段式，≤300 字"
- "超过自身 rating+200 的题目 AC 率必须从 `stats.breakthrough_ac_rate` 读取，不得重算"

---

### 支柱 2 · 外部工具调用

| 工具 | 用途 | 实现 |
|------|------|------|
| Codeforces REST API | `user.info` / `user.status` / `user.rating` 三个端点 | [`src/fetcher.py`](./src/fetcher.py) `_api_call` |
| SQLite 本地缓存 | API 响应 24 小时缓存，规避重复请求 | [`src/fetcher.py`](./src/fetcher.py) `_cache_get/put` |
| 结构化日志 | 每次 API 调用记录 `{event, method, attempt, error}` JSON Lines | `logs/fetcher.log` |
| OpenAI Responses API | Codex 模型生成评语 + 作 Judge 打分 | [`src/analyzer.py`](./src/analyzer.py) `generate_narrative` / `judge_report` |

为什么这么选：
- CF API 完全公开、不要 key、数据最全
- OpenAI Responses API 是当前推荐的新项目接口，Codex 模型可直接承担 coding / judge 型工作流
- SQLite 零依赖，cache/ 目录已 gitignore

---

### 支柱 3 · 验证与反馈循环（三道闸门）

#### 闸门 1：Pydantic Schema 校验

10+ 个模型定义在 [`src/schemas.py`](./src/schemas.py)：`CFUserInfo`、`CFSubmission`、`AggregatedStats`、`AbilityReport`、`JudgeResult` …

- CF API 返回字段错类型 → `FetchError` 立即抛出
- 评分越界（`score` 不在 0-100）→ Pydantic 自动拒绝
- `JudgeResult.score` 声明 `Field(ge=1, le=5)`，返回 0 就炸

#### 闸门 2：pytest 测试矩阵（24 tests）

| 测试文件 | 覆盖 |
|----------|------|
| `tests/test_fetcher.py` | CF API 可达 + schema 可解析 |
| `tests/test_aggregator.py` | 8 个结构性不变量（solved≤attempted，verdict 求和等） |
| `tests/test_analyzer.py` | 分数范围、8+5 维齐全、confidence 规则、**Um_nik 的 geometry 必须显著低于 dp** |
| `tests/test_judge_loop.py` | 用 monkeypatch 模拟第 1 次 2 分 / 第 2 次 5 分，验证触发一次重写；穷举 retry 上限等 6 种场景 |

```bash
$ pytest -v
================ 24 passed in 0.38s ================
```

每次改动分析逻辑 → `pytest -x` 10 秒内反馈，比人肉看代码快 100 倍。

#### 闸门 3：LLM-as-Judge 自动重写循环（**Ensemble 升级**）⭐

核心代码在 [`src/analyzer.py`](./src/analyzer.py) `generate_narrative_with_judge`：每次生成完评语，**3 个风格迥异的 judge 并行打分**，取中位数作为最终评价。

```
          narrative
             │
   ┌─────────┼─────────┐
   ▼         ▼         ▼
strict    lenient    data        ← 并行 (ThreadPoolExecutor, 3 workers)
格式 1-5   可读 1-5   数字 1-5
   └─────────┼─────────┘
             ▼
         median 分
```

| judge | 关注点 | 典型打分 |
|-------|--------|----------|
| `strict` | 三段格式/完整性、空泛措辞 | 较低，1-3 常见 |
| `lenient` | 可读性与实用性 | 较高，4-5 常见 |
| `data` | 引用数字与 data JSON 的偏差（见 `_JUDGE_DATA`） | 随 narrative 真实度波动 |

三个 prompt 风格不同，系统性偏好可以相互抵消。任何单个 judge 挂掉 → 降级为中性 3 分，中位数仍可计算，不阻塞流程。

```python
for attempt in range(max_retries + 1):
    narrative = generate_narrative(report, feedback=last_feedback)
    ensemble = judge_report_ensemble(report)                         # 并行 3 judge
    log_to_judge_log(attempt, ensemble)
    if ensemble.median_score >= 4:
        break
    last_feedback = ensemble.combined_reason                         # 3 个 reason 拼起来反馈
```

**实机输出**（`python src/cli.py tourist`）：

```
── 评语生成 · Ensemble Judge 审阅循环 ──
  [✗] 第 1 次 · median 3/5  [strict=2  lenient=5  data=3]
  [✓] 第 2 次 · median 4/5  [strict=3  lenient=5  data=4]

── 教练评语 ──
【强项】 tourist 在 graph(100)、math(97.7)、greedy(99.3) 三项技能突出...
...
— 最终 Judge 中位数：4/5 · 共 2 轮 · [strict=3 lenient=5 data=4] · trace → logs/judge.log
```

完整 trace（含每个 judge 的独立分数）落盘 `logs/judge.log`（JSON Lines）。

---

### 支柱 4 · 可观测性（Observability）

新增 [`src/metrics.py`](./src/metrics.py) 集中采集 JSON Lines 指标到 `logs/metrics.jsonl`：

| event | 字段 | 来源 |
|-------|------|------|
| `cache_hit` / `cache_miss` | `method`, `key_hash` | fetcher |
| `api_call_done` | `method`, `attempt`, `latency_ms`, `ok` | fetcher |
| `judge_run` | `handle`, `judge_name`, `score`, `reason` | analyzer.judge_report_ensemble |
| `judge_loop_done` | `handle`, `attempts`, `final_score`, `individual_scores` | analyzer.generate_narrative_with_judge |
| `baseline_diff` | `handle`, `dimension`, `delta` | baseline.diff_baseline |

Summary CLI：

```bash
$ python src/metrics.py stats --since 24
metrics.jsonl · records=113 · window=24h

── Cache ──
  hit=22  miss=2  hit_rate=0.917

── API ──
  calls=2  avg=1365.9ms  p95=1380.9ms

── Judges ──
  data       count=15  median=5  mean=4.6
  lenient    count=15  median=5  mean=5.0
  strict     count=15  median=3  mean=3.6

── Judge loops ──
  count=27  avg_attempts=1.56  first_pass_rate=0.56
```

一眼就能看出「strict judge 系统性偏严」「缓存命中率正常」等观测结论。`emit_metric` 永不抛异常 —— observability 不会破坏业务逻辑。

---

### 支柱 5 · Baseline 回归

[`src/baseline.py`](./src/baseline.py) 把固定选手的评分快照到 `baselines/{handle}.json`（Git 跟踪）。任何公式/prompt 改动导致分数漂移 >5 分 → `check` 立刻报警。

```bash
# 生成（或刷新）baseline
$ python src/baseline.py update tourist
baseline 已写入 baselines/tourist.json
  skills: 8, traits: 5

# 本次评分 vs baseline 比对
$ python src/baseline.py check tourist --threshold 5
baseline 对比: 0 drift

# 假设把 _clamp 改成 *0.8 后
$ python src/baseline.py check tourist --strict
baseline drift:
  dimension                    old     new       Δ
  skill.graph                100.0    80.0   -20.0
  skill.math                  97.7    78.2   -19.5
  ...
[--strict] 有 8 处超阈值 drift → exit 1
```

主 CLI 也集成了：`python src/cli.py tourist --check-baseline [--strict]`。

只存数字维度（8 技能 + 5 特征 + rating/peak），不存 narrative —— AI 生成每次必变，不适合 baseline。

---

## 快速开始

```bash
git clone <repo-url> harness-acm
cd harness-acm
pip install -r requirements.txt

# 无 API key 也能跑（模板评语兜底）
python src/cli.py tourist

# 有 key 体验完整 Judge 循环
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL=gpt-5.3-codex
python src/cli.py jiangly --submissions 500

# 跑测试
pytest -v

# 同时分析多个选手（批量对照）
for u in $(cat samples/usernames.txt); do
  python src/cli.py "$u" --no-ai --submissions 200
done

# Ensemble Judge + baseline 回归（推荐）
python src/baseline.py update tourist                 # 首次：生成 baseline
python src/cli.py tourist --check-baseline            # 后续：自动对比
python src/cli.py tourist --check-baseline --strict   # CI 模式：有 drift 即 exit 1

# 观测仪表
python src/metrics.py stats --since 24                # 近 24h 的 cache/API/judge 统计
```

---

## Web UI · 浏览器可视化

同一条分析流水线套了一层 FastAPI + Chart.js 前端，所有数字维度都有图：

```bash
pip install -r requirements.txt                              # 含 fastapi / uvicorn
PYTHONPATH=src python -m uvicorn server:app --reload --port 8000
# 浏览器打开 http://localhost:8000/
```

四个视图（sidebar 切换）：

| 视图 | 展示内容 |
|------|----------|
| **选手画像** | 双雷达（8 维技能 + 5 维特征）· 难度分布 · verdict 饼图 · rating 轨迹 · 近 180 天热力图 · 三段式评语（SSE 实时推送 judge 每一轮） |
| **Judge Loop** | strict / lenient / data 三评委打分曲线 + median 覆盖线 + 每轮 reason |
| **Baseline 对比** | old / new 双雷达叠图 · drift 表（按 \|Δ\| 排序，色标）· 「以当前为新 baseline」一键按钮 |
| **Harness 健康度** | cache hit rate · API p95 · judge first-pass rate · baseline drift 计数 · 10s 自动刷新 |

关键端点（`src/server.py`）：

```
GET  /api/analyze/{handle}            # 快路径：不跑 AI，亚秒返回
GET  /api/narrate/{handle}            # SSE：每轮 judge 完成推一条 event
GET  /api/baseline/{handle}/diff      # 当前 vs baseline 的 drift
POST /api/baseline/{handle}           # 保存当前为新 baseline
GET  /api/metrics?since=24            # 透传 metrics.summarize
GET  /api/logs/judge?handle=X&limit=N # tail logs/judge.log
```

业务模块零改动 —— server.py 只是对 fetcher/aggregator/analyzer/baseline/metrics
已有函数的 HTTP 外壳。`generate_narrative_with_judge` 的 `on_attempt` 回调被桥接到
SSE 事件流上，浏览器能看到每一轮打分出来的瞬间。

---

## 使用示例：一个完整 case

**输入**：

```bash
$ python src/cli.py Um_nik --submissions 200
```

**处理链路**：

```
1. fetcher → CF API 3 个 endpoint → 缓存到 cache/cf.sqlite
2. aggregator → 去重后 Um_nik 200 题 → 每题按 tag 归入 8 维桶
3. analyzer → 算 8+5 维分数
              → OpenAI/Codex 生成评语
              → Judge 打 3/5（建议不够具体）
              → 携带反馈重写
              → Judge 打 5/5，通过
4. cli → ANSI 彩色渲染
```

**输出（节选）**：

```
── 8 维算法技能 ──
  dp              ████████████████████████  99.6   ● 51 AC / peak 3500
  graph           ████████████████████████  98.9   ● 48 AC / peak 3500
  math            ████████████████████████  99.9   ● 130 AC / peak 3500
  greedy          ████████████████████████  99.7   ● 145 AC / peak 3500
  data_structure  ████████████████████████  99.6   ● 68 AC / peak 3500
  string          ████████████████████░░░░  83.2   ○  7 AC / peak 2700
  search          ████████████████████████  99.9   ● 75 AC / peak 3500
  geometry        ██████░░░░░░░░░░░░░░░░░░  24.1   ○  1 AC / peak 1200

── 5 维个性特征 ──
  pressure        ████████████████████████  100.0  rated AC 89% / practice AC 35%
  speed           █████████████████░░░░░░░  71.0   比赛内 AC 平均 49.3 分钟
  stability       ████████████░░░░░░░░░░░░  48.7   近 30 场 rating 变化 std=77
  ...
```

系统**没有任何硬编码**关于 Um_nik 的知识，纯粹从他 200 次提交计算出"geometry 是弱项"——这与他本人公开承认的短板完全一致。

---

## 评分公式

### 8 维技能（每维 0-100）

```
skill_score = 0.40 * 量 + 0.50 * 质 + 0.10 * 成功率
  量    = min(100, 25 * log₂(solved + 1))           ← 10 题 ≈ 87
  质    = clamp((max_rating_acd - 800) / 22, 0, 100)  ← 1900 = 50, 3000 = 100
  成功率 = 100 * solved / attempted
```

置信度：尝试题数 <10 → `low`、10-30 → `medium`、>30 → `high`。

### 5 维个性特征

| 维度 | 公式 |
|------|------|
| stability（稳定性） | `100 - clamp(std(近30场 Δrating) / 1.5, 0, 100)` |
| speed（速度） | `100 - 1.5 * (比赛内平均 AC 分钟 - 30)` |
| pressure（抗压） | `rated_ac_rate / practice_ac_rate`（≥1 → 100，<1 → 按比例） |
| breakthrough（攻坚） | `100 * 3 * (超 rating+200 的题 AC 率)` |
| activity（活跃） | `min(100, 近 30 天日均提交 * 10)` |

所有公式写在 [`src/analyzer.py`](./src/analyzer.py) 的 docstring，改公式必须同步更新测试。

---

## 项目结构

```
harness-acm/
├── AGENTS.md                            ← Codex 项目入口
├── README.md                            ← 本文档
├── requirements.txt
├── .codex/
│   └── project.md                       ← Codex 项目说明
├── src/
│   ├── schemas.py         (~190 行)     ← Pydantic 模型（第 1 道闸门）
│   ├── fetcher.py         (~160 行)     ← CF API + SQLite 缓存 + metrics 埋点
│   ├── aggregator.py      (~210 行)     ← 纯统计聚合
│   ├── analyzer.py        (~510 行)     ← 评分 + OpenAI/Codex 生成 + Ensemble Judge 循环
│   ├── baseline.py        (~140 行)     ← 快照 + drift 检测（支柱 5）
│   ├── metrics.py         (~170 行)     ← emit_metric + stats CLI（支柱 4）
│   └── cli.py             (~170 行)     ← TUI 渲染 + --check-baseline
├── tests/                               ← pytest，46 passed
│   ├── test_fetcher.py
│   ├── test_aggregator.py               (8 tests，含跨选手对照)
│   ├── test_analyzer.py                 (8 tests)
│   ├── test_judge_loop.py               (8 tests，mock ensemble)
│   ├── test_ensemble_judge.py           (6 tests，含并行加速验证)
│   ├── test_baseline.py                 (9 tests)
│   └── test_metrics.py                  (6 tests)
├── baselines/                           ← Git 跟踪；固定选手分数快照
│   └── {handle}.json
├── cache/cf.sqlite                      ← API 缓存（gitignore）
├── logs/
│   ├── fetcher.log                      ← API 调用 trace
│   ├── judge.log                        ← Judge 审阅 trace（含 individual_scores）
│   └── metrics.jsonl                    ← 结构化观测指标（支柱 4）
└── samples/usernames.txt                ← 10 位知名选手
```

---

## 技术栈

| 层 | 选型 | 原因 |
|----|------|------|
| 语言 | Python 3.10+ | 生态齐全，Pydantic 2 需要 |
| 数据校验 | Pydantic 2 | 反馈循环第一道闸门 |
| HTTP | requests | 简单够用 |
| 缓存 | SQLite (stdlib) | 零依赖 |
| TUI | ANSI 转义 | 零依赖，体积小 |
| LLM | OpenAI Responses API + `gpt-5.3-codex` | 统一接口，直接适配 Codex 风格 harness |
| 测试 | pytest | 反馈循环第二道闸门 |

---

## 已知限制

- Codeforces API 每次返回最多约 10000 次提交，超活跃老号会被截断（当前按 `contestId+index` 主键去重已缓解大部分影响）
- `breakthrough` 指标对 tourist 这种 3500+ 顶级选手无意义（题库没这么难的题），已降级为默认 80 分
- `activity` 基于近 30 天，短期休息的选手会被"冤枉"；可改为"近 180 天"
- AI 评语质量依赖 `OPENAI_API_KEY`，无 key 时降级到模板（功能仍完整，只是不走 Judge 循环）
- Codeforces tag 归类有粗糙处（例如 `2-sat` 被归入 geometry），可在 `analyzer.py:TAG_CATEGORIES` 自定义

---

## 核心理念

AI 不是"聪明的模型 + 一段 Prompt"就完事了。让 AI 能稳定输出的是**把缰绳写进代码**：

- **Schema** 约束数据形状
- **pytest** 约束逻辑不变量
- **Judge 循环** 约束输出质量

CF-Profiler 是这个理念的代码化落地：少一层校验 AI 都会胡编，三层叠起来才会收敛。
