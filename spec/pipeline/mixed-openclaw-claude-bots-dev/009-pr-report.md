# PR 收敛报告：mixed-openclaw-claude-bots-dev

## 范围

- Worktree / repo: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/mixed-openclaw-claude-bots-dev` / `inclusionAI/Avernet`
- Head / base: `rebase/mixed-openclaw-claude-bots-on-dev` (`2b2f46d7`) / `dev` (`009c527b`)
- PR: [#943](https://github.com/inclusionAI/Avernet/pull/943)
- PR title: `feat(singlebox): enable mixed Claude Code bot collaboration`
- PR description sections: Problem / Solution / Validation / Compatibility and risk / Spec
- 人工意见模式: auto

## PR 判定

| 结果 | 证据 | 说明 |
|---|---|---|
| OPEN | PR #943 | Head/base 与当前任务一致；标题和必填说明段落已核验。 |
| BLOCKED | GitHub merge state，2026-08-10 | 当前需要评审且远端检查仍在执行，不表示代码失败。 |

## 自动意见

| 轮次 | 来源 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 1 | 无 | PR #943 | CLEAR | 创建后没有 bot review、普通 comment 或 inline review comment。 | 无 | GitHub PR 查询。 |

## ACI/CI

| Job/指标 | 状态 | 证据 | 根因 | 修复/提交 | 验证 |
|---|---|---|---|---|---|
| BCS e2e (coverage gated) | PENDING | [GitHub job](https://github.com/inclusionAI/Avernet/actions/runs/31374816631/job/93411576448) | 新建 PR 的首轮执行中。 | 无 | 等待远端结论。 |
| Singlebox coverage | PENDING | [GitHub job](https://github.com/inclusionAI/Avernet/actions/runs/31374816592/job/93411576272) | 新建 PR 的首轮执行中。 | 无 | 等待远端结论。 |
| BCS / Backend / Engine / BaaS / Gateway unit tests | PENDING | [GitHub workflow](https://github.com/inclusionAI/Avernet/actions/runs/31374816560) | 新建 PR 的首轮执行中。 | 无 | 等待远端结论。 |

本地已执行：Backend provisioning 定向测试 `26 passed`；BCS system-message
`43` 单测与 `7` conformance 通过；BCS provider-http `22` 单测与 `18`
transport-contract 通过；BCS→BaaS bridge Node 合约通过；Claude gateway `323`
通过；Git diff 和预推送 lint/SAST 通过。预推送只执行 lint，不替代远端
coverage/E2E。

## 人工意见

| 轮次 | 作者 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 1 | 无 | PR #943 | CLEAR | 创建后未发现人工 review、普通 comment 或 inline comment。 | 无 | GitHub PR 查询。 |

## 当前结论

- PR: OPEN
- 自动意见: CLEAR
- ACI/CI: PENDING
- 人工意见: CLEAR
- 下一步: 等待并核验当前 head 的远端检查；若有合理自动或人工意见，按最小修复流程处理。

## 2026-08-10 范围校正

- 不扩展 BCS Provider webhook 协议：移除 `abort_run_id` 传递和对应
  序列化测试；bridge 不会将新建 abort request 的 `id` 猜作活动 send run。
  在协议携带可信目标 run ID 前，本地 `chat.abort` 显式返回 unsupported，
  而浏览器/SSE 连接关闭仍只清理它自身的上游 fetch。
- 不为 Provider-downlink Bot 改写普通 Chat 的初始化语义：Driver 保持
  `chat.send`，其他参与者保持 `chat.inject`。这保留用户期待的可见 send
  结果以及现有 BCS 默认行为。
- BaaS SQLite bootstrap 使用 SPI `Base.metadata` 的 `baas_api_key` 表插入
  本地 Provider 内部上下文身份，不从 database plugin 层导入 Core 私有 ORM
  模块；这保留相同的 seed 结果并满足分层与私有导入门禁。受影响的 BaaS
  router、分层、私有导入和数据库插件用例 `13 passed`，且四个本分支文件的
  Ruff check/format 均通过。
