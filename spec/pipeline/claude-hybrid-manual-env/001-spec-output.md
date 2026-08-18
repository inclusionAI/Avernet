---
agent: tc-review
status: approved-by-request
created: 2026-08-14
---

# Claude hybrid manual model environment

## 需求

在基于 `origin/dev` 的 Avernet worktree 中，使用 `./scripts/singlebox.sh start hybrid` 启动已启用 Claude profile 的混合栈时，Claude Code 必须复用 manual 模式的 `.env.local` 模型配置。

## 实施范围

- 仅在 `SINGLEBOX_MODEL_CONFIG_MODE=manual` 时，将已经校验的 `OPENCLAW_OPENAI_BASE_URL`、`OPENCLAW_OPENAI_API_KEY` 与当前解析的模型传给 Claude relay，分别映射为 Claude Code 的 `ANTHROPIC_BASE_URL`、`ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_MODEL` 和 `ANTHROPIC_SMALL_FAST_MODEL`。
- manual 模式不再让本机或 role 的 `settings.json` 覆盖上述 relay 配置。
- 保持 home、mock 和非 Claude hybrid 的现有行为；不写入或记录任何凭据。

## 验收

- `scripts/test_hybrid.sh` 断言 manual 映射完整、可用，且非 manual 模式没有 relay 覆盖项。
- 执行 Bash 语法检查和相关 shell 回归；检查 diff 中不出现凭据或无关改动。
