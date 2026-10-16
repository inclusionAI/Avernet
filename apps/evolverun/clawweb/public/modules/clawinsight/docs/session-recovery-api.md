# 会话自愈接口

创建接口同步确认受理；效果等待超时和任务结束时通过异步结果接口回报。消息投递成功与原故障恢复分别表达。

## 实时诊断自动触发（内测）

ClawWeb 在现有 `POST /api/insight/v1/internal/monitoring/diagnosis-events` 完成身份校验和诊断落库后，调用已装配的自愈任务服务。诊断发送端不增加自愈请求，也不修改上报 DTO。

自动触发同时满足：

- `decision = ALERT` 且 `handlerName = NOT_COMPLETED`。当前实时诊断将 `task_completed` 映射为 `COMPLETED / NOT_COMPLETED`；缺失或其他值不推断为未完成。`PASS`、`UNRESOLVED` 和已完成任务不触发。
- `ce_app_config` 中 `config_key = session_recovery_allowlist` 的记录已启用，且目标精确命中 `config_json.allowed_bots`。
- 原诊断有合法的原会话键和非空诊断文本。缺失会话键时，不使用物理 `sessionId` 或 `traceId` 替代。

白名单 JSON：

```json
{
  "allowed_bots": [
    { "bot_id": "example-bot", "entity_id": "example-owner", "env": "prod" }
  ]
}
```

每项仅包含 `bot_id / entity_id / env`，按完整三元组区分大小写精确匹配；环境为 `pre / prod`，不允许通配符、重复项或未知字段。`env` 指目标 Bot 环境。读取现有配置 Repository，使用现有管理员接口 `/api/evolve/app-config` 维护，不新增表、迁移、环境变量或凭据。代码不自动创建配置或填充内测名单。

配置不存在、`enabled = 0` 或名单为空时，诊断正常落库和 ACK，不自动创建任务。每次符合触发条件的上报重新读取配置，修改后即时影响后续自动创建；已创建任务按原流程继续。配置读取失败、启用记录的格式无效或自愈服务暂不可用返回 `503`，不会默认放行。

自动请求使用诊断的 `eventId / tcFaultLabel / botId / entityId / env / engine / sessionKey`。`diagnosis` 按“业务诊断＋系统诊断”保留原文本并校验总长，不静默截断。投递键固定为 `monitoring-<SHA-256(eventId)>`。不发送当前实时诊断未提供的 `error_location`，不发送 `execution_level`，沿用自愈服务默认 `approval`。无合法自愈输入时保存诊断、记录跳过原因并正常 ACK；自愈服务明确拒绝请求时返回不可重试的现有监控错误。

需要自动创建的诊断，只有自愈任务持久化成功后才确认上报；不等待 Agent 执行或最终效果。任务保存失败返回 `503`，复用诊断发送端原有持久队列与退避重试。重复诊断也执行同一触发检查；诊断内容冲突先返回 `409`，不会创建自愈任务。固定投递键复用已有任务，响应丢失、重启或并发上报不创建第二个任务。

诊断上报 ACK 继续使用原 `201 / 200` 和 `duplicate` 契约，不添加自愈状态。业务结果由现有自愈任务记录承载，不用诊断 ACK 表示恢复成功。本次自动触发接线不新增结果接收路由或再次调度逻辑。

与 2026-09-23《会话自愈对接协议 v2（最小实现版）》相比，创建请求的原必填字段不变；现有接口已增加可选的 `error_location / execution_level`、受理响应的 `task_id`、完整任务结果及其版本和证据，以及继续原任务接口。当前接口不要求新增独立服务 Token；旧文档中仅有 `delivery` 的结果不能代表完整任务结论。

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

错误响应为 `{ "error": "错误代码", "message": "原因" }`。`403` 无权操作，`409` 请求或状态冲突，`422` 字段／目标／引擎无效，`503` 执行依赖不可用。创建、继续任务及结果回传不要求额外的独立 Token。

## 异步任务结果

仅在**效果等待超时**或**自愈任务结束（成功、失败或 Debug 结束）**时，自愈服务向诊断服务的 `POST /api/insight/v1/internal/monitoring/session-recovery-results` 发送：

```json
{
  "event_id": "evt-20261010-001",
  "task_id": "SR-…",
  "result_version": 7,
  "status": "waiting_effect",
  "phase": "observe",
  "waiting_reason": "effect",
  "effect": "unknown",
  "result_text": "观察期限已到，尚未确认恢复，保存进度等待上游继续。",
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
| `status` | `waiting_effect` 效果等待超时、任务可继续；`completed` 任务结束；`failed` 执行或方案失败、任务结束。 |
| `phase` | `assess` Agent 判断初始干预方式；`observe` 观察消息或已有进展；`plan` 分析并形成方案；`apply` 执行完整方案；`verify` 验证原任务恢复。 |
| `waiting_reason` | 效果等待超时为 `effect`；任务结束为 `null`。 |
| `effect` | `recovered` 原故障已解除；`not_recovered` 有证据表明未解除；`unknown` 无法判断；`not_intervened` Debug 未实际干预。 |
| `result_text` | 本次等待结论、最终效果或执行中断原因，供展示。 |
| `evidence_ids` | 当前结论引用的已留存证据。 |
| `evidence` | 上述证据的来源、原文位置及脱敏摘要；完整原文由任务页面按审计引用读取。 |
| `delivery` | Agent 决定、经受控服务发送的初始消息投递明细，取得结果后才提供；其状态不代表效果。 |
| `delivery.delivery_key` | 原请求投递键。 |
| `delivery.status` | `accepted` 通道接受；`failed` 明确投递失败；`unknown` 未取得确定回执。 |
| `delivery.result_text` | 投递结果说明。 |
| `delivery.result_payload` | 已取得的脱敏通道元信息；目前使用 `provider_message_id`。 |
| `delivery.started_at_ms / finished_at_ms` | 处理开始和结束的 Unix 毫秒。 |
| `delivery.error` | `accepted` 为 `null`；其他状态为脱敏原因。 |

Owner 等待（包括到期释放容器）、阶段变化、审批、通知和人工标注只记录在任务内，不触发回传。

服务先保存事实与结果，再异步回报。回报失败重送原版本，不重发建议、不重做操作。同一任务可多次效果等待超时，每次生成独立结果版本；恢复和最终完成不会覆盖尚未送达的旧结果，按版本顺序回传。`completed + not_intervened` 是 Debug 演练结束；执行异常可以是 `failed + unknown`。

结果回传使用 ClawWeb 已有服务地址和上述固定接口路径。接收接口尚未就绪或回传失败时，任务仍可创建、执行并在任务页面查看；结果保留为待回传。接收接口成功响应后才记录为已回传。

## 继续等待效果的任务

`POST /api/insight/v1/internal/monitoring/session-recoveries/:taskId/continue`

返回 `202 { "status": "accepted", "task_id": "SR-…" }`。仅用于 `waiting_effect`，恢复同一个 Task 的暂停阶段；可创建新的 execution 和容器，但保留已有方案、操作、判断、证据及待回传结果，先读新现场，不重发初始建议。再次调用创建接口不会代替此继续接口。Owner 等待由任务页面确认完整方案后继续；等待到期释放执行，之后确认会为同一任务启动新容器。

## 任务页面与决策

任务入口：`/insight?module=monitoring&recoveryTaskId=<task_id>`。

页面查询、分享设置、决策和人工标注使用现有用户登录身份。默认仅任务 Owner 或管理员可查看；Owner 或管理员可开启分享，开启后其他登录用户可通过任务链接只读查看方案、时间线、效果和工具调用证据。方案确认、配合事项确认、人工标注和修改分享设置仍仅允许 Owner 或管理员。Agent 原始会话日志下载沿用现有日志接口的 Owner／管理员权限。

分享开关沿用自进化的 `ce_tasks.config_json.shared`，未设置视为关闭，不新增表或迁移。详情响应的 `shared / canOperate / canManageShare` 表达当前分享状态及当前用户的操作权限。关闭分享后，共享访客的详情和证据读取立即返回 `403`。分享设置不推进任务阶段、不重新执行 Agent、不触发上游结果回传。

监控页面展开诊断记录后，在记录顶部展示对应自愈任务的当前状态和一个查看入口。通过诊断的 `diagnosisId`（即 `event_id`）关联，任务编号悬停可见；没有可查看任务时不显示入口。

```text
GET  /api/insight/v1/monitoring/session-recoveries?event_id=…
GET  /api/insight/v1/monitoring/session-recoveries/:taskId
PATCH /api/insight/v1/monitoring/session-recoveries/:taskId/share
GET  /api/insight/v1/monitoring/session-recoveries/:taskId/tool-calls/:toolCallId
POST /api/insight/v1/monitoring/session-recoveries/:taskId/decision
POST /api/insight/v1/monitoring/session-recoveries/:taskId/annotations
```

分享设置请求：`{ "shared": true }`（关闭为 `false`），返回更新后的任务详情。仅布尔值合法；重复设置同一状态幂等。

方案确认请求：`{ "plan_digest": "…", "approved": true, "cooperation_done": true, "feedback": "已处理所需配合事项" }`。配合事项不为空时必须确认完成；决策绑定完整方案摘要，不能改写已确认的决策。

标注请求：`{ "layer": "effect", "correct": true, "reason": "原操作前后已核验", "evidence_ids": ["…"] }`。`layer` 为 `delivery / response / effect`；`correct` 为 `true / false / null`。标注追加保留，不覆盖 Agent 的判断。
