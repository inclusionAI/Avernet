# PR 收敛报告：bcs-provider-attributes-columns

## 范围

- Worktree / repo: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/ocb_worktrees/bcs-internal-bot-attributes-dev/ocb-public` / `git@github.com:inclusionAI/Avernet.git`
- Head / base: `fix/bcs-provider-attributes-columns` / `dev_refactory_collaboration`
- PR: https://github.com/inclusionAI/Avernet/pull/1352
- PR title: `fix(bcs): persist provider bot attributes in columns`
- PR description sections: Problem / Solution / Validation / Compatibility and risk / Spec
- 人工意见模式: auto

## PR 判定

| 结果 | 证据 | 说明 |
|---|---|---|
| 本地验证 | PASS | 见 `003b-regression-report.md`。 |
| PR metadata | PASS | PR #1352 为 OPEN，head/base 与本任务一致；标题及 Problem / Solution / Validation / Compatibility and risk / Spec 段落已核验。 |
| 合并条件 | PENDING | GitHub 判定 mergeable=MERGEABLE，mergeStateStatus=BLOCKED；7 个实际 CI check 已触发但尚未完成。 |

## 自动意见

第 1 轮查询未发现 review、普通 comment 或 inline review thread。

## ACI/CI

| Job/指标 | 状态 | 证据 | 根因 | 修复/提交 | 验证 |
|---|---|---|---|---|---|
| BCS e2e (coverage gated) | QUEUED | GitHub Actions run 32565831721 | 尚未开始 | 无 | 等待远端结果 |
| BCS unit tests | QUEUED | GitHub Actions run 32565831709 | 尚未开始 | 无 | 等待远端结果 |
| Singlebox coverage | IN_PROGRESS | GitHub Actions run 32565831716 | 正在执行 | 无 | 等待远端结果 |
| Backend / Engine / BaaS / Gateway unit tests | QUEUED | GitHub Actions run 32565831709 | 尚未开始 | 无 | 等待远端结果 |

## 人工意见

第 1 轮查询未发现人工 review、普通 comment 或 inline review thread。

## 当前结论

- PR: OPEN
- 自动意见: CLEAR
- ACI/CI: PENDING
- 人工意见: CLEAR
- 下一步: 等待实际远端 CI 结果；若出现失败或合理评审意见，定位后作最小修复。
