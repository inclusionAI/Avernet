---
name: archive
description: "工作流运行档案生成 — 为指定工作流实例生成完整运行档案，聚合所有执行数据用于 BUG 分析"
disable-model-invocation: false
command-dispatch: tool
command-tool: workflow_engine_dispatch
command-arg-mode: raw
---

<!-- @clawmind:generated-facade-command -->

# archive

工作流运行档案生成 — 为指定工作流实例生成完整运行档案，聚合所有执行数据用于 BUG 分析

## Usage

Call `workflow_engine_dispatch` with:
- action: "run"
- workflowId: "run-archive"
- input: user's full query

## Examples

- /archive 00d87945
- /archive flowId=00d87945
