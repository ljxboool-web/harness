# CF-Profiler 使用指南

> 完整命令参考 + 常见场景操作流程。快速上手见 [README.md](../README.md#快速开始)。

## 0. 给 Codex 的入口

如果你是在 `harness` 工作区里由 Codex 接手本项目，默认顺序是：

```bash
cd /home/ljxboool/harness/harness-acm
sed -n '1,220p' AGENTS.md
sed -n '1,220p' .codex/project.md
pytest -x
```

其中 `pytest -x` 是默认验证入口；除非只改纯文档，否则不要跳过它。

## 1. 环境准备

```bash
cd /home/ljxboool/harness/harness-acm

# 依赖（Python 3.10+）
pip install -r requirements.txt

# 可选：启用 AI 评语 + Judge 循环
export DASHSCOPE_API_KEY=sk-...
export DASHSCOPE_MODEL=qwen-turbo
export DASHSCOPE_JUDGE_MODEL=qwen-turbo
# 美国（弗吉尼亚）节点；北京可改为 https://dashscope.aliyuncs.com/compatible-mode/v1
export DASHSCOPE_BASE_URL=https://dashscope-us.aliyuncs.com/compatible-mode/v1
# 更快：不重写，只跑单路宽松 judge
export DASHSCOPE_MAX_RETRIES=0
export DASHSCOPE_JUDGE_MODE=fast
export DASHSCOPE_ENABLE_THINKING=false
```

也可以把同样的变量写进本目录的 `.env.local`；仓库提供了 `.env.example` 作为占位模板。

无 `DASHSCOPE_API_KEY` 时，评语自动降级为模板版，Judge 循环跳过 —— 功能完整，只是不走 AI 闸门。

---

## 2. 四个入口

| 命令 | 作用 |
|------|------|
| `python src/cli.py <handle>` | 主入口：抓数据 → 8+5 维评分 → AI 评语 → TUI 渲染 |
| `python src/baseline.py update <handle>` | 写入/刷新分数快照到 `baselines/<handle>.json` |
| `python src/baseline.py check <handle>` | 对比当前分数 vs baseline，报告 drift |
| `python src/metrics.py stats` | 打印 `logs/metrics.jsonl` 的聚合摘要 |

---

## 3. 主 CLI · `src/cli.py`

### 全量参数

```bash
python src/cli.py <handle>
  [--submissions N]           # 抓取最近 N 条提交，默认 500
  [--no-ai]                   # 跳过 AI 评语（省 token / 离线演示）
  [--max-retries N]           # Judge 中位数 <4 时最多重写 N 次，默认 2
  [--check-baseline]          # 流程末尾对比 baseline
  [--baseline-threshold F]    # drift 超过多少分报警，默认 5.0
  [--strict]                  # 搭配 --check-baseline：有 drift 即 exit 1
```

### 典型用法

**最小调用**（只要看分数，不花 token）：
```bash
python src/cli.py tourist --no-ai
```

**完整流程**（Ensemble Judge 循环 + baseline 对比）：
```bash
python src/cli.py tourist --check-baseline
```

**CI 守门**（drift 超阈值即非零退出）：
```bash
python src/cli.py tourist --check-baseline --strict
echo "exit code: $?"
```

**批量扫描 10 位知名选手**：
```bash
for u in $(cat samples/usernames.txt); do
  python src/cli.py "$u" --no-ai --submissions 200
done
```

### 输出解读

```
── 评语生成 · Ensemble Judge 审阅循环 ──
  [✗] 第 1 次 · median 3/5  [strict=2  lenient=5  data=3]
  [✓] 第 2 次 · median 4/5  [strict=3  lenient=5  data=4]
```

- `median`：3 个 judge 打分的中位数，≥4 时循环结束
- 方括号里是三个 judge 各自的分数，用来观察系统性偏差（strict 一般偏低是正常的）

---

## 4. Baseline 管理 · `src/baseline.py`

### 首次生成基线

```bash
python src/baseline.py update tourist
# → 写入 baselines/tourist.json
```

**推荐**：把 3-5 位风格差异大的选手（tourist / Um_nik / jiangly）都刷成 baseline 并 `git add baselines/*.json`，后续任何公式/prompt 改动都能立刻看出异常。

### 对比检查

```bash
python src/baseline.py check tourist
python src/baseline.py check tourist --threshold 3       # 阈值更严
python src/baseline.py check tourist --strict            # 有 drift 即 exit 1
```

输出示例（漂移时）：
```
baseline drift:
  dimension                    old     new       Δ
  skill.graph                100.0    80.0   -20.0
  skill.math                  97.7    78.2   -19.5
```

### 何时刷新 baseline

**应该刷新** —— 下列任一改动后：
- 改 `TAG_CATEGORIES`（tag 归类变了）
- 改评分公式（`_clamp` / `0.40/0.50/0.10` 权重 / 等）
- 样本选手刷了新题导致真实分数变化（次要）

**不应该刷新**：
- 只改了 narrative prompt
- 只改了日志/CLI 渲染
- 只改了测试

刷新前先用 `check` 看 drift 有多大，确认是代码意图而不是 bug 再 `update`。

---

## 5. 观测统计 · `src/metrics.py`

### 打印最近 24 小时指标

```bash
python src/metrics.py stats --since 24
```

```
metrics.jsonl · records=113 · window=24.0h

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

### 关注点

| 指标 | 健康区间 | 异常信号 |
|------|----------|----------|
| `cache.hit_rate` | > 0.8 | < 0.5 说明缓存失效或 TTL 太短 |
| `api.p95_ms` | < 3000 | > 5000 考虑网络 / 增加重试 |
| `judges.strict.median` | 2-3 | = 5 说明 strict judge 失效或 prompt 太松 |
| `judges.lenient.median` | 4-5 | = 1-2 说明 narrative 严重有问题 |
| `loops.first_pass_rate` | > 0.4 | < 0.2 考虑调整 narrative prompt |
| `loops.avg_attempts` | < 2.0 | > 2.5 多数样本在重写，Ensemble 成本高 |

### 事件清单（供下游工具解析 `logs/metrics.jsonl`）

| event | 字段 |
|-------|------|
| `cache_hit` / `cache_miss` | `method`, `key_hash` |
| `api_call_done` | `method`, `attempt`, `latency_ms`, `ok` |
| `judge_run` | `handle`, `judge_name`, `score`, `reason` |
| `judge_loop_done` | `handle`, `attempts`, `final_score`, `individual_scores` |
| `baseline_diff` | `handle`, `dimension`, `delta` |

每行一个 JSON，带 `ts` (unix float seconds) 时间戳。

---

## 6. 测试与调试

```bash
# 全部 46 个测试，< 1 秒
pytest

# 只跑离线测试（不需网络）
pytest tests/test_judge_loop.py tests/test_ensemble_judge.py tests/test_baseline.py tests/test_metrics.py

# 单一测试 + 全输出
pytest tests/test_ensemble_judge.py::test_parallel_execution -v -s

# 失败即停 + 本地调试
pytest -x --pdb
```

### 常见排障

| 现象 | 可能原因 | 解决 |
|------|----------|------|
| `FetchError: user.info: handle not found` | 选手 handle 拼写错（大小写敏感） | 查 `samples/usernames.txt` |
| `FetchError: ... after 3 retries` | CF 临时 429 / 网络中断 | 等几分钟再试；查 `logs/fetcher.log` 详细原因 |
| Judge 循环总在 3/5 徘徊 | 无 API key → 默认中性 3 分 | `export DASHSCOPE_API_KEY=...` |
| `baselines/X.json` 不存在 | 还没 update 过 | `python src/baseline.py update X` |
| pytest 个别 network test 失败 | CF API 偶尔 5xx | `pytest --deselect tests/test_fetcher.py` 跳过 |

---

## 7. 常见工作流

### A. 新贡献者首次上手

```bash
pip install -r requirements.txt
pytest                                      # 应 46 passed
python src/cli.py tourist --no-ai            # 看一次完整输出
python src/baseline.py update tourist        # 生成本地 baseline
python src/cli.py tourist --check-baseline   # 应 0 drift
```

### B. 改了评分公式，验证影响

```bash
# 改前先 check 当前 drift
python src/baseline.py check tourist --threshold 1

# 改代码后再 check
python src/baseline.py check tourist --threshold 1

# 确认改动是预期的 → 刷新 baseline
python src/baseline.py update tourist
git add baselines/tourist.json
git commit -m "refresh baseline after formula tweak"
```

### C. CI 集成

```bash
# .github/workflows/regression.yml 风格伪代码
- run: pip install -r requirements.txt
- run: pytest -v
- run: |
    for u in tourist jiangly Um_nik; do
      python src/cli.py "$u" --no-ai --check-baseline --strict
    done
```

任意一个样本选手漂移 → 整个 job 挂，引起 review。

### D. 定期观测

```bash
# 每天早上看昨天 24 小时指标
python src/metrics.py stats --since 24

# 看累计（含所有历史）
python src/metrics.py stats
```

---

## 8. 数据与缓存位置

| 路径 | 内容 | 是否 Git 跟踪 |
|------|------|---------------|
| `cache/cf.sqlite` | API 响应 24h 缓存 | 否 |
| `logs/fetcher.log` | fetcher 事件 JSON Lines | 否 |
| `logs/judge.log` | Judge 循环 trace | 否 |
| `logs/metrics.jsonl` | 结构化观测指标 | 否 |
| `baselines/*.json` | 分数快照 | **是** |

清缓存：`rm cache/cf.sqlite`（下次调用会重建）。
清指标：`> logs/metrics.jsonl`（或删文件，下次 `emit_metric` 自动创建）。
