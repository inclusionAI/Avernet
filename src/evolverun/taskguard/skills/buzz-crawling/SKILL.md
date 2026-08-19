---
name: buzz-crawling
description: "外部舆情策略更新全流程 — /buzz-crawling run 启动外部舆情策略更新全流程"
disable-model-invocation: false
command-dispatch: tool
command-tool: workflow_engine_dispatch
command-arg-mode: raw
---

<!-- @clawmind:generated-facade-command -->

# buzz-crawling

外部舆情策略更新全流程 — /buzz-crawling run 启动外部舆情策略更新全流程

## Usage

Call `workflow_engine_dispatch` with:
- action: "run"
- workflowId: "buzz-crawling-pipeline"
- input: user's full query

