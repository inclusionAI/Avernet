---
agent: tc-review
status: approved-for-local-qa
created: 2026-08-09T00:00:00+08:00
task: mixed-bot-message-semantics-qa
environment: local-standalone
---

# 混合 Bot 消息语义本地验收 Spec

## 目标

在现有 `feat/mixed-openclaw-claude-bots-dev` worktree 的本地 standalone
拓扑中，新建一个包含 2 个 OpenClaw Bot 与 2 个当前 Claude Provider Bot 的
独立群，验证 `chat.inject`、单目标 `chat.send`、多目标 fan-out，以及同一
Claude Bot 的并发 `chat.send` 行为。

## 固定拓扑

| 角色 | 类型 | 群内职责 |
| --- | --- | --- |
| CEO | OpenClaw | consultant |
| 产品经理 | OpenClaw | consultant |
| Claude Planner（当前） | Claude Code Provider | driver |
| Claude Developer（当前） | Claude Code Provider | consultant |

只使用新建测试群；不得修改、删除或重写已有群、历史消息、认证配置或用户工作区。
测试提示必须要求无工具调用、无文件修改。

## 验收场景

1. **建群初始化**：四个 Bot 都收到 `SessionContext/chat.inject`，且在第一条用户
   消息前没有由初始化触发的 `chat.send` 或 Claude final。
2. **默认 Driver 路由**：无 `@mention` 的用户消息仅以 `chat.send` 投递给 Planner；
   另外三个 Bot 收到 inject，Planner 产生 final。
3. **异构 fan-out**：同一用户消息 `@CEO` 与 `@Claude Developer（当前）`；两者都
   收到 `chat.send` 并产生 final，其余两个 Bot 收到 inject。
4. **多目标 Claude fan-out**：同一用户消息同时 `@Claude Planner（当前）`、
   `@Claude Developer（当前）` 和 `@产品经理`；两个 Claude 与一个 OpenClaw 都收到
   `chat.send`，全部终态成功，且无 `Concurrent request on session`。
5. **同一 Claude 的并发 send 探测**：向同一 session 的 Developer 并发发送两条
   明确 mention 的无副作用消息，记录每条请求的 HTTP/BCS 交付和最终事件。此项是
   能力判定，不预设结果：若当前实现串行化/超时/拒绝，报告必须明确该限制、日志阶段
   与是否影响不同 Bot 的 fan-out。
6. **安全与页面**：检查日志只记录投递方法、计数、run 相关元数据和状态；不在报告
   写入聊天正文、token、Provider credential 或完整 run ID。页面读取没有 JS error。

直接用户请求的 send/inject 映射以该请求返回的 `delivery_results` 为准。Bot final
可能被群策略作为一条新的 Bot-originated 消息继续路由；这类二次 `chat.send` 不得被
误判成原始 `chat.inject` 触发了回复，但每个原始 `chat.send` 接收者仍必须产生终态。

## 通过条件

- 场景 1–4 必须 PASS；两个 CC 的多目标 fan-out 是本任务的核心门禁。
- 场景 5 以实测能力结论记录；若失败，不能误报为“支持同一 Bot 同 session 并发”。
- `status all` 中聊天链路服务健康：三个 relay、BaaS、Backend、BCS、五 OpenClaw、
  三 Claude adapter、Provider bridge 与 Frontend 均处于 Running。BCSFuse 仅作为
  观察项，不承担本聊天链路门禁。
- 产物：`002-code-report.md`（测试执行说明）、`003-review-report.md`（消息语义
  静态审阅）、`003b-regression-report.md`（自动化测试结果）、`005-qa-report.md`
  （独立 QA 复核）。
