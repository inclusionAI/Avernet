# 会话自愈接口

## 历史离线仿真

登录态 `POST /api/insight/v1/monitoring/session-recoveries/simulations` 发起历史仿真，`cases` 接受 1～10 个 case。每个 case 独立复用现有任务流程；不增加页面创建入口，沿用任务详情展示判断、拟议消息、完整方案和取证过程。

```json
{
  "batch_id": "handbook-review-001",
  "cases": [{
    "request": {
      "event_id": "original-diagnosis-event",
      "delivery_key": "original-delivery-key",
      "diagnosis": "原始诊断文本",
      "tc_fault_label": "TC.MCP.PERMISSION",
      "target": { "entity_id": "example-owner", "bot_id": "default", "env": "prod", "engine": "OC" },
      "session_key": "agent:main:session:original",
      "session_id": "original"
    },
    "cutoff_node": { "message_id": "original-failure-message" },
    "cutoff_at_ms": 1792713600000
  }]
}
```

OC／CC 的 `cutoff_node` 为原会话 `message_id`；TE 使用 `trace_id`，可附 `observation_id`。截止节点包含在可见范围内，`cutoff_at_ms` 是对应的 Unix 毫秒时间，不可晚于当前时间。原目标与原会话沿用既有请求合同。调用方提供原始诊断和稳定节点，不能用当前会话代替原会话。仅 Owner 或管理员可发起；同一批次和 case 输入重试复用任务，输入不同返回 `409`；换批次创建新运行，保留旧结果。

HTTP `202` 返回 `{ "batch_id": "…", "items": [{ "event_id": "…", "delivery_key": "…", "task_id": "SR-…", "status": "accepted" }] }`。创建不要求解析当前 Bot 路由；Agent 按需使用原有会话、轨迹和日志工具，工具强制执行历史边界。允许只读环境调查，回执标明当前环境和查询时点；会话历史不能通过环境工具绕过边界。来源缺失或无法定位截止节点时返回不可取得，不默认读到最新。

消息建议作为 `assessment.message` 保存；环境决策进入原有 plan 阶段，完整方案提交后结束。`completed` 表示仿真产物已生成，effect 为 `not_intervened`；不发送消息、不执行方案、不投递线上业务通知或诊断结果，不允许审批或继续成为在线执行。详情、工具回执、日志归档和停止接口沿用现有实现。输入与边界责任见[离线仿真 Spec](../../../../../docs/clawweb/specs/clawinsight-session-recovery-simulation.md)。

以下在线自愈接口同步确认受理，效果等待超时和任务结束时通过异步结果接口回报。消息投递成功与原故障恢复分别表达；离线仿真不触发该回报。

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

自动请求使用诊断的 `eventId / tcFaultLabel / botId / entityId / env / engine / sessionKey`；存在物理 `sessionId` 时，作为可选的 `session_id` 一并保存。`diagnosis` 按“业务诊断＋系统诊断”保留原文本并校验总长，不静默截断。投递键固定为 `monitoring-<SHA-256(eventId)>`。不发送当前实时诊断未提供的 `error_location`，不发送 `execution_level`，沿用自愈服务默认 `approval`。无合法自愈输入时保存诊断、记录跳过原因并正常 ACK；自愈服务明确拒绝请求时返回不可重试的现有监控错误。

需要自动创建的诊断，只有自愈任务持久化成功后才确认上报；不等待 Agent 执行或最终效果。任务保存失败返回 `503`，复用诊断发送端原有持久队列与退避重试。重复诊断也执行同一触发检查；诊断内容冲突先返回 `409`，不会创建自愈任务。固定投递键复用已有任务，响应丢失、重启或并发上报不创建第二个任务。

诊断上报 ACK 继续使用原 `201 / 200` 和 `duplicate` 契约，不添加自愈状态。业务结果由现有自愈任务记录承载，不用诊断 ACK 表示恢复成功。结果回传由下述接收接口独立归档，不再次调度自愈。

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
| `target.runtime_identity` | 否 | 原运行阶段、设备和发布版本，详见[运行身份字段补充](./session-recovery-runtime-identity.md)。 |
| `target.engine` | 是 | 支持 `OC`、`CC`、`TE`。OC／CC 优先读 NAS，失败时经受控 Shell 读会话文件；TE 只通过 AIVision／TCLog 轨迹调查原会话、验证触达和恢复效果，不调用会话 API，不使用 Shell。各引擎的实际修复效果需真实现场验收。 |
| `session_key` | 是 | 发生故障、需要继续的原会话键；不是会话名称或 `session_id`。 |
| `session_id` | 否 | 上游确认的物理会话 ID，只允许字母、数字、下划线和连字符。用于固定原会话文件，不替代 `session_key`。OpenClaw Cron 键若包含 `:run:<id>`，两者必须一致。 |
| `delivery_key` | 是 | 本次尝试的唯一投递键。同一次请求重试复用，新尝试换新键。 |
| `diagnosis` | 是 | 脱敏诊断摘要，最多 8000 字符。 |
| `error_location` | 否 | 错误的具体位置，例如消息 ID、文件行号或工具调用位置。供 Agent 读取原故障证据。 |
| `execution_level` | 否 | 默认 `approval`；`debug` 只读分析并记录拟操作，`approval` 批准完整方案后修改，`automatic` 自动执行完整方案。需 Owner 配合的事项仍需确认。 |


请求采用 `snake_case`，不接受未定义字段。同一 `event_id + delivery_key` 对应同一任务；内容不同返回 `409`。

OpenClaw 读取优先校验原会话键的索引映射。索引没有该键时，可凭 `session_id`，或旧任务完整 Cron 键 `agent:<agent>:cron:<cron>:run:<id>` 中的 ID，在冻结的目标 Bot 和 Agent 目录读取同名文件，并校验文件头的会话 ID。不会使用已被下一轮覆盖的 Cron 基础键，也不会搜索其他 Bot。历史文件可读不代表消息可投递；发送消息仍要求原会话键存在且指向同一 ID，避免新建或误投会话。

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
  "intervention_status": "unknown",
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
| `status` | `waiting_effect` 效果等待超时、任务可继续；`completed` 任务结束；`failed` 执行或方案失败、任务结束；`canceled` 用户手动停止、任务结束。 |
| `phase` | `assess` Agent 判断初始干预方式；`observe` 观察消息或已有进展；`plan` 分析并形成方案；`apply` 执行完整方案；`verify` 验证原任务恢复。 |
| `waiting_reason` | 效果等待超时为 `effect`；任务结束为 `null`。 |
| `completion_reason` | 可选，`user_continued` 表示用户已继续原会话，自愈正常结束；仅与 `status=completed` 一起出现。任务详情使用 `completionReason`，列表和回传使用 `completion_reason`。不代表原任务已恢复，也不决定干预状态。 |
| `effect` | `recovered` 原故障已解除；`not_recovered` 有证据表明未解除；`unknown` 无法判断；`not_intervened` Debug 未实际干预。 |
| `intervention_status` | `none` 完整操作审计确认未实际干预；`performed` 至少一次干预已由实际回执或后续现场核实；`unknown` 无法确认是否发生过干预。新发送器始终提供；旧发送器和历史结果可省略，省略不等于 `none`。 |
| `result_text` | 本次等待结论、最终效果或执行中断原因，供展示。 |
| `evidence_ids` | 当前结论引用的已留存证据。 |
| `evidence` | 上述证据的来源、原文位置及脱敏摘要；完整原文由任务页面按审计引用读取。 |
| `delivery` | Agent 决定、经受控服务发送的初始消息投递明细，取得结果后才提供；其状态不代表效果。 |
| `delivery.delivery_key` | 该次消息的投递键；重试消息可能使用新的投递键，不用它代替任务结果版本去重。 |
| `delivery.status` | `accepted` 通道接受；`failed` 明确投递失败；`unknown` 未取得确定回执。 |
| `delivery.result_text` | 投递结果说明。 |
| `delivery.result_payload` | 已取得的脱敏通道元信息；可包含 `provider_message_id`、`session_id`、`message_id` 等字符串或有限数值。 |
| `delivery.started_at_ms / finished_at_ms` | 处理开始和结束的 Unix 毫秒。 |
| `delivery.error` | `accepted` 为 `null`；其他状态为脱敏原因。 |
| `error` | 可选的顶层错误说明，字符串或 `null`。 |

Owner 等待（包括到期释放容器）、阶段变化、审批、通知和人工标注只记录在任务内，不触发回传。

服务先保存事实与结果，再异步回报。回报失败重送原版本，不重发建议、不重做操作。同一任务可多次效果等待超时，每次生成独立结果版本；恢复和最终完成不会覆盖尚未送达的旧结果，按版本顺序回传。`completed + not_intervened` 是 Debug 演练结束；执行异常可以是 `failed + unknown`。

`intervention_status` 由控制面按整条任务的工具审计生成，累计所有 AIS 执行和重试，与 `status`、`effect` 独立。恢复消息须由受控原会话或轨迹读取确认本次投递键的实际消息；通道 `accepted` 只证明受理，尚未确认时为 `unknown`。环境操作须有成功执行回执或经现有 `reconcile_write` 核实为 `applied`；通用写入核实不能代替恢复消息的触达证明。超时、回执缺失、执行中的操作以及可能部分执行的失败均保留为 `unknown`；下发前拒绝、未领取操作或核实为 `not_applied` 不计实际干预。只有读取、方案、通知或 Owner 配合也不计干预，Debug 为 `none`。只要已有一次确认干预，后续失败不抹除该事实；`performed` 不代表全部动作成功或原任务恢复。仅新建任务在既有上下文产物中记录完整审计覆盖；历史产物缺少该标记时，即使此前已完成存储迁移，也不能推断 `none`。内部标记不暴露给 Agent，不增加表或数据库字段。回传快照固定生成时的结论，不重写旧版本或补写历史字段。

结果回传使用 ClawWeb 已有服务地址和上述固定接口路径。发送器每条结果首次发送失败后最多重试 3 次，分别退避 1、5、15 分钟；次数与允许时间持久化，发送前通过任务版本条件更新领取资格，避免多实例同时发送。每轮只读取一页最多 5 个候选任务、串行发送最多 5 条请求，下轮从游标继续。超限后停止自动回传，完整结果仍保留，任务时间线记录停止原因与累计尝试次数；历史已超限记录不再发送。回传失败或停止不改变自愈任务的业务状态、恢复效果或干预事实。

接收端每次接收一个结果对象，按 `event_id` 找到原诊断行，将对象追加到 `self_healing_results`（MEDIUMTEXT 中的 JSON 数组；SQL NULL 视为空数组）。不创建空诊断行，不改变原诊断结论、`handler_name` 或原接收时间，也不读写闲置的数据库 `task_completed` 列。

同一行内按 `task_id + result_version` 去重：同键同内容成功但不追加（忽略对象键顺序，保留数组顺序）；同键不同内容返回冲突。不同键的结果均保留，允许版本乱序，按接收顺序追加。条件更新保证并发回传不会直接覆盖另一条结果，竞争重试耗尽则返回失败，由现有发送器重试。存量不是合法结果数组时返回失败，不清空或覆盖它。

HTTP `200`（新结果或重复投递）：

```json
{ "accepted": true, "eventId": "evt-20261010-001", "taskId": "SR-example", "resultVersion": 7, "duplicate": false }
```

重复结果的 `duplicate` 为 `true`。接收端业务错误格式沿用 monitoring：`{ "error": { "code": "MONITORING_…", "requestId": "…" } }`（内部接口不返回详细 message）。

| HTTP | code | 含义 |
| --- | --- | --- |
| 404 | `MONITORING_RESULT_TARGET_NOT_FOUND` | 原诊断行不存在。 |
| 409 | `MONITORING_RESULT_CONFLICT` | 同任务同版本已保存不同内容。 |
| 422 | `MONITORING_INVALID_RESULT` | 字段、类型、枚举或版本无效。 |
| 413 | 沿用请求体中间件 | 超过 128 KiB 请求体限制，不截断证据。 |
| 503 | `MONITORING_NOT_READY` | 数据库或表结构不可用、存量损坏、容量超限或并发重试耗尽。 |

非 JSON、语法错误和压缩请求沿用现有路由规则。结果对象不接受未知字段；`delivery`、`evidence`、`error`、`intervention_status`、`completion_reason` 可省略，其他顶层字段必需。`intervention_status` 提供时只能为 `none`、`performed` 或 `unknown`，接收端原样保留在 `self_healing_results` 中，不根据该字段改变诊断决策。旧结果与新结果可混存，历史缺省值不自动补齐。`result_version` 是正整数。没有新增证据条数限制，但总请求体必须满足上述大小限制。

接收接口尚未部署或回传失败时，任务仍可创建、执行并在任务页面查看；结果保留为待回传，成功响应后才记录为已回传。非 2xx（含 404/409/422）会持续重试，需修复拒收原因。部署须确认内部路径已有访问限制，接收端不单独增加发送方未提供的 Token。生产表需已存在可空的 `self_healing_results MEDIUMTEXT`；应用仅校验，不自动执行生产 DDL。联调需确认真实结果体大小、目标数据库兼容性以及内部访问控制。

## AIS 执行恢复

CW 控制面负责 AIS 派发、启动与执行中断后的有限恢复。同一个自愈 Task 内最多自动重试两次，分别退避 20 秒、60 秒；失败次数和下次重试时间保存在现有 Task 配置中，服务重启不重置预算。每次重试创建新的 execution 和辅助 `agent_execution` Step，保留业务阶段、完整方案、Owner 决策、证据和操作审计。辅助执行失败不直接改变业务 Task 的终态。

已取得 AIS 作业编号时，先确认旧作业结束，再创建替代执行；查询失败或状态未确认时保持待重试，不并行启动。租约过期的运行中作业先请求停止，并等待终态。未取得派发回执时不推断作业未创建：先撤销旧执行票据，使旧执行不能继续调用受控工具。旧回调不能推进或终止替代执行。

恢复前等待尚在执行的工具调用回执；租约到期仍无回执的写操作记为 `unknown`，未派发的调用取消。新 Agent 读取已保存进度，未知写入必须通过现有读取和 `reconcile_write` 核实，不能从头重放消息或方案操作。

重试过程只记录任务时间线，不逐次回传上游。重试成功后继续原流程；预算耗尽或 Base 明确回报不可重试时，结束任务并回传 `failed + unknown`，`result_text` 保留脱敏失败原因、作业编号（已取得时）及重试情况。方案执行失败、Agent 明确回报受阻仍按原业务流程结束，不自动增加修复方案。等待 Owner、等待效果或已结束任务不会因 AIS 退出而变为失败；外部停止未过期的作业不自动重建。

## 继续等待效果的任务

`POST /api/insight/v1/internal/monitoring/session-recoveries/:taskId/continue`

返回 `202 { "status": "accepted", "task_id": "SR-…" }`。仅用于 `waiting_effect`，恢复同一个 Task 的暂停阶段；可创建新的 execution 和容器，但保留已有方案、操作、判断、证据及待回传结果，先读新现场，不重发初始建议。再次调用创建接口不会代替此继续接口。Owner 等待由任务页面确认完整方案后继续；等待到期释放执行，之后确认会为同一任务启动新容器。

## 任务页面与决策

任务入口：`/insight?module=monitoring&recoveryTaskId=<task_id>`。

页面查询、分享设置、决策和人工标注使用现有用户登录身份。默认仅任务 Owner 或管理员可查看；Owner 或管理员可开启分享，开启后其他登录用户可通过任务链接只读查看方案、时间线、效果和工具调用证据。方案确认、配合事项确认、人工标注和修改分享设置仍仅允许 Owner 或管理员。Agent 原始会话日志下载沿用现有日志接口的 Owner／管理员权限。

分享开关沿用自进化的 `ce_tasks.config_json.shared`，未设置视为关闭，不新增表或迁移。详情响应的 `shared / canOperate / canManageShare` 表达当前分享状态及当前用户的操作权限。关闭分享后，共享访客的详情和证据读取立即返回 `403`。分享设置不推进任务阶段、不重新执行 Agent、不触发上游结果回传。

详情的 `executions` 从现有 `agent_execution` Step 只读生成，按执行次数升序返回 `executionId / attempt / status / jobId / jobUrl`，不包含执行票据或归档上传地址。`jobUrl` 由 Host 的现有 AIS 服务生成；未取得作业编号或 Host 未提供链接时相应字段为 `null`。页面以时间线事件的 `payload.execution_id` 关联具体执行，在该条记录旁展示对应 AIS 作业链接；派发失败未取得编号时明确展示，不指向其他轮次的作业。读取不补写历史事件，也不改变任务状态或回传结果。

监控页面展开诊断记录后，在记录顶部展示对应自愈任务的当前状态和一个查看入口。通过诊断的 `diagnosisId`（即 `event_id`）关联，任务编号悬停可见；没有可查看任务时不显示入口。

```text
GET  /api/insight/v1/monitoring/session-recoveries?event_id=…
GET  /api/insight/v1/monitoring/session-recoveries/:taskId
PATCH /api/insight/v1/monitoring/session-recoveries/:taskId/share
GET  /api/insight/v1/monitoring/session-recoveries/:taskId/tool-calls/:toolCallId
POST /api/insight/v1/monitoring/session-recoveries/:taskId/decision
POST /api/insight/v1/monitoring/session-recoveries/:taskId/annotations
POST /api/insight/v1/monitoring/session-recoveries/:taskId/terminate
```

分享设置请求：`{ "shared": true }`（关闭为 `false`），返回更新后的任务详情。仅布尔值合法；重复设置同一状态幂等。

停止请求：`{ "reason": "用户手动停止任务" }`，仅 Owner 或管理员可调用。原因可省略；提供时须为 1–500 字符的非空文本。允许停止 `running / waiting_owner / waiting_effect`；已完成或失败的任务返回 `409`。

服务先保存 `canceled`、停止人、时间和原因，阻止后续干预与自动重试，再结束执行票据并停止 AIS。返回更新后的详情及 `termination: { status, aisJobId }`。状态为 `remote_stopped`（AIS 已结束）、`remote_stop_failed`（停止未确认，可再次调用）或 `stop_pending`（尚未取得作业编号；迟到派发回执返回后补停）。详情及对应 `executions` 也保留实际 AIS 停止状态。

重复停止同一已停止任务不会重新执行业务动作或追加最终回传，只重试停止 AIS。任务停止不自动撤销已完成操作；已有调查、方案、工具审计和效果判断保留，进行中的写操作按结果未知留档。迟到的 Agent／AIS 回报不能推进任务。人工停止以 `canceled` 回传一次终态；上游继续接口不恢复已停止任务。

方案确认请求：`{ "plan_digest": "…", "approved": true, "cooperation_done": true, "feedback": "已处理所需配合事项" }`。配合事项不为空时必须确认完成；决策绑定完整方案摘要，不能改写已确认的决策。

标注请求：`{ "layer": "effect", "correct": true, "reason": "原操作前后已核验", "evidence_ids": ["…"] }`。`layer` 为 `delivery / response / effect`；`correct` 为 `true / false / null`。标注追加保留，不覆盖 Agent 的判断。


### 用户续聊后正常结束

任何活动阶段核实原始故障后已有真实用户新消息时，Agent 回报
`{"kind":"blocked","phase":"当前阶段","reason":"user_continued","summary":"续聊位置和依据","evidence_ids":["已保存的读取证据ID"]}`。
控制面要求引用成功的 `session_read`／`trajectory_read` 审计，正常收口为 `completed`，记录 `completionReason=user_continued`，结束当前执行并清除等待／重试期限；不新增业务任务状态、不进入方案、不重放动作。
`effect` 保留已有证据结论，缺少判断时为 `unknown`；`intervention_status` 继续由任务全量审计生成，已有干预不会被让路抹除。
页面和群通知显示“用户已接手，自愈已让路”。统计单列该结束原因，不计为执行故障，也不能将 `completed` 直接计为恢复成功。
没有该原因的既有 `blocked` 仍表示无法继续，沿用原失败流程；未知原因拒绝。先部署支持新字段的发送／接收后端，再更新自愈包。没有新建数据库表、字段或 AIS Base 配置。

回报引用了不存在的证据 ID，或 `message` 判断缺少已保存的投递／拟发送记录时，返回 HTTP 422 并说明可纠正字段。纠错保留已核实的业务判断，证据引用必须使用工具返回的完整 ID。普通 `blocked` 省略 `reason`；执行归属、阶段或版本冲突继续返回 HTTP 409。

`blocked.reason=target_unavailable` 将自愈流程正常结束为 `completed`，同时回传 `completion_reason=target_unavailable`、`effect=unknown`。该结论必须引用已留存的受控工具证据，且对应审计中有 Provider 明确返回的 `target_unavailable` 错误；普通 HTTP 404、会话缺失、超时或模型断言均不足以成立。页面显示“目标不可用”，不算自愈执行故障，也不宣称业务恢复；是否干预仍由实际工具审计判断。

### 按手册转交 Owner 自行处理

Agent 根据适用手册及当前现场，确认剩余阻塞满足“转交 Owner 自行处理并结束”的条件，且没有其他待调查或修复事项时，回报
`{"kind":"owner_handoff","phase":"当前阶段","summary":"已核实的剩余阻塞、手册处理依据、具体对象及 Owner 处理指引（Markdown）","evidence_ids":["受控现场读取返回的完整证据 ID"]}`。
此结果表示处置决定，不使用普通 `blocked` 的错误原因表达；控制面不识别具体故障类别。`assess`、`observe`、`plan`、`verify` 均可提交，不能在 `apply` 执行环境动作中途切换，Debug／仿真禁止真实转交。

服务校验当前任务、执行归属、版本及已保存的现场读取证据，复用现有 Owner 通知发送指引并记录回执。
不创建方案、不进入等待确认／配合、不再执行或验证，结束为 `status=completed`、
`completion_reason=owner_handoff`、`effect=not_recovered`；已有触达／响应结论保留，干预情况仍由实际审计计算，通知不算干预。
通知失败或禁用时同样结束，但页面不得显示“已通知”；成功时显示“已通知 Owner 自行处理”。通知回执与任务结束均保存在原时间线，无新增数据库字段或配置。

其他需要 Owner 配合的情况仍进入完整方案；普通消息干预后的 `observe.effect=not_recovered` 仍推进 `plan`，不自动转交 Owner。

兼容与发布顺序：先部署支持 `owner_handoff` 的结果发送／接收后端和页面，再更新自愈包。旧包的 `blocked.reason=mcp_permission_required` 在 Agent 回报入口转换为同一个通用转交处理，之后生成 `completion_reason=owner_handoff`；历史任务与待发送结果中的旧原因仍可读取、展示及回传，不迁移既有记录。新 Skill／Wrapper 不再输出或依赖 MCP 专属原因。回滚自愈包可继续使用旧输入；已产生新结果后不应先回滚接收端。

## 任务整体摘要

既有任务详情 `GET /api/insight/v1/monitoring/session-recoveries/:taskId` 增加可选 `final_summary: string`，在线与仿真共用。它由自愈 Agent 在整体收尾时生成，说明原问题、关键判断依据、最终处理方式与实际进展；仿真明确未执行。表格导出可直接读取该字段，无需再从阶段分析、消息与方案拼接摘要。最多 2,000 个 UTF-16 代码单元，支持 Markdown。

字段缺失表示尚未生成或摘要不可用，不代表任务失败。旧任务、外部停止和执行异常可能没有摘要，不自动回填；原判断、消息、方案、证据和效果继续可查。在线等待方案时不提前保存最终摘要；观察到期暂停的摘要只说明截至暂停的进展，恢复观察后清除，后续按新的收尾结果保存。此字段不扩展诊断结果回传协议，也不改变仿真不回传和原统计规则。
