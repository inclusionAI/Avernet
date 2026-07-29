# PR 收敛报告：session-resource-upstream-errors

## 范围

- Worktree / repo: `fix-session-resource-upstream-errors` / `inclusionAI/Avernet`
- Head / base: `rebase/session-resource-upstream-errors-on-REL20260730` / `REL20260730` (`ebc04b0b`)
- PR: 未创建
- 人工意见模式: auto

## PR 判定

| 结果 | 证据 | 说明 |
|---|---|---|
| 待创建 | 本报告创建时 | 当前分支只包含本次 Backend 上游错误归一化提交，已以 `REL20260730` 为底 rebase。 |

## 自动意见

| 轮次 | 来源 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 0 | 未查询 | N/A | PENDING | PR 尚未创建。 | N/A | 创建 PR 后查询。 |

## ACI/CI

| Job/指标 | 状态 | 证据 | 根因 | 修复/提交 | 验证 |
|---|---|---|---|---|---|
| Session Resource BaaS client 与 service | PASS（本地） | 13 passed | N/A | `736ceecb` | focused pytest |
| Session Resource endpoints | PASS（本地） | 19 passed | N/A | `736ceecb` | endpoint runner |
| Ruff 与 diff check | PASS（本地） | `ruff check`、`git diff --check` | N/A | `736ceecb` | 本地执行 |
| 远端 CI | PENDING | PR 尚未创建 | N/A | N/A | 推送后查询。 |

## 人工意见

| 轮次 | 作者 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 0 | 未查询 | N/A | PENDING | PR 尚未创建。 | N/A | 创建 PR 后查询。 |

## 当前结论

- PR: NOT_CREATED
- 自动意见: PENDING
- ACI/CI: PENDING
- 人工意见: PENDING
- 下一步: 推送分支并创建以 `REL20260730` 为目标的 PR。
