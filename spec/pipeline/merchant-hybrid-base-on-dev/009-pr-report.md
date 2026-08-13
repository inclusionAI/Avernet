# PR 收敛报告：merchant-hybrid-base-on-dev

## 范围

- Worktree / repo: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/mixed-openclaw-claude-bots-dev` / `git@github.com:inclusionAI/Avernet.git`
- Head / base: `hybrid_base_on_dev` / `dev`
- PR: https://github.com/inclusionAI/Avernet/pull/1009
- PR title: `feat(singlebox): add merchant hybrid runtime`
- PR description sections: Problem / Solution / Validation / Compatibility and risk / Spec
- 人工意见模式: auto
- 任务边界: 提交 opt-in `merchant_hybrid` 的 3 OpenClaw + 1 Claude Code 最小运行链路；不提交 `output/merchant-hybrid/**` 运行证据或 relay `.tshy/**` 构建缓存。

## PR 判定

| 结果 | 证据 | 说明 |
|---|---|---|
| CREATED | https://github.com/inclusionAI/Avernet/pull/1009 | repo/head/base 已核验为 `inclusionAI/Avernet` / `hybrid_base_on_dev` / `dev`；非 Draft |
| METADATA_VALID | PR #1009 | 最终标题符合语义格式；说明包含 Problem / Solution / Validation / Compatibility and risk / Spec |
| REBASED | `origin/dev=d36cb3951`；feature commit `010f55461` | 聚焦提交无冲突 rebase 到创建 PR 时的最新 base，并完成 rebase 后验证 |

## 自动意见

| 轮次 | 来源 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 1 | 无自动意见 | https://github.com/inclusionAI/Avernet/pull/1009 | CLEAR | 初次查询 comments/reviews 均为空 | - | - |

## ACI/CI

| Job/指标 | 状态 | 证据 | 根因 | 修复/提交 | 验证 |
|---|---|---|---|---|---|
| BaaS 定向测试 | PASS | `pytest` 目标 3 个测试文件 | - | - | 143 passed |
| Claude relay 全量单测 | PASS | `npm run test-local` | - | - | 321 passing |
| Claude relay 构建 | PASS | `npm run prepublishOnly` | - | - | `tshy` / `tshy-after` 成功 |
| Python 静态检查 | PASS | `ruff check` + `py_compile` | - | - | 全部通过 |
| Shell / Node 语法 | PASS | `bash -n` + `node --check` | - | - | 全部通过 |
| merchant_hybrid Shell 回归 | PASS | `bash scripts/test_merchant_hybrid.sh` | - | - | profile、模型、顺序与回滚通过 |
| Provider bridge 回归 | PASS | `node scripts/test_bcs_baas_provider_bridge.mjs` | - | - | h2c/HTTP1、鉴权与日志脱敏通过 |
| singlebox service guards | PASS | `bash scripts/test_singlebox_service_guards.sh` | - | - | guard 与失败回滚通过 |
| BaaS 覆盖率缺口定向测试 | PASS | `pytest` 目标 2 个测试文件 + coverage JSON | - | 本次覆盖率测试提交 | 66 passed；CI 指出的 10 条未覆盖新增行全部 executed |
| BaaS 本地完整 CI | BLOCKED | `DEPLOY_ENV=dev bash scripts/ci_test.sh --base origin/dev` | E2E 启动阶段本地 8888 端口冲突，完整流水线未得到有效结论 | 停止本地全量运行，交由隔离的 GitHub runner 复验 | 定向测试与 ruff 均通过 |
| Claude relay ESLint | BLOCKED | `npm run lint` | 当前 package 与父目录缺少 ESLint 配置，ESLint 在规则加载前退出 | 未修改业务代码规避 | 单测、TypeScript 构建正常 |
| BCS e2e coverage | PASS | https://github.com/inclusionAI/Avernet/actions/runs/31681089545/job/94386515164 | - | - | GitHub check SUCCESS |
| BCS / Backend / Engine / Gateway unit tests | PASS | https://github.com/inclusionAI/Avernet/actions/runs/31681089519 | - | - | 4 个 GitHub checks 均 SUCCESS |
| BaaS unit tests（修复前 head） | FAIL | https://github.com/inclusionAI/Avernet/actions/runs/31681089519/job/94386518361 | 用例通过率 100%、总行覆盖率 92.94%，但 changed-line coverage 为 32/42（76.19%），低于 90% | 新增 SecretStore/本地 token 回退与 loopback 直连行为测试 | 本地确认原 10 条缺口全部 executed；等待新 head GitHub check |
| Singlebox coverage | PENDING | https://github.com/inclusionAI/Avernet/actions/runs/31681089517/job/94386516417 | 修复前 head 仍在运行 | - | 等待最终状态 |

## 人工意见

| 轮次 | 作者 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 1 | 无人工意见 | https://github.com/inclusionAI/Avernet/pull/1009 | CLEAR | 初次查询 comments/reviews 均为空 | - | - |

## 当前结论

- PR: OPEN
- 自动意见: CLEAR
- ACI/CI: PENDING
- 人工意见: CLEAR
- 下一步: 推送 BaaS changed-line coverage 测试，随后检查最终 head 的自动意见、人工意见与全部远端 checks。
