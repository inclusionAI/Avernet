# ClawInsight session recovery

Self-healing is a ClawInsight business workflow. Every task starts an independent Agent to assess the diagnosis and current evidence, choose a targeted message, observe ongoing progress, or form an environment repair plan. If advice does not resolve the original problem, the Agent submits one complete environment repair plan. Owner cooperation belongs to that plan. The service controls authorization, stores the execution record and reports task progress to monitoring.

## Existing architecture

- `SessionRecoveryTaskService` owns assess/observe/plan/apply/verify, Owner decisions and separate Owner/effect deadlines. Initial message attempts use the existing audited tool ledger.
- `SessionRecoveryAgentService` uses existing `BotRuntime`, target resolution, AIS dispatch, workload tickets and `RepairRepository` tool audit. No independent MIST client, Bot connection stack or database is added.
- `AgentExecutionService` and the execution monitor serve both Repair and self-healing. Business services keep their own state transitions. Base callbacks use a trusted, injected handler; a fixed execution Step retains its own attachment contract after business stages advance.
- OCB composes internal dependencies and the existing `DingTalkSender`; business notification content is injected into its shared sender. Public services do not select BaaS versus legacy Arca.

## State and evidence

Tasks use existing `ce_tasks` and `ce_steps`. Self-healing fields, immutable timeline events, judgment, Owner decisions and manual annotations live in task extension JSON. Full bounded source reads and operation receipts live in the existing tool audit; the timeline and judgments refer to their IDs. The Owner page loads full evidence on demand. Evidence redaction retains diagnostic message text while removing credentials and PII.

Task status and recovery effect are separate. A channel accepting a message is not delivery evidence. Only a marked user message in the original session proves delivery. Agent response and recovery require semantic judgment with stored evidence; environmental write success does not prove recovery.

Original-session matching accepts Engine-added metadata and timestamp prefixes. Bounded reads retain all matching record IDs, including repeated records, so delivery evidence does not conceal lower-layer duplication. The fixed read-only program is compressed for the existing BaaS command limit after runtime-user wrapping.

Debug performs no actual message or environment mutation. Approval binds one complete plan, rather than individual commands; automatic mode still waits for necessary Owner cooperation. Environment writes pass the existing audit ledger and enforce the current execution and approved plan. Agent-selected conversation messages do not require environmental approval, but remain audited writes with one initial delivery attempt, target validation and no blind replay of unknown outcomes.

A plan identifies the intended adjustment, scope, primary actions, risks, verification and Owner cooperation. Concrete Shell commands and Engine requests are optional implementation references. During execution, the Agent supplies actual commands or requests under an approved action ID, can complete several steps and adjust execution details from current evidence without changing the plan's direction or adding actions outside its scope. The service validates the current approval, execution, frozen target and declared engine capabilities, and audits every actual request and receipt; it does not require equality with reference commands or API payloads. A new request ID identifies a new step or adjusted attempt; the same ID still cannot identify different requests, and unknown write outcomes cannot be blindly replayed. The Owner page shows the readable plan and primary actions first, with technical references, basis and verification details collapsed by default.

Owner waiting and effect waiting have separate deadlines. Owner expiry releases the execution while preserving the business task. A later decision creates a new execution for that task; an in-window decision continues the current execution. Effect waiting resumes the paused phase after reading new records, without resending initial advice. Old execution reports cannot overwrite new execution state.

## Handbook and Agent assessment

Handbook content has one source: OCB `clawevolve-recovery-handbook/SKILL.md` and `mcp/handbook.md`. It is packaged into the AIS Agent workspace only, not installed in the target Bot. The public catalog retains category references without internal professional content. Unsupported categories have no generic-retry chapter; the Agent assesses them from the diagnosis and actual evidence.

There is no direct-intervention mode or category allowlist. Initial assessment cannot modify the environment. The Agent composes the advice; the original Bot receives that text, without being instructed to install or read the handbook. Initial messages use the upstream delivery key; stable tool retries return the saved receipt. Maintenance does not generate advice.

## Investigation and intervention

The shared `BotRuntime` connection uses existing service credentials and provider composition. Engine operations do not use the product OpenAPI or acquire user delegation. Controlled Engine reads and approved writes use a declared method/path contract. TE rejects Shell before contacting the provider.

OC/CC original-session reads prefer the existing read-only Base NAS mount. Frozen identity determines the Bot namespace; the original session must have an exact file mapping. Unavailable or ambiguous NAS reads fall back to controlled Shell file reads, not session APIs. CC requires an actual SDK session mapping. Both retain bounded original records and source references.

TE uses injected AIVision/TCLog trace and observation queries for investigation, with pagination and original uploaded inputs, outputs and tool records. The TE session API reached through the BaaS connection supplies original messages and current response. Missing, delayed or truncated uploaded data is not evidence of failure. Read receipts are stored in the existing tool audit and the Agent workspace for subsequent inspection.

Engine writes include declared file/configuration, MCP, Skill package and Cron operations, with differences published by the frozen engine capability contract. OC/CC may use approved Shell when an interface is unavailable; TE has no Shell fallback. Runtime file changes do not claim to change product Desired State, asset permissions or activation records. Owner cooperation needed for those operations belongs in the complete plan.

## Runtime packages

Repair and self-healing use separate Skills and business adapters. Shared container code has one source under OCB `clawweb-skills/runtime/agent_runtime`; the existing packager includes it in each independent package. Professional references have one Repair source and are included in the self-healing package during packaging. Session reads and engine boundaries follow the shared investigation contract above.

Base owns package startup, the outer hard timeout and its three archives. Business wrappers keep their heartbeat, waiting and reporting protocol. Heartbeat and decision polling use 20 seconds. Credentials are removed from Base's archived task input and passed via a private file outside archive roots; business packages must not echo them into outputs.

## Persistence and delivery contracts

Creation stores the exact event/delivery identity and request fingerprint in the existing task. Same-key changed requests return 409. Initial and subsequent sends use the existing tool audit claims; an unknown outcome cannot authorize blind replay. No separate delivery table is created.

Task result snapshots are saved before callbacks in the existing task extension. Callback failure retains the exact version for retry; successful acceptance adds a timeline event. Callbacks never repeat the initial message or plan actions. Monitoring deduplicates by task ID and result version.

Database definitions use the existing shared migration registry: v141 enlarging the existing task JSON column on managed databases. SQLite runs canonical migrations; MySQL/ZDAS provisioning follows the existing external migration process. Requests perform no DDL. The API contract is `session-recovery-api.md` in this directory.

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

容器端公共执行代码由 OCB 的 `clawweb-skills/runtime/agent_runtime/` 维护，打包时放入两种 Skill 包；Repair 与自愈分别保留自己的 Skill 和业务主流程。专业 references 在 Repair 源目录维护，打包时交付给自愈包。没有安装到目标 Bot 的自愈手册，也没有后端直接发送的默认重试分支。
