---
agent: tc-main-agent
status: pass
created: 2026-08-14
---

# Focused regression report

## 通过

- `bash -n scripts/modules/claude_relays.sh scripts/test_hybrid.sh`
- `bash scripts/test_hybrid.sh`
- `bash scripts/test_singlebox_model_config.sh`
- `git diff --check`

## 启动前置校验

在不含 `.env.local` 的全新 worktree 中，以 manual 模式执行含 Claude profile 的 `start hybrid` 预检，命令在“缺少 required model config”阶段退出，未启动服务、未占用端口。

## 未执行

- 未运行完整 singlebox 服务栈：本 worktree 没有用户的 manual endpoint/key/model 配置，不能安全完成真实外部模型调用。
- `shellcheck` 未安装，已由 Bash 语法检查替代。
