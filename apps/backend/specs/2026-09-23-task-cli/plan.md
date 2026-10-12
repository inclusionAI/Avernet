# Plan: task-cli 与现有 Relay 执行链路重构

> Review draft，2026-10-12。依据 [design.md](design.md)、[end-to-end-relay-design.md](end-to-end-relay-design.md) 与 [Bot S1–S6 合同](../2026-09-16-distributed-relay-execution/bot-s1-s8-functional-contract.md)。本阶段仅写计划，不修改实现。项目尚无单独的 `spec.md`；上述已确认设计作为需求来源。

## Approach

保留现有 Relay 与中心化执行能力，按可独立验证的阶段迁移其接口与恢复语义；新增 task-cli 作为人身份发起入口。HTTP `/start` 只收 `session_id`，应用层加载已确认的 `TaskIntakeResult` 并构造 `TaskInfoRequest`，唯一正式创建方法仍是 `TaskService.execute(request)`。所有新行为先通过合同/回归测试，再切换 Relay Runtime；旧 `/execute` 和现有中心化调用保持兼容，避免两个模式互相影响。

## Review Decisions Before Implementation

1. **task-cli 载体（已确认）**：独立 Rust CLI，参考 `bcs-cli` 的人身份鉴权和结构化输出；不直接依赖 BCS 业务客户端。建议落在 `apps/task-cli/`，其 Cargo workspace 接入方式在阶段 7 按仓库现状确定。
2. **intake 的 `session_id` HTTP 承载（待确认）**：设计只规定不放进 `TaskIntakeCommand`，建议续接请求使用 `X-Task-Intake-Session-ID` 请求头；`START` 不传。`/start` 则按已确认合同放在唯一请求体字段中。需在第 1 阶段合同测试前定稿。
3. **可靠创建提交方式（待验证）**：优先验证同一 `transactional_orm_session()` 可覆盖 TaskInfo、根图、`session_id` 唯一关联和投递意图。若现有仓储不能共享同一事务，先评审可恢复提交协议和故障注入证据，不能放出 `/start`。
4. **动态建群开关（条件开放）**：首版 S5.4 禁止 `HIT_GROUP`；`HIT_MULTI_BOTS` 只有 BCS 稳定去重或可对账且唯一 manager 的 `manager_worker` 成立后才开放。否则先只交付 `HIT_SINGLE/MISS`，不伪称完整多 Bot 接力。

## Affected Components

- `apps/backend/src/agentclaw/community/core/task/domain/` — Intake 对象、TaskContext、TaskInfoRequest、TaskOpResult 和上报合同。
- `apps/backend/src/agentclaw/community/core/task/task_center/` — Intake、唯一 `execute` 创建入口、Relay 上报与查询。
- `apps/backend/src/agentclaw/community/core/task/task_context/` — 图谱写入、节点级投影和已接纳阶段恢复。
- `apps/backend/src/agentclaw/community/core/repository/implementations/task/` — Intake 仓储、共享事务、接纳与投递意图持久化。
- `apps/backend/src/agentclaw/community/core/task/task_dispatch/`、`task_runner/` — 模式策略与单节点投递原语。
- `apps/backend/src/agentclaw/community/adapters/http/task/` — `/intake`、`/start`、节点 context 与既有 search/report/dispatch 路由合同。
- `apps/backend/src/agentclaw/community/di/modules/task_module.py` — 按冻结 runtime profile 装配策略和后台恢复消费者。
- `apps/backend/src/agentclaw/community/core/task/task_runner/client/` — Relay 指令、S1–S6 调用约束与 Backend 生成的代次值传递；Bot 身份凭证由 Bot 本地调用工具读取。
- `apps/task-cli/`（独立 Rust CLI）— CLI 命令、HTTP 客户端、鉴权适配与交互渲染；不承担 Backend 的任务领域策略。
- `apps/backend/src/agentclaw/community/core/task/README.md`、相关 Service API 合同与测试 — 同步边界元数据和兼容说明。

## Data Model Changes

目标物理形态草案；先做第 0 阶段事务验证，再落最终迁移。`TaskIntakeResult` 仍是唯一 Intake 领域状态对象，以下是存储表，不新增同义领域包装类。

```sql
-- apps/backend/src/agentclaw/community/core/task/sql/2026_10_12_task_intake.sql (new)
CREATE TABLE task_intake_session (
  session_id VARCHAR(128) PRIMARY KEY,
  owner_user_id VARCHAR(256) NOT NULL,
  status VARCHAR(32) NOT NULL,
  result_json TEXT NOT NULL,
  version BIGINT NOT NULL DEFAULT 0
);
ALTER TABLE task_info ADD COLUMN intake_session_id VARCHAR(128) NULL;
CREATE UNIQUE INDEX uk_task_info_intake_session ON task_info(intake_session_id);
```

`intake_session_id` 仅供 Intake 来源的任务使用；已有 `/execute` 调用可为 `NULL`。TaskInfo、根节点和唯一 session 关联必须同提交。根/后继待投递意图与报告接纳记录须持久化并与对应图谱迁移同事务；复用现有 callback/graph 存储还是增表，由第 0 阶段仓储验证定稿，不能只存在进程内字典。

## API / Interface Changes

```python
# apps/backend/src/agentclaw/community/core/task/task_service_protocol.py:19
class TaskServiceProtocol(Protocol):
    async def execute(self, request: TaskInfoRequest) -> TaskOpResult: ...
    async def intake(self, command: TaskIntakeCommand,
                     context: TaskIntakeContext) -> TaskIntakeResult: ...
    def get_node_context(self, task_id: str, node_id: str) -> TaskContext: ...
    async def report(self, command: TaskReportCommand) -> TaskReportResult: ...
    async def search_bots(self, *, query: str,
                          task_id: str | None = None,
                          node_id: str | None = None) -> list[BotCandidate]: ...
```

不增加 `TaskService.start()`；既有调用方继续使用 `execute(TaskInfoRequest)`。文档对外统一称 `TaskService`；代码中已有的 `TaskServiceProtocol` 是架构要求的 Service API 声明与 DI 接口，须同步签名，但不另造一个同义业务入口。旧 `report_task_event`、`dispatch_task` 的消费方先迁移并通过兼容合同测试，再考虑删除旧方法。

```http
# apps/backend/src/agentclaw/community/adapters/http/task/router.py:80
POST /api/v1/collaboration/tasks/intake
POST /api/v1/collaboration/tasks/start
GET  /api/v1/collaboration/tasks/{task_id}/nodes/{node_id}/context
POST /api/v1/collaboration/tasks/callback/report
POST /api/v1/collaboration/tasks/dispatch
```

```jsonc
// POST /api/v1/collaboration/tasks/start → TaskOpResult
// request: only session_id; owner/spec/config from saved confirmed result
{"session_id": "tis_..."}
// response.data: {"task_id":"task_...","node_id":"task_...","success":true,"run_id":1}
```

```python
# apps/backend/src/agentclaw/community/core/task/task_dispatch/dispatcher.py:62
class TaskDispatcher:
    async def dispatch(self, node: TaskNode) -> TaskNode: ...

# apps/backend/src/agentclaw/community/core/task/task_runner/task_runner.py:56
class TaskRunner:
    async def start_run(self, node: TaskNode) -> bool: ...
```

旧批量入口只在迁移期作为外层逐节点适配；不把 Relay 搜索/选人重新放进 Dispatcher。中心化和 Relay 从任务图冻结的 `runtime_profile` 选择不同实现。

## Key Files & Functions / Implementation Sequence

### 0. 固定基线与验证存储能力

- 为现有 Relay 的创建、S1、两次上报、派发、BBS、harness 重试，以及中心化模式补可重复运行的回归用例；先记录现有合同，迁移期逐阶段运行。
- 验证 `DatabasePlugin.transactional_orm_session()`（`plugin_api/database.py:43`）、`TaskInfoRepository.insert()`（`task_info_repository.py:53`）与 `TaskGraphRepository.create_graph()`（`task_graph_repository.py:176`）能否使用同一事务；注入 TaskInfo/根图/意图各写入点失败。
- 验证当前 BCS 建群调用能否以稳定键去重或查询原建群结果；确定首版是否开放 `HIT_MULTI_BOTS`。

```python
# apps/backend/tests/community/core/task/task_center/test_execute_persist.py (extend)
def test_task_creation_rolls_back_every_root_fact_on_graph_failure(): ...
def test_existing_execute_callers_keep_their_result_contract(): ...
```

### 1. Intake 领域与会话接口

- 在 Task 模块定义 `TaskIntakeCommand/Context/Result`，实现 START、CONTINUE、RESUME、CONFIRM、CANCEL；保存唯一 Result，条件更新版本。CONFIRM 只冻结完整 TaskSpec，不建任务。
- HTTP Adapter 从既有认证 Principal 构造 Context；target 解析、owner 校验和澄清完整性由应用层负责。新增 `/intake` 合同测试：重试、并发更新、跨进程恢复、无目标、Group 原目标不丢失。

```python
# apps/backend/src/agentclaw/community/core/task/task_center/task_service.py:417
async def intake(command: TaskIntakeCommand,
                 context: TaskIntakeContext) -> TaskIntakeResult: ...
```

### 2. 可靠正式创建与首棒意图

- `/start` 应用处理只读取 `session_id`；校验 owner/CONFIRMED，构造内部 TaskInfoRequest，再调用唯一 `TaskService.execute`。未经重构的 `execute` 不能直接充当幂等实现。
- 增强 `execute`：同一 transaction 写 TaskInfo、唯一 session 关联、根图、根投递意图；重复 session 返回原 `TaskOpResult`。增加 `TaskOpResult.node_id`，成功时根节点等于 task_id。
- 根节点意图由 Backend 消费者恢复；不能依赖 CLI 收到响应后继续运行。非 Intake `/execute` 保持现有模式行为。

```diff
# apps/backend/src/agentclaw/community/core/task/domain/models.py:455
 class TaskOpResult:
     task_id: str
     success: bool
+    node_id: str | None = None
```

### 3. 节点级 TaskContext 与 S1 恢复投影

- 新增节点级 context 路由；第一棒、后继棒、harness 恢复使用同一投影。`all_done_output` 只含本棒之前沿 Relay 前驱链的已接纳事实；本棒结果单独投影为 `accepted_execution_patch/accepted_plan_report`。
- `TaskContext` 不持久化，任务级 gaps 不在图谱中保存；已有节点验收 `gap_items` 保留。投递 Prompt 只含 task_id/node_id，不复制权威上下文。

```diff
# apps/backend/src/agentclaw/community/core/task/domain/models.py:222
 class TaskContext:
+    task_id: str
+    node_id: str
+    root_task_spec: TaskSpec
+    current_task_spec: TaskSpec
     all_done_output: list[DoneOutput]
+    accepted_execution_patch: TaskNodePatch | None = None
+    accepted_plan_report: TaskReportResult | None = None
```

现有 `spec/gaps` 被中心化调用方使用；迁移期间先保留兼容投影，逐一迁移消费方后才从目标合同移除，不能只删字段使中心化流程中断。Relay 的节点级新合同从第一天起不读取任务级 `gaps`。

### 4. 统一上报与可恢复状态迁移

- 将 S4b `EXECUTION_RESULT` 与 S5.5 唯一 `PLAN_RESULT` 映射为统一 `TaskReportCommand → TaskReportResult`。S5.5 才落图并写已决定的执行者，不再走 Relay 的 `DISPATCH_RESULT` 第二次决策上报。
- 图谱迁移同事务保存接纳阶段、规范化内容指纹和原结果；相同内容重试返原结果，不同内容冲突。Harness 为真正新尝试递增 attempt；已接纳的响应丢失只从 S1 续跑。
- Backend 在首派前生成节点的 `relay_token`，Harness 对同一 `node_id` 重试时轮换；Bot 原样携带本次代次值。Bot 调用层为每次上报生成 `event_id` 并在该请求重试时复用。身份鉴权仍使用 Bot 现有 BCS 会话凭证，经 Backend Bot 认证链解析；不新增 Prompt 中的身份密钥，也不修改 BaaS/OpenClaw 插件。Bot 不提供 holder、graph_version 或 attempt。

```python
# apps/backend/src/agentclaw/community/core/task/task_center/task_service_relay.py:170
async def report(self, command: TaskReportCommand) -> TaskReportResult: ...

# apps/backend/src/agentclaw/community/core/task/task_context/task_graph_service.py:903
def apply_relay_plan_result(..., next_node: TaskNode | None,
                            task_acceptance_result: AcceptanceResult | None,
                            failure_reason: str | None) -> TaskNode | None: ...
```

### 5. 策略化派发、Runner 与可靠交接

- 在组合根注册 `centralized/default` 和 `relay/relay_handoff + relay_prompt` 策略；Relay 不装配 Planner。先保持中心化旧行为可回归，再替换统一单节点 `dispatch(node)` / `start_run(node)` 接口。
- 根节点消费者、S6 HTTP 请求与恢复消费者共用同一意图认领和派发核心。返回 TaskNode 只表示投递通道接纳；`DELIVERING` 返回处理中而非成功；失败可恢复。
- BBS 发布、动态建群和下一棒指令以目标节点 ID 派生稳定操作键或对账；没有真实投递后端时 Runner 返回失败，不能以 stub 日志冒充成功。

```yaml
# apps/backend/src/agentclaw/community/core/task/domain/models.py:166
orchestration_mode: relay
runtime_profile:
  dispatcher_strategy: relay_handoff
  runner_strategy: relay_prompt
  allowed_run_modes: [single_bot, coop_group, bbs]
# Relay 不包含 planner_strategy；centralized 保留 default planner。
```

### 6. Bot Skill S1–S6 与搜索合同

- Runtime 按节点 ID 拉取 Context；S2/S5.1 本地重算 task_gaps，S3 能力匹配，S4a 执行、S4b 上报；S5.2 生成未落图 TaskNode，S5.3 搜 Bot，S5.4 决策执行者，S5.5 一次上报，S6 HTTP 触发交接。
- `search_bots` 归一化返回 `list[BotCandidate]`；保留现有 HTTP 候选响应的兼容窗口。首版拒绝 `HIT_GROUP`；动态建群门槛见上。

### 7. task-cli 与人身份鉴权

- 实现默认目标、显式 Bot/Group、会话内补充/确认/取消、`resume <session-id>`、`get <task-id>`；CLI 不构造 TaskInfoRequest 或自行选择执行者。正常确认后自动 POST `/start`，恢复 CONFIRMED 时由用户选择「继续提交/查看任务」。
- 独立 Rust 包建议 `apps/task-cli/Cargo.toml`、`src/main.rs`、`src/auth.rs`、`src/client.rs` 和 `tests/cli_contract.rs`。参考 `apps/bcs/crates/tools/bcs-cli/src/main.rs:2197` 的人身份 OAuth 获取与结构化错误处理，但不依赖其 BCS 业务客户端，也不复制公司专属凭证或私有地址。Bot Runtime 则使用能解析 Bot 身份的可信链路，不沿用只标明 `task_loop` 的 HMAC 为唯一授权事实。
- `--json/--no-interactive` 输出稳定状态，缺少澄清或确认时不擅自提交任务。

```rust
// apps/task-cli/src/main.rs (new; CLI 仅发送用户意图及会话标识)
enum Command {
    Submit { goal: String, target: Option<String> },
    Resume { session_id: String },
    Get { task_id: String },
}
```

### 8. 端到端验收与发布

- 测 Human→Bot、Human→已有 Group、Bot 接力、MISS/BBS、同节点 harness 重试、响应丢失、并发 `/start`、重复 S6、中心化回归。故障注入覆盖每个提交点与外部投递超时。
- 新合同先灰度、保留旧路由/旧调用方兼容；观察重复创建、重复投递、丢棒、旧会话迟到上报。通过后再移除旧字段和两次上报路径，不在同一提交中大面积删除。

## Dependencies

- 后端优先复用现有 SQLAlchemy、FastAPI、`DatabasePlugin.transactional_orm_session()`；不预设新增 Python 包。
- Rust CLI 优先复用仓库已有 clap/reqwest/serde 版本与 `bcs-cli` 的认证扩展点模式；是否抽取中立共享认证库须先评估依赖边界，不让 task-cli 依赖 BCS 业务客户端，不复制内网凭证或硬编码地址。
- BCS 动态建群若无稳定幂等/对账能力，先禁用 Relay `HIT_MULTI_BOTS`，不为这个功能偷偷扩展 BCS 合同。

## Risks & Mitigations

- **跨仓储事务不共享**：已有 `TaskInfoRepository.insert` 与 `TaskGraphRepository.create_graph` 各自开会话。先验证共享事务/单仓储原子写；若不可行，停在第 0 阶段评审可恢复协议。
- **双模式行为回归**：现有 Dispatcher/Runner 是批量且 Dispatcher 不投递。保留中心化回归测试、用策略装配迁移，拒绝在入口堆模式 if/else。
- **旧 Bot/HTTP 消费方合同变化**：报告与派发字段会变化。先列调用方并提供迁移窗口，不允许旧 Skill 把新事件误上报为旧形态。
- **投递成功与业务完成混淆**：记录待投递意图，只有真实通道接纳才标记 DELIVERED；消费端对目标节点去重。
- **设计文档少量遗留项**：`design.md` 仍有旧 candidate/READY 错误语义和 Intake session_id HTTP 承载未定；第 1 阶段先修合同并补测试，不把旧文字当成新增首版功能。

## Alternatives Considered

- **新增 `TaskService.start(request)`**：与既有 `execute(request)` 同义，扩大 Service API；否决。HTTP `/start` 保留，核心创建只经 `execute`。
- **Backend 内 Python CLI**：会简化 Backend 模型复用，但与用户已选的独立 CLI 载体及参考 `bcs-cli` 的鉴权/分发方式不一致；首版不用。
- **HTTP Adapter 直接完成会话业务校验与构造**：违反薄 Adapter 约束；否决。应用层负责，Adapter 只映射协议与认证事实。
- **派发消息携带完整 TaskContext**：产生双权威来源与陈旧快照；否决。所有棒 S1 拉节点级 Context。
- **先实现动态 Group，再补去重**：超时可能重复建群；否决。先证明外部幂等/对账。
- **立即删除旧 Relay 路径**：无法保障现有功能；否决。合同迁移和回归先行。

## Rollout / Review Gates

```text
Gate A  现有 Relay/中心化回归基线 + 共享事务/外部去重可行性结论
Gate B  Intake + /start 幂等可靠创建 + 根节点自动启动
Gate C  节点级 Context + 报告接纳/恢复 + relay_token 代次栅栏
Gate D  单节点策略派发 + S1–S6 + CLI 端到端
Gate E  故障注入、旧调用方兼容、灰度观察通过后删除旧合同
```

Gate A/B/C/D 均须独立通过测试；任何阶段失败可继续运行既有 Relay/中心化路径。本文提交给用户 review 后才拆 `tasks.md`、编码，不自动进入下一阶段。

## Test Strategy

```python
# apps/backend/tests/community/core/task/task_center/test_relay_execution.py
def test_relay_previous_done_outputs_exclude_current_node(): ...
def test_execution_report_response_loss_resumes_from_accepted_patch(): ...
def test_plan_report_response_loss_reuses_target_node(): ...

# apps/backend/tests/community/core/task/task_center/test_execute_persist.py
def test_same_confirmed_session_creates_one_task_under_concurrency(): ...
def test_root_intent_survives_process_restart(): ...

# apps/backend/tests/community/adapters/http/task/test_router.py
def test_start_rejects_client_spec_owner_and_execution_config(): ...
def test_dispatch_in_progress_is_retryable_not_success(): ...
```

同时运行受影响的 Task module 单测、Service API/HTTP 合同测试、架构边界与配置测试，以及 singlebox 中现有 Relay 与中心化故事；Bcs 动态建群若开放，补 BCS 合同和 E2E。计划中的新文件需同步 `README.md` Context Boundary 元数据；源文件超过 1,000 行须按职责拆分，不能靠 allowlist 掩盖。
