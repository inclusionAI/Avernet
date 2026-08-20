---
name: cct-sop-alert
description: "营销智评分流（告警测试） — 测试钉钉告警通知的分流工作流，botId带test前缀会触发失败"
disable-model-invocation: false
command-dispatch: tool
command-tool: workflow_engine_dispatch
command-arg-mode: raw
---

<!-- @clawmind:generated-facade-command -->

# cct-sop-alert

营销智评分流（告警测试） — 测试钉钉告警通知的分流工作流，botId带test前缀会触发失败

## Usage

Call `workflow_engine_dispatch` with:
- action: "run"
- workflowId: "marketing-flow-dispatch-alert"
- input: user's full query

## Examples

- /cct-sop-alert 4000009

