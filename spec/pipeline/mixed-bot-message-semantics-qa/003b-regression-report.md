---
agent: tc-engine-regression
status: pass
created: 2026-08-09T11:15:00+08:00
iteration: 1
task: mixed-bot-message-semantics-qa
environment: local-standalone
---

# 本地消息语义回归报告

## 静态检查

```text
node --check scripts/test_mixed_bot_message_semantics.mjs  PASS
git diff --check                                      PASS
```

## 端到端命令

```bash
node scripts/test_mixed_bot_message_semantics.mjs
```

## 实测结果（仅元数据）

```json
{
  "result": "pass",
  "group": "created",
  "topology": { "openclaw": 2, "claude": 2 },
  "initialization": { "providerInjects": 2, "providerSends": 0, "recipients": 4 },
  "defaultDriver": "pass",
  "heterogeneousFanOut": "pass",
  "multiTargetFanOut": "pass",
  "sameClaudeConcurrentChatSend": {
    "capability": "supported",
    "accepted": 2,
    "finals": 2,
    "concurrentTimeouts": 0
  }
}
```

## 服务状态

三 relay、BaaS、Backend、BCS、5 个 OpenClaw、3 个 Claude adapter、Provider bridge
与 Frontend 均为 Running。BCSFuse 健康检查为 FAIL；它不在本次 BCS → Provider →
BaaS 聊天链路的回归门禁内，已作为观察项交由独立 QA 记录。
