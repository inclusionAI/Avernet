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
| OPEN | [#1283](https://github.com/inclusionAI/Avernet/pull/1283) | 新分支相对 `dev_refactory_collaboration` 仅含本功能提交；修复前 head `8f6e83c92b` 的全部 7 个远端检查已成功，本轮修复待推送后重新验证。 |

## 自动意见

| 轮次 | 来源 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 1 | `chatgpt-codex-connector` | [P1：禁用 Provider](https://github.com/inclusionAI/Avernet/pull/1283#discussion_r3820336130) | 采纳 | 原属性路由仅校验 Token 与路径 Provider 的对应关系，禁用后的 Provider 仍可访问。复用 Provider application service 的 active-provider 校验，并将禁用拒绝统一为 403。 | `9bf13a695` | 新回归先验证修复前 GET=200；修复后 GET/PATCH 均为 403。 |

## ACI/CI

| Job/指标 | 状态 | 证据 | 根因 | 修复/提交 | 验证 |
|---|---|---|---|---|---|
| Provider 属性 HTTP 路由 | PASS（本地） | `cargo test -p bcs-http --test provider_routes_contract provider_bot_attributes -- --nocapture` | 禁用 Provider 可使用旧 Admin Token 访问属性接口 | `9bf13a695` | 3 passed；新增 GET/PATCH 禁用 Provider=403。 |
| 既有 API 路由隔离 | PASS（本地） | `cargo test -p bcs-api-http --test bot_routes` | N/A | 迁移后分支 | 5 passed |
| OpenAPI V1 挂载 | PASS（本地） | `cargo test -p bcs --test openapi_v1_mount` | N/A | 迁移后分支 | 5 passed |
| Provider application | PASS（本地） | `cargo test -p bcs-bot --lib` | N/A | `9bf13a695` | 15 passed |
| Bot application | PASS（本地） | `cargo test -p bcs-app-bot` | N/A | 迁移后分支 | 20 passed |
| Bot store | PASS（本地） | `cargo test -p bcs-bot-store` | N/A | 迁移后分支 | 75 passed；5 个需要外部 Cache/DB 的既有集成测试忽略 |
| Git diff 校验 | PASS（本地） | `git diff --check` | N/A | 迁移后分支 | 无输出 |
| GitHub Actions（修复前 head） | PASS | [#1283 checks](https://github.com/inclusionAI/Avernet/pull/1283/checks) | N/A | `8f6e83c92b` | BCS e2e、Singlebox coverage、BCS/Backend/Engine/BaaS/Gateway unit tests 共 7 项成功。 |
| BCS e2e / Singlebox coverage（初始 head） | FAIL | [E2E job](https://github.com/inclusionAI/Avernet/actions/runs/32351175302/job/96370558981) / [Singlebox job](https://github.com/inclusionAI/Avernet/actions/runs/32351175270/job/96370533081) | 568 个 E2E 场景、行/方法覆盖均通过；新 GET/PATCH 属性路由未被命中，endpoint coverage 为 137/139，低于 100%。 | 在现有 Provider E2E 场景增加两个 fail-closed 403 请求。 | 本地完整 `e2e_coverage.sh` 运行中；Bash 语法通过。 |
| BCS unit coverage（初始 head） | FAIL | [Unit job](https://github.com/inclusionAI/Avernet/actions/runs/32351175353/job/96370526612) | 3,483 个测试均通过、总行覆盖 80.27%，但 changed-line coverage 为 76.16%，低于 80%。 | 覆盖属性 application error 到 HTTP 状态的全部映射分支。 | 新单元测试及 Provider 属性合约测试通过。 |

## 人工意见

| 轮次 | 作者 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 原 PR | `vzvince` | [配置意见](https://github.com/inclusionAI/Avernet/pull/1277#discussion_r3819517890) | 采纳 | 按 reviewer 和用户确认复用 `allowed_switch_provider_ids`；同时校验 Bearer Token 对应 Provider、Provider 启用状态、allowlist 和 Bot 绑定归属。 | `79642e3c1` | Provider 属性合约测试覆盖 allowlist 失败关闭。 |
| 原 PR | `vzvince` | [模块位置意见](https://github.com/inclusionAI/Avernet/pull/1277#discussion_r3819522467) | 采纳 | 接口位于 `bcs-http` 的 Provider 路由组，路径为 `GET/PATCH /providers/{provider_id}/bots/{bot_uuid}/attributes`；路径参数提供 Provider ID，不再需要 `X-BCN-Provider-Id`。 | `79642e3c1` | 路由挂载与属性读写合约测试通过。 |

## 当前结论

- PR: OPEN（[#1283](https://github.com/inclusionAI/Avernet/pull/1283)）
- 自动意见: 已采纳本轮 P1；等待推送后再次收集。
- ACI/CI: 修复前 head 的 7 项远端检查均为 PASS；`9bf13a695` 已完成本地定向验证，推送后远端状态为 PENDING。
- 人工意见: CLEAR；原 PR 两条合理意见均已迁移并采纳。
- 下一步: 提交本报告并推送，重新收集自动意见与远端门禁结果。
