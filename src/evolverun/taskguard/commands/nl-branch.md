---
description: "自然语言分支选择（L1 优先） — 演示 keywordAliases 关键词匹配触发分支，用户只需说\"快速\"或\"深入\""
disable-model-invocation: false
---

<!-- @clawmind:generated-facade-command -->

The user invoked the `/nl-branch` facade command from the `nl-branching-demo` pack.

Call the `mcp__clawmind__workflow_engine_dispatch` tool with:
- action: "run"
- workflowId: "nl-branch-selection"
- Pass the user's full query as the `input` parameter

If the MCP tool is not available, tell the user the ClawMind plugin MCP server is not running and suggest restarting Claude Code.

## Examples
- /nl-branch 任务标题
