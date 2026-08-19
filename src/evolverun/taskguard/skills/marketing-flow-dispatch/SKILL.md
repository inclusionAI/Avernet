---
name: marketing-flow-dispatch
description: "营销智评分流 — 输入活动ID，自动获取buName并匹配分流规则，路由到下游评审bot"
disable-model-invocation: false
command-dispatch: tool
command-tool: workflow_engine_dispatch
command-arg-mode: raw
---

<!-- @clawmind:generated-facade-command -->

# marketing-flow-dispatch

营销智评分流 — 输入活动ID，自动获取buName并匹配分流规则，路由到下游评审bot

## Usage

Call `workflow_engine_dispatch` with:
- action: "run"
- workflowId: "marketing-flow-dispatch"
- input: user's full query

## Examples

- /marketing-flow-dispatch 4000009
- /marketing-flow-dispatch CP128769143

