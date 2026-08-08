# PR 收敛报告：rel20260806-rebase-on-dev

## 范围

- Worktree / repo: `/Users/helloworld/Desktop/codes/teamclaw/Avernet` / `inclusionAI/Avernet`
- Head / base: `rebase/REL20260806-rebase-on-dev@74fb4c4e` / `origin/dev@71f7c141`
- PR: created this run
- PR title: `feat(backend): integrate REL20260806 bot and Skills updates`
- PR description sections: Problem / Solution / Validation / Compatibility and risk
- 人工意见模式: auto

## PR 判定

| 结果 | 证据 | 说明 |
|---|---|---|
| READY_TO_CREATE | head 领先 `origin/dev` 70 个提交，工作区干净，且没有同 head/base 的既有 PR | 变更涉及 Local Skills、Gateway Principal、Service Bot、BaaS、Engine、BCS 和 Gateway；创建后由远端评审与门禁收敛。 |

## 自动意见

| 轮次 | 来源 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 0 | 无 | — | 待 PR 创建后查询 | 尚未有可关联 PR | — | — |

## ACI/CI

| Job/指标 | 状态 | 证据 | 根因 | 修复/提交 | 验证 |
|---|---|---|---|---|---|
| local focused regression | PASS | Principal/Skills/Local Skills 聚焦套件 115 passed；原始 ZIP 上传 endpoint 2 passed | — | — | `git diff --check origin/dev...HEAD` 与冲突标记扫描通过 |
| pre-push SAST | PASS | 本分支推送时的仓库 pre-push gate | — | — | Backend、BaaS、Engine 的本地 SAST/lint gate 通过 |
| remote ACI/CI | PENDING | PR 尚未创建 | 无远端检查可读取 | — | 创建 PR 后查询 |

## 人工意见

| 轮次 | 作者 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 0 | 无 | — | 待 PR 创建后查询 | 尚未有可关联 PR | — | — |

## 当前结论

- PR: NOT_CREATED
- 自动意见: PENDING
- ACI/CI: PENDING
- 人工意见: PENDING
- 下一步: 创建以 `dev` 为 base、当前 rebase 分支为 head 的 PR，并核验 metadata。
