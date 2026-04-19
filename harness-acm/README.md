# CF-Profiler · Codeforces 选手实力画像 CLI

> 输入一个 handle，输出 **8 维算法技能 + 5 维个性特征 + AI 教练评语**，带 LLM-as-Judge 自动审阅 / 重写循环

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
- AI 教练三段式评语（强项 / 弱项 / 建议），**经过 Haiku-as-Judge 审阅 + 自动重写**，确保建议具体可执行

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
      │  analyzer.py   │  8+5 维打分 → Haiku 生成评语 → Haiku-Judge → <4 分重写
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
| [`CLAUDE.md`](./CLAUDE.md) | 项目宪法：角色定位、**分层职责边界**、数据获取规则、AI 评语硬约束、失败处理流程 |
| [`.claude/skills/cf-analyzer/SKILL.md`](./.claude/skills/cf-analyzer/SKILL.md) | 可复用技能：触发条件 + 5 步执行流程 + 禁止事项 + 输出格式契约 |
| [`.claude/settings.json`](./.claude/settings.json) | 工具白名单（python / pytest / pip + CF 域名 WebFetch） |

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
| Anthropic SDK | Haiku 4.5 生成评语 + 作 Judge 打分 | [`src/analyzer.py`](./src/analyzer.py) `generate_narrative` / `judge_report` |
| Prompt Cache | 系统提示用 `cache_control: ephemeral`，重复调用省 token | 同上 |

为什么这么选：
- CF API 完全公开、不要 key、数据最全
- Haiku 4.5 在中文评语任务上质量够用，价格比 Sonnet 低一个数量级，适合作 Judge（高频调用）
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

#### 闸门 3：LLM-as-Judge 自动重写循环 ⭐

核心代码在 [`src/analyzer.py`](./src/analyzer.py) `generate_narrative_with_judge`：

```python
for attempt in range(max_retries + 1):
    narrative = generate_narrative(report, feedback=last_feedback)   # Haiku 生成
    judge = judge_report(report)                                     # Haiku 审阅
    log_to_judge_log(attempt, narrative, judge.score, judge.reason)  # 落盘证据
    if judge.score >= 4:
        break
    last_feedback = f"[{judge.score}/5: {judge.reason}]"             # 反馈给下一轮
```

**实机运行示例**（`python src/cli.py tourist` 部分输出）：

```
── 评语生成 · Judge 审阅循环 ──
  [✗] 第 1 次 · 3/5 · 建议不够具体到题量
  [✗] 第 2 次 · 3/5 · 弱项引用偏笼统
  [✓] 第 3 次 · 5/5 · 数据引用准确、建议可执行

── 教练评语 ──
【强项】 tourist 在 graph(99.9)、math(99.7)、greedy(99.7) 三项技能突出...
...
— 最终 Judge 评分：5/5 · 共 3 轮 · trace → logs/judge.log
```

全过程落盘在 `logs/judge.log`（JSON Lines 格式），是 **AI 自己发现错误 → 自己改正**的完整证据链。这是作业要求"自动感知并纠正"的直接交付物。

---

## 快速开始

```bash
git clone <repo-url> harness-acm
cd harness-acm
pip install -r requirements.txt

# 无 API key 也能跑（模板评语兜底）
python src/cli.py tourist

# 有 key 体验完整 Judge 循环
export ANTHROPIC_API_KEY=sk-ant-...
python src/cli.py jiangly --submissions 500

# 跑测试
pytest -v

# 同时分析多个选手（批量对照）
for u in $(cat samples/usernames.txt); do
  python src/cli.py "$u" --no-ai --submissions 200
done
```

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
              → Haiku 生成评语
              → Haiku-Judge 打 3/5（建议不够具体）
              → 携带反馈重写
              → Haiku-Judge 打 5/5，通过
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
├── CLAUDE.md                            ← Harness 宪法
├── README.md                            ← 本文档
├── requirements.txt
├── .claude/
│   ├── settings.json                    ← 工具白名单
│   └── skills/cf-analyzer/SKILL.md      ← 可复用技能定义
├── src/
│   ├── schemas.py         (166 行)      ← Pydantic 模型（第 1 道闸门）
│   ├── fetcher.py         (152 行)      ← CF API + SQLite 缓存
│   ├── aggregator.py      (208 行)      ← 纯统计聚合
│   ├── analyzer.py        (407 行)      ← 评分 + Haiku 生成 + Judge 循环
│   └── cli.py             (132 行)      ← TUI 渲染
├── tests/                               ← pytest（第 2 道闸门），24 passed
│   ├── test_fetcher.py
│   ├── test_aggregator.py               (8 tests，含跨选手对照)
│   ├── test_analyzer.py                 (8 tests)
│   └── test_judge_loop.py               (7 tests，mock Judge 循环)
├── cache/cf.sqlite                      ← API 缓存（gitignore）
├── logs/
│   ├── fetcher.log                      ← API 调用 trace
│   └── judge.log                        ← Judge 审阅 trace（第 3 道闸门证据）
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
| LLM | Anthropic Haiku 4.5 | 中文评语稳定，价格友好 |
| 测试 | pytest | 反馈循环第二道闸门 |

---

## 已知限制

- Codeforces API 每次返回最多约 10000 次提交，超活跃老号会被截断（当前按 `contestId+index` 主键去重已缓解大部分影响）
- `breakthrough` 指标对 tourist 这种 3500+ 顶级选手无意义（题库没这么难的题），已降级为默认 80 分
- `activity` 基于近 30 天，短期休息的选手会被"冤枉"；可改为"近 180 天"
- AI 评语质量依赖 `ANTHROPIC_API_KEY`，无 key 时降级到模板（功能仍完整，只是不走 Judge 循环）
- Codeforces tag 归类有粗糙处（例如 `2-sat` 被归入 geometry），可在 `analyzer.py:TAG_CATEGORIES` 自定义

---

## 核心理念

AI 不是"聪明的模型 + 一段 Prompt"就完事了。让 AI 能稳定输出的是**把缰绳写进代码**：

- **Schema** 约束数据形状
- **pytest** 约束逻辑不变量
- **Judge 循环** 约束输出质量

CF-Profiler 是这个理念的 1430 行落地——少一层校验 AI 都会胡编，三层叠起来就能自我收敛。
