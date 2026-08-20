# PR 收敛报告：bcs-internal-bot-attributes

## 范围

- Worktree / repo: `bcs-internal-bot-attributes-dev/ocb-public` / `inclusionAI/Avernet`
- Head / base: `feat/bcs-internal-bot-attributes-dev-refactory` / `dev_refactory_collaboration`
- PR: https://github.com/inclusionAI/Avernet/pull/1283
- PR title: `feat(bcs): add provider bot attributes API`
- PR description sections: Problem / Solution / Validation / Compatibility and risk / Spec
- 人工意见模式: auto

## PR 判定

| 结果 | 证据 | 说明 |
|---|---|---|
| CLOSED | [#1277](https://github.com/inclusionAI/Avernet/pull/1277) | 原 PR 的 head/base 为 `feat/bcs-internal-bot-attributes-dev` / `dev`，已于 2026-08-20 关闭，不能承接本次提交。 |
| OPEN | [#1283](https://github.com/inclusionAI/Avernet/pull/1283) | 新分支相对 `dev_refactory_collaboration` 仅含本功能提交；当前 `mergeable=MERGEABLE`，但远端检查尚在运行。 |

## 自动意见

| 轮次 | 来源 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 1 | 无 | N/A | CLEAR | 新 PR 创建后未发现机器人 review、inline comment 或普通 comment。 | N/A | 初始 head 已复查。 |

## ACI/CI

| Job/指标 | 状态 | 证据 | 根因 | 修复/提交 | 验证 |
|---|---|---|---|---|---|
| Provider 属性 HTTP 路由 | PASS（本地） | `cargo test -p bcs-http --test provider_routes_contract provider_bot_attributes -- --nocapture` | N/A | 迁移后分支 | 2 passed |
| 既有 API 路由隔离 | PASS（本地） | `cargo test -p bcs-api-http --test bot_routes` | N/A | 迁移后分支 | 5 passed |
| OpenAPI V1 挂载 | PASS（本地） | `cargo test -p bcs --test openapi_v1_mount` | N/A | 迁移后分支 | 5 passed |
| Bot application | PASS（本地） | `cargo test -p bcs-app-bot` | N/A | 迁移后分支 | 20 passed |
| Bot store | PASS（本地） | `cargo test -p bcs-bot-store` | N/A | 迁移后分支 | 75 passed；5 个需要外部 Cache/DB 的既有集成测试忽略 |
| Git diff 校验 | PASS（本地） | `git diff --check` | N/A | 迁移后分支 | 无输出 |
| GitHub Actions | PENDING | [#1283 checks](https://github.com/inclusionAI/Avernet/pull/1283/checks) | BCS e2e、Singlebox coverage 与 BCS/Backend/Engine/BaaS/Gateway unit tests 已为当前 head 触发，尚未终态。 | N/A | 持续观察新 head |

## 人工意见

| 轮次 | 作者 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 原 PR | `vzvince` | [配置意见](https://github.com/inclusionAI/Avernet/pull/1277#discussion_r3819517890) | 采纳 | 按 reviewer 和用户确认复用 `allowed_switch_provider_ids`；同时校验 Bearer Token 对应 Provider、Provider 启用状态、allowlist 和 Bot 绑定归属。 | `79642e3c1` | Provider 属性合约测试覆盖 allowlist 失败关闭。 |
| 原 PR | `vzvince` | [模块位置意见](https://github.com/inclusionAI/Avernet/pull/1277#discussion_r3819522467) | 采纳 | 接口位于 `bcs-http` 的 Provider 路由组，路径为 `GET/PATCH /providers/{provider_id}/bots/{bot_uuid}/attributes`；路径参数提供 Provider ID，不再需要 `X-BCN-Provider-Id`。 | `79642e3c1` | 路由挂载与属性读写合约测试通过。 |

## 当前结论

- PR: OPEN（[#1283](https://github.com/inclusionAI/Avernet/pull/1283)）
- 自动意见: CLEAR
- ACI/CI: 本地 PASS；远端 PENDING
- 人工意见: 原 PR 两条合理意见均已迁移并采纳；新 PR 创建后重新检查。
- 下一步: 等待并检查当前 head 的远端门禁；若有确定性测试失败，最小修复后重新验证并推送。
