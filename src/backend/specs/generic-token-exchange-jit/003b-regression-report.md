---
agent: tc-engine-regression
status: completed
created: 2026-09-09T12:40:00+08:00
iteration: 2
conclusion: PASS
---

# Avernet 本地回归报告

| 验证 | 结果 |
|---|---:|
| Token Pipeline/Orchestrator + BaaS 定向 | 31 passed |
| Caller core、IAM routers、API、DI 综合定向 | 455 passed |
| Token Pipeline/API coverage | 99% |
| Ruff changed files | passed |
| git diff --check | passed |

完整 `tests/community` 曾运行至约 8% 且当时无失败，因测试集规模较大主动停止；完整门禁交由 GitHub PR CI，不能把该次未完成运行记为 PASS。
