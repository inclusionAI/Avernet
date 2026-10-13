# 会话自愈接口

创建接口同步确认受理；处理进展、等待状态和最终结果通过异步结果接口回报。消息投递成功与原故障恢复分别表达。

## 创建任务

`POST /api/insight/v1/internal/monitoring/session-recoveries`

```json
{
  "event_id": "evt-20261010-001",
  "tc_fault_label": "TC.MCP.PERMISSION",
  "target": { "bot_id": "default", "entity_id": "example-owner", "env": "prod", "engine": "OC" },
  "session_key": "agent:main:session:original",
  "delivery_key": "evt-20261010-001-send-1",
  "diagnosis": "原 MCP 操作返回权限不足，任务未能继续。",
  "error_location": "原会话消息 m-18 的工具调用",
  "execution_level": "approval"
}
```

| 字段 | 必填 | 含义 |
| --- | --- | --- |
| `event_id` | 是 | 诊断事件编号，关联 `insight_monitoring_diagnose.event_id`。 |
| `tc_fault_label` | 是 | 错误标签；无标签时为 `null`。按一级类别选择手册章节；所有类别由自愈 Agent 判断并组织消息；手册只供自愈 Agent 参考。 |
| `target.bot_id` | 是 | Bot 业务标识。 |
| `target.entity_id` | 是 | Bot 所属实体／Owner 标识。 |
| `target.env` | 是 | 目标环境：`pre` 或 `prod`。 |
| `target.engine` | 是 | 支持 `OC`、`CC`、`TE`。OC／CC 优先读 NAS，失败时经受控 Shell 读会话文件；TE 读会话 API 与 AIVision／TCLog 轨迹，不使用 Shell。各引擎的实际修复效果需真实现场验收。 |
| `session_key` | 是 | 发生故障、需要继续的原会话键；不是会话名称或 `session_id`。 |
| `delivery_key` | 是 | 本次尝试的唯一投递键。同一次请求重试复用，新尝试换新键。 |
| `diagnosis` | 是 | 脱敏诊断摘要，最多 8000 字符。 |
| `error_location` | 否 | 错误的具体位置，例如消息 ID、文件行号或工具调用位置。供 Agent 读取原故障证据。 |
| `execution_level` | 否 | 默认 `approval`；`debug` 只读分析并记录拟操作，`approval` 批准完整方案后修改，`automatic` 自动执行完整方案。需 Owner 配合的事项仍需确认。 |


请求采用 `snake_case`，不接受未定义字段。同一 `event_id + delivery_key` 对应同一任务；内容不同返回 `409`。

HTTP `202`：

```json
{ "event_id": "evt-20261010-001", "delivery_key": "evt-20261010-001-send-1", "task_id": "SR-…", "status": "accepted" }
```

`task_id` 是后续查询和继续执行的标识；`accepted` 只表示受理。

错误响应为 `{ "error": "错误代码", "message": "原因" }`。`401` 凭据无效，`403` 无权操作，`409` 请求或状态冲突，`422` 字段／目标／引擎无效，`503` 执行依赖不可用。

## 异步任务结果

自愈服务向诊断服务的 `POST /api/insight/v1/internal/monitoring/session-recovery-results` 发送：

```json
{
  "event_id": "evt-20261010-001",
  "task_id": "SR-…",
  "result_version": 7,
  "status": "waiting_owner",
  "phase": "apply",
  "waiting_reason": "owner_cooperation",
  "effect": "not_recovered",
  "result_text": "请确认工具权限已开放，再执行完整方案。",
  "evidence_ids": ["evidence-tc-example"],
  "evidence": [{ "evidence_id": "evidence-tc-example", "tool_call_id": "tc-example", "source": "session_read", "location": "原会话文件及消息位置", "excerpt": "原失败及重试回执的脱敏摘要" }],
  "delivery": {
    "delivery_key": "evt-20261010-001-send-1",
    "status": "accepted",
    "result_text": "已发送建议；原任务是否恢复仍需验证。",
    "result_payload": { "provider_message_id": "msg-example" },
    "started_at_ms": 1791590400000,
    "finished_at_ms": 1791590401000,
    "error": null
  }
}
```

| 字段 | 含义 |
| --- | --- |
| `event_id`、`task_id` | 关联原诊断事件与自愈任务。 |
| `result_version` | 已保存的结果版本。接收方按 `task_id + result_version` 幂等处理，旧版本不覆盖新版本。 |
| `status` | `running` 执行中；`waiting_owner` 等待方案确认或配合；`waiting_effect` 等待效果；`completed` 结束；`failed` 执行或方案失败。 |
| `phase` | `assess` Agent 判断初始干预方式；`observe` 观察消息或已有进展；`plan` 分析并形成方案；`apply` 执行完整方案；`verify` 验证原任务恢复。 |
| `waiting_reason` | `owner_approval`、`owner_cooperation`、`effect`；不在等待时为 `null`。 |
| `effect` | `recovered` 原故障已解除；`not_recovered` 有证据表明未解除；`unknown` 无法判断；`not_intervened` Debug 未实际干预。 |
| `result_text` | 当前判断、完整方案概要或执行中断原因，供展示。 |
| `evidence_ids` | 当前结论引用的已留存证据。 |
| `evidence` | 上述证据的来源、原文位置及脱敏摘要；完整原文由任务页面按审计引用读取。 |
| `delivery` | Agent 决定、经受控服务发送的初始消息投递明细，取得结果后才提供；其状态不代表效果。 |
| `delivery.delivery_key` | 原请求投递键。 |
| `delivery.status` | `accepted` 通道接受；`failed` 明确投递失败；`unknown` 未取得确定回执。 |
| `delivery.result_text` | 投递结果说明。 |
| `delivery.result_payload` | 已取得的脱敏通道元信息；目前使用 `provider_message_id`。 |
| `delivery.started_at_ms / finished_at_ms` | 处理开始和结束的 Unix 毫秒。 |
| `delivery.error` | `accepted` 为 `null`；其他状态为脱敏原因。 |

服务先保存事实与结果，再异步回报。回报失败重送原版本，不重发建议、不重做操作。`completed + not_intervened` 是 Debug 演练结束；执行异常可以是 `failed + unknown`。

## 继续等待效果的任务

`POST /api/insight/v1/internal/monitoring/session-recoveries/:taskId/continue`

返回 `202 { "status": "accepted", "task_id": "SR-…" }`。仅用于 `waiting_effect`，恢复暂停阶段，先读新现场，不重发初始建议。Owner 等待由任务页面确认完整方案后继续；等待到期释放执行，之后确认会为同一任务启动新容器。

## 任务页面与决策

任务入口：`/insight?module=monitoring&recoveryTaskId=<task_id>`。

页面查询、决策和人工标注使用现有用户登录身份，操作只允许该任务 Owner。

```text
GET  /api/insight/v1/monitoring/session-recoveries?event_id=…
GET  /api/insight/v1/monitoring/session-recoveries/:taskId
GET  /api/insight/v1/monitoring/session-recoveries/:taskId/tool-calls/:toolCallId
POST /api/insight/v1/monitoring/session-recoveries/:taskId/decision
POST /api/insight/v1/monitoring/session-recoveries/:taskId/annotations
```

方案确认请求：`{ "plan_digest": "…", "approved": true, "cooperation_done": true, "feedback": "已处理所需配合事项" }`。配合事项不为空时必须确认完成；决策绑定完整方案摘要，不能改写已确认的决策。

标注请求：`{ "layer": "effect", "correct": true, "reason": "原操作前后已核验", "evidence_ids": ["…"] }`。`layer` 为 `delivery / response / effect`；`correct` 为 `true / false / null`。标注追加保留，不覆盖 Agent 的判断。
