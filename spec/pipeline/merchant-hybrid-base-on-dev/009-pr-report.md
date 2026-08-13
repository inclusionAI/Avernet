# PR 收敛报告：merchant-hybrid-base-on-dev

## 范围

- Worktree / repo: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/mixed-openclaw-claude-bots-dev` / `git@github.com:inclusionAI/Avernet.git`
- Head / base: `hybrid_base_on_dev` / `dev`
- PR: NOT_CREATED
- PR title: `feat(singlebox): add merchant hybrid runtime`
- PR description sections: Problem / Solution / Validation / Compatibility and risk / Spec
- 人工意见模式: auto
- 任务边界: 提交 opt-in `merchant_hybrid` 的 3 OpenClaw + 1 Claude Code 最小运行链路；不提交 `output/merchant-hybrid/**` 运行证据或 relay `.tshy/**` 构建缓存。

## PR 判定

| 结果 | 证据 | 说明 |
|---|---|---|
| NEW_PR_REQUIRED | `gh pr list --head hybrid_base_on_dev --base dev --state all` 返回空列表 | 同 repo/head/base 不存在历史 PR；提交并同步最新 `origin/dev` 后创建 |
| BASE_UPDATED | `origin/dev=d36cb3951`，当前 merge base 为 `91b641d42` | 远端 `dev` 新增 3 个提交；本地聚焦提交后 rebase 到最新 base |

## 自动意见

| 轮次 | 来源 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 0 | 尚未创建 PR | - | PENDING | 创建 PR 后收集 | - | - |

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
| 远端 checks | PENDING | - | PR 尚未创建 | - | - |

## 人工意见

| 轮次 | 作者 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 0 | 尚未创建 PR | - | PENDING | 创建 PR 后收集 | - | - |

## 当前结论

- PR: NOT_CREATED
- 自动意见: CLEAR
- ACI/CI: PENDING
- 人工意见: CLEAR
- 下一步: 仅暂存任务 allowlist，创建 commit 并 rebase 到 `origin/dev=d36cb3951`。
