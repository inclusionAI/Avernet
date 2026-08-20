---
name: ai-branch
description: "AI 解读分支（L0 回退） — 演示无 keywordAliases 时由 AI Agent 解读自然语言触发分支"
disable-model-invocation: false
command-dispatch: tool
command-tool: workflow_engine_dispatch
command-arg-mode: raw
---

<!-- @clawmind:generated-facade-command -->

# ai-branch

AI 解读分支（L0 回退） — 演示无 keywordAliases 时由 AI Agent 解读自然语言触发分支

## Usage

Call `workflow_engine_dispatch` with:
- action: "run"
- workflowId: "ai-interpreted-branch"
- input: user's full query

## Examples

- /ai-branch 审核目标

