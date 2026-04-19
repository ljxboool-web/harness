---
name: cf-analyzer
description: 分析 Codeforces 选手实力并生成多维评估报告。当用户给出 CF handle 或要求"分析某选手"时触发。
---

# Codeforces 选手分析技能

## 触发条件

用户输入满足以下任一条件：
- 提供一个 Codeforces handle（如 "分析 tourist"）
- 要求对比多位选手
- 要求生成个人实力画像

## 执行步骤

1. **数据抓取**：调用 `src.fetcher.fetch_profile(handle)` 获取 user_info + 近 500 次提交 + rating 历史
2. **维度计算**：调用 `src.analyzer.compute_abilities(profile)` 得到 `AbilityReport`
3. **AI 解读**：基于 `AbilityReport` 生成"强项/弱项/建议"三段式评语（300 字内）
4. **验证**：将评语交给 `src.analyzer.judge_report()`，若评分 < 4 则重写（最多 2 次）
5. **渲染**：通过 `src.cli.render(report)` 在 TUI 输出雷达图 + 面板

## 输出格式契约

AI 生成的 `narrative` 必须满足：

```
【强项】 ... (<=100字, 引用 skills 中 score >= 70 的维度)
【弱项】 ... (<=100字, 引用 skills 中 score <= 40 的维度)
【建议】 ... (<=100字, 给出 3 条可执行训练建议)
```

## 禁止事项

- 不得跳过 schema 校验
- 不得伪造选手数据（例如选手不存在时必须报错，不能编造）
- 不得在评语中引用未在报告里出现的具体题号
- 不得使用"非常"、"极其"等无实际信息量的形容词
