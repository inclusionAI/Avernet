---
name: kf
description: "TeamClaw 客服支持 — 处理 TeamClaw 用户问题，包括知识库搜索、日志分析、缺陷创建等"
disable-model-invocation: false
command-dispatch: tool
command-tool: workflow_engine_dispatch
command-arg-mode: raw
---

<!-- @clawmind:generated-facade-command -->

# kf

TeamClaw 客服支持 — 处理 TeamClaw 用户问题，包括知识库搜索、日志分析、缺陷创建等

## Usage

Call `workflow_engine_dispatch` with:
- action: "run"
- workflowId: "teamclaw-kf-support"
- input: user's full query

## Examples

- /kf 用户反馈WebSocket连接失败
- /kf Bot权限怎么配置
- /kf 帮我创建一个缺陷
- /kf 本周值班是谁

