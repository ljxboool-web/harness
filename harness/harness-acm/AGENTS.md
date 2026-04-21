# CF-Profiler Codex Harness

> 这是 `harness` 工作区里的 Codex 项目入口。Codex 进入本项目后，默认在 `harness-acm/` 内工作。

## 启动顺序

1. 先留在仓库根目录确认工作区，再进入 `cd /home/ljxboool/harness/harness-acm`
2. 先读本文件，再读 `./.codex/project.md`
3. 要运行验证时优先 `pytest -x`

## 项目目标

输入一个 Codeforces handle，输出多维算法实力评估 + 个性化成长建议（TUI / HTTP API）。

## 角色定位

你是一位资深竞赛教练助手。当用户提供 Codeforces handle 时，你要：
1. 通过 CF 官方 API 抓取公开数据（无需登录）
2. 从提交历史中提取 8 维算法技能 + 5 维个性特征
3. 基于客观数据生成评估报告，并优先使用阿里云百炼 DashScope Chat Completions API 的 Qwen 模型

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
│ aggregator.py│   去重 / 分桶 / verdict 聚合 / rating 稳定性 → AggregatedStats
└──────┬───────┘  （纯统计，不评分）
       ▼
┌──────────────┐
│  analyzer.py │   8 维技能 + 5 维特征打分 + DashScope 评语 → AbilityReport
└──────┬───────┘
       ▼
┌──────────────┐
│ cli.py/server│   TUI 渲染 / HTTP 封装
└──────────────┘
```

## 核心工作规则

- `fetcher` 只抓数据，不做统计。
- `aggregator` 只统计，不评分。
- `analyzer` 只基于 `AggregatedStats` 评分和生成文本，不直接读取 `Profile`。
- `cli.py` / `server.py` 只做展示和协议封装，不下沉业务判断。
- LLM 默认使用阿里云百炼 DashScope Chat Completions API；缺少 `DASHSCOPE_API_KEY` 时退化到模板评语，不得伪造已调用模型。
- 评语必须维持 `【强项】/【弱项】/【建议】` 三段式，总长不超过 300 字。

## Codex 工作约定

- 优先修改 `AGENTS.md`、`.codex/project.md`、`docs/USAGE.md` 来表达项目规则，不把关键约束只藏在 README。
- 需要排障时先看 `logs/fetcher.log`、`logs/judge.log`、`logs/metrics.jsonl`，再决定是否改代码。
- 改评分、聚合、接口边界后，必须跑 `pytest -x`。
- 改 prompt 或 judge 流程后，至少跑一次 `python src/cli.py tourist --no-ai` 和一次带 `DASHSCOPE_API_KEY` 的完整链路。

## 目录约定

| 路径 | 用途 |
|------|------|
| `src/` | 业务代码 |
| `tests/` | pytest 测试 |
| `cache/cf.sqlite` | API 响应缓存 |
| `logs/` | 结构化日志 |
| `samples/usernames.txt` | 演示用户名 |

## 失败处理流程

1. 网络或 API 错误：先查 `logs/fetcher.log`
2. DashScope 调用异常：保留结构化原因，Judge 降级为中性 3 分
3. 评分或接口改动后：运行 `pytest -x`

## 设置一下系统环境变量

- `DASHSCOPE_API_KEY`: 阿里云百炼 API key
- `ALIYUN_API_KEY`: 可选别名；未设置 `DASHSCOPE_API_KEY` 时使用
- `DASHSCOPE_BASE_URL`: DashScope Chat Completions 兼容地址，默认华北2（北京）
- `DASHSCOPE_MODEL`: narrative 使用的模型，默认 `qwen-turbo`
- `DASHSCOPE_JUDGE_MODEL`: judge 使用的模型，默认跟随 `DASHSCOPE_MODEL`
- `DASHSCOPE_MAX_RETRIES`: Web 评语重写次数，默认 `0`
- `DASHSCOPE_JUDGE_MODE`: `ensemble` 跑 3 个 judge；`fast` 只跑宽松 judge
- `DASHSCOPE_ENABLE_THINKING`: 设为 `true` / `1` 时通过 `extra_body` 开启百炼思考模式
