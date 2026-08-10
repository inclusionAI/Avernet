---
agent: tc-qa
status: completed
created: 2026-08-09T11:08:19+08:00
iteration: 1
task: mixed-bot-message-semantics-qa
environment: local-standalone
---

# QA 测试报告

## 测试环境

- Worktree: `feat/mixed-openclaw-claude-bots-dev` 的独立本地 standalone 环境。
- 拓扑：CEO、产品经理、Claude Planner（当前）、Claude Developer（当前）组成的新建隔离群；未修改既有群、用户工作区或认证配置。
- 方法：默认消息经前端 Sender 发送；显式 mention 与并发场景使用同一前端 BCS WebSocket 协议、同一已加入的用户会话进行独立连接复核。所有提示均限制为无工具调用、无文件修改的短回复。
- 服务状态：三个 Claude relay、BaaS、Backend、BCS、5 个 OpenClaw、3 个 Claude adapter、Provider bridge 与 Frontend 均为 Running。BCSFuse health 为 FAIL；按 QA Spec，此项仅记录为观察项，不阻断聊天链路验收。

## 测试结果汇总

- 总用例数: 6
- 通过: 6
- 失败: 0
- 通过率: 100%

## 逐条测试结果

| 编号 | 用例名称 | 结论 | 证据（仅方法、计数和状态） |
| --- | --- | --- |
| TC-01 | 建群初始化 | PASS | 4 个成员均完成 SessionContext 投递；Provider 侧可观测 2 次 `chat.inject`，OpenClaw 投递亦均成功。第一条用户消息前 `chat.send=0`、Claude final=0、失败=0。 |
| TC-02 | 默认 Driver 路由 | PASS | 无 mention 的前端用户消息：Planner `chat.send=1`；Developer、CEO、产品经理各 `chat.inject=1`；Planner 终态 final=1，失败=0。 |
| TC-03 | CEO + Claude Developer 异构 fan-out | PASS | 显式双目标消息：CEO 与 Developer 各 `chat.send=1`，Planner 与产品经理各 `chat.inject=1`；客户端收到 2 个 final、0 个 error。 |
| TC-04 | Planner + Developer + 产品经理多目标 fan-out | PASS | 显式三目标消息：Planner、Developer、产品经理各 `chat.send=1`；CEO `chat.inject=1`；客户端收到 3 个 final、0 个 error；本轮无 `Concurrent request on session`。 |
| TC-05 | 同一 Developer、同一 session 并发 `chat.send` | PASS（能力支持） | 两个独立前端 WebSocket 在同一已加入 session 中并发发出、均明确指向 Developer。两条请求均连接成功、均被 BCS 接受并分配运行请求；各自收到 final=1，error=0、aborted=0。BCS 同秒记录 2 个用户请求与 2 次 Developer `chat.send` 投递，均成功。 |
| TC-06 | 页面与安全诊断 | PASS | 页面加载完成且聊天输入可用；浏览器错误监控无 JS error。页面未发现 bearer/API-key 类凭据模式或完整 UUID/run-id 暴露；本报告未记录聊天正文、凭据或完整 run ID。 |

## 并发能力结论

当前实现**支持同一 Claude Developer 在同一 session 的两条并发 `chat.send`**。两条经不同前端连接同时进入的显式 mention 请求均完成交付并分别收到 successful final；未观察到串行化拒绝、超时、`Concurrent request`、error 或 abort。该结论仅覆盖本地 standalone 的无副作用短回复负载。

## 整体结论

**结论: PASS**

核心门禁（两个 Claude 与一个 OpenClaw 的多目标 fan-out）已通过；混合 OpenClaw + Claude Code 群聊消息路由、终态回传与同 Bot 并发行为均在隔离本地群中复核成功。

## 观察项

- BCSFuse 健康检查仍为 FAIL；它不在本次 BCS → Provider → BaaS 聊天链路门禁内，未影响上述六项结果，建议作为独立运维健康项后续排查。
