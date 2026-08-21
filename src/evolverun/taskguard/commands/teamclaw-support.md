---
description: "TeamClaw 客服支持 — 处理 TeamClaw 用户问题，包括知识库搜索、日志分析、缺陷创建等"
disable-model-invocation: false
---

<!-- @clawmind:generated-facade-command -->

The user invoked the `/kf` facade command from the `teamclaw-kf` pack.

Call the `mcp__clawmind__workflow_engine_dispatch` tool with:
- action: "run"
- workflowId: "teamclaw-kf-support"
- Pass the user's full query as the `input` parameter

If the MCP tool is not available, tell the user the ClawMind plugin MCP server is not running and suggest restarting Claude Code.

## Examples
- /kf 用户反馈WebSocket连接失败
- /kf Bot权限怎么配置
- /kf 帮我创建一个缺陷
- /kf 本周值班是谁
