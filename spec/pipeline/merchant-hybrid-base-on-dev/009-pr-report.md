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
| Claude relay ESLint | BLOCKED | `npm run lint` | 当前 package 与父目录缺少 ESLint 配置，ESLint 在规则加载前退出 | 未修改业务代码规避 | 单测、TypeScript 构建正常 |
| Gateway unit tests | PASS | https://github.com/inclusionAI/Avernet/actions/runs/31681025528/job/94386258462 | - | - | GitHub check SUCCESS |
| BCS e2e / Singlebox coverage / BCS、Backend、Engine、BaaS unit tests | PENDING | PR #1009 checks | 正在运行 | - | 等待最终状态 |

## 人工意见

| 轮次 | 作者 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 1 | 无人工意见 | https://github.com/inclusionAI/Avernet/pull/1009 | CLEAR | 初次查询 comments/reviews 均为空 | - | - |

## 当前结论

- PR: OPEN
- 自动意见: CLEAR
- ACI/CI: PENDING
- 人工意见: CLEAR
- 下一步: 推送本报告更新，随后检查最终 head 的自动意见、人工意见与全部远端 checks。
