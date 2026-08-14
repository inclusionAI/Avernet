# PR 收敛报告：hybrid-singlebox-defaults

## 范围

- Worktree / repo: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/hybrid-singlebox-debug-dev-20260814` / `inclusionAI/Avernet`
- Head / base: `debug/hybrid-singlebox-dev-20260814` / `dev`
- PR: 创建前未发现同 head/base 的 PR
- PR title: `fix(singlebox): align merchant hybrid bot profiles`
- PR description sections: Problem / Solution / Validation / Compatibility and risk
- 人工意见模式: auto

## PR 判定

| 结果 | 证据 | 说明 |
|---|---|---|
| 仓库与目标分支已核验 | 当前 HEAD、`origin/dev` 与 merge base 均为 `81cb9696d` | 当前分支不是目标分支，且从 `dev` 基线创建。 |
| 任务范围已核验 | 三个脚本文件的未提交 diff | 仅包含 Provider 展示名与 `merchant_hybrid` 启动默认值及其 shell 回归。 |
| 匹配 PR | `gh pr list --head ... --base dev --state all` 返回空数组 | 可在推送后创建新 PR。 |

## 自动意见

| 轮次 | 来源 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 0 | 无 | - | 待 PR 创建 | 尚无可查询的自动评审意见。 | - | - |

## ACI/CI

| Job/指标 | 状态 | 证据 | 根因 | 修复/提交 | 验证 |
|---|---|---|---|---|---|
| 本地 hybrid shell 回归 | PASS | `bash scripts/test_hybrid.sh` | - | 待提交 | 通过。 |
| Shell 语法与差异检查 | PASS | `bash -n ...`、`git diff --check` | - | 待提交 | 通过。 |
| 远端 CI | PENDING | PR 尚未创建 | 尚无远端 job。 | - | PR 创建后查询。 |

## 人工意见

| 轮次 | 作者 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 0 | 无 | - | 待 PR 创建 | 尚无可查询的人工意见。 | - | - |

## 当前结论

- PR: NOT_CREATED
- 自动意见: CLEAR
- ACI/CI: PENDING
- 人工意见: CLEAR
- 下一步: 仅暂存任务文件，创建提交并推送 topic 分支。
