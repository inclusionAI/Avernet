# 待办模板、阶段执行与生命周期重设计

> 状态：Confirmed（实现依据）
> 日期：2026-11-20
> 范围：Backend Todo Core、Bot/Runtime 协作契约、Frontend Todo Workbench
> 前序规格：`docs/specs/backend/2026-11-10-todo-workbench/`

## 1. 背景与目标

现有待办模型把一个 Todo 直接绑定到一个 Bot，并以旧的
`pending -> in_progress -> pending_acceptance -> completed` 表达生命周期。
本次实现保留旧接口兼容别名，但将模板 Todo 的正式产品状态升级为
`not_started -> in_progress -> interrupted/pending_action -> completed/cancelled`，
并用显式 Human 阶段替代隐式人工验收。它适合单次 Bot 执行，但不能准确表达以下产品需求：

- 一类待办可复用同一套产品流程，例如“需求澄清 -> 代码开发 -> 部署验证”；
- 每个阶段可由 Bot 或 Human 执行，并由明确的 Human 承担阶段责任；
- 待办状态应反映真实工作情况，而不是简单镜像一次 Runtime 执行状态；
- Bot 的执行失败、自动恢复、等待人工处理和产品中断必须被区分；
- 阶段完成可要求机器可验证的硬产物，例如必须已创建 PR；
- 模板发布后应稳定可追溯，后续编辑不能改变已经运行的待办。

本设计引入**待办模板、模板版本、参与角色槽位和阶段实例**，建立从 Bot/Runtime
执行结果到阶段状态、再到 Todo 产品状态的明确转换。

## 2. 本次设计的核心判断

### 2.1 五类对象必须分离

| 对象 | 负责什么 | 不负责什么 |
| --- | --- | --- |
| Todo/Dima | 一次具体工作及其当前产品状态 | 可复用流程定义、Bot 内部恢复策略 |
| Todo Template | 一类待办的可复用产品流程和 Bot/Human 阶段 | 具体任务内容、实际执行记录 |
| Bot/Skill | 如何完成工作、如何组织详细上报内容 | 产品流程编排、Todo 产品状态 |
| Runtime | 执行、重试、恢复、切换 Bot、上下文连续性 | 产品模板和 Human 责任关系 |
| Environment | 仓库、分支、部署目标、凭据等执行环境 | 待办流程定义 |

因此，模板不是 Workflow 脚本，也不是 Bot Skill 配置。模板只描述用户可感知的产品流程：

1. 有哪些有序阶段；
2. 每个阶段在流程中的目的；
3. 阶段由 Bot 还是 Human 执行，以及具体执行者如何绑定；
4. 阶段阻塞或中断时谁负责；
5. 是否存在机器可验证的硬完成门禁。

### 2.2 产品状态由领域转换产生，不允许任意修改

Runtime 状态、Bot 上报结果和 Todo 状态不是同一个概念：

```text
Runtime attempt/event
        ↓
Stage domain transition
        ↓
Todo product status projection
```

Todo 状态可持久化用于列表查询，但只能由领域命令和状态机更新，不提供任意
`PATCH status` 接口。

### 2.3 效果质量由执行 Bot 保证，模板不编码主观判断

“需求是否清晰”“验收项是否合理”是执行效果，不适合作为通用产品协议里的硬规则。
Bot 和其 Skill 应自行检查质量，并在无法继续时上报 `blocked`。

模板可以配置机器能够可靠校验的硬门禁，例如“至少一个状态为 created 的 PR”，但不应配置：

- “需求已经足够清晰”；
- “代码质量良好”；
- “没有遗漏风险”；
- 任意依赖模型主观判断的布尔条件。

如果未来确实需要模型评审，应作为显式的评审阶段或独立质量服务，而不是伪装成确定性门禁。

## 3. 领域模型

```text
Todo Template
  └── Template Version
        ├── Participant Binding Schema
        ├── Ordered Stage Definitions
        └── Completion Gates

Todo
  ├── pinned Template Version
  ├── concrete Participant Bindings
  └── Stage Instances
        ├── Runtime Executions
        └── Artifacts
```

### 3.1 待办模板

模板拥有稳定身份、名称和生命周期。用户界面统一使用“待办模板”，不暴露 Profile 术语。

模板自身不直接保存可变流程；流程放在版本中。模板可被归档，归档后不可用于新建待办，但不影响历史待办。

### 3.2 模板版本

- `draft` 可编辑；
- `published` 发布后不可变；
- 修改已发布模板时创建新版本；
- Todo 创建时必须绑定确切的 `template_version_id`；
- 已创建 Todo 永远使用创建时的版本，不跟随模板最新版本漂移；
- 版本保存定义摘要 `definition_hash`，便于审计和防止意外变更。

### 3.3 参与角色与角色绑定

用户可理解的产品术语为：

- **参与角色**：模板声明的可变参与者，例如“开发 Bot”“技术负责人”；
- **角色绑定**：创建 Todo 时，为这些角色选择具体 Bot 或 Human。

内部可使用 binding slot，但不建议直接显示“槽位”。

V1 支持三种角色引用来源：

| source | 含义 | 限制 |
| --- | --- | --- |
| `binding` | 创建 Todo 时提供具体身份 | 必要绑定在创建时一次性补齐 |
| `fixed` | 模板固定具体身份 | 仅适合确实稳定的共享角色 |
| `todo_creator` | 当前 Todo 创建人 | 只允许作为 Human owner |

V1 不支持任意表达式、组织角色动态查询或执行到阶段时再晚绑定。晚绑定会导致运行到中途才发现无人负责，初版不接受这种不确定性。

示例：

```json
{
  "binding_schema": [
    {
      "key": "requirement_bot",
      "label": "需求澄清 Bot",
      "subject_type": "bot",
      "required_at": "todo_create"
    },
    {
      "key": "development_bot",
      "label": "开发 Bot",
      "subject_type": "bot",
      "required_at": "todo_create"
    },
    {
      "key": "product_owner",
      "label": "产品负责人",
      "subject_type": "human",
      "required_at": "todo_create"
    },
    {
      "key": "technical_owner",
      "label": "技术负责人",
      "subject_type": "human",
      "required_at": "todo_create"
    }
  ]
}
```

### 3.4 阶段定义

一个阶段定义包含：

- `key`：版本内稳定唯一键；
- `name`：用户可读名称；
- `purpose`：这个阶段在产品流程中的目的；
- `stage_kind`：`bot` 或 `human`；
- `executor_ref`：阶段执行者引用，类型必须与 `stage_kind` 一致；
- `owner_ref`：阶段负责人 Human 引用；Bot 阶段必须配置，Human 阶段可省略并默认等于 executor；
- `completion_gate`：可选硬产物门禁。

`purpose` 不是当前 Todo 的具体任务。对于 Bot 阶段，实际发送给 Bot 的执行上下文由以下内容组合：

```text
Todo title/description
+ 当前阶段 purpose
+ 前序阶段产物与摘要
+ 已解析执行环境
```

Bot 如何工作、安装什么 Skill、使用什么详细上报字段，仍由 Bot/Skill 决定。

示例：

```json
{
  "key": "code_development",
  "name": "代码开发",
  "purpose": "实现当前待办，并形成可供评审和验证的代码变更",
  "stage_kind": "bot",
  "executor_ref": {
    "source": "binding",
    "key": "development_bot"
  },
  "owner_ref": {
    "source": "binding",
    "key": "technical_owner"
  },
  "completion_gate": {
    "required_artifacts": [
      {
        "type": "pull_request",
        "state": "created",
        "min_count": 1
      }
    ]
  }
}
```

### 3.5 Bot 阶段与 Human 阶段

V1 支持两种 `stage_kind`：

| stage_kind | 执行者 | 阶段激活后的 Todo 状态 | 完成方式 |
| --- | --- | --- | --- |
| `bot` | Bot | `in_progress` | 当前 execution 通过 `/outcome` 上报 `completed`，并通过硬门禁 |
| `human` | Human | `pending_action` | 被绑定的 Human 显式提交阶段完成动作 |

Human 阶段表示产品流程本来就要求用户介入，不是异常阻塞。为避免两种“待处理”语义混淆，阶段实例需要记录：

```text
pending_action_kind = planned_human_stage | blocked
```

- `planned_human_stage`：模板定义的正常人工步骤，例如上线确认、人工验收；
- `blocked`：Bot 阶段原本应自动执行，但遇到必须由 Human 处理的问题。

两者在 Todo 顶层都显示“待处理”，但详情、通知文案和可执行动作不同。

Human 阶段 V1 只支持“执行并确认完成”的线性语义，不同时引入 approve/reject 分支。如果需要驳回到前序阶段，应在未来的分支状态机中显式建模，不能用隐式回退破坏阶段顺序。

示例：

```json
{
  "key": "release_confirmation",
  "name": "上线确认",
  "purpose": "由发布负责人确认当前变更可以进入部署验证",
  "stage_kind": "human",
  "executor_ref": {
    "source": "binding",
    "key": "release_approver"
  },
  "completion_gate": {
    "required_artifacts": []
  }
}
```

### 3.6 V1 只支持线性阶段

```text
stages[0] -> stages[1] -> stages[2] -> Todo completed
```

数组顺序就是执行顺序，不存储冗余 `next` 字段。条件分支、并行和回环会显著增加状态机、可视化和恢复复杂度，留待未来通过新的模板 schema version 引入。

## 4. 一个完整模板示例

```json
{
  "schema_version": "1.0",
  "name": "标准研发交付",
  "description": "适用于需要需求澄清、代码开发和部署验证的研发待办",
  "binding_schema": [
    {
      "key": "requirement_bot",
      "label": "需求澄清 Bot",
      "subject_type": "bot",
      "required_at": "todo_create"
    },
    {
      "key": "development_bot",
      "label": "开发 Bot",
      "subject_type": "bot",
      "required_at": "todo_create"
    },
    {
      "key": "verification_bot",
      "label": "部署验证 Bot",
      "subject_type": "bot",
      "required_at": "todo_create"
    },
    {
      "key": "product_owner",
      "label": "产品负责人",
      "subject_type": "human",
      "required_at": "todo_create"
    },
    {
      "key": "technical_owner",
      "label": "技术负责人",
      "subject_type": "human",
      "required_at": "todo_create"
    }
  ],
  "stages": [
    {
      "key": "requirement_clarification",
      "name": "需求澄清",
      "purpose": "澄清当前待办，并整理可供后续阶段继续执行的结论",
      "stage_kind": "bot",
      "executor_ref": {"source": "binding", "key": "requirement_bot"},
      "owner_ref": {"source": "binding", "key": "product_owner"},
      "completion_gate": {"required_artifacts": []}
    },
    {
      "key": "code_development",
      "name": "代码开发",
      "purpose": "实现当前待办，并形成可供评审和验证的代码变更",
      "stage_kind": "bot",
      "executor_ref": {"source": "binding", "key": "development_bot"},
      "owner_ref": {"source": "binding", "key": "technical_owner"},
      "completion_gate": {
        "required_artifacts": [
          {"type": "pull_request", "state": "created", "min_count": 1}
        ]
      }
    },
    {
      "key": "deployment_verification",
      "name": "部署验证",
      "purpose": "完成部署后的验证并上报结果",
      "stage_kind": "bot",
      "executor_ref": {"source": "binding", "key": "verification_bot"},
      "owner_ref": {"source": "binding", "key": "technical_owner"},
      "completion_gate": {"required_artifacts": []}
    }
  ]
}
```

这里不配置仓库、开发分支、合并分支、重试次数、换 Bot 策略或 Skill。它们分别属于 Environment、Runtime 和 Bot 能力。Runtime 在每次执行时应把实际解析到的环境和执行者快照记录下来，确保审计与恢复有据可查。

## 5. 阶段完成契约

### 5.1 Bot 阶段结果信封

Bot/Runtime 对一个阶段尝试的业务结果统一上报：

```yaml
outcome: completed | blocked | failed
summary: string
artifacts: []
result: {}
```

`blocked` 还必须提供：

```yaml
blocker:
  reason: string
  requested_action: string
```

字段含义：

- `completed`：Bot 认为本阶段工作已经完成；领域层仍需检查硬门禁；
- `blocked`：存在明确 Human 或外部动作，不处理就无法继续；
- `failed`：本次执行尝试失败，是否重试/恢复由 Runtime 决定。

`result` 是 Skill 自定义详情，供展示和后续 Bot 使用；Todo Core 不应理解任意 Skill 字段并以此驱动通用状态机。

### 5.2 Human 阶段完成

Human 阶段不伪装成 Bot outcome。被绑定的 Human 通过显式阶段完成命令提交：

```yaml
summary: string
artifacts: []
result: {}
```

服务端验证 actor 是当前 Human stage 的 executor，检查可选硬产物门禁，然后推进阶段。Human 阶段激活后即进入 `pending_action`，完成后根据下一个阶段类型继续投影 Todo 状态。

### 5.3 协议状态与产品状态分离

- `completed/blocked/failed` 属于阶段执行结果契约；
- `PENDING/RUNNING/FAILED/TIME_OUT/ABORTED` 等属于 Runtime attempt 状态；
- `not_started/in_progress/interrupted/pending_action/completed/cancelled` 属于 Todo 产品状态。

`interrupted` 和 `cancelled` 应存在于 Todo Service API 的资源契约中，但不应被强加为 Bot 执行结果。Bot 不决定产品是否取消；一次尝试 `failed` 也不等于 Todo 已中断。

### 5.4 幂等与可信上报

- Bot outcome 请求必须带可去重的 `execution_id` 或事件 ID；Human 完成动作也必须带幂等键；
- Bot outcome 只接受当前阶段已授权 Bot/Runtime 的上报；Human complete 只接受当前阶段绑定的 Human executor；
- 重复上报或重复完成动作应幂等；
- 已推进阶段后到达的旧 execution 结果不得回退状态；
- 产物验证和阶段推进应在同一领域事务边界内完成，或使用可补偿的 outbox/event 方案。

## 6. 完成门禁与产物

### 6.1 默认完成规则

```text
Bot stage: 已认证的当前执行 Bot 上报 completed
或
Human stage: 已绑定的 Human 显式提交 complete
+
当前阶段配置的所有硬产物要求通过
=
当前阶段 completed
```

如果阶段没有配置硬产物，可信 Bot `completed` 或 Human `complete` 即可完成阶段。不能把“任意 artifact 字段有内容”作为默认门禁。

### 6.2 产物语义

现有 `artifact_kind`：

```text
text | file | url_link | code
```

它描述的是展示形式，不足以判断门禁。产物需要增加：

```text
artifact_type   语义类型，例如 pull_request、deployment、document
artifact_state  语义状态，例如 created、merged、verified
artifact_ref    规范化引用，例如 pr://inclusionAI/Avernet/123
```

示例：

```text
artifact_kind  = url_link
artifact_type  = pull_request
artifact_state = created
artifact_ref   = pr://inclusionAI/Avernet/123
```

只有后端已注册、能够实际验证的 `artifact_type + artifact_state` 才允许配置为硬门禁。前端通过 capability API 获取可选项，不能允许用户随意输入一个无法校验的类型。

### 6.3 Human 介入

Human 介入分成两类，不能都解释为 Bot 阻塞：

1. 模板中显式配置的 `human` 阶段，是正常产品流程；
2. Bot 上报 `blocked`，是自动阶段执行中的异常等待。

阶段负责人不是每次 Bot 执行的隐式验收人，而是当前阶段的责任承接者：

- Human 阶段激活时通知该阶段 Human executor，Todo 进入 `pending_action`，并标记 `planned_human_stage`；
- Bot 上报 `blocked` 时通知阶段负责人，Todo 进入 `pending_action`，并标记 `blocked`；
- 计划内 Human stage 在 UI 展示阶段 `purpose`、执行人和完成入口；
- blocked 场景在 UI 展示 `reason` 和 `requested_action`，Human 提交 interaction 后恢复 Bot 阶段。

`blocked` 和 `failed` 的通知对象可以不同：

- `blocked`：通知阶段负责人，因为需要业务或决策动作；
- 自动恢复中的 `failed`：只记录执行事件，通常不打扰 Human；
- 自动恢复耗尽转为 `interrupted`：默认通知阶段负责人，并可由平台运维告警渠道额外接收技术告警。

通知策略本身可以由平台策略管理，但 V1 不在模板中暴露“重试几次、换哪个 Bot、是否发 BBS”等 Runtime 内部参数。

## 7. 生命周期

### 7.1 Todo 产品状态

| 状态 | 中文 | 语义 |
| --- | --- | --- |
| `not_started` | 未开始 | Todo 从未启动，首个 Bot 阶段尚未派发 |
| `in_progress` | 进行中 | 正在执行、重试、恢复或切换 Bot |
| `interrupted` | 已中断 | 自动恢复已经耗尽，无法自动继续 |
| `pending_action` | 待处理 | 正在执行计划内 Human 阶段，或有明确 Human/外部动作等待处理 |
| `completed` | 已完成 | 最后一个阶段已完成 |
| `cancelled` | 已取消 | 用户明确取消，不再自动执行 |

### 7.2 状态投影规则

| 事实 | Todo 状态 |
| --- | --- |
| 创建成功，首阶段是 Bot 且尚未首次派发 | `not_started` |
| 首个 Bot 阶段已经派发；当前 Bot 阶段正在执行、重试、恢复、切换 Bot 或等待下一 Bot 阶段调度 | `in_progress` |
| 当前阶段是计划内 Human 阶段 | `pending_action`（`planned_human_stage`） |
| Bot 阶段上报 `blocked`，需要 Human/外部动作 | `pending_action`（`blocked`） |
| 当前阶段执行失败且 Runtime 恢复耗尽 | `interrupted` |
| 当前阶段完成且下一阶段是 Bot | 保持 `in_progress`，不得回退到 `not_started` |
| 当前阶段完成且下一阶段是 Human | `pending_action` |
| 最后阶段完成 | `completed` |
| 用户明确取消 | `cancelled` |

`not_started` 只允许出现在 Todo 从未启动的初始阶段。一旦首阶段已经开始，阶段切换不能让 Todo 回退为 `not_started`。`failed` 是一次 execution/attempt 的结果，不是 Todo 状态。恢复过程在产品端仍显示“进行中”，但事件流应透明展示“第 N 次尝试失败、切换 Bot、恢复执行”等事实。

### 7.3 阶段推进

1. Bot 阶段由当前 execution 上报 `completed`；Human 阶段由绑定的 Human 显式提交完成；
2. Todo Core 校验当前 actor/execution、当前阶段和硬产物；
3. 门禁不满足时不推进，并返回结构化缺失项；这是“完成声明未通过门禁”，不是自动变成 `blocked`；
4. 门禁满足时将当前阶段标记 `completed`；
5. 存在下一 Bot 阶段时创建/激活该阶段，Todo 保持 `in_progress`，由调度器派发；
6. 存在下一 Human 阶段时创建/激活该阶段，Todo 进入 `pending_action`；
7. 无下一阶段时 Todo 进入 `completed`。

如果缺失硬产物导致 Bot 无法继续，Bot 可根据返回结果补交产物；只有确实需要 Human 动作时才上报 `blocked`。

### 7.4 取消与恢复

- `cancel` 是显式用户命令，应停止或废弃当前 Runtime execution，并将 Todo/未完成阶段置为 `cancelled`；
- `resume` 适用于 `interrupted` 和 `pending_action(blocked)`，创建新的 execution attempt，Todo 回到 `in_progress`；
- `pending_action(planned_human_stage)` 只能通过 Human stage complete 推进；`pending_action(blocked)` 不会因普通 interaction 自动推进，需显式 resume 恢复原 Bot 阶段；
- 已 `completed` 或 `cancelled` 的 Todo 默认不可恢复，若未来需要重开，应建立明确的新命令和审计语义。

## 8. 与现有待验收流程的关系

本设计的首选方向是：

```text
Bot completed + 已配置硬门禁通过 = 阶段完成
```

Human 只在模板显式配置的 Human 阶段，或 Bot `blocked` 形成的 `pending_action` 中介入，不再在每次 Bot 执行后强制进入 `pending_acceptance`。

这会替代前序规格中的以下假设：

- 一个 Todo 只绑定一个 Bot；
- Todo 只有四个状态；
- 每次 Runtime 完成都必须人工验收；
- Runtime 失败可以长期保持 `in_progress` 而没有恢复耗尽后的产品状态。

本次实现已经移除模板 Todo 的强制 `pending_acceptance`。如果业务需要人工验收，必须把它建模为一个明确的 Human 阶段，而不是所有阶段共享的隐式状态；旧非模板接口仍保留兼容验收流。

## 9. 持久化设计

### 9.1 新增 `ac_todo_template`

```text
template_id
space_id
name
description
status                    active | archived
published_version_id
created_by
updated_by
env
avernet_tenant
created_at
updated_at
```

### 9.2 新增 `ac_todo_template_version`

```text
template_version_id
template_id
version_no
status                    draft | published
schema_version
definition                JSON/MEDIUMTEXT
definition_hash
created_by
published_by
published_at
env
avernet_tenant
created_at
updated_at
```

阶段、角色 schema 和门禁作为一份经过 schema 校验的 JSON definition 原子发布。不要为 V1 过早拆成大量可独立变更的配置表，否则会破坏版本快照的一致性。

不要复用现有 `ac_templates`，它属于 Bot Template，不是 Todo Template。

### 9.3 调整 `ac_todo`

```text
template_id
template_version_id
template_name_snapshot
current_stage_instance_id
status                    六状态
participant_bindings      创建时解析后的绑定快照
```

现有顶层 `bot_id/bot_name/collab_context_id` 与多阶段执行冲突，应逐步下沉到阶段实例。Todo 可保留必要的显示快照，但不能再把单个 Bot 当作整个 Todo 的唯一负责人。

### 9.4 新增 `ac_todo_stage_instance`

```text
stage_instance_id
todo_id
template_version_id
stage_key
stage_order
stage_name
purpose
stage_kind                 bot | human

executor_binding_key
executor_subject_type      bot | human
executor_subject_id
executor_subject_name
owner_binding_key
owner_user_id
owner_user_name

completion_gate            JSON snapshot
status
collab_context_id
pending_action_kind       planned_human_stage | blocked | null
state_reason
requested_action
started_at
completed_at
env
avernet_tenant
created_at
updated_at
```

所有动态引用在 Todo 创建时解析成具体身份并快照，防止模板、成员昵称或组织关系变化影响历史事实。

### 9.5 execution 与 artifact

`ac_todo_execution` 增加：

```text
stage_instance_id
resolved_runtime_context    实际 Bot、Runtime、环境摘要快照
outcome
outcome_payload
```

`ac_todo_artifact` 增加：

```text
stage_instance_id
artifact_type
artifact_state
artifact_ref
```

数据库写失败必须传播错误，不能在状态未落库时返回推进成功。状态迁移应使用 expected-status CAS/行锁，避免并发 outcome、cancel 和 resume 相互覆盖。

## 10. API 设计

### 10.1 模板管理

```text
POST   /api/v1/collaboration/tasks/todo-templates
GET    /api/v1/collaboration/tasks/todo-templates
GET    /api/v1/collaboration/tasks/todo-templates/{template_id}
GET    /api/v1/collaboration/tasks/todo-templates/{template_id}/versions/{version_id}
POST   /api/v1/collaboration/tasks/todo-templates/{template_id}/versions
PUT    /api/v1/collaboration/tasks/todo-templates/{template_id}/versions/{version_id}
POST   /api/v1/collaboration/tasks/todo-templates/{template_id}/versions/{version_id}/validate
POST   /api/v1/collaboration/tasks/todo-templates/{template_id}/versions/{version_id}/publish
POST   /api/v1/collaboration/tasks/todo-templates/{template_id}/archive
POST   /api/v1/collaboration/tasks/todo-templates/{template_id}/activate
```

只有 draft version 可更新；publish 必须执行 schema、角色引用、阶段顺序、门禁 capability 和权限校验。

### 10.2 capability

```text
GET /api/v1/collaboration/tasks/todo-template-capabilities
```

返回：

- 支持的模板 schema version；
- 支持的角色引用来源和 `bot/human` stage kind；
- 受控 artifact type/state；
- 哪些 artifact state 可作为硬门禁；
- V1 是否支持分支、并行或晚绑定。

### 10.3 Todo

```text
POST   /api/v1/collaboration/tasks/todos
GET    /api/v1/collaboration/tasks/todos
GET    /api/v1/collaboration/tasks/todos/{todo_id}
GET    /api/v1/collaboration/tasks/todos/{todo_id}/stages
POST   /api/v1/collaboration/tasks/todos/{todo_id}/interactions
POST   /api/v1/collaboration/tasks/todos/{todo_id}/stages/{stage_instance_id}/complete
POST   /api/v1/collaboration/tasks/todos/{todo_id}/cancel
POST   /api/v1/collaboration/tasks/todos/{todo_id}/resume
```

创建请求至少包含：

```json
{
  "title": "...",
  "description": "...",
  "template_version_id": "...",
  "participant_bindings": {
    "development_bot": {"subject_type": "bot", "subject_id": "..."},
    "technical_owner": {"subject_type": "human", "subject_id": "..."}
  }
}
```

服务端必须在一个一致性边界内：验证 published version、验证 Space 范围和身份类型、补齐所有必要绑定、创建 Todo、展开阶段实例并保存快照。首阶段为 Bot 时初始状态可为 `not_started`；首阶段为 Human 时应直接进入 `pending_action`。

### 10.4 Human stage complete

```text
POST /api/v1/collaboration/tasks/todos/{todo_id}/stages/{stage_instance_id}/complete
```

只允许当前 Human stage 的 executor 调用。请求包含幂等键、summary、可选 result/artifacts。它是计划内人工阶段的完成命令，不与 Bot `/outcome` 混用。

### 10.5 Bot outcome

```text
POST /api/v1/collaboration/tasks/todos/{todo_id}/outcome
```

请求应包含 `stage_instance_id`、`execution_id`、`outcome`、`summary`、产物和可选 blocker。

兼容期内：

- 保留现有 `/report` 和 `/upload`，但产物绑定到 stage instance；
- 现有 Todo 级 `/complete` 暂时映射成 `outcome=completed`，不要与新的 Human stage complete 路径混淆；
- 新客户端应统一使用 `/outcome`；
- 兼容接口的退场时间和调用方清单必须在实施阶段记录。

## 11. 前端信息架构

不新增全局“Profile 配置”，也不放到 Bot 管理或通用 Space 管理中。模板是 Todo 产品能力，应位于现有 Todo 模块：

```text
实验室
  └── 待办
        ├── 待办列表
        └── 模板管理
```

建议路由：

```text
/lab/todos
/lab/todos/templates
/lab/todos/templates/new
/lab/todos/templates/{template_id}
/lab/todos/templates/{template_id}/versions/{version_id}
/lab/todos/{todo_id}
```

`/templates` 静态路由必须注册在 `/lab/todos/:todoId` 之前，避免被动态路由吞掉。

模块二级导航：

```text
[待办列表] [模板管理]                         [发起待办]
```

模板阶段编辑器必须先选择 `Bot 阶段` 或 `Human 阶段`，再根据类型限制 executor 角色。

模板编辑器分为：

1. 基本信息；
2. 参与角色；
3. 有序阶段；
4. 校验与发布。

发起待办流程变为：

```text
填写 title/description
-> 选择已发布模板版本
-> 绑定动态参与角色
-> 预览阶段与版本
-> 创建
```

不再显示一个顶层“负责人 Bot”选择器。详情页应展示当前阶段类型、阶段执行 Bot 或 Human、阶段负责人、待处理类型、阻塞请求、尝试事件和产物，而不是只展示 Todo 级单一 Bot。

## 12. 环境配置边界

仓库、开发分支、合并分支、部署环境通常不属于模板，原因是：

- 它们可能已经由 Bot、Dima 上下文或 Runtime 自动解析；
- 把它们复制进模板会形成多个配置源；
- 凭据和私有 endpoint 不能进入产品模板；
- 同一流程可能在多个项目环境复用。

如果后续确认存在“稳定项目环境复用”的独立需求，应设计单独的 Environment Profile，并让 Todo/模板只引用它的稳定 ID；不要把环境字段直接扩散进阶段定义。无论来源如何，每次 execution 都要保存实际解析结果快照。

## 13. 权限、审计与可观测性

- 模板、版本和 Todo 必须按 Space/Tenant/Env 隔离；
- 创建/编辑/发布/归档权限需要单独定义，发布权限应高于普通使用权限；
- published definition、Todo binding、阶段执行者和 owner 都要保留快照；
- 状态转换事件至少记录 actor、from/to、reason、stage、execution 和时间；
- Runtime 自动恢复过程作为事件展示，但不把内部策略暴露为模板配置；
- 通知失败不能伪造状态失败，但必须可重试并可观测；
- 列表筛选以持久化 Todo 状态为准，详情可附带 Runtime 最新事件。

## 14. 数据与兼容策略

用户计划在上线前删除旧 Todo 数据，因此本次**不做历史 Todo 数据迁移**。但仍然需要正式数据库 DDL/版本变更，以新增模板、版本、阶段实例和新字段，不能因为无历史数据而跳过 schema 管理。

建议发布顺序：

1. 部署兼容读写的新 schema；
2. 上线模板和新 Todo API；
3. 上线新前端；
4. 清理旧数据或在环境初始化阶段重建；
5. 停止旧客户端调用兼容 endpoint；
6. 后续单独移除废弃字段和接口。

本文件是前序 Todo Workbench 规格的后继设计。模板 Todo 已按本文件实施；前序规格中的四状态和隐式验收仅适用于兼容的非模板 Todo，后续应在调用方迁移完成后单独移除。Todo Context Boundary README、A2A/Todo 契约和 conformance tests 必须以本文件的六状态及阶段语义为准，避免新增代码继续引入旧语义。

## 15. 非目标

V1 不实现：

- 任意 DAG、并行阶段、条件分支和循环；
- 模板内配置 Skill 安装或具体 Skill 上报 schema；
- 模板内配置 Runtime 重试次数、切 Bot、发 BBS 或知识注入策略；
- 模板内保存仓库凭据、私有 endpoint 或部署凭据；
- 基于模型主观判断的通用质量门禁；
- 运行到某阶段时才临时选择必要负责人；
- Human 阶段的 approve/reject 分支或隐式回退；
- 历史 Todo 数据自动迁移。

## 16. 已确认实施决策

1. 正式移除隐式 `pending_acceptance`；需要人工验收时使用显式 Human stage。
2. `interrupted` 和 `pending_action(blocked)` 的 resume 由当前阶段负责人或 Space Admin 执行，并要求填写原因。`completed/cancelled` 默认不可恢复。
3. V1 首批硬门禁为 `pull_request:created`。校验当前阶段由可信 Bot/Human 上报并持久化的规范化 PR 产物事实；远程 SCM 实时真实性查询不在本期。
4. 模板发布、归档和重新激活 V1 使用 Space Admin 权限；独立发布权限留作后续。
5. blocked interaction 只记录处理信息，不隐式恢复；必须显式调用 resume。计划内 Human stage 始终使用独立 complete 命令。
6. 现有 `/complete`、`/report`、`artifacts/object-file` 在本次保留为兼容入口并绑定当前 stage；移除版本不在本期，调用方清单必须随实现记录。

## 17. Context Findings Gate

- Existing related modules/designs found: `apps/backend/src/agentclaw/community/core/task/todos/`、Todo repository、OpenAPI/Bot routes、`apps/frontend-nextgen` Todo 工作台、前序 `docs/specs/backend/2026-11-10-todo-workbench/`。
- Relationship to requested design: **extend + replace semantics**。复用 Todo 边界、Repository/CAS、Scheduler、ContextCollab 和现有 HTTP 面；替换四状态与隐式验收语义，新增模板/版本/阶段实例。
- Existing references: `TodoStatus`、`TodoExecutionService`、`TodoScheduler`、`TodoRepository`、`ac_todo*` 表、现有 `/report`/`/complete`/artifact routes 和前端四状态类型。
- Gaps: 没有模板版本、参与角色 schema、Human stage、stage instance、六状态投影或语义 artifact gate。
- Compatibility risks: 旧前端和 Bot 假设每次完成后进入 `pending_acceptance`；兼容 adapter 必须单向映射到当前 stage，不能维持两套产品状态机。
- Architecture constraints: Core transport agnostic；contract/docs/tests 同步；tenant/env/space 隔离；持久化写失败传播；发布版本不可变。
- Safe to finalize design: **yes**。用户已确认产品语义；兼容和权限选择已封板，非阻断远程 SCM 校验明确为非范围。

## 18. Domain Model Understanding Gate

| Requested concept | Existing concept | Overlap | Decision | Rationale |
| --- | --- | --- | --- | --- |
| Todo product lifecycle | `TodoModel` + four-state `TodoStatus` | high | replace semantics | 继续复用 Todo identity/repository，但状态机升级为六状态 |
| Todo Template | Bot Template / generic templates | low | create | 产品流程定义与 Bot 配置所有权不同，不复用现有 Bot Template |
| Template Version | 无 | none | create | published immutable 和 Todo pinned version 是可追溯前提 |
| Stage Instance | Context/Execution | medium | create + link | Stage 是产品流程实例；Context/Execution 是运行事实，不能互相替代 |
| Human stage | implicit acceptance | high | replace | 显式 Human stage 取代所有 Bot 完成后的默认验收 |
| Artifact gate | display artifact kind | medium | extend | 保留展示 kind，新增 semantic type/state/ref 与 registry |

## 19. 验收点设计覆盖矩阵

| AC ID | Blocking | Design answer | Design ref | Code/data/API impact | Verification plan | Status |
| --- | --- | --- | --- | --- | --- | --- |
| AC-001 | yes | 六状态、单向 `not_started`、领域投影 | §7 | domain/service/repository/frontend | state-machine + API + UI tests | implemented |
| AC-002 | yes | `bot|human` stage 和 pending action kind | §3.5, §7 | template schema/stage service/UI | transition integration tests | implemented |
| AC-003 | yes | immutable version、binding schema 和 snapshots | §3, §9 | new tables/repositories/APIs | version/binding tests | implemented |
| AC-004 | yes | Human complete actor/stage/idempotency checks | §5.2, §10.4 | service/API/idempotency storage | contract tests | implemented |
| AC-005 | yes | attempt outcome 与 Todo 状态分离、显式 resume/cancel | §5.3, §7.4 | scheduler/service/events/API | recovery/concurrency tests | implemented |
| AC-006 | yes | capability registry + stage artifact fact matching | §6 | schema/artifact/service/API | gate validator tests | implemented |
| AC-007 | yes | tenant/env/space、transaction、row lock/CAS | §5.4, §9, §13 | repositories/services | isolation/failure/concurrency tests | implemented |
| AC-008 | yes | Todo 内模板管理、动态绑定和阶段化详情 | §11 | frontend routes/services/components | frontend tests + typecheck/lint | implemented |
| AC-009 | yes | 兼容 endpoint 单向适配并同步 contract/docs | §10.5, §14 | adapters/contracts/docs | compatibility/conformance tests | implemented |
| AC-010 | yes | 正式 DDL，不做历史语义迁移 | §9, §14 | SQL/runbook | schema/repository review | implemented |
