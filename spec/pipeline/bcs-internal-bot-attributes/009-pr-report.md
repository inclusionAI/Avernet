# PR 收敛报告：bcs-internal-bot-attributes

## 范围

- Worktree / repo: `bcs-internal-bot-attributes-dev/ocb-public` / `inclusionAI/Avernet`
- Head / base: `feat/bcs-internal-bot-attributes-dev` / `dev`
- PR: https://github.com/inclusionAI/Avernet/pull/1277
- PR title: `feat(bcs): add internal bot attributes API`
- PR description sections: Problem / Solution / Validation / Compatibility and risk / Spec
- 人工意见模式: auto

## PR 判定

| 结果 | 证据 | 说明 |
|---|---|---|
| OPEN | [#1277](https://github.com/inclusionAI/Avernet/pull/1277) | `feat/bcs-internal-bot-attributes-dev` 指向 `dev`；标题和说明包含 Problem、Solution、Validation、Compatibility and risk、Spec。 |

## 自动意见

| 轮次 | 来源 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 1 | 无 | N/A | PENDING | 初次查询没有机器人 review、inline comment 或普通 comment。 | N/A | 等待检查稳定后复查。 |

## ACI/CI

| Job/指标 | 状态 | 证据 | 根因 | 修复/提交 | 验证 |
|---|---|---|---|---|---|
| BCS workspace test | PASS（本地） | `cargo test --workspace -q` | N/A | `efce88cf9` | exit 0 |
| 私有属性 HTTP 路由 | PASS（本地） | route、公开路由隔离和 bootstrap fail-closed 定向测试 | N/A | `efce88cf9` | exit 0 |
| Git diff 校验 | PASS（本地） | `git diff --check` | N/A | 本分支 | 无输出 |
| GitHub Actions | PENDING | [#1277 checks](https://github.com/inclusionAI/Avernet/pull/1277/checks) | BCS e2e、Singlebox coverage、BCS unit tests 等首轮 job 正在运行或排队。 | N/A | 等待当前 head 终态 |

## 人工意见

| 轮次 | 作者 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 1 | 无 | N/A | PENDING | 初次查询没有人工评论；GitHub 当前要求 review。 | N/A | 等待 CI 稳定后复查。 |

## 当前结论

- PR: OPEN（[#1277](https://github.com/inclusionAI/Avernet/pull/1277)）
- 自动意见: PENDING
- ACI/CI: 本地 PASS，GitHub Actions PENDING
- 人工意见: PENDING
- 下一步: 等待当前 head 的 GitHub Actions 终态，并重新检查机器人与人工意见。
