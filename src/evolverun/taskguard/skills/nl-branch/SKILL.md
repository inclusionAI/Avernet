---
name: nl-branch
description: "自然语言分支选择（L1 优先） — 演示 keywordAliases 关键词匹配触发分支，用户只需说\"快速\"或\"深入\""
disable-model-invocation: false
command-dispatch: tool
command-tool: workflow_engine_dispatch
command-arg-mode: raw
---

<!-- @clawmind:generated-facade-command -->

# nl-branch

自然语言分支选择（L1 优先） — 演示 keywordAliases 关键词匹配触发分支，用户只需说"快速"或"深入"

## Usage

Call `workflow_engine_dispatch` with:
- action: "run"
- workflowId: "nl-branch-selection"
- input: user's full query

## Examples

- /nl-branch 任务标题

