---
description: "TeamClaw 直接技能支持 — 直接调用 teamclaw-support 技能处理用户问题，跳过多阶段编排"
disable-model-invocation: false
---

<!-- @clawmind:generated-facade-command -->

The user invoked the `/kf-direct` facade command from the `teamclaw-kf` pack.

Call the `mcp__clawmind__workflow_engine_dispatch` tool with:
- action: "run"
- workflowId: "teamclaw-kf-direct-support"
- Pass the user's full query as the `input` parameter

If the MCP tool is not available, tell the user the ClawMind plugin MCP server is not running and suggest restarting Claude Code.

## Examples
- /kf-direct WebSocket连接失败
- /kf-direct Bot权限配置
