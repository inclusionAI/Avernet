# PR 收敛报告：merchant-hybrid-stable-claude-bot

## 范围

- Worktree / repo: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/hybrid-singlebox-debug-dev-20260814` / `github.com/inclusionAI/Avernet`
- Head / base: `yunhai_mix_yunhai_dev` / `dev`
- PR: pending publication
- PR title: `fix(singlebox): stabilize hybrid Claude bot startup`
- PR description sections: Problem / Solution / Validation / Compatibility and risk / Spec
- 人工意见模式: auto

本次范围包括 hybrid 单箱的 Claude Code CLI 预检、manual 配置透传、固定的 Claude Provider Bot 身份和启动生命周期；不包含运行时数据库、日志、token 或本机 `.env.local`。

## PR 判定

| 结果 | 证据 | 说明 |
|---|---|---|
| READY_TO_PUSH | `4a3614087` + `4b971edba` | 功能提交后已合并 `origin/dev@0f49ed27b`；冲突解析保留了上游的 Anthropic 配置、运行时状态和 Provider 凭据复用，以及本次稳定身份和 GLM 策略。 |

## 自动意见

| 轮次 | 来源 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 0 | — | — | 待创建 PR 后检查 | 当前无匹配 PR | — | — |

## ACI/CI

| Job/指标 | 状态 | 证据 | 根因 | 修复/提交 | 验证 |
|---|---|---|---|---|---|
| 本地受影响测试 | PASS | shell/Rust 命令均以零退出 | — | `4a3614087`、`4b971edba` | `bash scripts/test_hybrid.sh`、`bash scripts/test_singlebox_toolchain.sh`、`bash scripts/test_singlebox_bcs_runtime_config.sh`、`bash scripts/test_singlebox_model_config.sh`、`cargo test -p bcs --lib trusted_provider_bot_id_overrides`、`cargo test -p bcs-http --test provider_routes_contract`（35 passed）、`cargo test -p bcs-bot --lib`（15 passed）与 `git diff --cached --check` 通过。 |
| 远端 checks | NOT_STARTED | 尚未创建 PR | — | — | 创建并推送最终 head 后检查。 |

## 人工意见

| 轮次 | 作者 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 0 | — | — | 待创建 PR 后检查 | 当前无匹配 PR | — | — |

## 当前结论

- PR: NOT_CREATED
- 自动意见: CLEAR
- ACI/CI: NOT_STARTED
- 人工意见: CLEAR
- 下一步: 发布 `yunhai_mix_yunhai_dev` 并创建指向 `dev` 的 PR。
