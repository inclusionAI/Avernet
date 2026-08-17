---
agent: tc-main-agent
status: pass
created: 2026-08-14
---

# Code review（主流程复核）

## 结论

PASS。

## 检查项

- manual 模式需要的三项 `OPENCLAW_OPENAI_*` 已在既有准备阶段校验；relay 再次 fail-closed，避免以不完整 provider 配置启动。
- 映射只在 manual 模式生效；home、mock 和 OpenClaw-only hybrid 保持原路径。
- settings provider source 在 manual 模式置空，避免 gateway 侧重新加载而覆盖显式映射。
- API key 不进入命令行参数、日志或仓库文件，只进入 relay 子进程环境。
- Bash 数组通过 `env` 的逐元素参数传递，能够保留 URL、token 中的特殊字符。

## 残余风险

真实服务启动需要用户在 worktree 的 `.env.local` 或调用 shell 中提供完整 manual 配置；该文件当前不存在，因此未进行外部模型连通性验证。
