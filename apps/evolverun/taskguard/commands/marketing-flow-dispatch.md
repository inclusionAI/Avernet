---
description: "营销智评分流 — 输入活动ID，自动获取buName并匹配分流规则，路由到下游评审bot"
disable-model-invocation: false
---

<!-- @clawmind:generated-facade-command -->

The user invoked the `/marketing-flow-dispatch` facade command from the `marketing-flow-dispatch` pack.

Call the `mcp__clawmind__workflow_engine_dispatch` tool with:
- action: "run"
- workflowId: "marketing-flow-dispatch"
- Pass the user's full query as the `input` parameter

If the MCP tool is not available, tell the user the ClawMind plugin MCP server is not running and suggest restarting Claude Code.

## Examples
- /marketing-flow-dispatch 4000009
- /marketing-flow-dispatch CP128769143
