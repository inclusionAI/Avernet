# PR 收敛报告：session-resource-upstream-errors

## 范围

- Worktree / repo: `fix-session-resource-upstream-errors` / `inclusionAI/Avernet`
- Head / base: `rebase/session-resource-upstream-errors-on-REL20260730` / `REL20260730` (`ebc04b0b`)
- PR: https://github.com/inclusionAI/Avernet/pull/593
- 人工意见模式: auto
- 范围: Session File Sharing 通用 Backend 上游错误归一化，以及 Engine 的受控 BaaS proxypass 物化/内容读取。

## PR 判定

| 结果 | 证据 | 说明 |
|---|---|---|
| 已创建 PR | [#593](https://github.com/inclusionAI/Avernet/pull/593) | head 为 `rebase/session-resource-upstream-errors-on-REL20260730`，base 为 `REL20260730`。 |

## 自动意见

| 轮次 | 来源 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 1 | 无 | N/A | CLEAR | GitHub review、issue comment 与未解决 inline thread 均为空。 | N/A | 2026-07-29 GitHub API |

## ACI/CI

| Job/指标 | 状态 | 证据 | 根因 | 修复/提交 | 验证 |
|---|---|---|---|---|---|
| Session Resource BaaS client 与 service | PASS（本地） | 13 passed | N/A | `736ceecb` | focused pytest |
| Session Resource endpoints | PASS（本地） | 19 passed | N/A | `736ceecb` | endpoint runner |
| Engine proxied materialization | PASS（本地） | 10 passed | N/A | `7251ca7d` | OCB 组合 namespace 环境下的 community router 与 Corp callback tests |
| Ruff 与 diff check | PASS（本地） | `ruff check`、`git diff --check` | N/A | `736ceecb` | 本地执行 |
| 远端 CI | PENDING | [PR checks](https://github.com/inclusionAI/Avernet/pull/593/checks) | 追加 Engine commit 后重新触发。 | `7251ca7d` | GitHub Actions |

## 人工意见

| 轮次 | 作者 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 1 | 无 | N/A | CLEAR | GitHub issue comment 与非机器人 review 均为空。 | N/A | 2026-07-29 GitHub API |

## 关联 OCB 交付

- Corp Engine callback、Corp Backend 流式代理需在 OCB `REL20260730` 上单独重放，并将 `ocb-public` gitlink 指向本 PR 的合并结果。
- 当前 OCB `ocb-public` 镜像拒绝任何以 `REL20260730` 为祖先的新分支：该既有发布历史包含不符合镜像邮箱策略的 `b4e362eb`，服务端 pre-receive hook 拒绝推送。不能用不可 fetch 的 gitlink 创建 OCB PR；需镜像管理员对既有 REL 历史放行后继续。

## 当前结论

- PR: OPEN
- 自动意见: CLEAR
- ACI/CI: PENDING
- 人工意见: CLEAR
- 下一步: 复核当前 head 的远端门禁结果；等待 `ocb-public` 镜像放行后创建关联 OCB PR。
