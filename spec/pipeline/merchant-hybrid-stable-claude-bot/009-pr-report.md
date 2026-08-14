# PR 收敛报告：merchant-hybrid-stable-claude-bot

## 范围

- Worktree / repo: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/hybrid-singlebox-debug-dev-20260814` / `github.com/inclusionAI/Avernet`
- Head / base: `yunhai_mix_yunhai_dev` / `dev`
- PR: https://github.com/inclusionAI/Avernet/pull/1064
- PR title: `fix(singlebox): stabilize hybrid Claude bot startup`
- PR description sections: Problem / Solution / Validation / Compatibility and risk / Spec
- 人工意见模式: auto

本次范围包括 hybrid 单箱的 Claude Code CLI 预检、manual 配置透传、固定的 Claude Provider Bot 身份和启动生命周期；不包含运行时数据库、日志、token 或本机 `.env.local`。

## PR 判定

| 结果 | 证据 | 说明 |
|---|---|---|
| OPEN | https://github.com/inclusionAI/Avernet/pull/1064 | Head `02ba9c284`，base 为 `dev`；标题与 Problem / Solution / Validation / Compatibility and risk / Spec 段落均已核验。 |

## 自动意见

| 轮次 | 来源 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 1 | 无 | — | CLEAR | 当前没有机器人 review 或普通评论 | — | `gh pr view 1064` 查询。 |

## ACI/CI

| Job/指标 | 状态 | 证据 | 根因 | 修复/提交 | 验证 |
|---|---|---|---|---|---|
| 本地受影响测试 | PASS | shell/Rust 命令均以零退出 | — | `4a3614087`、`4b971edba` | `bash scripts/test_hybrid.sh`、`bash scripts/test_singlebox_toolchain.sh`、`bash scripts/test_singlebox_bcs_runtime_config.sh`、`bash scripts/test_singlebox_model_config.sh`、`cargo test -p bcs --lib trusted_provider_bot_id_overrides`、`cargo test -p bcs-http --test provider_routes_contract`（35 passed）、`cargo test -p bcs-bot --lib`（15 passed）与 `git diff --cached --check` 通过。 |
| BCS e2e (coverage gated) | PENDING | https://github.com/inclusionAI/Avernet/actions/runs/31814107054/job/94811518264 | 远端运行中 | — | 尚无完成结果。 |
| Singlebox coverage | PENDING | https://github.com/inclusionAI/Avernet/actions/runs/31814107029/job/94811518284 | 远端运行中 | — | 尚无完成结果。 |
| BCS unit tests | PENDING | https://github.com/inclusionAI/Avernet/actions/runs/31814107074/job/94811518343 | 远端运行中 | — | 尚无完成结果。 |
| 其余 Unit Tests | PENDING | https://github.com/inclusionAI/Avernet/actions/runs/31814107074 | 远端运行中 | — | Backend、Engine、BaaS、Gateway job 均无完成结果。 |

## 人工意见

| 轮次 | 作者 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 1 | 无 | — | CLEAR | 当前没有非机器人 review 或普通评论 | — | `gh pr view 1064` 查询。 |

## 当前结论

- PR: OPEN
- 自动意见: CLEAR
- ACI/CI: PENDING
- 人工意见: CLEAR
- 下一步: 推送本报告更新后，等待并检查最终 head 对应的远端门禁。
