# avernet-goal-cli 任务发起、批量提交与澄清设计

任务确认后的 Bot 接力执行函数契约见
[`bot-s1-s8-functional-contract.md`](../2026-09-16-distributed-relay-execution/bot-s1-s8-functional-contract.md)。

本文采用成熟 Agent CLI 的“命令单一语义、结构化输出、可恢复会话”原则，但 Task Intake、批量 Goal 和多目标展开的领域合同以本文为准。

> 目录名保留历史名称 `2026-09-23-task-cli`，产品和命令名称统一为
> `avernet-goal-cli`。旧 `task-cli` 命令和单目标字段合同不再作为兼容接口保留。

## 1. 目标与范围

`avernet-goal-cli` 是面向 Human 和第三方 Agent 的 Goal 提交入口，负责：

```text
解析可选 A2A 代理 Bot 和执行目标
-> 创建 Goal Intake Session
-> 任务识别与信息澄清
-> 用户或 Agent 明确确认
-> 按执行目标展开正式任务
-> 为每个目标创建独立 TaskExecutionGraph
-> 返回每个目标对应的 task_id、node_id
```

首版支持：

- 单个 Goal；
- 单个 Goal 指定一个或多个 `to`；
- 文件中批量提交多个 Goal；
- Human 直接发起；
- Human 授权名下 Bot 作为 A2A 权限代理；
- `relay` 与 `centralized` 两种执行模式；
- Human 与第三方 Agent 共用交互式会话控制器，并支持 Agent 的非交互单次状态读取与结构化输出。

首版不向 CLI 用户开放以下内部运行策略：

```text
dispatcher_strategy
runner_strategy
allowed_run_modes
```

CLI 不提供通用 `--runtime` 参数。Backend 根据执行模式、目标类型和服务端配置加载 Runtime Profile，调用者不能直接组合内部策略。

## 2. 统一领域语言与数量关系

### 2.1 Goal

Goal 是用户或 Agent 希望完成的一项任务目标。一个 Batch 文件可以包含多个 Goal；每个 Goal 独立识别、澄清、确认和恢复。

### 2.2 Goal Intake Session

Goal Intake Session 是“一个输入 Goal 面向一个执行目标”在正式创建任务之前的多轮识别与澄清会话，使用 `session_id` 标识。

同一输入 Goal 指定多个 `to` 时，CLI 先展开为多个 Target Execution；每个 Target Execution 分别创建 Intake Session、由自己的目标执行者参与澄清并单独确认。

### 2.3 Target Execution

Target Execution 是“一个输入 Goal 在一个目标上的独立澄清和执行实例”。每个 Target Execution 拥有：

- 独立 `session_id`；
- 独立澄清过程和确认后的 `TaskSpec`；
- 独立 `task_id`；
- 独立根 `node_id`；
- 独立 `TaskExecutionGraph`；
- 复用该目标完成澄清与确认的当前 Bot 会话执行根节点；
- 独立状态、输出、验收结果、gaps 和 Relay 接力链。

多个 Target Execution 只共享最初输入的 Goal 文本及公共 CLI 参数，不共享澄清结果、确认后的 `TaskSpec` 或任务图运行状态。

### 2.4 Runner 执行会话

Runner 执行会话是 Bot 或 Group 执行一个 `TaskNode` 的运行时会话。它不是 Goal Intake Session。

Relay 模式下，根节点复用现有确认会话；后继节点才可能创建更多 Runner 执行会话。

### 2.5 数量关系

对于一个 Goal 和 N 个最终解析出的目标：

```text
Goal 数量                 = 1
Goal Intake Session 数量  = N
Target Execution 数量     = N
TaskExecutionGraph 数量   = N
根 TaskNode 数量          = N
新增根节点 Runner 会话数量 = 0
```

对于包含 M 个 Goal 的 Batch：

```text
Goal Intake Session 数量 = Σ resolved_target_count(goal)
TaskExecutionGraph 数量  = Σ resolved_target_count(goal)
```

如果某个 Goal 没有显式 `to`，Backend 必须解析出一个默认目标；解析失败时该 Goal 不创建任务。

## 3. Module 与 seam

### 3.1 CLI Adapter

CLI Adapter 负责：

- 解析命令和 Batch 文件；
- 将 `bot:<ref>` 用户引用传给 Backend 解析；
- 调用 Task Intake 与 `/start` HTTP Service API；
- 在交互模式中保存当前 `session_id`；
- 渲染澄清问题和确认摘要；
- 在 Agent 模式输出稳定 JSON；
- 对 Batch 文件执行无副作用的本地 Schema 校验；
- 在进入 Backend Intake 前按 `Goal × target` 展开独立 Target Execution；
- 编排多个独立 Target Execution 的 Intake/Start 调用并聚合结果。

CLI Adapter 不负责：

- 将客户端 Bot 引用视为可信身份；
- Bot 权限判定；
- 默认目标选择策略；
- `TaskSpec` 完整性判断；
- `TaskInfoRequest` 构造；
- Runtime Profile 或内部执行策略选择；
- 生成 `session_id`、`task_id`、`node_id`；
- 把多个目标拼成一个多根任务图；
- 在 Batch 部分失败时回滚已成功创建的任务。

### 3.2 TaskService Service API

任务澄清和正式任务创建使用同一个 `TaskService` Module 的两个操作：

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

`TaskService.intake()` 隐藏：

- A2A 代理 Bot 与目标引用解析后的权限校验；
- 默认目标解析；
- Intake Session 持久化；
- Task Intake Skill 调用；
- `TaskSpec` 完整性校验；
- 状态迁移和确认幂等。

`TaskService.execute()` 仍是唯一正式任务创建入口。每个 Intake Session 只绑定一个 target；HTTP `/start` 读取一个已确认 `TaskIntakeResult`，构造一个 `TaskInfoRequest` 并调用一次 `execute()`。

### 3.3 内部 Repository seam

```python
class TaskIntakeRepository(Protocol):
    def get(self, session_id: str) -> TaskIntakeResult: ...
    def create(self, result: TaskIntakeResult) -> TaskIntakeResult: ...
    def save(
        self,
        previous: TaskIntakeResult,
        updated: TaskIntakeResult,
    ) -> TaskIntakeResult: ...
```

Repository 是 `TaskService` 实现内部依赖，不作为第二个对外澄清 Service。

## 4. CLI interface

每条命令必须只有一个明确动作，帮助信息必须说明输入、副作用、输出和恢复方式。第三方 Agent 不得依赖自然语言文档猜测文件格式。

### 4.1 单 Goal 提交

```bash
avernet-goal-cli "<任务目标>"
  [--from human | bot:<bot-ref>]
  [--to bot:<bot-ref>]...
  [--mode relay|centralized]
  [--output text|json]
  [--non-interactive]
```

形式化语义：

```text
根命令                   = 提交一个 Goal
--from                   = 可选 A2A 权限代理；默认 human 直连
重复 --to                = CLI 将同一 Goal 展开为多个独立“澄清 + 执行”实例
未设置 --to              = 使用 Backend 默认目标解析
--mode                   = 执行模式；默认 relay
--output json            = stdout 只输出一个 JSON 对象
--non-interactive        = 不等待人工输入，也不隐式确认
```

单目标：

```bash
avernet-goal-cli "分析支付失败率" --to bot:risk-bot
```

同一 Goal 多目标：

```bash
avernet-goal-cli "分析支付失败率" \
  --to bot:risk-bot-a \
  --to bot:risk-bot-b \
  --to bot:risk-bot-c
```

Human 授权名下 Bot 作为 A2A 权限代理：

```bash
avernet-goal-cli "分析支付失败率" \
  --from bot:my-assistant \
  --to bot:risk-bot-a \
  --to bot:risk-bot-b
```

`--from bot:...` 不表示 CLI 以 Bot Token 登录，也不表示该 Bot 负责澄清、执行或拥有任务。认证主体仍是当前 Human；Backend 验证该 Bot 属于或已授权给当前 Human，并以该 Bot 的 BCS 身份把 Intake 澄清消息通过 A2A `chat-async` 实际转发给目标 Bot。真正负责澄清与执行、并作为任务归属方的仍是每个 `--to` Bot。

### 4.2 恢复 Goal Intake Session

```bash
avernet-goal-cli resume <session-id>
  [--output text|json]
  [--non-interactive]
```

`resume` 只读取并恢复一个 Intake Session，不创建新 Session，不重放 `CONFIRM`，也不重复创建任务。

读取成功后，CLI 将该 `session_id` 重新绑定为 active session，并进入与首次 Goal 请求完全相同的会话控制器：

```text
CLARIFYING + TaskSpec 不完整
  -> 展示 questions
  -> 用户或 Agent 在当前进程输入普通补充内容
  -> CLI 内部构造 TaskIntakeAction.CONTINUE

CLARIFYING + TaskSpec 完整
  -> 展示确认摘要和确认/取消选择
  -> CLI 内部构造 TaskIntakeAction.CONFIRM 或 CANCEL

CONFIRMED
  -> 展示“继续提交/查看任务”
  -> 调用幂等 /start；不重复确认

CANCELLED
  -> 展示终态并退出，不允许继续输入
```

`CONTINUE`、`CONFIRM`、`CANCEL` 是 CLI 会话控制器与 Backend 之间的内部 action，不是公开子命令。首版不提供 `reply`、`confirm`、`cancel`，也不提供 `resume --message/--confirm/--cancel`。

第三方 Agent 需要继续会话时，执行 `resume <session-id>` 并像正常首次会话一样读取问题、发送普通回复、选择确认或取消。`--non-interactive` 是单次状态读取模式；它不会等待回复，也不能修改 Session，返回需要处理的状态后以退出码 `3` 结束。

### 4.3 查询任务

```bash
avernet-goal-cli get <task-id> [--output text|json]
```

`get` 查询一个 Target Execution 的任务 dashboard，不属于 Intake Module。

### 4.4 Batch 命令组

Batch 命令面固定为：

```bash
avernet-goal-cli batch schema  [--output json|yaml]
avernet-goal-cli batch example [--format yaml|json]
avernet-goal-cli batch validate --file <path|-> [--output text|json]
avernet-goal-cli batch submit   --file <path|-> [--output text|json] [--non-interactive]
```

命令语义：

| 命令 | 输入 | 副作用 | 输出 |
| --- | --- | --- | --- |
| `batch schema` | 无 | 无；不访问 Backend | 当前 CLI 支持的标准 JSON Schema |
| `batch example` | 无 | 无；不创建文件 | 一份可通过当前 Schema 的 YAML/JSON 示例 |
| `batch validate` | Batch 文件 | 无；不创建 Session 或 Task | 字段级校验结果和归一化摘要 |
| `batch submit` | 已声明版本的 Batch 文件 | 为每个 `Goal × target` 创建独立 Intake Session；各自确认后创建 Task | Goal、Target、Session 和 Task 的聚合结果 |

不提供语义模糊的：

```bash
avernet-goal-cli --file goals.yaml
```

第三方 Agent 的标准调用顺序：

```bash
avernet-goal-cli batch schema --output json
avernet-goal-cli batch example --format yaml
avernet-goal-cli batch validate --file goals.yaml --output json
avernet-goal-cli batch submit --file goals.yaml --non-interactive --output json
```

### 4.5 结构化输出约束

使用 `--output json` 时：

- stdout 只包含一个合法 JSON 对象；
- spinner、进度条、诊断日志和提示文本只能写入 stderr；
- 字段名、状态枚举和错误码是稳定合同；
- Agent 同时检查进程退出码和 JSON `status`；
- `--non-interactive` 只表示不等待输入，不等于自动确认。

建议退出码：

| 退出码 | 语义 |
| ---: | --- |
| `0` | 命令完成且全部成功 |
| `2` | CLI 参数或 Batch 文件无效 |
| `3` | 至少一个 Goal 需要澄清或确认 |
| `4` | 部分 Goal 或部分 Target 创建成功 |
| `5` | 认证或授权失败 |
| `6` | Gateway/Backend 暂时不可用 |
| `7` | 所有 Goal 均启动失败 |

## 5. Batch Goal 文件合同

### 5.1 标准 YAML

```yaml
schema_version: "1"

defaults:
  from: human
  mode: relay

goals:
  - id: goal1
    goal: 分析支付失败率
    from: bot:xx
    to:
      - bot:bot1

  - id: goal2
    goal: 分析退款失败率
    from: bot:yy
    to:
      - bot:bot1
      - bot:bot2

  - id: goal3
    goal: 输出支付风险分析报告
    from: bot:zz
    to:
      - bot:bot1
      - bot:bot2
      - bot:bot3
```

上述文件表示：

```text
3 个 Goal
6 个 Goal Intake Session
6 个 Target Execution
6 个 TaskExecutionGraph
6 个初始 Runner 执行会话
```

### 5.2 文件对象

```python
@dataclass(frozen=True)
class GoalBatchFile:
    schema_version: str
    goals: list[GoalBatchItem]
    defaults: GoalBatchDefaults | None = None


@dataclass(frozen=True)
class GoalBatchDefaults:
    from_ref: str | None = None
    mode: TaskExecutionMode | None = None


@dataclass(frozen=True)
class GoalBatchItem:
    id: str
    goal: str
    from_ref: str | None = None
    to: list[str] | None = None
    mode: TaskExecutionMode | None = None
```

这些是独立 Rust CLI 的文件输入模型，不进入 Backend Task 领域模型。CLI 将每个 `GoalBatchItem` 按 `to` 展开；每个目标转换成一次普通的单 target Goal Intake 调用。

### 5.3 字段规则

| 字段 | 必填 | 规则 |
| --- | ---: | --- |
| `schema_version` | 是 | 首版固定为字符串 `"1"`；不允许缺省猜测 |
| `defaults` | 否 | 只允许 `from`、`mode` |
| `goals` | 是 | 至少一个元素 |
| `goals[].id` | 是 | 文件内唯一；用于输入、错误和结果关联 |
| `goals[].goal` | 是 | 去除空白后非空 |
| `goals[].from` | 否 | `human` 或 `bot:<bot-ref>`；覆盖 defaults |
| `goals[].to` | 否 | 始终是数组；元素唯一；省略表示默认目标解析 |
| `goals[].mode` | 否 | `relay` 或 `centralized`；覆盖 defaults |

首版 `defaults` 不允许设置 `to`，避免一个默认目标集合意外放大到全部 Goal。

文件中不允许 `runtime`，未知字段必须报错。相同 Goal 的单目标也必须写成数组：

```yaml
to:
  - bot:risk-bot
```

不能写成：

```yaml
to: bot:risk-bot
```

### 5.4 引用语法

```text
SenderRef := human | bot:<bot-ref>
TargetRef := bot:<bot-ref>
Mode      := relay | centralized
```

解析 `bot:` 时只按第一个 `:` 分割，允许稳定引用本身包含 `:`。首版出现 `group:` 或其他类型前缀时必须在 CLI 本地校验阶段返回 `UNSUPPORTED_TARGET_TYPE`，不创建 Intake Session。

### 5.5 默认值和覆盖

```text
服务端/CLI 固定默认值
  < 文件 defaults
  < 单个 Goal 字段
```

固定默认值：

```text
from = human
mode = relay
to   = Backend 默认目标解析
```

### 5.6 校验顺序

`batch validate` 和 `batch submit` 必须在任何网络副作用前校验整个文件：

1. YAML/JSON 语法；
2. `schema_version`；
3. 未知字段；
4. `goals` 非空；
5. Goal `id` 非空且唯一；
6. Goal 文本非空；
7. `from`、`to`、`mode` 格式；
8. 同一 Goal 中 target 不重复；
9. defaults 应用后的归一化结果。

静态校验失败时，不创建任何 Intake Session。

字段错误使用稳定路径和错误码：

```json
{
  "valid": false,
  "errors": [
    {
      "path": "goals[1].to[1]",
      "code": "DUPLICATE_TARGET",
      "message": "target bot:bot1 is duplicated"
    }
  ]
}
```

### 5.7 Schema 自发现

`batch schema --output json` 输出 Draft 2020-12 JSON Schema，至少包含：

- `additionalProperties: false`；
- `schema_version` 常量 `"1"`；
- `goals.minItems = 1`；
- `to.uniqueItems = true`；
- sender/target pattern；
- mode enum；
- 示例。

Rust 文件输入类型、反序列化、JSON Schema、`batch example` 和 `batch validate` 必须来自同一份类型定义。Golden Test 必须证明：

```text
batch example 的输出
-> 能被当前 CLI 反序列化
-> 能通过 batch validate
```

## 6. 调用者与目标

### 6.1 可信调用上下文

```python
@dataclass(frozen=True)
class TaskIntakeContext:
    session_id: str | None
    owner_user_id: str
    source_type: TaskSourceType
    # 仅真正由 Bot Runtime 调用 Intake 时设置，不来自 CLI --from。
    caller_bot_id: str | None = None
```

字段来源：

```text
统一 CLI 认证取得 Human Principal
-> owner_user_id

可选 --from bot:<ref>
-> TaskIntakeCommand.requested_a2a_proxy_bot_ref
-> Backend 验证 Human 对该 Bot 的代理权
-> Backend 取得该 Bot 的 BCS 身份和运行时 token
-> 通过 BCS A2A chat-async 向 target 实际发送 Intake 消息
-> BCS 在发送入口校验 proxy Bot 到 target 的准入权限
-> 不改变 source_type、owner_bot_id 或目标澄清执行者

未指定 --from
-> Human 按自身权限直接访问 target

CLI 请求始终：
-> caller_bot_id=None + source_type=API
```

客户端提交的 A2A 代理 Bot 引用不是可信 `caller_bot_id`。`caller_bot_id` 只描述真正由 Bot Runtime 发起的调用；CLI 的 `--from` 是待校验的权限代理请求，两者不能混用。

### 6.2 A2A Intake 转发

`requested_a2a_proxy_bot_ref` 非空时，Intake Skill 不能继续使用 Human/OpenAPI 直连目标 Bot 的发送路径，必须复用现有 BCS A2A 能力：

```text
Human Principal
  -> 验证 proxy Bot 归属/代理权
  -> 解析 proxy Bot BCS UUID
  -> BcsBotTokenProvider.get_token(proxy_bot_uuid)
  -> BcsClientPort.send_message_a2a(
       target_bot_id,
       target_user_id,
       intake_prompt,
       session_id=a2a_intake_session_key,
       caller_bearer=proxy_bot_token,
     )
  -> 轮询 GET /chat/runs/{run_id}
  -> 解析目标 Bot 返回的 TaskSpec/questions
```

已有 `BcsClientPort.send_message_a2a()`、`BcsBotTokenProvider` 和 BCS
`GET /chat/runs/{run_id}` 合同必须复用；Backend 只补齐中性的 Chat Run 查询 Port 和
Intake Skill Adapter，不复制 BCS CLI 客户端实现，不在 Prompt、日志、TaskSpec 或
TaskIntakeResult 中保存 token。

具体实现以现有 `POST /api/v1/collaboration/tasks/benchmark_execute` 的 A2A 分支为基线，复用其已经跑通的调用链：

```text
compose_bot_identity(proxy_bot_id, owner_user_id)
-> BcsBotTokenProvider.get_token(proxy_bot_identity)
-> BcsClientPort.send_message_a2a(
     target_bot_id,
     target_user_id,
     message,
     session_id,
     caller_bearer,
   )
-> BotSendResult(run_id, session_id)
```

`benchmark_execute` 当前只提交并返回 A2A run handle；Task Intake 在相同 Adapter/Port
之上增加终态等待和结果解析即可。不得复制 benchmark 路由中的 transport 代码到
Task Core，应将可复用的“发送并等待 A2A Bot 结果”能力收敛到 transport-agnostic Port
及其 Adapter，再由 benchmark 和 Intake 各自编排业务语义。

未设置 `--from` 时继续使用 Human 直连目标 Bot 的既有 Intake Skill 路径。两条发送路径必须产生相同的 `TaskSpec + questions` 领域输出。

A2A 发送由 BCS 权威准入检查决定；403 映射为 `TASK_A2A_FORBIDDEN`，proxy Bot token 缺失映射为 `TASK_A2A_PROXY_UNAVAILABLE`。发送成功但本次轮询超时时，Backend 保留 Chat Run handle，并返回同一个 Session 的 `TaskIntakeResult`；HTTP 只额外投影 `processing=true`，不暴露 run ID 或 token。CLI 返回 `session_id` 和退出码 3，调用者稍后执行 `resume <session-id>` 继续轮询，不能盲目重发 START。

### 6.3 目标对象

```python
class IntakeTargetType(StrEnum):
    BOT = "BOT"
    # Backend 任务领域为未来能力保留；avernet-goal-cli 首版不得提交。
    GROUP = "GROUP"


@dataclass(frozen=True)
class IntakeTarget:
    target_type: IntakeTargetType
    target_id: str
```

一个 Intake Session 的单一 target 在创建后不可变。改变 `to` 必须取消对应 Session 并重新发起。

### 6.4 默认目标解析

Human 未显式设置 `to` 时，Backend 按服务端策略解析一个目标，例如：

1. 用户同名且可运行的 Bot；
2. `default` Bot；
3. 能力最匹配且可运行的 Bot；
4. 可访问且可运行 Bot 中的受控兜底选择；
5. 无可用目标时返回 `NO_AVAILABLE_TARGET`。

解析出的稳定目标 ID 必须写入 `TaskIntakeResult.target`，同一 Session 后续不得重新选择。

`GROUP` 是 Backend 既有/未来任务能力的保留类型，不属于 `avernet-goal-cli` 首版公开合同。CLI help、JSON Schema 和 example 只允许 `bot:<bot-ref>`；任何 `group:` 输入均以 `UNSUPPORTED_TARGET_TYPE` 失败，且不能静默退化为 Bot。

## 7. Intake 领域对象

### 7.1 执行模式

```python
class TaskExecutionMode(StrEnum):
    RELAY = "relay"
    CENTRALIZED = "centralized"
```

执行模式在 Intake Session 创建时冻结。Backend 根据它选择服务端 Runtime Profile；它不是客户端可展开的策略对象。

### 7.2 Command

```python
class TaskIntakeAction(StrEnum):
    START = "START"
    RESUME = "RESUME"
    CONTINUE = "CONTINUE"
    CONFIRM = "CONFIRM"
    CANCEL = "CANCEL"


@dataclass(frozen=True)
class TaskIntakeCommand:
    action: TaskIntakeAction
    message: str | None = None
    requested_a2a_proxy_bot_ref: str | None = None
    requested_target: IntakeTarget | None = None
    requested_mode: TaskExecutionMode | None = None
```

`--from bot:<ref>` 是用户请求的 A2A 权限代理，因此进入 Command 的 `requested_a2a_proxy_bot_ref`；它必须由 Backend 结合 `TaskIntakeContext.owner_user_id` 校验，不能直接当作可信 Bot ID。

字段有效性：

| action | 必填/允许字段 |
| --- | --- |
| `START` | `message` 必填；`requested_a2a_proxy_bot_ref`、`requested_target`、`requested_mode` 可选 |
| `RESUME` | `context.session_id` 必填；其他 Command 字段为空 |
| `CONTINUE` | `context.session_id`、`message` 必填 |
| `CONFIRM` / `CANCEL` | `context.session_id` 必填；其他 Command 字段为空 |

`requested_a2a_proxy_bot_ref` 和 `requested_target` 只在 `START` 接受；不得通过 `CONTINUE` 更改权限代理或目标。CLI 的重复 `--to` 必须在调用 Backend 前展开，不能把目标列表传入单次 Intake。

### 7.3 Result

```python
class TaskIntakeStatus(StrEnum):
    CLARIFYING = "CLARIFYING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


@dataclass
class TaskIntakeResult:
    session_id: str
    owner_user_id: str
    source_type: TaskSourceType
    status: TaskIntakeStatus
    target: IntakeTarget
    execution_mode: TaskExecutionMode
    task_spec: TaskSpec | None = None
    missing_fields: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    create_time_ms: int = 0
    update_time_ms: int = 0
```

约束：

```text
target 和 execution_mode 在 Session 创建后不可变
CLARIFYING + TaskSpec 不完整 -> missing_fields/questions 非空
CLARIFYING + TaskSpec 完整   -> 展示确认摘要
CONFIRMED                    -> TaskSpec 完整；不代表正式任务已创建
CANCELLED                    -> 不再创建新的 Task
```

`TaskIntakeResult` 不持有 `task_id`、`node_id` 或 Runner 会话 ID。它只描述一个 Target Execution 的 Goal 是否已完整、确认以及绑定到哪个目标。

HTTP DTO 额外提供只读派生字段 `processing: bool`。它由 Backend 内部是否存在未终态 A2A Chat Run 推导，不进入 Intake 领域状态机，也不暴露内部 `a2a_run_id`、`a2a_session_id` 或 Bot bearer。

### 7.4 TaskSpec 完整性

确认前复用既有 `TaskSpec`，不新增 TaskDraft：

```text
TaskSpec.goal.objective 非空
TaskSpec.context.extend_props.deliverables 非空
TaskSpec.goal.acceptances 非空
constraints 已明确；允许 []，但必须经过确认
```

同一输入 Goal 展开的多个 Target Execution 各自澄清，因此允许形成不同 `TaskSpec`。CLI 不负责合并或强制对齐这些规格。

## 8. `TaskService.intake()` 处理逻辑

```python
async def intake(
    command: TaskIntakeCommand,
    context: TaskIntakeContext,
) -> TaskIntakeResult:
    if command.action == TaskIntakeAction.START:
        require(context.session_id is None)
        require_nonempty(command.message)

        target = await resolve_and_authorize_target(
            owner_user_id=context.owner_user_id,
            caller_bot_id=context.caller_bot_id,
            requested_a2a_proxy_bot_ref=command.requested_a2a_proxy_bot_ref,
            requested_target=command.requested_target,
            goal_message=command.message,
        )

        result = TaskIntakeResult(
            session_id=new_session_id(),
            owner_user_id=context.owner_user_id,
            source_type=context.source_type,
            status=TaskIntakeStatus.CLARIFYING,
            target=target,
            execution_mode=command.requested_mode or TaskExecutionMode.RELAY,
            task_spec=None,
            create_time_ms=now_ms(),
            update_time_ms=now_ms(),
        )

        task_spec, missing_fields, questions = await intake_skill.start(
            message=command.message,
            target=target,
        )
        return intake_repository.create(
            replace(
                result,
                task_spec=task_spec,
                missing_fields=missing_fields,
                questions=questions,
            )
        )

    session = intake_repository.get(require(context.session_id))
    require_owner(session, context.owner_user_id)

    if command.action == TaskIntakeAction.RESUME:
        return session

    if command.action == TaskIntakeAction.CONTINUE:
        require(session.status == TaskIntakeStatus.CLARIFYING)
        require_nonempty(command.message)
        require(not task_exists_for_session(session.session_id))

        task_spec, missing_fields, questions = await intake_skill.continue_(
            task_spec=session.task_spec,
            message=command.message,
            target=session.target,
        )
        return intake_repository.save(
            session,
            replace(
                session,
                task_spec=task_spec,
                missing_fields=missing_fields,
                questions=questions,
                update_time_ms=now_ms(),
            ),
        )

    if command.action == TaskIntakeAction.CONFIRM:
        if session.status == TaskIntakeStatus.CONFIRMED:
            return session
        require(session.status == TaskIntakeStatus.CLARIFYING)
        require_complete(session.task_spec)
        return intake_repository.save(
            session,
            replace(
                session,
                status=TaskIntakeStatus.CONFIRMED,
                missing_fields=[],
                questions=[],
                update_time_ms=now_ms(),
            ),
        )

    if command.action == TaskIntakeAction.CANCEL:
        if session.status == TaskIntakeStatus.CANCELLED:
            return session
        require(session.status == TaskIntakeStatus.CLARIFYING)
        require(not task_exists_for_session(session.session_id))
        return intake_repository.save(
            session,
            replace(
                session,
                status=TaskIntakeStatus.CANCELLED,
                update_time_ms=now_ms(),
            ),
        )

    raise InvalidTaskIntakeAction(command.action)
```

Task Intake Skill 只生成或修订当前 target 对应的 `TaskSpec` 和追问文案，不能设置 owner、source、target、execution mode、状态或任务 ID。

## 9. 确认后的单 Target `/start`

每个 Intake Session 只绑定一个 target，因此 Backend `/start` 保持单任务合同，直接返回现有 `TaskOpResult`。多目标聚合属于 CLI，不进入 Backend Task Service API。

### 9.1 TaskInfoRequest 转换

```python
def build_task_info_request(
    result: TaskIntakeResult,
    runtime_profile: RuntimeProfile,
) -> TaskInfoRequest:
    require(result.status == TaskIntakeStatus.CONFIRMED)
    require(result.task_spec is not None)

    owner_bot_id = resolve_target_owner_bot_id(result.target)

    return TaskInfoRequest(
        task_spec=result.task_spec,
        source_type=result.source_type,
        owner_user_id=result.owner_user_id,
        owner_bot_id=owner_bot_id,
        execution_config={
            **runtime_profile.to_execution_config(),
            "intake_session_id": result.session_id,
            "initial_target": {
                "target_type": result.target.target_type.value,
                "target_id": result.target.target_id,
            },
            "orchestration_mode": result.execution_mode.value,
        },
    )
```

`RuntimeProfile` 由 Backend 根据 `execution_mode` 加载。客户端不能提交 `execution_config`、dispatcher strategy 或 runner strategy。

### 9.2 Start 逻辑

```python
async def start_confirmed_goal(
    session_id: str,
    owner_user_id: str,
) -> TaskOpResult:
    result = intake_repository.get(session_id)
    require_owner(result, owner_user_id)
    require(result.status == TaskIntakeStatus.CONFIRMED)

    runtime_profile = runtime_profiles.load(result.execution_mode)
    request = build_task_info_request(
        result=result,
        runtime_profile=runtime_profile,
    )
    return await task_service.execute(request)
```

同一 `session_id` 重试 `/start` 时返回原 `TaskOpResult`，不创建第二个任务。

### 9.3 Start 后执行

对成功的 `TaskOpResult`：

```text
读取 TaskOpResult.extend_props.root_execution_instruction
-> Frontend 不解析协议，原样附加到任务面板消息
-> 发送到完成澄清与确认的当前 Bot 会话
-> 目标 Bot S1 调节点级 context API
```

同一输入 Goal 展开的不同 Target Execution 已经拥有不同 `session_id/task_id/node_id`，因此各自 S1 使用自己的节点标识投影 `TaskContext`。

## 10. Batch 提交流程

Batch 是 CLI 编排能力，首版不要求新增一个把所有 Goal 一次提交给 Backend 的 HTTP Service API。

```python
async def submit_batch(file: GoalBatchFile) -> GoalBatchSubmitResult:
    normalized = validate_and_normalize_entire_file(file)
    execution_units = expand_goal_targets(normalized)

    execution_results = []
    for unit in execution_units:
        intake_result = await start_target_intake(
            goal=unit.goal,
            from_ref=unit.from_ref,
            target=unit.target,
            mode=unit.mode,
        )

        if not is_complete(intake_result.task_spec):
            execution_results.append(from_intake_result(unit, intake_result))
            continue

        if not batch_interaction.has_explicit_confirmation(unit.key):
            execution_results.append(
                ready_for_confirmation(unit, intake_result)
            )
            continue

        confirmed = await confirm_intake(intake_result.session_id)
        execution_results.append(
            await start_confirmed_execution(unit, confirmed.session_id)
        )

    return aggregate_batch_result(normalized.goals, execution_results)
```

交互模式：

- 使用 `Goal id + target` 标识当前澄清对象；
- 同一 Goal 的不同 target 各自澄清，可以得到不同的 `TaskSpec`；
- 汇总展示所有完整 Target Execution；
- 用户在确认界面明确选中要创建的 Target Execution 后，CLI 对各自 Session 发送 `CONFIRM`，随后分别调用 `/start`；
- 未被确认的完整 Target Execution 保持 `CLARIFYING` 领域状态，但 CLI 聚合视图标记为 `READY_FOR_CONFIRMATION`。

非交互模式：

- 不读取 stdin；
- 不把 `--non-interactive` 解释为自动确认；
- TaskSpec 不完整时返回 `CLARIFYING`、`session_id`、`missing_fields`、`questions`；
- TaskSpec 完整但未确认时返回派生状态 `READY_FOR_CONFIRMATION` 和 `session_id`；
- Agent 使用交互式 `resume <session-id>` 重新进入同一会话控制器，而不是重新提交整个 Batch 文件；不新增 message/confirm/cancel 命令。

Batch 不具有全局事务语义。一个 Target Execution 失败不回滚其他成功任务。

## 11. HTTP Service API

### 11.1 路径

```http
POST /api/v1/collaboration/tasks/intake
POST /api/v1/collaboration/tasks/start
GET  /api/v1/collaboration/tasks/{task_id}
```

Batch 的 `schema/example/validate` 均为 CLI 本地能力，不调用 HTTP。`batch submit` 编排多次普通 `/intake` 和 `/start` 调用。

### 11.2 Intake 请求

领域输入仍为：

```python
TaskIntakeCommand + TaskIntakeContext
```

HTTP Adapter：

- 从统一认证取得 Human Principal；
- 将可选 `--from bot:<ref>` 映射为待校验的 `requested_a2a_proxy_bot_ref`；
- 将 `--to` 引用解析为稳定 `IntakeTarget`；
- 构造可信 `TaskIntakeContext`；
- 不接受客户端直接设置 `owner_user_id`、`caller_bot_id` 或 `source_type`；
- Backend 校验 Human 对 A2A 代理 Bot 的代理权，并通过真实 BCS A2A 发送让 BCS 执行 proxy Bot 到 target 的准入检查；
- 不要求调用者提交请求级幂等键。

### 11.3 Start 请求和响应

请求只包含：

```json
{
  "session_id": "tis_01HXYZ"
}
```

客户端不能提交：

- `owner_user_id`；
- `TaskSpec`；
- target 执行配置；
- `execution_config`；
- Runtime Profile；
- `task_id`、`node_id`；
- 幂等键。

响应保持既有 `TaskOpResult`：

```json
{
  "task_id": "task-101",
  "node_id": "task-101",
  "success": true,
  "run_id": 1
}
```

CLI 使用自己的聚合输出把 `Goal id + target + session_id` 与该 `TaskOpResult` 关联；Backend 不新增多目标 Start 响应对象。

## 12. 幂等、并发与崩溃恢复

### 12.1 Intake

- `session_id` 由 Backend 在 `START` Intake 时生成；
- `RESUME` 只读；
- 同一 Session 的并发更新使用版本条件更新；
- `CONFIRM`、`CANCEL` 对相同终态重复调用返回已保存 Result；
- `CONTINUE` 响应未知时先 `RESUME`，不盲目重发文本。

### 12.2 单 Target Start

每个 target 已有独立 `session_id`，因此继续使用：

```text
UNIQUE(session_id)
```

每个目标的创建提交边界必须原子包含：

```text
TaskInfo
根 TaskNode / TaskExecutionGraph
session_id 唯一创建关联
Backend 生成的根节点执行指令
```

`/start` 重试时：

- 已创建 Session 返回原 `TaskOpResult`；
- 不为同一 Session 生成第二个 `task_id`；
- 不同 Session 的部分成功由 CLI 聚合，不进入单次 `/start` 事务。

调用者不传 `request_id` 或幂等键。

### 12.3 Batch 重试

重新执行同一个 Batch 文件表示一次新的 Batch 提交，不能被 CLI 静默视为原请求重试。

如果某个 Target Execution 已返回 `session_id`，调用者必须使用：

```bash
avernet-goal-cli resume <session-id>
```

继续该目标的澄清，避免因为重新提交文件创建新的 Intake Session。

## 13. Agent 自发现合同

第三方 Agent 只需预先知道二进制名称，不应依赖外部语雀或 README 才能构造 Batch 文件。

发现流程：

```text
avernet-goal-cli batch --help
-> 了解四个 Batch 子命令及副作用

avernet-goal-cli batch schema --output json
-> 获取机器可读 JSON Schema

avernet-goal-cli batch example --format yaml
-> 获取合法示例

avernet-goal-cli batch validate --file <file> --output json
-> 无副作用预校验

avernet-goal-cli batch submit --file <file> --non-interactive --output json
-> 提交并解析结构化结果
```

`batch --help` 必须明确说明：

- 每个 `Goal × target` 创建一个 Intake Session；
- N 个最终 target 分别澄清并创建 N 个独立任务；
- `to` 省略时由 Backend 默认解析；
- Batch 不保证全局原子性；
- `--non-interactive` 不自动确认；
- 失败后的恢复方式是 `resume <session-id>`，恢复后沿用正常会话交互。

## 14. Batch 输出模型

以下模型属于 CLI 聚合输出，不进入 Task Core：

```python
@dataclass(frozen=True)
class BatchExecutionResult:
    goal_id: str
    target: IntakeTarget
    session_id: str | None
    status: str
    task_result: TaskOpResult | None = None
    missing_fields: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    error_code: str | None = None


@dataclass(frozen=True)
class GoalBatchSubmitResult:
    status: str
    executions: list[BatchExecutionResult]
```

示例：

```json
{
  "command": "batch.submit",
  "status": "PARTIALLY_COMPLETED",
  "executions": [
    {
      "goal_id": "goal1",
      "target": {"target_type": "BOT", "target_id": "bot1"},
      "session_id": "tis-101",
      "status": "STARTED",
      "task_result": {"task_id": "task-101", "node_id": "task-101"}
    },
    {
      "goal_id": "goal2",
      "target": {"target_type": "BOT", "target_id": "bot1"},
      "session_id": "tis-102",
      "status": "CLARIFYING",
      "missing_fields": ["acceptance_criteria"],
      "questions": ["任务完成的验收标准是什么？"]
    }
  ]
}
```

## 15. 错误语义

| 条件 | HTTP/CLI | 错误码 |
| --- | --- | --- |
| Batch Schema 不支持 | CLI exit 2 | `UNSUPPORTED_SCHEMA_VERSION` |
| Batch 未知字段 | CLI exit 2 | `UNKNOWN_FIELD` |
| Goal ID 重复 | CLI exit 2 | `DUPLICATE_GOAL_ID` |
| 同一 Goal target 重复 | CLI exit 2 | `DUPLICATE_TARGET` |
| 首版使用 `group:` 或其他 target 类型 | CLI exit 2 | `UNSUPPORTED_TARGET_TYPE` |
| session 不存在 | 404 | `TASK_INTAKE_NOT_FOUND` |
| session 不属于 caller | 403 | `TASK_INTAKE_FORBIDDEN` |
| action 与状态冲突 | 409 | `TASK_INTAKE_STATE_CONFLICT` |
| 发送 Bot 未授权 | 403 | `TASK_SENDER_FORBIDDEN` |
| A2A proxy Bot 到 target 无准入权限 | 403 | `TASK_A2A_FORBIDDEN` |
| A2A proxy Bot 运行时身份不可用 | 409 | `TASK_A2A_PROXY_UNAVAILABLE` |
| A2A Intake 已提交但仍在处理 | HTTP 200 + `processing=true` / CLI exit 3 | `TASK_A2A_STATUS_UNKNOWN`（CLI 派生提示） |
| target 不存在或不可访问 | 404/403 | `TASK_TARGET_NOT_FOUND` / `TASK_TARGET_FORBIDDEN` |
| 没有可用默认目标 | 409 | `NO_AVAILABLE_TARGET` |
| TaskSpec 未完整时确认 | 409 | `TASK_INTAKE_NOT_READY` |
| 单个 target 创建失败 | CLI 聚合结果 | 对应 BatchExecutionResult 错误码 |
| 所有 target 创建失败 | CLI exit 7 | `GOAL_START_FAILED` |

错误响应必须提供稳定 `code` 和字段 `path`；Agent 不应依赖自然语言 message 做控制流判断。

## 16. 安全约束

- CLI 使用统一 Human 认证，不自建 token/login/keychain 体系；
- `--from bot:<ref>` 必须由 Backend 验证 Human 对代理 Bot 的权限以及代理 Bot 到 target 的 A2A 权限；
- A2A 代理 Bot 不得写入 `TaskInfoRequest.owner_bot_id`，不得代替 target 执行澄清或任务；
- 客户端 Bot 引用不作为可信 ID；
- 每个 Intake Session 的读写校验 `owner_user_id`；
- 每个 target 在 Session 创建时校验并冻结；
- CLI 首版拒绝所有 `group:` 目标，不创建 Intake Session；
- CLI 不接受内部 Runtime Profile 字段；
- `--non-interactive` 不绕过确认；
- 日志不得记录凭证或完整私有资源内容；
- Batch 文件未知字段必须拒绝，不能静默忽略。

## 17. 测试矩阵

### 17.1 CLI Parser 与 Schema

- 根命令提交单 Goal；
- `--from` 仅接受 `human` 或 `bot:<ref>`；
- `--to` 可重复并保持输入顺序；
- `--to` 首版只接受 `bot:<ref>`，`group:` 在联网前拒绝；
- target 重复时拒绝；
- `--mode` 默认 `relay`；
- 不存在 `--runtime`；
- `batch schema` 输出可解析 Draft 2020-12 JSON Schema；
- `batch example` 输出通过 `batch validate`；
- YAML 与 JSON 使用同一输入模型；
- 未知字段、错误版本、重复 ID、空 Goal、错误 target 格式均在联网前失败；
- `to` 标量而非数组时失败；
- `defaults.to` 和 `runtime` 字段失败。

### 17.2 Intake

- 一个 Goal 多 target 创建多个独立 Intake Session；
- 每个 Session 只冻结一个 target 和 execution mode；
- 多轮澄清保持同一 Session 和 target；
- 同一输入 Goal 的不同 target 可以形成不同 TaskSpec；
- CONFIRM 只返回 `TaskIntakeResult(CONFIRMED)`；
- `TaskIntakeResult` 不含 task/node/Runner session ID；
- `resume` 只读，不创建 Session 或 Task；
- `resume` 读取成功后绑定 active session，并复用首次请求的 CONTINUE/CONFIRM/CANCEL 会话控制器；
- CLI 不暴露 `reply/confirm/cancel` 子命令或 `resume --message/--confirm/--cancel` 参数；
- `resume --non-interactive` 只返回当前状态，不修改 Session；
- 非 owner 访问拒绝；
- Human 默认目标解析后固化稳定 ID；
- `--from bot` 未授权时拒绝。
- A2A 代理 Bot 对 target 没有发起权限时拒绝；
- `--from bot` 不改变 `source_type=API`、任务 owner 或 Intake Skill 使用的目标 Bot；
- `--from bot` 的 Intake 消息实际经过 BCS A2A，不能只做权限探测后走 Human/OpenAPI 直连；
- A2A token 不进入日志、Prompt、TaskSpec、Result 或 CLI 输出；
- A2A 状态轮询覆盖完成、失败、超时和提交结果未知；

### 17.3 单 Target Start 与 CLI 多目标展开

- 每个 `/start` 返回一个 `TaskOpResult`；
- N 个 target 创建 N 个不同 `task_id` 和根 `node_id`；
- 每个 Task 拥有独立 `TaskExecutionGraph`；
- 各根节点分别复用对应目标完成澄清与确认的当前 Bot 会话；
- 多目标不共享 Intake Session、TaskSpec 或运行时状态；
- 单个 target 失败由 CLI 聚合为部分成功，不回滚其他任务；
- 同一 `session_id` 重试返回原任务；
- 成功目标不会因 `/start` 重试产生第二个 task；
- TaskInfo、根节点和唯一关联故障注入验证原子提交；根节点执行指令必须来自已提交的权威节点；
- Relay 与 Centralized 根据服务端 Runtime Profile 选择不同策略。

### 17.4 Batch 与 Agent

- Batch 创建的 Intake Session 数量等于所有 Goal 的最终 target 数量之和；
- Batch 的 Task 数量等于各 Goal 目标数量之和；
- Batch 部分失败不回滚成功项；
- `--output json` stdout 不混入日志或进度文本；
- `--non-interactive` 遇到澄清或确认时返回结构化状态，不自动确认；
- Agent 可只通过 `batch --help/schema/example/validate` 构造合法文件；
- 返回结果可通过 Goal `id`、`session_id`、target、`task_id` 完整关联；
- 已有 `session_id` 的恢复使用 `resume`，不要求重跑整个文件。
- Agent 通过交互式 `resume` 回复问题和选择确认/取消，不需要学习第二套命令协议；

### 17.5 执行链路回归

- 根节点、后继节点和 harness 恢复均通过节点级 Context Service API 读取 `TaskContext`；
- 一个 Target Execution 的 `all_done_output` 不包含其他 target 的输出；
- S4b/S5.5 重试不重复执行业务或创建第二个后继节点；
- 同节点旧 Runner 会话的迟到 report 由 `event_id/relay_token` fencing 拒绝；
- Relay trajectory 继续记录 Context、执行、验收、规划、搜索、决策、上报和派发事实；
- Centralized 模式既有规划和 Runner 行为不因多目标展开而改变。

## 18. 实现边界

### 18.1 Avernet Repository

负责：

- Task Intake/Start 领域合同；
- 单 Target Intake/Start 领域合同；
- HTTP Service API；
- TaskExecutionGraph、TaskDispatcher、TaskRunner；
- Runtime Profile；
- 授权、持久化、幂等和轨迹。

### 18.2 独立 avernet-goal-cli Rust Package

源码由 CLI 组织下的独立 `avernet-goal-cli` 仓库维护，通过 Ant CLI 工程初始化；Avernet 仓库不再保存 CLI 源码副本。它与 Backend 领域代码解耦，负责：

- Rust 命令解析；
- 统一认证 SDK；
- API Gateway Client；
- Batch 文件类型、Schema、example、validate；
- TTY 交互；
- Agent JSON 输出和退出码；
- Batch 编排与结果聚合。

CLI 只调用 Gateway 相对路径，不暴露或读取 Backend URL。

## 19. 相对旧设计的合同变化

| 旧设计 | 新设计 |
| --- | --- |
| 命令名 `task-cli` | 统一为 `avernet-goal-cli` |
| `@bot bot` / `@group group` | 首版只开放 `--to bot:<ref>`；Group 延后 |
| Backend 接收多 target | CLI 在 Intake 前展开，Backend 保持单 `requested_target` |
| 一个 Goal 多 target 共用澄清 | 每个 `Goal × target` 各自澄清、确认和执行 |
| `/start` 聚合多个任务 | Backend 保持返回单个 `TaskOpResult`，CLI 聚合 |
| 修改 `UNIQUE(session_id)` | 保留现有 `UNIQUE(session_id)` |
| 无 Batch 合同 | 明确 `batch schema/example/validate/submit` |
| Batch 格式依赖外部文档 | CLI 自带 JSON Schema、示例和校验 |
| `--non-interactive` 语义不完整 | 明确不等待输入且不自动确认 |
| CLI 可传 runtime 策略 | 首版不开放 `--runtime`，策略由 Runtime Profile 选择 |

Backend 只保留单 target Intake/Start 合同；重复 `--to` 和 Batch 聚合只存在于独立 CLI Adapter。
