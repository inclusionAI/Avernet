# ClawInsight session recovery

Self-healing is a ClawInsight business workflow. Every task starts an independent Agent to assess the diagnosis and current evidence, choose a targeted message, observe ongoing progress, or form an environment repair plan. If advice does not resolve the original problem, the Agent submits one complete environment repair plan. Owner cooperation belongs to that plan. The service controls authorization, stores the execution record and reports effect-wait expiry and terminal results to monitoring.

## Existing architecture

- `SessionRecoveryTaskService` owns assess/observe/plan/apply/verify, Owner decisions and separate Owner/effect deadlines. Initial message attempts use the existing audited tool ledger.
- `SessionRecoveryAgentService` uses existing `BotRuntime`, target resolution, AIS dispatch, workload tickets and `RepairRepository` tool audit. No independent MIST client, Bot connection stack or database is added.
- `AgentExecutionService` and the execution monitor serve both Repair and self-healing. Business services keep their own state transitions. Base callbacks use a trusted, injected handler; a fixed execution Step retains its own attachment contract after business stages advance.
- OCB composes internal dependencies and the existing `DingTalkSender`; business notification content is injected into its shared sender. Public services do not select BaaS versus legacy Arca.

## State and evidence

Tasks use existing `ce_tasks` and `ce_steps`. Task extension JSON retains current execution identity and bounded state. Business phases use `ce_steps`; phase reports, complete plans, judgments and Owner decisions use the existing object store with immutable digest references. The timeline combines phase reports, tool audit and result-delivery acknowledgments. Full bounded source reads and operation receipts live in the existing tool audit; the timeline and judgments refer to their IDs. The Owner page loads full evidence on demand. Evidence redaction retains diagnostic message text while removing credentials and PII.

Task status and recovery effect are separate. A channel accepting a message is not delivery evidence. Only a marked user message in the original session proves delivery. Agent response and recovery require semantic judgment with stored evidence; environmental write success does not prove recovery.

Original-session matching accepts Engine-added metadata and timestamp prefixes. Bounded reads retain all matching record IDs, including repeated records, so delivery evidence does not conceal lower-layer duplication. The fixed read-only program is compressed for the existing BaaS command limit after runtime-user wrapping.

Debug performs no actual message or environment mutation. Approval binds one complete plan, rather than individual commands; automatic mode still waits for necessary Owner cooperation. Environment writes pass the existing audit ledger and enforce the current execution and approved plan. Agent-selected conversation messages do not require environmental approval, but remain audited writes with one initial delivery attempt, target validation and no blind replay of unknown outcomes.

A plan identifies the intended adjustment, scope, primary actions, risks, verification and Owner cooperation. Concrete Shell commands and Engine requests are optional implementation references. During execution, the Agent supplies actual commands or requests under an approved action ID, can complete several steps and adjust execution details from current evidence without changing the plan's direction or adding actions outside its scope. The service validates the current approval, execution, frozen target and declared engine capabilities, and audits every actual request and receipt; it does not require equality with reference commands or API payloads. A new request ID identifies a new step or adjusted attempt; the same ID still cannot identify different requests, and unknown write outcomes cannot be blindly replayed. The Owner page shows the readable plan and primary actions first, with technical references, basis and verification details collapsed by default.

Bootstrap publishes `planProtocol.allowedOperations` from the same definition used to validate action scopes. Cron enable/disable uses the `cron.update` business scope and the exact Cron ID; its actual request must still use the target's declared adapter endpoint. Missing or malformed scope fields and unknown operation names return 422 with correction guidance, allowing the existing bounded result-correction loop to resubmit the plan before approval without executing operations. Protected MCP/Skill changes and execution requests outside the approved action or target remain forbidden with 403.

Owner waiting and effect waiting have separate deadlines. Owner expiry releases the execution while preserving the business task. A later decision creates a new execution for that task; an in-window decision continues the current execution. Effect waiting resumes the paused phase after reading new records, without resending initial advice. Old execution reports cannot overwrite new execution state.

## Handbook and Agent assessment

Handbook content has one source: OCB `clawevolve-recovery-handbook/SKILL.md` and `mcp/handbook.md`. It is packaged into the AIS Agent workspace only, not installed in the target Bot. The public catalog retains category references without internal professional content. Unsupported categories have no generic-retry chapter; the Agent assesses them from the diagnosis and actual evidence.

There is no direct-intervention mode or category allowlist. Initial assessment cannot modify the environment. The Agent composes the advice; the original Bot receives that text, without being instructed to install or read the handbook. Initial messages use the upstream delivery key; stable tool retries return the saved receipt. Maintenance does not generate advice.

## Investigation and intervention

The shared `BotRuntime` connection uses existing service credentials and provider composition. Engine operations do not use the product OpenAPI or acquire user delegation. Controlled Engine reads and approved writes use a declared method/path contract. TE rejects Shell before contacting the provider.

OC/CC original-session reads prefer the existing read-only Base NAS mount. Frozen identity determines the Bot namespace; the original session must have an exact file mapping. Unavailable or ambiguous NAS reads fall back to controlled Shell file reads, not session APIs. CC requires an actual SDK session mapping. Both retain bounded original records and source references.

TE uses only the injected AIVision/TCLog trace and observation reader for original-session investigation, delivery confirmation and effect verification, with pagination and original uploaded inputs, outputs and tool records. Bootstrap disables `session_read` for TE, the Agent tool catalog omits it, and stale session-read calls are rejected before contacting the runtime. Message dispatch retains its frozen target and original session key without an additional session API query, including tasks with historical session-read receipts. Missing, delayed or truncated uploaded data is not evidence of failure. Read receipts are stored in the existing tool audit and the Agent workspace for subsequent inspection.

Engine writes include declared file/configuration, MCP, Skill package and Cron operations, with differences published by the frozen engine capability contract. OC/CC may use approved Shell when an interface is unavailable; TE has no Shell fallback. Runtime file changes do not claim to change product Desired State, asset permissions or activation records. Owner cooperation needed for those operations belongs in the complete plan.

## Runtime packages

Repair and self-healing use separate Skills and business adapters. Shared container code has one source under OCB `clawweb-skills/clawevolve-skills/clawevolve-repair/scripts/repair_runtime`; the existing packager includes it in each independent package. Professional references have one Repair source and are included in the self-healing package during packaging. Session reads and engine boundaries follow the shared investigation contract above.

Base owns package startup, the outer hard timeout and its three archives. Business wrappers keep their heartbeat, waiting and reporting protocol. Heartbeat and decision polling use 20 seconds. Credentials are removed from Base's archived task input and passed via a private file outside archive roots; business packages must not echo them into outputs.

## Persistence and delivery contracts

Creation stores the exact event/delivery identity and request fingerprint in the existing task. Same-key changed requests return 409. Initial and subsequent sends use the existing tool audit claims; an unknown outcome cannot authorize blind replay. Bot message idempotency remains in the tool ledger. Task result delivery uses compact pending references in the existing Task configuration.

Only transitions into `waiting_effect`, `completed`, `failed` or `canceled` create an immutable result snapshot. Owner waiting (including expiry), approvals, notifications, annotations and other phase changes do not generate callbacks. Repeated annotations after task completion do not create another final result.

New result snapshots include `intervention_status: none | performed | unknown`, independently of task status and recovery effect. The control plane derives it from the complete task-wide tool ledger, including previous executions and active calls. Controlled session/trajectory receipts must confirm the exact sent message; channel acceptance alone remains unknown. Successful environment execution receipts or existing validated environment-write reconciliation can confirm an intervention. Generic message reconciliation does not replace exact message-presence proof. Uncertain or partially failed writes remain unknown; verified non-execution and investigation alone do not count. Any confirmed intervention survives later failures. The computed projection is transient. New and historical tasks use the same ledger-based rules regardless of task creation time or storage format. The retired audit-coverage marker is ignored when reading existing data and is no longer written. Historical delivery metadata without matching audit proof still remains unknown. No Agent output, model call or table is introduced; Debug is none. The monitoring receiver accepts this field optionally and retains it in the existing result-history JSON, preserving old payloads and immutable snapshots without changing diagnosis decisions.

`ce_tasks.config_json.resultDelivery` stores each pending snapshot's result version, object key, digest, attempt count and retry time, without its full payload. These references are saved in the same database transaction as the business state and Step report references. Phase reports retain snapshot references and successful delivery events. No record or result-delivery table is added.

A separate 20-second reporting loop retries saved versions in order without blocking state transitions or deadline handling. A failed first result delays only that task's later results. Success removes the corresponding pending reference and appends an acknowledgment to the phase report atomically. Resuming a task and generating later results cannot replace unacknowledged snapshots; restarting the service uses the same persisted references. Monitoring deduplicates by task ID and result version. Callbacks never repeat the initial message or plan actions.

Database definitions use the existing shared migration registry. Version 141 is reserved without altering shared columns; the unshipped v142 result-table migration is removed. This change requires no new table or column expansion. Existing widened columns are not shrunk. Legacy task JSON is converted on subsequent updates, including pending result delivery; legacy progress-only snapshots are excluded from callbacks. SQLite runs canonical migrations; MySQL/ZDAS provisioning follows the existing external migration process. Requests perform no DDL. The API contract is `session-recovery-api.md` in this directory.

## Manual termination

Owners and administrators can stop a running, Owner-waiting or effect-waiting recovery task through the existing task page. The service first persists `canceled`, the actor and reason, clears waiting/retry deadlines and rejects further Agent operations. The existing execution service invalidates the workload ticket; the AIS executor stops the current job. Task and execution records remain available through the same Task/Step storage.

The termination response and task detail distinguish `remote_stopped`, `remote_stop_failed` and `stop_pending`. Repeating termination retries only the remote stop and its audit, without repeating business actions or final result delivery. An AIS job ID received after cancellation is stopped and recorded against that exact execution. Late reports cannot revive the task. Already dispatched operations are not rolled back; interrupted write receipts remain unknown until independently verified.

Cancellation generates one terminal result with `status: canceled`, the existing effect/evidence and the stop reason. It does not fabricate a recovery claim or a repair failure. The monitoring receiver accepts this status using the same deduplication and delivery contract. The manual stop follows the task's existing control-plane ownership guard.

## Optional group notifications

Group delivery is a backend after-hook for saved recovery-message receipts, complete plans, execution summaries and terminal task results. It does not add Agent tools, output fields, model calls or waiting stages. Group notification events are omitted from the Agent bootstrap context.

One application-wide `ce_app_config` row, `session_recovery_notifications`, controls this channel. The existing `enabled` column is the switch. Its JSON value contains the default `webhook`, `timeout_seconds` (integer 1–60, normally 10), and optional `bot_webhooks` entries with `bot_id`, `entity_id` and `webhook`. Each entry must match both the task's exact Bot ID and Owner/entity ID; this keeps different Owners' `default` Bots separate. A match replaces the default webhook for that notification; unmatched Bots retain the default group. The legacy two-field configuration is unchanged. Duplicate target pairs and malformed overrides invalidate the configuration rather than silently sending to a different group. A selected-group failure never falls back to the default group. Missing or disabled configuration means no group notification. An unavailable config table, invalid configuration, timeout or rejected robot response is isolated from task execution. No control-plane environment selector, administrator list or mentions are added. Routing changes only the group destination; trigger points, formatting, notification claims and the independent Owner-notification channel are unchanged. Deploy all backends consuming this configuration before writing overrides: an older strict parser rejects the additional field.

The notification formatter uses existing plan, action and judgment fields and retains the task link. Message notices show the saved operation purpose and delivery-call state; the complete diagnosis and recovery message remain on the task page. Terminal notices show recorded message purposes and business summaries of audited plan actions, map `judgment.delivery` and `judgment.response` into verification lines, and display `judgment.effect` separately as the recovery conclusion. They do not copy the long `judgment.summary`, list unexecuted plan actions as interventions, or infer recovery from task completion. Failed or canceled tasks additionally retain their ending reason. Only `recovered` receives a fixed green font tag; actual rendering must be checked in the DingTalk client.

The backend owns the Markdown title and bold `【】` section labels. Complete field values are redacted before being parsed with the existing Markdown parser into plain text, excluding fenced commands, HTML and images, and escaped for display. Presentation limits preserve complete Unicode characters and sentences; an oversized single sentence is replaced by a task-page reference, never a partial command or `[TRUNCATED]`. Each value and list is bounded individually so no final byte slice can swallow the recovery conclusion or task link. Agent output contracts, original Markdown and the Owner page remain unchanged.

The Webhook transport posts a Markdown message directly and checks both HTTP status and the robot result. Actual Webhooks stay in application configuration and are never passed to the Agent or saved in task evidence. Existing Owner approval notifications remain unchanged.

Notification claims and outcomes append to the existing phase timeline under the Task repository lock. The source event ID and notification kind identify a single notification attempt across repeated callbacks and workers. These facts do not increment the business-state version or enqueue an upstream task result. The after-hook performs no automatic resend of an uncertain delivery and never repeats a Bot message or repair action. A process interrupted during notification may leave an unconfirmed attempt; this optional channel does not introduce a durable message queue.

## Validation boundary

Local tests use real temporary SQLite, real session JSONL and the actual relay parsing program. Bot, AIS, model and DingTalk services are controlled fixtures. Independently packaged imports, MCP catalogs, business continuation, timeout recovery, callbacks and UI decisions are tested locally. Real Bot behavior, deployed AIS and notification delivery require the subsequent acceptance run.

## 模块与执行代码

完整 Repair 与会话自愈同属 ClawInsight 的 Repair 模块：

```text
server/services/repair/
├── common/
│   ├── agent-execution.ts、agent-execution-monitor.ts  # 执行票据、租约、心跳和外层状态核对
│   └── agent-tools/                                 # 共用受控运行操作、日志与证据合同
├── full-repair/                                     # 完整 Repair 的业务状态与方案
└── session-recovery/                                # 自愈业务状态、效果判断与方案通知
server/repositories/repair/
├── agent-tool-call-repository.ts                    # 共用幂等审计和执行租约，沿用 ce_repair_tool_calls
├── repair-repository.ts                             # 完整 Repair 的 Task / Step 转换
└── session-recovery-task-repository.ts               # 自愈任务与时间线，使用现有 Task 扩展 JSON
server/services/notifications/dingtalk-sender.ts      # 治理与修复共用的钉钉投递能力
```

公共工具不决定修复流程或批准方案；具体业务校验阶段、冻结目标和已批准操作后，调用同一工具实现。业务记录在各自任务内，操作回执进入共用审计账本。通知的鉴权、Token 缓存和投递只有一份实现；治理与自愈分别生成业务内容。内部 Host 只装配具体 Provider、轨迹读取器和运行配置。

容器端公共执行代码由 OCB 的 `clawweb-skills/clawevolve-skills/clawevolve-repair/scripts/repair_runtime/` 维护，打包时放入两种 Skill 包；Repair 与自愈分别保留自己的 Skill 和业务主流程。专业 references 在 Repair 源目录维护，打包时交付给自愈包。没有安装到目标 Bot 的自愈手册，也没有后端直接发送的默认重试分支。

Execution-scoped updates validate the execution identity inside each retried atomic change. Audit requests retain redacted business text and a canonical request digest, so a stable request ID cannot hide different advice or uploaded content. Debug follows assessment too: it records proposed advice and the decision, or a proposed environment plan, without sending messages or changing the environment.

## Execution boundaries

Agent actions bind the approved business operation and resource, derived from a reference adapter request or structured `operationScope: { operation, target? }`. For example, `cron.delete` with a cron ID cannot change to `file.delete` or another cron. Every Agent action needs this structured scope or reference request before approval; request content and auxiliary parameters may change within it. Repair and recovery share the same Agent-facing interface allowlist: MCP and user Skill reads remain available, while their mutations require Owner action through the product workflow. Cron management uses declared adapter operations, never a Shell fallback, native engine CLI/RPC or scheduling-file edits. Ordinary-file and other authorized environment actions remain available. Shared guards reject explicit managed paths and known command/interface bypasses; they do not claim to infer the complete semantics of arbitrary Shell programs.

Pre-dispatch failures are `failed` with `dispatched: false`, and do not block a later attempt. After a possible dispatch, an uncertain outcome stays `unknown`. `reconcile_write` requires the original call ID, an `applied` or `not_applied` conclusion, and successful subsequent read calls from the current execution. It appends an audit record, preserves the original unknown record, permits continuation after verification, and never automatically replays an operation. Contradictory reconciliation is rejected.


## Owner notification switch and linked task visibility

Owner DingTalk notifications use the independent `ce_app_config` key
`session_recovery_owner_notifications`. Its existing `enabled` column controls
sending; `config_json` is `{}`. Missing or disabled configuration skips Owner
notifications without recording a successful send. Enable the row to resume
notifications on subsequent notification events; skipped notifications are not
replayed. This does not affect group webhooks (`session_recovery_notifications`),
Agent execution, plan approval, or Owner feedback through the task page.

Administrators can list all tasks linked to a diagnosis event, including private
tasks. Other users still see only their own tasks or tasks explicitly shared by
the Owner. Task detail and operation authorization remain unchanged.
