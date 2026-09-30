---
description: "营销智评分流（告警测试） — 测试钉钉告警通知的分流工作流，botId带test前缀会触发失败"
disable-model-invocation: false
---

<!-- @clawmind:generated-facade-command -->

The user invoked the `/cct-sop-alert` facade command from the `marketing-flow-dispatch` pack.

Call the `mcp__clawmind__workflow_engine_dispatch` tool with:
- action: "run"
- workflowId: "marketing-flow-dispatch-alert"
- Pass the user's full query as the `input` parameter

If the MCP tool is not available, tell the user the ClawMind plugin MCP server is not running and suggest restarting Claude Code.

## Examples
- /cct-sop-alert 4000009
