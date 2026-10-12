# task-cli 任务发起与澄清设计

任务确认后的 Bot 执行函数契约见 [`bot-s1-s8-functional-contract.md`](../2026-09-16-distributed-relay-execution/bot-s1-s8-functional-contract.md)。

Claude Code 与 Codex CLI 的官方交互模式调研见
[`2026-10-07-task-cli-claude-codex-cli-patterns.md`](../../../../docs/research/2026-10-07-task-cli-claude-codex-cli-patterns.md)。本文仅采用其可迁移的 CLI 交互原则；Task Intake 的领域契约仍以本文为准。

## 1. 目标

`task-cli` 提供统一任务发起入口，支持 Human、Bot 和协作群场景，通过同一个 intake 会话完成：

```text
目标执行者解析
-> 任务识别
-> 信息澄清
-> 用户确认
-> TaskInfoRequest 构造
-> TaskService.execute
-> 向 CLI 返回 task_id、node_id
-> 将根节点 task_id/node_id 随启动指令交给目标 Bot；Bot S1 拉取 TaskContext
```

## 2. Module 与 seam

### 2.1 CLI Adapter

CLI 只负责：

- 解析命令行参数；
- 调用 `TaskService.intake` 对外接口；
- 根据返回状态渲染问题、候选项、确认摘要和任务状态；
- 在交互模式下保存当前 `session_id`。
- 将交互结果渲染为问题、选择卡或确认卡，并回显已解析的 target；
- 在 `--json` / `--no-interactive` 模式输出稳定机器结果。

CLI 不负责：

- Bot 或 Group 选择策略；
- 从自然语言判断“确认”或“取消”；
- 任务字段完整性判断；
- `TaskInfoRequest` 构造；
- 直接调用 `TaskService.execute()`；CLI 只请求 HTTP `/start`；
- 生成 `task_id` 或 `node_id`。

### 2.2 TaskService 中的澄清接口

任务澄清与正式任务创建共用 `TaskService` 对外接口，但保持两个独立操作：

```python
class TaskService:
    async def intake(
        self,
        command: TaskIntakeCommand,
        context: TaskIntakeContext,
    ) -> TaskIntakeResult:
        ...

    async def execute(self, request: TaskInfoRequest) -> TaskOpResult:
        ...
```

`intake` 隐藏：

- 默认 Bot 解析；
- 权限校验；
- intake session 持久化；
- task-loop 识别与澄清调用；
- 草稿状态迁移；
- 确认幂等。

`CONFIRM` 只更新并返回 `TaskIntakeResult(CONFIRMED)`，不调用 `execute`。确认后的独立 HTTP `/start` 创建链路由应用层读取并校验已确认 Result、组装 `TaskInfoRequest`，再调用同一个 `TaskService.execute()`。HTTP Adapter 只做协议转换，不承担会话状态、任务创建幂等或执行策略决策。第一棒 `TaskContext` 投影与根节点派发由 Task Module 的图谱与 Runner 实现，澄清方法不接管执行编排。

### 2.3 内部 seams

```python
class TaskIntakeRepository(Protocol):
    def get(self, session_id: str) -> TaskIntakeResult: ...
    def create(self, result: TaskIntakeResult) -> TaskIntakeResult: ...
    def save(
        self, previous: TaskIntakeResult, updated: TaskIntakeResult,
    ) -> TaskIntakeResult: ...
```

`TaskIntakeRepository` 是 `TaskService` 实现内部的持久化依赖，不作为另一个对外澄清 Service。Production 使用 HTTP/Bot Runtime/Repository adapters；测试使用 in-memory adapters。

## 3. CLI interface

### 3.1 发起任务

```bash
task-cli "<一句话任务目标>"
```

显式指定 Bot：

```bash
task-cli "<一句话任务目标>" @<bot-ref> bot
```

显式指定 Group：

```bash
task-cli "<一句话任务目标>" @<group-ref> group
```

`bot` 与 `group` 互斥。CLI Adapter 将 `@<target-ref>` 在调用者可见范围内解析为稳定 ID，再构造 `IntakeTarget`；引用不存在或不唯一时请求用户重新选择。领域层只接收稳定 ID，不以展示名称作为身份。

### 3.2 恢复 intake 会话

恢复到交互模式：

```bash
task-cli resume <session-id>
```

`resume` 将用户指定的 `session_id` 填入内部 `TaskIntakeAction.RESUME`。CLI 和 Backend 按当前认证调用者校验该 session 的所有权；会话不存在或不属于调用者时拒绝恢复。

`RESUME` 调用本身只读，不创建任务，也不重放 `CONFIRM`。恢复后 CLI 回显 target、状态和 `TaskSpec` 摘要；若状态为 `CONFIRMED`，展示「继续提交/查看任务」操作，由该操作另行调用幂等 `/start`，请求只传 `session_id`。CLI 不凭 Intake Result 猜测任务是否已经创建：服务端若发现已有创建关联就返回原 `TaskOpResult`，否则创建一次。

### 3.3 会话内继续、选择、确认与取消

同一 CLI 进程会持有 active session；跨进程时先执行 `task-cli resume <session-id>`，随后仍进入同一种交互会话。进入会话后，用户不再输入 session ID。

```text
task-cli> 本次需要交付哪些具体产物？
user> 上线记录、监控看板链接和回滚预案

任务澄清确认
目标：@alice bot (bot-payment-diagnosis)
交付物：上线记录、监控看板链接、回滚预案
验收：...
[确认创建任务]  [取消]
user> 选择「确认创建任务」
```

CLI 根据当前 `TaskIntakeResult.status` 渲染对应控件，并在用户操作后构造内部 `TaskIntakeCommand`：

| 当前状态 | 用户可见操作 | CLI 内部 action |
| --- | --- | --- |
| `CLARIFYING` + TaskSpec 不完整 | 输入补充内容 | `CONTINUE` |
| `CLARIFYING` + TaskSpec 完整 | 选择确认创建任务或取消 | `CONFIRM` / `CANCEL` |
| `CONFIRMED` | 继续提交/查看任务 | 不再调用 Intake；单独 `POST /start {session_id}`，服务端返回原结果或创建一次 |

CLI 从 active session 自动填充 `session_id`，不生成或传递业务幂等键。确认和取消是确认卡的结构化选择，不能把用户自由文本“确认提交”“就这样吧”交给 LLM 猜测。

正常路径中，用户点击「确认创建任务」后，CLI 先发送 `CONFIRM`，收到 `TaskIntakeResult(CONFIRMED)` 再自动发送独立的 `POST /start {session_id}`。两次请求之间若中断，恢复会话展示「继续提交/查看任务」；此按钮不重复确认，只调用幂等的 start。

### 3.4 查询任务

```bash
task-cli get <task-id>
```

调用既有任务 dashboard interface，不属于 Intake Module。

### 3.5 交互与自动化输出

默认 TTY 模式可继续提问和展示确认卡。没有 TTY 或显式指定 `--no-interactive` 时，CLI 不得等待 stdin：

```bash
task-cli "<一句话任务目标>" --no-interactive --json
task-cli resume <session-id> --no-interactive --json
```

`--json` 直接输出完整 `TaskIntakeResult`。默认 TTY 同样展示 `session_id`、`owner_user_id`、`source_type` 与时间戳；自动化调用方据 `status` 判断下一动作，不解析人类文案。

```json
{
  "session_id": "tis-...",
  "owner_user_id": "user-...",
  "source_type": "api",
  "status": "CLARIFYING",
  "target": {"target_type": "BOT", "target_id": "bot-..."},
  "missing_fields": ["deliverables"],
  "questions": ["需要交付什么？"]
}
```

`--no-interactive` 遇到 `CLARIFYING` 时输出 `TaskIntakeResult` 并以非零退出码退出；调用方根据 `TaskSpec` 完整性决定继续澄清还是确认。`CONFIRMED` 与 `CANCELLED` 成功退出。确认后的独立 start 链路输出 `TaskOpResult`。HTTP/领域错误沿用既有错误码映射。

人类 CLI 默认输出必须回显最终绑定的稳定 target。例如默认解析或随机选择 Bot 后，都显示：`目标：@<display-name> bot (<stable-id>)`。后续同一 session 不得重新解析或重新随机选择。

当前不引入 `task-cli exec`：Task 创建有明确确认这个领域前置条件，不能把类似 Codex 的无人值守执行命令误导为会绕过确认。自动化调用使用 Task Intake HTTP API 的结构化 action；`--no-interactive --json` 只读取或启动一次 intake 并返回状态，缺少澄清或确认时必须由调用方显式处理。

## 4. 调用者与目标执行者

### 4.1 调用者字段

`TaskIntakeCommand` 不携带认证、会话控制或调用来源。HTTP Adapter 不把 Principal 透传进 Core，而是将已经校验的系统事实和本次请求控制信息收敛为一个应用层上下文：

```python
@dataclass(frozen=True)
class TaskIntakeContext:
    # 当前 intake 会话；START 时为空，由 Task Intake 创建后返回。
    session_id: str | None

    # 已认证调用者所代表的用户；用于 intake 所有权和正式任务 owner。
    owner_user_id: str

    # 当前调用来源；在 START 固化到 TaskIntakeResult。
    source_type: TaskSourceType

    # 仅 Bot 主动发起时存在；用于未指定目标时选择调用 Bot 自身。
    caller_bot_id: str | None = None
```

这不是新的认证领域模型，也不替代既有 `VerifiedCaller`；它是 Task Intake 应用边界所需的最小上下文。HTTP Adapter 复用既有 `caller_owner_id(principal)` 解析 `owner_user_id`，从 CLI/HTTP 会话控制信息取得 `session_id`，并从既有调用上下文取得 `caller_bot_id` 与 `source_type`，再构造 `TaskIntakeContext`：

```text
HTTP 已认证 Principal
  -> caller_owner_id(principal) -> owner_user_id
  + CLI/HTTP 会话控制信息 -> session_id
  + 既有调用上下文 -> caller_bot_id / source_type
  -> TaskIntakeContext(...)
  -> TaskService.intake(command, context)
```

字段用途：

- `owner_user_id`：创建 `TaskIntakeResult.owner_user_id`，并校验 `RESUME`、`CONTINUE`、`CONFIRM`、`CANCEL` 的 session owner；确认时写入 `TaskInfoRequest.owner_user_id`。
- `caller_bot_id`：仅在 Bot 身份且未显式指定目标时作为默认 target；Human 调用时为 `None`。
- `source_type`：仅在 `START` 时写入 `TaskIntakeResult.source_type`；确认时从 result 写入 `TaskInfoRequest.source_type`。

三个字段均不来自 CLI 请求体，不由 Skill/LLM 生成或覆盖。

### 4.2 IntakeTarget

```python
class IntakeTargetType(StrEnum):
    BOT = "BOT"
    GROUP = "GROUP"


@dataclass(frozen=True)
class IntakeTarget:
    target_type: IntakeTargetType
    target_id: str
```

Target 在 intake session 创建后不可变。切换目标必须取消当前 session 并重新发起，避免不同 Bot 的识别结果混入同一草稿。

### 4.3 Human 默认目标解析

Human 未显式指定 target 时按下列顺序解析：

1. 当前用户下恰好一个名称与用户展示名完全匹配且可运行的 Bot；
2. 用户配置的 `default_bot_id`；
3. 根据一句话任务目标匹配可访问 Bot 的能力，选择最匹配且可运行的 Bot；
4. 若没有需求匹配结果，从调用者可访问且可运行的 Bot 中随机选择一个；
5. 若没有任何可用 Bot，提示用户创建或启用 Bot。

随机选择必须将最终选中的稳定 Bot ID 写入 `TaskIntakeResult.target`，后续澄清、确认和审计均以该 ID 为准；不得在同一 session 中重新抽取。

如果没有任何可用 Bot，返回 `NO_AVAILABLE_TARGET` 并提示用户创建或启用 Bot。

### 4.4 Bot 默认目标解析

Bot 身份调用：

```bash
task-cli "<一句话任务目标>"
```

默认 target 为调用 Bot 自身。第一版不允许 Bot 通过 `@xxx bot` 或 `@xxx group` 指向其他目标；跨 Bot 接力由 Relay S1-S6 协议完成。

### 4.5 Group 目标

显式 Group 必须满足：

- 调用者对 Group 可见且有发起权限；
- Group 状态可运行；
- Group 存在唯一 manager/master；
- manager/master 是 intake recognition 和后续 Relay 的 holder。

## 5. Intake 任务规格与对象职责

确认前不得使用 `TaskInfo`。`TaskInfo` 含正式 `task_id`，而 intake session 在确认前没有任务实体。

确认前的任务内容直接复用既有 `TaskSpec`，不新增草稿规格模型。`TaskSpec` 是任务纯业务规格，不包含 `task_id`、调用者、目标执行者或运行配置；其字段承载关系如下：

```python
TaskSpec(
    context=Context(
        title="",
        background="",
        extend_props={
            "deliverables": [],
            "constraints": [],
            "resources": [],
        },
    ),
    goal=Goal(
        objective="",
        acceptances=[AcceptanceCriteria(id="ac1", description="")],
    ),
)
```

完整性约束：

```text
TaskSpec.goal.objective 非空
TaskSpec.context.extend_props.deliverables 非空
TaskSpec.goal.acceptances 非空
constraints 已明确；允许 []，但必须经过用户确认
```

识别阶段首次写入验收项时，由 Task Intake Module 分配稳定 ID（例如 `ac1`）；后续澄清更新同一验收项时必须保留该 ID。

对象职责和数据关系：

```text
TaskIntakeResult
  任务澄清唯一的持久化状态对象，也是 TaskService.intake() 的返回对象
  └── 表示任务识别与澄清的当前结果；只用于恢复、补充与完整性判断

TaskInfoRequest
  完整 TaskIntakeResult 经用户确认后由 HTTP /start 应用层转换为 TaskService.execute() 的创建命令
  └── 由 TaskIntakeResult + 服务端策略组装

TaskOpResult
  TaskService.execute() 创建正式任务的结果
  └── 唯一承载 task_id、node_id
```

## 6. Intake command

### 6.1 Command 类型

```python
class TaskIntakeAction(StrEnum):
    START = "START"
    RESUME = "RESUME"
    CONTINUE = "CONTINUE"
    CONFIRM = "CONFIRM"
    CANCEL = "CANCEL"
```

### 6.2 Command 对象

```python
@dataclass(frozen=True)
class TaskIntakeCommand:
    action: TaskIntakeAction
    message: str | None = None
    requested_target: IntakeTarget | None = None
```

`TaskIntakeCommand` 的边界是“调用者本次希望 Intake 做什么”，而不是“谁在调用”、在哪个会话中调用，或“最终任务归谁”。它只包含用户意图：`message`、显式目标请求和结构化 action；最终 `TaskIntakeResult.target` 仍由服务端解析并固化。字段来源固定如下：

```text
用户输入
  任务文本 / 澄清文本 / @bot 或 @group / 确认或取消卡片操作
    -> Adapter
    -> TaskIntakeCommand

认证与调用链解析
  Principal + 调用入口 + CLI/HTTP 控制信息
    -> Adapter
    -> TaskIntakeContext

TaskService.intake
  TaskIntakeCommand + TaskIntakeContext
    -> TaskIntakeResult.target + TaskIntakeResult.task_spec
```

| 字段 | 填充者 | 规则 |
| --- | --- | --- |
| `message` | 用户 | 一句话任务目标或澄清补充内容；由 CLI 原样传递。 |
| `requested_target` | 用户 + CLI / HTTP Adapter | 用户显式写 `@bot` / `@group`；Adapter 在调用者可见范围内解析为稳定 ID。未显式指定时为空，由服务端根据 `TaskIntakeContext` 解析默认 target。 |
| `action` | CLI / HTTP Adapter | CLI 根据启动、补充、选择、确认、取消或恢复等结构化交互动作填写；不得从自由文本猜测确认或取消。 |

HTTP 请求体直接反序列化为 `TaskIntakeCommand`；HTTP Adapter 完成相同的 target 解析和 action 字段校验，并构造 `TaskIntakeContext`。领域服务接收稳定 ID、已校验的 command 和已认证的 intake context。

`session_id` 位于 `TaskIntakeContext`，不是 command 字段；它是服务端生成的会话续接标识，不是每次动作的幂等键。字段有效性由 `action` 决定：

| action | 必填字段 |
| --- | --- |
| `START` | `message`；`requested_target` 可选 |
| `RESUME` | `context.session_id` |
| `CONTINUE` | `context.session_id`、`message` |
| `CONFIRM` / `CANCEL` | `context.session_id` |

### 6.3 会话标识生成与传递责任

`session_id` 是控制面会话标识，不是用户输入、`TaskSpec` 字段或 Skill 推理结果。

| 字段 | 首次设置者 | 设置时机 | 后续传递与重试 | 默认 TTY 用户可见性 |
| --- | --- | --- | --- | --- |
| `session_id` | Task Intake Module | 成功处理 `START` 时，创建并持久化 `TaskIntakeResult` 前 | 写入同一 `TaskIntakeResult` 返回给 CLI；CLI 在 active session 中自动原样携带。跨进程恢复时，作为 `task-cli resume <session-id>` 的参数。 | 正常会话内不展示；会话暂停提示和 JSON/API 可显示，供用户跨进程复制恢复。 |

伪代码：

```python
# CLI Adapter：正常会话内自动携带 session_id；仅 resume 由用户指定。
def send_user_action(action, *, active_session_id, **fields):
    command = TaskIntakeCommand(
        action=action,
        **fields,
    )
    context = TaskIntakeContext(
        session_id=active_session_id,
        owner_user_id=authenticated_owner_id(),
        source_type=resolved_source_type(),
        caller_bot_id=resolved_caller_bot_id(),
    )
    return post_intake(command, context)


```

`START` 的完整 Backend 处理见第 8 节。`session_id` 标识跨多轮澄清的业务会话，并作为确认后任务创建的唯一持久关联。它不能区分同一会话内两次 `CONTINUE` 是否为重试；CLI 对 `START/CONTINUE` 不做盲目自动重放。响应丢失时，`CONTINUE` 先 `RESUME` 读取最新结果；`START` 尚未取得 `session_id` 时无法严格判定是否创建成功，必须把结果标为未知，不能直接声称再次发送必然返回同一会话。

## 7. Intake result

### 7.1 状态

```python
class TaskIntakeStatus(StrEnum):
    CLARIFYING = "CLARIFYING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
```

### 7.2 Result 对象

```python
@dataclass
class TaskIntakeResult:
    session_id: str
    owner_user_id: str
    source_type: TaskSourceType
    status: TaskIntakeStatus
    target: IntakeTarget | None = None
    task_spec: TaskSpec | None = None
    missing_fields: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    create_time_ms: int = 0
    update_time_ms: int = 0
```

`TaskIntakeResult` 是任务澄清唯一的持久化状态对象，也是所有 `TaskService.intake()` 动作的直接返回值；包括 `CONFIRM`。所有 Result 字段均可返回给用户。字段有效性由 `status` 约束：

```text
CLARIFYING -> 尚未由用户确认；TaskSpec 可完整或不完整
CLARIFYING + TaskSpec 不完整 -> missing_fields、questions 非空
CLARIFYING + TaskSpec 完整 -> target、task_spec 非空；展示确认卡
CONFIRMED -> target、task_spec 非空；代表用户已确认该完整 TaskSpec，不代表正式任务已创建
CANCELLED -> 不创建 TaskInfo 和 TaskExecutionGraph
```

`TaskIntakeResult` 的完备性只由 `task_spec` 是否满足完整性约束决定，与是否已创建 `TaskInfo`、是否生成 `task_id/node_id` 无关。它不持有也不返回这两个标识。`TaskIntakeResult` 持久化在 Backend；CLI 退出不丢失澄清状态。

## 8. `TaskService.intake()` 处理伪代码

输入固定为 `TaskIntakeCommand + TaskIntakeContext`，输出固定为 `TaskIntakeResult`；非法输入、无可用目标、无权限和状态冲突以领域错误返回，不伪造一个成功 Result。以下 `resolve_target`、`intake_skill`、`missing_fields` 和 `intake_repository` 是 Service 的内部依赖/函数，不新增领域对象。`intake_skill` 只生成或修订 `TaskSpec` 与追问文案，不能设置 owner、source、target、状态或任务 ID。

```python
async def intake(
    self,
    command: TaskIntakeCommand,
    context: TaskIntakeContext,
) -> TaskIntakeResult:
    require_nonempty(context.owner_user_id)
    validate_action_fields(command, context)  # 按第 6.2 节校验必填与禁止字段

    if command.action == TaskIntakeAction.START:
        require(context.session_id is None)
        # 显式目标先做可见性/运行状态/发起权限校验；未指定时执行第 4 节
        # 的 Human/Bot 默认规则。无可用 Bot 抛 NO_AVAILABLE_TARGET。
        target = self.resolve_target(command.requested_target, command.message, context)
        spec, proposed_questions = await self.intake_skill.recognize(
            message=command.message,
            target=target,
        )
        spec = assign_stable_acceptance_ids(spec)
        missing = missing_fields(spec)
        now = now_ms()
        result = TaskIntakeResult(
            session_id=new_session_id(),
            owner_user_id=context.owner_user_id,
            source_type=context.source_type,
            status=TaskIntakeStatus.CLARIFYING,
            target=target,
            task_spec=spec,
            missing_fields=missing,
            questions=questions_for(missing, proposed_questions),
            create_time_ms=now,
            update_time_ms=now,
        )
        # 原子创建会话；没有动作标识时无法识别响应丢失后的重复 START。
        return self.intake_repository.create(result)

    session = self.intake_repository.get(context.session_id)
    require(session.owner_user_id == context.owner_user_id)  # 否则拒绝访问

    if command.action == TaskIntakeAction.RESUME:
        return session  # 不重新识别、不更新时间、不创建任务

    if command.action == TaskIntakeAction.CONFIRM:
        if session.status == TaskIntakeStatus.CONFIRMED:
            return session  # 按会话状态幂等确认
        require(session.status == TaskIntakeStatus.CLARIFYING, conflict=409)
        require(session.target is not None and session.task_spec is not None)
        require(missing_fields(session.task_spec) == [])
        updated = replace(
            session,
            status=TaskIntakeStatus.CONFIRMED,
            missing_fields=[],
            questions=[],
            update_time_ms=now_ms(),
        )
        # 只持久化 Result 状态；不调用 start，不构造 TaskInfoRequest。
        return self.intake_repository.save(session, updated)

    if command.action == TaskIntakeAction.CANCEL and session.status == TaskIntakeStatus.CANCELLED:
        return session  # 按会话状态幂等取消

    require(session.status == TaskIntakeStatus.CLARIFYING, conflict=409)
    # 正式任务已按此 session_id 创建后，不允许继续或取消澄清。
    require(not self.task_exists_for_intake(session.session_id), conflict=409)

    if command.action == TaskIntakeAction.CANCEL:
        updated = replace(session, status=TaskIntakeStatus.CANCELLED, update_time_ms=now_ms())
        return self.intake_repository.save(session, updated)

    require(command.action == TaskIntakeAction.CONTINUE)
    spec, proposed_questions = await self.intake_skill.clarify(
        existing_spec=session.task_spec,
        message=command.message,
        target=session.target,
    )
    # Skill 返回修订后的完整 TaskSpec，不另定义 patch 领域对象；未提到的
    # 字段沿用旧值，不能清空用户已经确认过的上下文。
    spec = preserve_acceptance_ids(session.task_spec, spec)
    missing = missing_fields(spec)
    updated = replace(
        session,
        task_spec=spec,
        missing_fields=missing,
        questions=questions_for(missing, proposed_questions),
        update_time_ms=now_ms(),
    )
    # 仅保存用户澄清后的业务规格；保留 owner/source/target/session ID。
    return self.intake_repository.save(session, updated)
```

`missing_fields(spec)` 必须同时校验 objective、deliverables、acceptances 和 constraints 是否**已明确**：`constraints` 键缺失表示未澄清；键存在且值为 `[]` 表示用户明确无约束。Skill 不能在用户尚未回答时预填 `constraints=[]` 来绕过确认。`questions_for` 在缺项为空时返回 `[]`，有缺项但 Skill 未给追问时必须根据缺项生成追问；CLI 据完整性展示确认卡，状态仍是 `CLARIFYING`。

`create/save` 是内部持久化操作；`save` 必须以读取时的会话版本做条件更新，防止两个并发修改互相覆盖。版本仅在 Backend 内部保存，不进入请求领域对象。持久化失败必须抛错，不能返回未保存的 Result。`CONFIRM/CANCEL` 可按终态返回已保存的 Result；`START/CONTINUE` 不承诺仅凭相同文本严格识别重试。`intake()` 永远不隐式调用 `TaskService.execute()`。

## 9. 状态迁移

```text
START
  -> CLARIFYING

任意已持久化 session
  --RESUME----------> 保持原 status；只返回当前 TaskIntakeResult，不写状态、不创建任务

CLARIFYING
  --CONTINUE-------> CLARIFYING
  --CONFIRM--------> CONFIRMED（前置：TaskSpec 完整）
  --CANCEL---------> CANCELLED

CONFIRMED
  --再次 CONFIRM---> 返回同一 TaskIntakeResult，不重复更新
  --CONTINUE/CANCEL--> 409；正式任务由独立 start 链路创建或重试

CANCELLED
  --任何写命令--> 409
```

非法 command 与当前 session 状态不匹配时返回 `409`，不得猜测调用方意图。

## 10. 确认与 start

### 10.1 组装 TaskInfoRequest

```python
def build_task_info_request(
    result: TaskIntakeResult,
) -> TaskInfoRequest:
    ...
```

该函数位于确认后的任务创建链路，不是 `TaskService.intake(CONFIRM)` 的内部实现。`target_resolver` 与 `execution_policy` 是该创建链路的内部依赖，不是调用方传入的领域包装对象。

```python
def build_task_info_request(result: TaskIntakeResult) -> TaskInfoRequest:
    require(result.status == TaskIntakeStatus.CONFIRMED)
    require(result.task_spec is not None)
    require(result.target is not None)

    if result.target.target_type == IntakeTargetType.BOT:
        owner_bot_id = result.target.target_id
    else:
        # 校验 Group 仍可运行、调用者仍有发起权限，并取得唯一 manager/master Bot。
        owner_bot_id = target_resolver.resolve_group_manager_bot_id(
            result.target.target_id,
            result.owner_user_id,
        )

    return TaskInfoRequest(
        # 同一份已确认的 TaskSpec，不重新解析、不复制、不由 LLM 重写。
        task_spec=result.task_spec,
        source_type=result.source_type,
        owner_user_id=result.owner_user_id,
        owner_bot_id=owner_bot_id,
        execution_config={
            **execution_policy.for_new_task(),
            "session_id": result.session_id,
            "initial_target": {
                "target_type": result.target.target_type.value,
                "target_id": result.target.target_id,
            },
        },
    )
```

转换期间不改变 `result.task_spec`、`result.target`、`result.owner_user_id` 或 `result.source_type`。`TaskService.execute()` 成功后也不回写 `TaskIntakeResult`：任务标识只存在于该次 `TaskOpResult` 和 Task Module 内部持久化的创建幂等关联中。

映射规则：

| 权威来源 | TaskInfoRequest |
| --- | --- |
| `result.task_spec` | `task_spec`；直接传递同一份已确认的 `TaskSpec` |
| `result.source_type` | `source_type`；Human task-cli 为 `API`，Bot 为 `BOT`，协作群为 `COOP_GROUP` |
| `result.target` 经服务端解析 | `owner_bot_id`；Group 解析为其 manager/master Bot ID，仅表示归属 Bot |
| `result.target` 原始类型与 ID | `execution_config.initial_target`；冻结首棒实际执行目标，Group 不得退化为 manager Bot |
| `result.owner_user_id` | `owner_user_id` |
| 服务端任务运行策略 | `execution_config` |
| `result.session_id` | `execution_config.session_id`，作为创建幂等键 |

目标设计将 `TaskInfoRequest.task_spec` 收敛为 `TaskSpec`，删除与其语义重复的 `RequestContext`、`RequestGoal`、`RequestAcceptance` 和 `RequestTaskSpec`。现有实现仍使用这些请求规格类型，实施时须同步完成该合同迁移。

### 10.2 确认后调用 `TaskService.execute()`

`CONFIRM` 只更新并持久化 `TaskIntakeResult.status=CONFIRMED`，不创建任务、不构造 `TaskInfoRequest`，也不写入 `task_id/node_id`。确认后的独立 HTTP `/start` 请求**只传 `session_id`**；应用层据此读取并校验已确认 Result，组装内部 `TaskInfoRequest` 并调用 `TaskService.execute()`。创建链路直接返回既有 `TaskOpResult`，不新增包装对象。intake 来源的创建必须在同一持久化事务中提交 `TaskInfo`、图谱根节点、`session_id` 唯一创建关联及根节点投递意图；任一步失败都不返回成功，Backend 随后异步唤起首棒。

处理顺序：

```text
校验 session owner
-> 校验 status == CLARIFYING 且 TaskSpec 完整
-> 持久化 TaskIntakeResult(status=CONFIRMED)
-> 返回 TaskIntakeResult

独立任务创建链路：
  CLI/HTTP 调用 POST /start，body 仅为 {session_id}
-> Backend 按认证用户读取 TaskIntakeResult(status=CONFIRMED)
-> TaskIntakeResult 与服务端执行策略组装为 TaskInfoRequest
-> TaskService.execute(TaskInfoRequest)
-> 同事务提交 TaskInfo、图谱根节点、session_id 唯一创建关联和待投递意图
-> 获得 TaskOpResult(task_id, node_id)
-> 直接返回 TaskOpResult

Backend 首棒启动分支（由 start 原子登记的意图驱动）：
  认领待投递意图，读取已创建的权威根节点
  -> TaskDispatcher.dispatch(root_node)
     -> 接力派发策略内部调用 TaskRunner.start_run(root_node)
  -> 投递含 task_id/node_id 的指令
  -> Bot S1 调节点级 context API
```

`TaskService.execute(TaskInfoRequest)` 是唯一正式任务创建入口，沿用当前方法名，不新增同构的 `TaskService.start()`。HTTP `/start` 的应用层先从 `session_id` 读取已确认 Result 并构造请求；它不能把未经重构的 `execute()` 误认为幂等创建，因为当前实现先插入 TaskInfo、再初始化图谱，尚无 session 唯一创建关联。增强 `execute()` 的可靠提交能力后，intake 来源受已确认 session 的唯一创建约束，既有调用方仍使用同一创建方法。成功的 `TaskOpResult` 必须直接提供 `node_id`。当前 `TaskOpResult` 只有 `task_id`、`success`、`error`、`run_id` 和 `extend_props`，因此实现时需要增加 `node_id: str | None`；Relay 根节点创建成功后必须返回 `node_id == task_id`，不能要求调用方从 `extend_props.root_node_id` 猜测。`execute()` 返回任务已创建，不等于投递已接纳或首棒已完成。

创建幂等关联不能在 `TaskInfo` 和图谱落盘后另起事务补写：这会在响应丢失时留下“任务已创建但 session 无法定位”的窗口。当前实现的 TaskInfo repository 写入与图谱初始化是两个调用，不能把现有顺序误称为同一事务。目标实现优先让 TaskInfo、图谱根节点、`UNIQUE(session_id)` 关联和投递意图共享一个持久化事务/提交边界；若当前存储层做不到，必须先提交经评审的可恢复提交协议及故障注入证明，再开放 `/start`，不能仅靠事后扫描猜测重复任务。

第一棒上下文由 Task Module 在根节点持久化后，根据已持久化的 `TaskInfo` 和根 `TaskNode` 投影；Bot S1 经节点级接口读取，确认处理不得根据 `TaskInfoRequest` 手工拼装上下文。

`execute()` 从已冻结的 `execution_config.initial_target` 初始化根节点：Bot 目标为 `run_mode=single_bot, assignee=target_id`；Group 目标为 `run_mode=coop_group, assignee=target_id`。`owner_bot_id` 仍是归属 Bot，不能代替 Group 执行者；Group 的唯一 manager/master Bot 作为 Relay 协调执行者完成 S1–S5 与上报，S3 只匹配其在群运行时真实可调用的能力，S4 才可内部协调群成员。`source_type` 表示发起来源，不能用来推断目标类型。首棒、后续棒与 harness 恢复均使用同一节点级 API 与 Task Module 上下文投影逻辑；派发指令不是权威上下文。

确认成功约束：

```text
task_id == node_id
task_context.task_id == task_id
task_context.node_id == node_id
task_context.root_task_spec == task_context.current_task_spec
task_context.all_done_output == []
```

`task_context`、`task_id`、`node_id` 都不进入 `TaskIntakeResult`。任务发起调用方从 `TaskOpResult` 取得任务标识；目标 Bot 从启动指令取得 `task_id/node_id`，S1 调节点级 context API 读取 `TaskContext`。harness 重试也调用同一接口。

## 11. 确认幂等与崩溃恢复

仅保存 `TaskIntakeResult` 不足以避免确认后的崩溃窗口重复创建任务。Task Module 必须建立 intake session 与任务创建的内部持久关联：

```text
UNIQUE(session_id)
```

`TaskInfoRequest.execution_config.session_id` 必须进入 TaskInfo 持久化。TaskService 在创建任务的同一事务中写入具有 `UNIQUE(session_id)` 约束的幂等记录；命中已有记录时返回原 `TaskOpResult`，不能再次创建 TaskInfo 或根节点。幂等元数据属于执行控制面，不写入业务 `TaskSpec`。

一旦该内部关联存在，Task Intake 拒绝该 `session_id` 的 `CONTINUE` 和 `CANCEL`；它不把“已提交”状态、`task_id` 或 `node_id` 反写到 `TaskIntakeResult`。因此 intake 结果始终只回答“这份 TaskSpec 是否完备”，而任务是否已创建只由 Task Module 的幂等关联和 `TaskOpResult` 表示。

重试与恢复规则：

- `RESUME` 只读；再次 `CONFIRM/CANCEL` 同一对应终态 session 返回已保存的 `TaskIntakeResult`；并发冲突由 Backend 的会话版本条件更新处理；
- `START/CONTINUE` 不具有严格的动作级去重保证：响应丢失时不可仅凭相同文本盲目重发。已知 `session_id` 的 `CONTINUE` 先 `RESUME` 检查最新规格；尚未取得 `session_id` 的 `START` 结果为未知，不能声称再次发送一定返回原会话；
- 对同一 CONFIRMED Result 重试 HTTP `/start`：通过 session 创建关联返回同一 `TaskOpResult`，不重复创建；
- start 成功后进程崩溃：通过 `session_id` 命中已创建任务并返回同一 `TaskOpResult`；
- 不允许第二次生成新 `task_id`。

## 12. HTTP Service API

### 12.1 路径

```http
POST /api/v1/collaboration/tasks/intake
POST /api/v1/collaboration/tasks/start
```

### 12.2 请求对象

```python
TaskIntakeCommand
```

HTTP 请求体只按 `TaskIntakeCommand` 的字段反序列化。`session_id` 由 CLI/HTTP 的会话控制元数据传递，不进入请求体；`START` 时为空。Adapter 负责 HTTP 格式校验、target 解析和按 `action` 校验字段有效性，并从认证/调用上下文取得 `owner_user_id`、`caller_bot_id`、`source_type`，统一构造 `TaskIntakeContext` 后调用 `TaskService.intake()`。请求不要求动作幂等键。

独立 `/start` 请求体只有 `{"session_id": "..."}`。Backend 应用层按认证用户校验该会话属于调用者且状态为 `CONFIRMED`，从已保存的 `TaskIntakeResult` 和服务端策略构造 `TaskInfoRequest`；不接受客户端提供 `task_spec`、owner、target 或 `execution_config`。内部调用 `TaskService.execute(TaskInfoRequest)`；HTTP Adapter 只映射协议，不负责业务校验、构造策略或事务编排。

### 12.3 响应对象

所有 intake action（包括 `CONFIRM`）的 HTTP 响应 `data` 都直接序列化 `TaskIntakeResult`；它不包含任务标识。独立的 `TaskService.execute()` 调用直接返回 `TaskOpResult`。不定义同构 Response DTO 或包装结果：

```python
TaskIntakeResult
```

若现有 HTTP Envelope 自动附带请求追踪 ID，它只用于日志关联，不作为 Intake 的请求字段或幂等依据。字段有效性由 action 与 intake status 校验；Adapter 只做 JSON 与既有领域对象之间的序列化/反序列化。

`CONFIRM` 的 `TaskIntakeResult` 示例：

```json
{
  "code": 200000,
  "message": "OK",
  "data": {
    "session_id": "tis_01HXYZ",
    "owner_user_id": "user-alice",
    "source_type": "api",
    "status": "CONFIRMED",
    "target": {"target_type": "BOT", "target_id": "bot-risk-owner"},
    "missing_fields": [],
    "questions": []
  }
}
```

## 13. 完整调用流程

```text
Human/Bot
  -> task-cli "<一句话任务目标>"
  -> POST /api/v1/collaboration/tasks/intake action=START
  -> TaskService.intake
  -> CLARIFYING

Human
  -> 当前 task-cli 交互会话中补充或取消
  -> CLI 按当前 status 构造 CONTINUE / CANCEL action
  -> POST /api/v1/collaboration/tasks/intake
  -> TaskService.intake
  -> 更新持久化 TaskIntakeResult
  -> CLARIFYING；TaskSpec 完整时展示确认卡

Human/Bot
  -> 当前 task-cli 确认卡中选择「确认创建任务」
  -> CLI 构造 POST /api/v1/collaboration/tasks/intake action=CONFIRM
  -> TaskService.intake
  -> TaskIntakeResult(CONFIRMED)

Task 创建链路
  -> CLI POST /api/v1/collaboration/tasks/start，body={session_id}
  -> Backend 读取并校验 TaskIntakeResult(CONFIRMED)
  -> build_task_info_request
  -> TaskService.execute(TaskInfoRequest)
  -> TaskOpResult(task_id, node_id)
  -> Backend 认领 start 同事务登记的根节点投递意图，直接调用 TaskDispatcher.dispatch(root_node)
     -> 接力派发策略内部通过 Runner 投递 task_id/node_id
  -> CLI 获得 task_id、node_id

目标 Bot
  -> 从根节点启动指令获得 task_id/node_id
  -> S1 调节点级 context API 获得 TaskContext
  -> Bot 进入 S1–S5.5；命中后继执行者时 Skill 调 S6 /dispatch
  -> Backend 校验并投递；待投递意图供首棒唤起和失败恢复
```

### 13.1 分布式接力执行示例：支付失败峰值复盘

本例 HTTP Adapter 已从既有认证 Principal 解析出调用者字段：

```text
owner_user_id = "user-alice"
caller_bot_id = None
source_type = API
```

`user-alice` 可访问的可运行 Bot 中，`bot-payment-diagnosis` 的展示名与用户展示名均为 `alice`，因此命中默认解析规则 1；CLI 将稳定 ID 写入 session：

```python
IntakeTarget(
    target_type=IntakeTargetType.BOT,
    target_id="bot-payment-diagnosis",
)
```

用户不需要输入 `start` 或 target 参数：

```shell
$ task-cli "复盘昨晚支付失败率峰值，给出可验证根因、影响范围和处置建议"
```

CLI Adapter 构造并提交：

```python
TaskIntakeCommand(
    action=TaskIntakeAction.START,
    message="复盘昨晚支付失败率峰值，给出可验证根因、影响范围和处置建议",
)
```

`TaskService.intake` 持久化 `TaskIntakeResult(target=bot-payment-diagnosis)`。任务识别已确定目标和任务主题，但还缺少资源、约束和验收标准，因此返回：

```python
TaskIntakeResult(
    session_id="tis-pay-001",
    owner_user_id="user-alice",
    source_type=TaskSourceType.API,
    status=TaskIntakeStatus.CLARIFYING,
    target=IntakeTarget(BOT, "bot-payment-diagnosis"),
    task_spec=TaskSpec(
        context=Context(title="支付失败率峰值复盘"),
        goal=Goal(
            objective="复盘昨晚支付失败率峰值，给出可验证根因、影响范围和处置建议",
            acceptances=[],
        ),
    ),
    questions=["可使用哪些数据源？", "需要交付什么？", "如何验收？"],
)
```

用户补充澄清内容；同一交互进程自动携带 `tis-pay-001`，跨进程则使用 `task-cli resume tis-pay-001` 回到该会话。Intake Skill 补全 `TaskSpec` 后，Result 仍为 `CLARIFYING`，但由完整性校验形成确认卡片；其 `task_spec` 为：

```python
TaskSpec(
    context=Context(
        title="支付失败率峰值复盘",
        extend_props={
            "deliverables": ["证据清单", "影响范围", "处置建议"],
            "constraints": ["只使用已授权的日志和指标数据"],
            "resources": ["支付日志", "支付指标看板"],
        },
    ),
    goal=Goal(
        objective="复盘昨晚支付失败率峰值，给出可验证根因、影响范围和处置建议",
        acceptances=[
            AcceptanceCriteria("ac1", "给出失败率峰值的时间范围和影响订单范围"),
            AcceptanceCriteria("ac2", "给出至少一条可复核的根因证据"),
            AcceptanceCriteria("ac3", "给出按优先级排序的处置建议"),
        ],
    ),
)
```

用户在确认卡片中选择「确认创建任务」。CLI 从 active session 读取 `tis-pay-001` 并构造 `CONFIRM` action，用户不输入 session ID。

确认、创建与第一棒派发：

```text
TaskIntakeCommand(CONFIRM)
+ TaskIntakeContext(
    session_id="tis-pay-001",
    owner_user_id="user-alice",
    source_type=API,
  )
+ TaskIntakeResult(CLARIFYING, target=bot-payment-diagnosis, task_spec=完整)
  -> TaskService.intake(...)
  -> TaskIntakeResult(CONFIRMED, target=bot-payment-diagnosis)
  -> 独立任务创建链路读取该 CONFIRMED Result
  -> build_task_info_request(...)
  -> TaskInfoRequest(
       owner_bot_id="bot-payment-diagnosis",
       owner_user_id="user-alice",
       task_spec=TaskSpec(
         acceptances=[ac1, ac2, ac3],
       ),
       execution_config={"session_id":"tis-pay-001"},
     )
  -> TaskService.execute(...)
  -> TaskOpResult(task_id="task-pay-001", node_id="task-pay-001")
  -> Backend 消费根节点投递意图，向目标 Bot 交付 task_id + node_id
  -> Bot S1 调节点级 context API，取得 TaskContext
```

第一棒 `bot-payment-diagnosis` 只具备日志、指标分析能力，不具备编制处置方案的能力。Backend 首棒投递直接调用内部 `TaskDispatcher.dispatch(root_node)`；接力派发策略在内部调用 `TaskRunner.start_run(root_node)`，只交付节点标识，不交付权威 `TaskContext`：

```text
S1  GET /tasks/task-pay-001/nodes/task-pay-001/context
      -> TaskContext(root_task_spec=TaskSpec(ac1, ac2, ac3),
                     current_task_spec=TaskSpec(ac1, ac2, ac3), all_done_output=[])

S2  TaskContext.root_task_spec + []
      -> task_gaps = ["ac1：影响范围", "ac2：根因证据", "ac3：处置建议"]

S3  current_task_spec + task_gaps + BotCapability(日志、指标)
      -> CapabilityMatchResult(
           ACCEPTED,
           actual_goal=Goal("定位峰值影响范围和根因证据", [ac1, ac2])
         )

S4  TaskContext + CapabilityMatchResult
      -> TaskExecutor 执行
      -> TaskNodePatch(
           task_id="task-pay-001", node_id="task-pay-001",
           actual_goal=Goal(..., [ac1, ac2]),
           output_patch={"peak_window":"21:15-21:42", "affected_orders":1248,
                         "evidence":["issuer_timeout 指标较基线 +320%"]},
           local_acceptance_result=AcceptanceResult(DONE, done_items=[ac1, ac2]),
           execution_decision=ACCEPTED,
         )
      -> TaskReportCommand(
           event_type=EXECUTION_RESULT,
           event=TaskNodePatch,
         )
      -> TaskReportResult(APPLIED)

S5.1  TaskContext.all_done_output + 当前 TaskNodePatch
      -> 临时 DoneOutput(ac1, ac2 的事实)
      -> (task_gaps = ["ac3：按优先级排序的处置建议"],
          task_acceptance_result = None)

S5.2  TaskContext + [ac3] + TaskNodePatch
      -> next_node=TaskNode(node_id=None, status=None, node_run_graph=None,
           task_spec=TaskSpec(goal=Goal("基于已验证证据制定支付失败处置建议", [ac3])),
           run_info=RuntimeInfo())

S5.3  query = next_node.task_spec.goal.objective.strip()
      -> TaskService.search_bots(query, task_id="task-pay-001", node_id="task-pay-001")
      -> POST /search；task_id/node_id 只用于归属当前棒搜索轨迹
      -> candidates: list[BotCandidate]

S5.4  next_node + candidates: list[BotCandidate]
      -> SearchResult(outcome=HIT_SINGLE, bot_id="bot-incident-response")
      -> next_node.run_info.run_mode="single_bot"
      -> next_node.run_info.assignee="bot-incident-response"
      -> 输出仍是同一个 next_node，不另建 selected_node

S5.5  TaskService.report(TaskReportCommand(
          task_id="task-pay-001", node_id="task-pay-001",
          event_type=PLAN_RESULT, event=next_node))
      -> TaskReportResult(APPLIED, target_node_id="node-pay-002")
      # Backend 此时才创建节点并写入执行者决策；仅一次规划节点上报

S6  Skill 在 S5.5 接纳后 POST /api/v1/collaboration/tasks/dispatch
         {task_id="task-pay-001", target_node_id="node-pay-002"}
      -> Backend 认领待投递意图、校验身份
      -> TaskGraphService.get_node("task-pay-001", "node-pay-002") -> next_node（已落图）
      -> TaskDispatcher.dispatch(next_node) -> dispatched_node
         # 接力派发策略内部调用 TaskRunner.start_run(next_node)
         # Runner 未接纳则抛出投递失败，不返回成功节点
      -> 下一棒收到含 task_id/node_id 的接力 Prompt
      -> 下一棒 S1 从节点级 context API 获取 TaskContext
```

第二棒 `bot-incident-response` 具备故障处置和 runbook 编制能力：

```text
S1  以 task_id/node_id 调节点级 context API，取得 node-pay-002 的 TaskContext。
S2  root_task_spec + all_done_output -> task_gaps = ["ac3：按优先级排序的处置建议"]。
S3  -> CapabilityMatchResult(ACCEPTED, actual_goal=Goal(..., [ac3]))。
S4  -> TaskNodePatch(output_patch={"actions":["P0：限流", "P1：联系发卡行"]},
                     local_acceptance_result=AcceptanceResult(DONE, done_items=[ac3]))
      -> TaskReportResult(APPLIED)。
S5.1  历史 DoneOutput(ac1/ac2) + 当前 TaskNodePatch(ac3)
      -> task_gaps=[]，task_acceptance_result=AcceptanceResult(DONE, done_items=[ac1, ac2, ac3])。
S5.2～S5.4  无 gaps，跳过。
S5.5  -> TaskService.report(TaskReportCommand(
             task_id="task-pay-001", node_id="node-pay-002",
             event_type=PLAN_RESULT, event=None,
             task_acceptance_result=上述根任务验收))
      -> TaskReportResult(APPLIED, task_completed=True)。
S6  不执行。
```

若任一棒在 S5.2 有缺口却无法生成下一棒节点，`plan_next_node` 抛出 `PlanningUnavailable`；Runtime 捕获后以非空 `failure_reason=str(exc).strip()` 构造 `PLAN_RESULT(event=None, task_acceptance_result=None)`，调用同一个 `TaskService.report` 进入 HUNG。该失败原因不来自 `TaskContext` 或 S5.1；S5.3 搜索故障由 Runtime 重试，不走此分支。

用户查询最终任务：

```shell
$ task-cli get task-pay-001
task-pay-001  DONE
  task-pay-001  DONE     bot-payment-diagnosis
  node-pay-002  SUCCESS  bot-incident-response
```

本例未触发 harness 恢复。若第一棒在 S4 上报前后崩溃，Runner 以同一任务和节点标识恢复；正常执行与恢复均调用：

```text
GET /api/v1/collaboration/tasks/task-pay-001/nodes/task-pay-001/context
  -> TaskContext(task-pay-001, task-pay-001, root_task_spec,
                 current_task_spec, all_done_output=[])
  -> 重新进入 S1；上下文仍由同一投影逻辑构造。
```

同一根节点在 S4b 前后读取到的 `all_done_output` 都是 `[]`：它只投影**本棒之前**的已接纳事实，不包含本棒或后继节点。S1 同时从图谱投影 `accepted_execution_patch` 与 `accepted_plan_report`：前者非空则跳过本棒业务执行并以该 patch 继续 S5；后者非空则跳过 S2–S5，命中后继时直接恢复 S6。最终只持久化图谱中的节点执行事实和上报接纳记录，不持久化 `TaskContext` 或任务级 `task_gaps`。

## 14. 错误语义

| 条件 | HTTP | 错误码 |
| --- | --- | --- |
| session 不存在 | 404 | `TASK_INTAKE_NOT_FOUND` |
| session 不属于 caller | 403 | `TASK_INTAKE_FORBIDDEN` |
| command 与当前状态不匹配 | 409 | `TASK_INTAKE_STATE_CONFLICT` |
| target 不存在或不可访问 | 404/403 | `TASK_TARGET_NOT_FOUND` / `TASK_TARGET_FORBIDDEN` |
| 没有可用目标 | 409 | `NO_AVAILABLE_TARGET` |
| candidate ID 无效 | 409 | `TASK_INTAKE_CANDIDATE_INVALID` |
| READY 前确认 | 409 | `TASK_INTAKE_NOT_READY` |
| execute 失败 | 透传映射 | `TASK_EXECUTE_FAILED` |
| 并发修改同一 session | 409 | `TASK_INTAKE_STATE_CONFLICT` |

## 15. 安全约束

- 调用者身份来自认证上下文。
- session 所有读写都校验 `owner_user_id` 和 caller。
- 显式 Bot/Group target 必须校验可见性和发起权限。
- Bot 身份默认只能以自身为 target。
- `confirm` 只确认创建领域任务；它不授予 Bot 任意工具、目录、网络或资源访问权限。运行时权限必须由独立、可审计的执行策略决定；无人交互时缺少运行时许可默认拒绝。
- 日志不得记录完整用户会话、凭证或私有资源内容。
- `TaskSpec.context.extend_props["resources"]` 只保存用户明确提供的资源引用，不自动抓取内容。

## 16. 测试矩阵

### 16.1 Module interface tests

- Human 显式 Bot 发起并一次 READY；
- Human 默认同名 Bot 命中；
- Human 默认 Bot 命中；
- Human 按一句话目标匹配 Bot；
- 无需求匹配时随机选择可运行 Bot，并在 session 固化目标；
- 无目标返回 `NO_AVAILABLE_TARGET`；
- `resume <session-id>` 恢复指定的同一 session，不创建新任务；
- 跨进程 `resume` 后，补充、候选选择、确认和取消均在 active session 内完成，CLI 不再要求用户输入 session ID，也不暴露 reply/select/confirm/cancel 子命令；
- `--no-interactive --json` 在需要澄清或确认时返回结构化 status 并非零退出；
- Bot caller 自动使用自身；
- 多轮澄清保持同一 session 和 target；
- `START` 响应丢失且未取得 `session_id` 时报告结果未知，不自动重发并声称原会话被复用；
- `CONTINUE` 响应丢失时先 `RESUME` 读取最新 `TaskIntakeResult`，不自动再次调用 Skill；
- 同一 session 的并发 `CONTINUE` 由仓储条件更新拦截，不能互相覆盖；
- 重复 `CONFIRM` 或重复 `CANCEL` 返回对应已保存的终态 Result；
- CLARIFYING 阶段修改后仍为 CLARIFYING；确认卡是否展示由 TaskSpec 完整性重新推导；
- CONFIRM 只返回 `TaskIntakeResult(CONFIRMED)`；
- 对 CONFIRMED Result 的 start 重试返回同一任务；
- Human 指定 Group 时，`TaskInfoRequest.execution_config.initial_target` 保留 Group ID，根节点 `run_mode=coop_group/assignee=Group ID`；manager Bot 仅写入 `owner_bot_id`；Bot 目标则是 `single_bot/assignee=Bot ID`；
- CANCEL 后拒绝继续；
- 非 owner 访问 session 被拒绝。

### 16.2 Adapter conformance tests

- CLI 参数映射到正确 command；
- HTTP DTO 按 action 的字段校验；
- 正常确认后 CLI 自动 `POST /start {session_id}`；恢复 `CONFIRMED` 会话时 RESUME 只读，用户选择「继续提交/查看任务」后才以同一 session_id 调 start；HTTP 不接受客户端提交 `TaskInfoRequest`；
- Backend 从已确认 Result 构造 `TaskInfoRequest` 并调用 `TaskService.execute(TaskInfoRequest)`；
- start 返回的 `TaskOpResult.task_id == TaskOpResult.node_id`；
- 首棒、后续棒与 harness 恢复均经节点级 API 取得 `TaskContext`；派发指令只提供节点标识；
- 同一棒 S4b 前后读取的 `all_done_output` 完全相同且不含本棒，第二棒只看到前序棒事实；空 `output_patch={}` 仍投影为已接纳执行；
- S4b/S5.5 响应丢失后，S1 分别投影原 `accepted_execution_patch/accepted_plan_report` 并续跑，不能重执行业务或因重新规划产生第二个后继节点；
- 同节点 harness 重试递增服务端 attempt，当前 S1 不返回旧失败 patch，旧 Runner 会话的迟到 report 被拒绝；
- `start` 在事务故障注入下不留下缺少 `session_id` 唯一关联或缺少根节点投递意图的孤儿任务；
- Relay 首版 S5.4 拒绝 `HIT_GROUP`，`HIT_MULTI_BOTS` 只接受有唯一协调 Bot 的 `manager_worker` 组群；显式已有 Group 目标仍可作为根节点执行；Relay 轮次达到冻结 `max_rounds` 时不创建后继；
- BBS 报告仅确认发布意图已持久化；S6 `DELIVERING` 不返回成功节点；
- `TaskIntakeResult` 不暴露或转发 `TaskContext`。

## 17. 与现有实现的变更

| 当前对象/行为 | 调整 |
| --- | --- |
| 确认前返回 `TaskInfo` | 改为既有 `TaskSpec` |
| `message="确认提交"` 触发确认 | 改为 `action=CONFIRM` |
| 多套 intake action/result 派生类型 | intake 动作复用 `TaskIntakeCommand` 与 `TaskIntakeResult`；CONFIRM 返回 `TaskIntakeResult(CONFIRMED)` |
| 旧 `intake_session_id` 命名 | 在 Intake Context/Result、CLI、HTTP 控制面、`TaskInfoRequest.execution_config` 与任务创建唯一关联中统一为 `session_id`；实施时迁移或兼容读取旧持久化会话 |
| 每次动作的请求级幂等键 | 从 Intake 领域合同与 CLI 自动生成逻辑中删除；以服务端状态/条件更新处理可判定的重试，明确 `START/CONTINUE` 的未知结果窗口 |
| 未匹配需求 Bot 时的目标选择 | 从可运行 Bot 中随机选择，并将稳定 ID 固化到 intake session |
| TaskIntakeResult 绑定任务标识 | TaskIntakeResult 只表示 TaskSpec 完备性；start 的 `TaskOpResult` 返回 task_id、node_id |
| 澄清与任务创建使用不同 Service | 统一为 `TaskService.intake` 与既有 `TaskService.execute` 两个独立操作；前者不隐式调用后者，HTTP `/start` 只是确认后的会话提交入口 |
| 确认后创建可能重复 | 以 `session_id` 建立唯一任务创建关联；`CONFIRM/CANCEL` 按会话终态幂等 |
| START/CONTINUE 响应丢失 | 不盲目自动重放；已知 `session_id` 时先恢复会话查看最新结果，未知时明确提示结果不确定 |
