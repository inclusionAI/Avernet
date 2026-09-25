# 会话自愈 API v2

监控后端发起自愈；自愈模块向原会话发送一条手册建议，并将投递结果回调监控后端。消息被接受不代表故障恢复。

## 发起自愈

`POST /api/insight/v1/internal/monitoring/session-recoveries`

```json
{
  "event_id": "evt-20260923-000001",
  "tc_fault_label": "TC.MCP.PERMISSION",
  "target": { "bot_id": "default", "entity_id": "example-owner", "env": "prod", "engine": "OC" },
  "session_key": "agent:main:session:example-session:user:example-owner",
  "delivery_key": "evt-20260923-000001-send-1",
  "diagnosis": "MCP 调用返回权限不足，当前会话未能完成原任务。"
}
```

以下字段均必填；请求不接受未定义字段，字段命名使用 snake_case。

| 字段 | 类型 | 含义及约束 |
| --- | --- | --- |
| event_id | string | 对应监控表 insight_monitoring_diagnose.event_id，关联告警，不是数据库自增 id；最长 128 字符。 |
| tc_fault_label | string/null | 故障分类标签，用于选择建议；无标签传 null，使用通用建议。 |
| target.bot_id | string | 目标 Bot 业务 ID。 |
| target.entity_id | string | 目标实体／Owner 标识。 |
| target.env | string | 目标 Bot 环境，目前为 pre 或 prod。 |
| target.engine | string | 引擎标识；本次 MVP 支持 OC，TE 待验证，当前返回 422。还会校验实际 Bot 引擎与该快照一致。 |
| session_key | string | 原会话完整键，最长 1024 字符；不得传 session_id、会话名称或 trace_id。 |
| delivery_key | string | 投递尝试的唯一标识；同次重试复用，新尝试换新值。1–128 字符，首位字母或数字，其余允许字母、数字及 `_.:-`。 |
| diagnosis | string | 脱敏后的诊断摘要，最长 8000 个 Unicode 字符。目前建议按标签选择，摘要用于请求一致性校验，不写入投递记录原文。 |

同步响应 HTTP **202**：

```json
{ "event_id": "evt-20260923-000001", "delivery_key": "evt-20260923-000001-send-1", "status": "accepted" }
```

| 字段 | 含义 |
| --- | --- |
| event_id | 原告警事件编号。 |
| delivery_key | 本次实际采用的投递键，与请求一致。 |
| status | 固定 accepted，表示请求已受理，不表示消息发送成功或故障恢复。 |

当前实现同步等待本次发送及回调，不承诺立即返回。前端不直接调用此接口。

错误响应：

```json
{ "error": "invalid_session_recovery", "message": "session_key 不能为空" }
```

| HTTP | 含义 |
| --- | --- |
| 401 | 服务间凭据无效。 |
| 403 | 无权操作目标。 |
| 422 | 字段、目标或引擎不合法。 |
| 409 | 相同 event_id + delivery_key 的请求内容不一致。 |
| 413 | 请求体超过 64 KiB。 |
| 503 | 存储／回调暂不可用，或同一次投递仍在处理中；后续重试必须保留原键和内容。 |

## 接收投递结果

自愈模块调用监控后端：

`POST /api/insight/v1/internal/monitoring/session-recovery-results`

```json
{
  "event_id": "evt-20260923-000001",
  "delivery": {
    "delivery_key": "evt-20260923-000001-send-1",
    "status": "accepted",
    "result_text": "建议消息已被投递通道接受；尚未判断原任务是否恢复。",
    "result_payload": { "provider_message_id": "msg-8f2c" },
    "started_at_ms": 1790121600000,
    "finished_at_ms": 1790121601000,
    "error": null
  }
}
```

| 字段 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| event_id | string | 是 | 发起请求中的事件编号。 |
| delivery.delivery_key | string | 是 | 发起请求中的投递键。 |
| delivery.status | string | 是 | accepted：通道已接受；failed：通道明确拒绝；unknown：不能确认投递结果。 |
| delivery.result_text | string | 是 | 展示用结果文字，不是发给 Agent 的建议正文；没有结果时为空字符串。 |
| delivery.result_payload | object | 否 | 必要且脱敏的实际回执元信息；本实现返回对象，无元信息为 `{}`。 |
| delivery.started_at_ms | integer | 是 | 开始处理时间，Unix 毫秒。 |
| delivery.finished_at_ms | integer | 是 | 结束处理时间，Unix 毫秒。 |
| delivery.error | string/null | 是 | accepted 为 null；failed／unknown 为脱敏错误说明。 |

result_payload 只可能包含实际获得的 `provider_message_id`（通道消息 ID）、`session_id`（实际会话 ID）、`message_id`（会话内消息 ID），均为 string。没有相应回执时不生成该字段。delivery_key 是关联键，不等同于这些消息 ID。

监控后端按 event_id 定位告警，以 event_id + delivery_key 幂等接收；相同键不同结果返回 409，相同结果返回 2xx。自愈模块保存固定结果，因此补回调时连时间戳也保持一致。

## 重试约定

- 自愈侧先保存最小投递记录，再尝试发送。同键、同内容的重试不会再次发消息。
- 回调失败时返回 503，保留结果；上游用原键重试，仅补回调。没有后台自动重试队列。
- 同一投递仍处理中，重复请求返回 503。处理超过 10 分钟仍无已保存结果，下一次同键请求将其固化为 unknown，并回调；不会重新发送，迟到结果也不覆盖。
- 进程可能在已发送但未保存回执时退出，因此不承诺精确一次送达。unknown 可能已经送达；是否使用新键再次尝试由监控侧决定。
