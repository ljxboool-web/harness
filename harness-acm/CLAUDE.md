# Codeforces 选手实力画像生成器 (CF-Profiler)

> 输入一个 Codeforces handle，输出多维算法实力评估 + 个性化成长建议（TUI 呈现）。

## 角色定位

你是一位资深竞赛教练助手。当用户提供 Codeforces handle 时，你要：
1. 通过 CF 官方 API 抓取公开数据（无需登录）
2. 从提交历史中提取 8 维算法技能 + 5 维个性特征
3. 生成客观、有针对性的评估报告

## 分层架构（必须严格遵守）

```
Codeforces API
      │
      ▼
┌──────────────┐
│  fetcher.py  │   抓取 + 缓存 + 结构校验 → Profile
└──────┬───────┘
       ▼
┌──────────────┐
│ aggregator.py│   去重 / 难度分桶 / verdict 聚合 / rating 稳定性 → AggregatedStats
└──────┬───────┘  （纯统计，不评分）
       ▼
┌──────────────┐
│  analyzer.py │   8 维技能 + 5 维特征打分 + AI 评语 → AbilityReport
└──────┬───────┘
       ▼
┌──────────────┐
│    cli.py    │   TUI 渲染
└──────────────┘
```

**职责边界**：
- `fetcher` 只管拿数据；不做任何计数
- `aggregator` 只做计数 / 分桶 / 去重；不做评分
- `analyzer` 只做评分 + AI 解读；不直接访问 Profile（只用 AggregatedStats）
- 逾越边界 = 违反 Harness 规则，测试会卡

## 核心工作规则

### 数据获取
- **只调用** `src/fetcher.py` 中封装的函数，禁止直接 `requests.get`（避免绕开缓存）
- API 返回的字段必须通过 `src/schemas.py` 的 Pydantic 模型校验后再使用
- 网络请求失败必须抛 `FetchError`，不得静默 fallback 到旧缓存（除非用户显式指定 `--cache-only`）

### 数据分析
- 8 维技能 tag 映射定义在 `src/analyzer.py:TAG_CATEGORIES`，修改需同步更新测试
- 对于提交数不足 20 的选手，标记为 `confidence: "low"` 而非编造结果
- 所有评分用 0-100 标准化，算法在 docstring 中必须写清公式

### AI 评语生成
- 只基于已计算的客观数据（`AbilityReport`）生成文字评语
- **禁止编造**具体题号/比赛名；只引用数据里真实存在的项
- 评语不超过 300 字，按"强项-弱项-建议"三段式

## 目录约定

| 路径 | 用途 |
|------|------|
| `src/` | 业务代码（fetcher / schemas / analyzer / cli） |
| `tests/` | pytest 测试，运行 `pytest -x` 自动反馈 |
| `cache/cf.sqlite` | API 响应缓存（24h 有效） |
| `logs/` | 结构化日志（JSON Lines），失败时查看 `logs/error.log` |
| `samples/usernames.txt` | 演示用选手名单 |
| `.claude/skills/cf-analyzer/` | Agent Skill 定义 |

## 失败处理流程

1. 网络或 API 错误 → 查 `logs/fetcher.log` → 定位到具体 handle/endpoint → 重试最多 3 次（指数退避）
2. Schema 校验失败 → 打印缺失字段 → 检查 CF API 是否变更 → 更新 schemas
3. 测试失败 → 先读 `pytest` 报错 → 定位失败断言 → 修业务代码而不是修测试

## 反馈循环（Feedback Loop）

本项目的三道验证关：

1. **Pydantic Schema 校验** — 所有 API 输入、所有分析输出都过 schema，数据类型错了立即炸
2. **pytest + 固定样本对照** — `samples/usernames.txt` 里的选手有已知特征，每次改动后跑 `pytest` 对照
3. **LLM-as-Judge** — 生成的评语会被 Haiku 评分（见 `src/analyzer.py:judge_report`），低于 4 分自动重写

每次修改分析逻辑后，**必须**运行 `pytest -x` 并确认全绿。
