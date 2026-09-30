---
description: "approval-web — ClawMind facade: /approval-web"
disable-model-invocation: false
---

<!-- @clawmind:generated-facade-command -->

The user invoked the `/approval-web` facade command from the `approval-node-pipeline` pack.

Call the `mcp__clawmind__workflow_engine_dispatch` tool with:
- action: "run"
- workflowId: "approval-web-demo"
- Pass the user's full query as the `input` parameter

If the MCP tool is not available, tell the user the ClawMind plugin MCP server is not running and suggest restarting Claude Code.
