---
description: "riskreview — ClawMind facade: /riskreview"
disable-model-invocation: false
---

<!-- @clawmind:generated-facade-command -->

The user invoked the `/riskreview` facade command from the `risk-review-pipeline` pack.

Call the `mcp__clawmind__workflow_engine_dispatch` tool with:
- action: "run"
- workflowId: "risk-review-pipeline"
- Pass the user's full query as the `input` parameter

If the MCP tool is not available, tell the user the ClawMind plugin MCP server is not running and suggest restarting Claude Code.
