---
description: "外部舆情策略更新全流程 — /buzz-crawling run 启动外部舆情策略更新全流程"
disable-model-invocation: false
---

<!-- @clawmind:generated-facade-command -->

The user invoked the `/buzz-crawling` facade command from the `buzz-crawling-pipeline` pack.

Call the `mcp__clawmind__workflow_engine_dispatch` tool with:
- action: "run"
- workflowId: "buzz-crawling-pipeline"
- Pass the user's full query as the `input` parameter

If the MCP tool is not available, tell the user the ClawMind plugin MCP server is not running and suggest restarting Claude Code.
