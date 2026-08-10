---
agent: tc-code
status: completed
created: 2026-08-09T11:15:00+08:00
iteration: 1
task: mixed-bot-message-semantics-qa
environment: local-standalone
---

# 自动化消息语义探测实现报告

## 范围

新增 `scripts/test_mixed_bot_message_semantics.mjs`。它只创建一个新的隔离群并
发送无副作用短消息；不重启服务、不修改现有群、认证配置或用户工作区。

## 实现的可验证断言

- 从 Provider 运行态文件读取唯一 Planner/Developer，并与 BCS 中在线的
  `Claude Planner（当前）`、`Claude Developer（当前）` 卡片逐一核对。
- 创建精确的四成员群：两个 OpenClaw（CEO、产品经理）与两个当前 Claude；拒绝
  重复成员或错误 driver。
- 按日志**字节** offset 捕获该群 `SessionContext` 从 dispatch start 到 completion
  的有界窗口：两个 Claude 必须恰好各收到一次 `chat.inject`，无 send，四成员全部
  成功；初始化后 assistant final 必须为零。
- 对默认 driver、CEO+Developer 异构 fan-out、Planner+Developer+产品经理多目标
  fan-out，严格断言四条 delivery result 唯一、目标 `send/inject` 精确匹配，且只有
  `chat.send` 接收者产生 final。
- 为每个 BCS HTTP 调用设置本地超时；对同一 Developer 的两条并发消息同时提交，
  记录是否均被接受、是否得到两个 final，以及该群范围内是否出现并发 session 超时。

## 诊断安全性

脚本的成功输出仅包含方法计数、拓扑计数和能力状态；不会输出消息正文、token、
credential、group/session ID 或完整 run ID。失败信息同样不拼接动态会话 URL。
