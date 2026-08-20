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
| BCS e2e / Singlebox coverage（初始 head） | FAIL | [E2E job](https://github.com/inclusionAI/Avernet/actions/runs/32351175302/job/96370558981) / [Singlebox job](https://github.com/inclusionAI/Avernet/actions/runs/32351175270/job/96370533081) | 568 个 E2E 场景、行/方法覆盖均通过；新 GET/PATCH 属性路由未被命中，endpoint coverage 为 137/139，低于 100%。 | 在现有 Provider E2E 场景增加两个 fail-closed 403 请求。 | 本地完整 `e2e_coverage.sh` 运行中；Bash 语法通过。 |
| BCS unit coverage（初始 head） | FAIL | [Unit job](https://github.com/inclusionAI/Avernet/actions/runs/32351175353/job/96370526612) | 3,483 个测试均通过、总行覆盖 80.27%，但 changed-line coverage 为 76.16%，低于 80%。 | 覆盖属性 application error 到 HTTP 状态的全部映射分支。 | 新单元测试及 Provider 属性合约测试通过。 |

## 人工意见

| 轮次 | 作者 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 原 PR | `vzvince` | [配置意见](https://github.com/inclusionAI/Avernet/pull/1277#discussion_r3819517890) | 采纳 | 按 reviewer 和用户确认复用 `allowed_switch_provider_ids`；同时校验 Bearer Token 对应 Provider、Provider 启用状态、allowlist 和 Bot 绑定归属。 | `79642e3c1` | Provider 属性合约测试覆盖 allowlist 失败关闭。 |
| 原 PR | `vzvince` | [模块位置意见](https://github.com/inclusionAI/Avernet/pull/1277#discussion_r3819522467) | 采纳 | 接口位于 `bcs-http` 的 Provider 路由组，路径为 `GET/PATCH /providers/{provider_id}/bots/{bot_uuid}/attributes`；路径参数提供 Provider ID，不再需要 `X-BCN-Provider-Id`。 | `79642e3c1` | 路由挂载与属性读写合约测试通过。 |

## 当前结论

- PR: OPEN（[#1283](https://github.com/inclusionAI/Avernet/pull/1283)）
- 自动意见: CLEAR
- ACI/CI: 初始 head 的三项 BCS 门禁已定位并修复；新 head 的本地完整 E2E 运行中、远端 CI 待重新触发。
- 人工意见: 原 PR 两条合理意见均已迁移并采纳；新 PR 创建后重新检查。
- 下一步: 推送覆盖修复，等待本地完整 E2E 及新 head 的远端门禁；若仍有确定性失败，最小修复后重试。
