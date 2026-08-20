---
name: kf-direct
description: "TeamClaw 直接技能支持 — 直接调用 teamclaw-support 技能处理用户问题，跳过多阶段编排"
disable-model-invocation: false
command-dispatch: tool
command-tool: workflow_engine_dispatch
command-arg-mode: raw
---

<!-- @clawmind:generated-facade-command -->

# kf-direct

TeamClaw 直接技能支持 — 直接调用 teamclaw-support 技能处理用户问题，跳过多阶段编排

## Usage

Call `workflow_engine_dispatch` with:
- action: "run"
- workflowId: "teamclaw-kf-direct-support"
- input: user's full query

## Examples

- /kf-direct WebSocket连接失败
- /kf-direct Bot权限配置

