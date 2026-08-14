# PR 收敛报告：hybrid-singlebox-defaults

## 范围

- Worktree / repo: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/hybrid-singlebox-debug-dev-20260814` / `inclusionAI/Avernet`
- Head / base: `debug/hybrid-singlebox-dev-20260814` / `dev`
- PR: https://github.com/inclusionAI/Avernet/pull/1045
- PR title: `fix(singlebox): align merchant hybrid bot profiles`
- PR description sections: Problem / Solution / Validation / Compatibility and risk
- 人工意见模式: auto

## PR 判定

| 结果 | 证据 | 说明 |
|---|---|---|
| 仓库与目标分支已核验 | 当前 HEAD、`origin/dev` 与 merge base 均为 `81cb9696d` | 当前分支不是目标分支，且从 `dev` 基线创建。 |
| 任务范围已核验 | 三个脚本文件的未提交 diff | 仅包含 Provider 展示名与 `merchant_hybrid` 启动默认值及其 shell 回归。 |
| 匹配 PR | `gh pr list --head ... --base dev --state all` 返回空数组 | 推送后已创建新的 PR。 |
| PR 元数据 | PR #1045 的 title、body、head、base | 标题与四个必填说明段落均与已核验 diff 一致。 |
| 代码变更提交 | `db6c05972` | 已推送的首个聚焦提交。 |
| 人工 review | 两个 `User` 类型 APPROVED review | 均未提出需要处理的修改意见。 |

## 自动意见

| 轮次 | 来源 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 1 | 无 | - | CLEAR | reviews、issue comments 与 inline comments 均为空。 | - | - |

## ACI/CI

| Job/指标 | 状态 | 证据 | 根因 | 修复/提交 | 验证 |
|---|---|---|---|---|---|
| 本地 hybrid shell 回归 | PASS | `bash scripts/test_hybrid.sh` | - | 待提交 | 通过。 |
| Shell 语法与差异检查 | PASS | `bash -n ...`、`git diff --check` | - | 待提交 | 通过。 |
| BCS unit tests | PASS | GitHub Actions `BCS unit tests` | - | `db6c05972` | 已完成且成功。 |
| Backend unit tests | PASS | GitHub Actions `Backend unit tests` | - | `db6c05972` | 已完成且成功。 |
| Engine unit tests | PASS | GitHub Actions `Engine unit tests` | - | `db6c05972` | 已完成且成功。 |
| BaaS unit tests | PASS | GitHub Actions `BaaS unit tests` | - | `db6c05972` | 已完成且成功。 |
| Gateway unit tests | PASS | GitHub Actions `Gateway unit tests` | - | `db6c05972` | 已完成且成功。 |
| BCS e2e (coverage gated) | PASS | GitHub Actions `BCS e2e (coverage gated)` | - | `db6c05972` | 已完成且成功。 |
| Singlebox coverage | PENDING | GitHub Actions `Singlebox coverage` | 正在执行 `Run singlebox coverage`。 | - | 等待终态。 |

## 人工意见

| 轮次 | 作者 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 1 | cassiuscai | https://github.com/inclusionAI/Avernet/pull/1045#pullrequestreview-4933875774 | APPROVED | 空正文批准，无修改项。 | - | - |
| 1 | totalfrank | https://github.com/inclusionAI/Avernet/pull/1045#pullrequestreview-4933895725 | APPROVED | `LGTM`，无修改项。 | - | - |

## 当前结论

- PR: OPEN
- 自动意见: CLEAR
- ACI/CI: PENDING
- 人工意见: CLEAR
- 下一步: 等待当前 head 的远端检查完成，并在有评审意见时按自动模式处理。
