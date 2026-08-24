# OpenAPI Application Coding Bot 支持方案与决策

- **日期**：2026-08-24
- **状态**：已决策，部分实施（Task 7 Blocked）
- **范围**：OpenAPI 创建云端 Bot，重点覆盖 `Claudecode-应用 coding`
- **不在本次范围**：直接修改代码、开放服务 Bot 的 Application Coding、开放本地 Application Coding
- **实施进度**：Task 1–6、Task 8 已实施并通过当前回归测试；Task 7“删除 hosted workspace”等待 corp-only DIMA delete contract。
- **Task 7 前置契约**：需明确 HTTP/RPC 类型、endpoint/RPC 方法、workspace_id 参数位置、鉴权、成功/不存在/超时语义、幂等删除语义和 request_id 透传方式；契约缺失期间不实现猜测性的删除调用。

## 1. 决策摘要

### 1.1 最终决策

**支持 Application Coding，但第一期仅开放“云端 + 个人空间 + 非服务 Bot”组合。**

对外不新增 `claudecode-app` 这样的独立引擎值，而是沿用现有引擎字段，并通过模板字段表达 Coding 模式：

```text
engine = claude_code
template_type = applicationCoding
```

其中：

- `engine` 表示 Bot 的引擎/runtime 维度；Application Coding 第一期固定使用 `claude_code` 作为对外 engine 值；
- `template_type` 表示 Coding Bot 的业务模板/运行模式；
- `applicationCoding` 不是新的引擎类型；
- `claude_code + applicationCoding` 按历史逻辑路由到 `aicoding` adapter，但 Bot 数据中的调用方 `engine` 值仍保留为 `claude_code`。

OpenAPI 只有在 Workspace Hosting 已完成真实绑定并通过能力检查后，才允许创建 `applicationCoding` Bot。若当前部署没有 Workspace Hosting 能力，必须在创建前失败，不能先创建 Bot、Passport 或设备后再失败。

### 1.2 第一期支持矩阵

| 组合 | 是否开放 | 说明 |
| --- | :---: | --- |
| `claude_code` + 云端 + 个人 + 非服务 | ✅ | 现有能力，继续支持 |
| `claude_code` + 云端 + 团队 + 非服务 | ✅ | 是否开放由现有空间权限控制 |
| `claude_code` + 本地 + 个人 + 非服务 | ✅ | 现有能力，继续支持 |
| `claude_code` + 云端 + 服务 | ✅ | 现有服务化策略支持 |
| `aicoding` + 云端 + 个人 + 非服务 | ✅ | 当前引擎组合策略支持 |
| `aicoding` + 本地 + 非服务 | ❌ | 当前本地能力策略拒绝 |
| `aicoding` + 云端 + 服务 | ❌ | 当前服务化能力策略拒绝 |
| `applicationCoding` + 云端 + 个人 + 非服务 | ✅ | 第一期新增 OpenAPI 能力，依赖 Workspace Hosting |
| `applicationCoding` + 云端 + 团队 + 非服务 | ❌ | 第一期收窄到个人空间，避免团队 workspace/协作语义未定义 |
| `applicationCoding` + 云端 + 服务 | ❌ | 服务生命周期、发布资源和多实例语义尚未完成 |
| `applicationCoding` + 本地 | ❌ | 本地没有对应的 hosted workspace 运行链路 |

## 2. 背景与问题

当前 OpenAPI `POST /openapi/v1/bots` 的创建契约只接收以下字段：

```text
bot_name
bot_desc
engine
cluster_name
bot_type
space_id
```

请求模型配置了 `extra="forbid"`，因此以下字段会被拒绝：

```text
template_type
template_key
template_uid
template_config
engine_options
```

这导致一个重要的不一致：后端内部创建链路已经能够表达模板创建，但 OpenAPI 公共契约无法提交 Application Coding 所需的信息。

因此，当前问题不是简单地把前端选项显示出来，而是需要同时补齐：

1. OpenAPI 请求契约；
2. 创建授权异步流程中的参数保存与恢复；
3. Workspace Hosting/DIMA 前置能力检查；
4. Application Coding 的 ready 语义；
5. 组合约束和回归测试。

## 3. 历史逻辑依据

### 3.1 Application Coding 历史上不是独立引擎

历史提交 `5f35d8f8d feat(baas): route claude_code active-engine to aicoding adapter by template_type (#1056)` 已经形成了以下路由规则：

```text
engine = claude_code 且 template_type 为空或 normalCC
    → claude_code adapter

engine = claude_code 且 template_type 为其他 Coding 模板
    → aicoding adapter

其他 engine
    → 按各自引擎逻辑处理
```

因此，Application Coding 的正确表达方式是“引擎 + 模板”的组合，而不是新增一个 `claudecode-app` 引擎。

新增独立引擎会把模板维度错误提升为引擎维度，进而要求重复改造：

- 引擎注册与组合策略；
- BaaS/运行时路由；
- Skill/MCP 能力挂载；
- 服务化判定；
- 健康检查；
- 资源和 workspace 管理；
- 版本及发布逻辑。

### 3.2 Coding workspace 能力已被抽象，但仍有部署前置条件

历史提交 `5a55cfd12 feat: allow coding bots to ensure DIMA workspace` 将 workspace 初始化能力从只处理 `applicationCoding` 扩展为 Coding Bot 通用能力，覆盖：

- `applicationCoding`；
- `aicoding`；
- `claude_code`。

这证明 workspace hosting 是 Coding Bot 创建链路的一部分，但不等于所有环境都已经具备该能力。当前社区实现中，`WorkspaceHostingService` 没有默认绑定，相关部署必须显式提供真实实现。

结论：**不能因为内部 Service 已有接口，就直接宣称 OpenAPI 已完整支持 Application Coding。**

### 3.3 服务 Bot 删除逻辑不应被本次创建改造带偏

服务 Bot 的删除仍然必须遵循发布生命周期，而不是走普通 Bot 删除：

- 草稿且没有成功发布历史时，可以真正删除；
- `RELEASED`、`UPGRADED` 都属于正式发布历史；
- 下线后生成的新草稿不能仅根据当前草稿状态删除整个源 Bot；
- 有正式发布历史时，应通过服务发布生命周期判断删除资格并清理资源；
- OpenAPI 普通删除接口对服务 Bot 应保持拒绝，避免绕过发布记录、预发资源和线上资源清理。

这部分是既有生命周期约束，与本次 Application Coding 创建能力独立。Application Coding 第一期不开放服务化，因此不会新增 Application Coding 服务 Bot 删除分支。

## 4. `aicoding` 与 `applicationCoding` 的定义

| 维度 | `aicoding` | `applicationCoding` |
| --- | --- | --- |
| 所属维度 | 引擎/runtime | 模板/业务模式 |
| 对外表达 | `engine=aicoding` | `template_type=applicationCoding` |
| 是否可以单独出现 | 可以作为云端非服务引擎使用 | 不应脱离 Coding 引擎单独出现 |
| 主要语义 | AI Coding 运行时能力 | 面向应用开发的模板化工作区能力 |
| workspace | 依赖 Coding workspace 能力 | 必须依赖 Workspace Hosting/DIMA workspace |
| 当前服务化 | 不支持 | 第一期不支持 |
| 当前本地创建 | 不支持 | 不支持 |

必须避免把两者混成一个枚举：

```text
错误：engine = claudecode-app
正确：engine = claude_code + template_type = applicationCoding
```

## 5. 当前代码能力盘点

### 5.1 OpenAPI 层

当前 `BotCreate` 仅允许基础字段，且 `extra="forbid"`。创建前已经存在以下校验：

- 引擎是否在支持列表中；
- 引擎与集群是否匹配；
- 服务 Bot 是否使用可服务化引擎；
- 通过授权后进入统一创建流程。

缺口是：OpenAPI 没有接收并传递 `template_type` 与 `template_config`。

### 5.2 内部创建层

`BotCreateSpec` 已经包含：

```python
template_type: str | None
template_config: dict[str, Any] | None
extra_properties: dict[str, Any]
```

`BotService.create_bot()` 已经能够：

- 持久化 `template_type`；
- 在模板参数完整时创建模板记录；
- 对 `applicationCoding` 调用 Workspace Hosting 创建 workspace；
- 继续执行设备分配、模板配置透传、记忆初始化等流程。

因此本次改造重点是公共契约、参数完整性和能力前置检查，而不是重新设计内部 Bot 创建服务。

### 5.3 引擎组合层

当前实际组合策略为：

```text
本地可用：openclaw、claude_code
个人云端可用：全部已注册引擎
服务可用：openclaw、claude_code、teclaw
```

所以：

- `aicoding`：云端非服务可用，本地和服务不可用；
- `claude_code`：云端、本地、服务可用；
- `applicationCoding`：不能仅根据 `engine` 判断，必须联合模板、部署方式、空间和服务化属性校验。

## 6. OpenAPI 契约设计

### 6.1 推荐方案：最小兼容扩展

在 `POST /openapi/v1/bots` 的请求体增加两个可选字段：

```json
{
  "bot_name": "my-app-coding-bot",
  "bot_desc": "application coding bot",
  "engine": "claude_code",
  "cluster_name": "ACRA",
  "bot_type": "personal",
  "space_id": "personal-space-id",
  "template_type": "applicationCoding",
  "template_config": {
    "devflow_workflow": "app-flow",
    "yuque_kb_repos": [],
    "code_repos": [],
    "token": "caller-supplied-business-token",
    "bot_template_config": {
      "preset_capabilities": {},
      "ext_config": {"thetaKey": "caller-supplied-business-value"}
    }
  }
}
```

内部转换为：

```python
BotCreateSpec(
    entity_id=owner_id,
    engine_type=body.engine,
    bot_type=body.bot_type,
    bot_name=body.bot_name,
    bot_desc=body.bot_desc,
    space_id=current_space.numeric_id,
    template_type=body.template_type,
    template_config=body.template_config,
)
```

> **转换说明**：`owner_id` 来自 `UserIdDep`（已验证调用者）；`current_space = space_context.resolve_current(owner_id=owner_id, header_space_id=body.space_id)`。
> `cluster_name` **不在 `BotCreateSpec` 上** —— 它仅在请求期做 `validate_engine_cluster(body.engine, body.cluster_name)` 校验，不进入创建 spec。
> `space_id` 在 `BotCreateSpec` 上是 `int | None`（numeric id，个人空间时为 `None`），必须经 `resolve_current` 解析，不能用 `body.space_id` 这个 `str | None` 直传。
> 新增的 `template_type`/`template_config` 与既有字段并列即可，不改变 `entity_id`/`engine_type`/`space_id` 的既有解析路径。

### 6.2 `template_config` 不做猜测性强 DTO

历史 Application Coding 的真实 fixture 位于
`src/backend/tests/community/acceptance/bot_management/test_bot_live_lifecycle.py`：

```python
{
    "devflow_workflow": "old-flow",
    "yuque_kb_repos": [],
    "code_repos": [],
    "token": "old-token",
}
```

同时，引擎/模板消费路径会读取如下嵌套结构：

```python
{
    "bot_template_config": {
        "preset_capabilities": {...},
        "ext_config": {"thetaKey": "..."},
    }
}
```

这说明 `template_config` 是历史兼容的自由字典，而不是可以抽象成
`model/runtime/repos/config` 的稳定 DTO。第一期采用受控薄透传：

```python
template_config: dict[str, Any] | None
```

边界规则：

- 只接受 JSON object；不做字段重命名、扁平化或自动包装；
- `to_internal_template_config()` 只做深拷贝/JSON 归一化，保证上述扁平和嵌套结构原样进入内部 `BotCreateSpec`；
- 拒绝客户端写入服务端字段：顶层 `dima_space_id`、`template_uid`、Bot ID、workspace 状态字段；
- `template_key` 不作为本期公共字段单独定义；模板身份由既有 resolver/服务端逻辑决定；
- 其余字段由既有 Application Coding/模板校验器解释，校验失败返回 `OPENAPI_BOT_TEMPLATE_INVALID`，未知业务字段不得被静默丢弃。

这不是放弃校验，而是把校验放在真实拥有字段语义的历史校验器中，避免公共层制造第二套不一致的 schema。后续若要开放稳定模板工厂契约，应另立版本化 DTO，不在本期混入 legacy Application Coding。

### 6.3 普通 Bot 的兼容行为

当 `template_type` 未传时：

- 按现有普通 Bot 流程创建；
- `template_config` 不应单独传入；
- 若只传 `template_config` 而未传 `template_type`，请求应被拒绝，而不是静默忽略配置。

当前仅允许的模板类型：

```text
无模板（普通 Bot）
applicationCoding
```

其他模板类型第一期拒绝，并返回明确的“不支持模板类型”错误。

## 7. 创建前校验规则

所有组合和依赖校验必须发生在以下副作用之前：

- Passport 授权申请；
- Bot 落库；
- 设备分配；
- workspace 创建；
- 模板记录创建。

### 7.1 Application Coding 校验

当：

```text
template_type = applicationCoding
```

必须同时满足：

```text
source = cloud
bot_type = personal
engine = claude_code
服务化 = false
template_config 存在且为 JSON object
template_config 通过历史 Application Coding 配置校验
WorkspaceHostingService 已绑定且可用
```

其中 `source` 由创建端点决定——`POST /openapi/v1/bots` 即 cloud（本地走 `POST /openapi/v1/bots/local`），不应由调用方通过不受控的额外字段绕过组合策略。`cluster_name` 是引擎集群（ANDC/ACRA）判定，只在请求期 `validate_engine_cluster` 使用、不进入 `BotCreateSpec`，与 source（cloud/local）无关。

### 7.2 拒绝场景

| 请求 | 结果 |
| --- | --- |
| `template_type=applicationCoding` + 本地 | 拒绝，组合不支持 |
| `template_type=applicationCoding` + 服务 Bot | 拒绝，服务生命周期未支持 |
| `template_type=applicationCoding` + 团队空间 | 第一期拒绝，先收敛到个人空间 |
| `template_type=applicationCoding` + 非 Coding 引擎 | 拒绝，模板与引擎不兼容 |
| `template_type` 未传但传 `template_config` | 拒绝，参数关系非法 |
| 未配置 Workspace Hosting | 创建前拒绝，不产生副作用 |
| 不支持的 `template_type` | 拒绝 |

### 7.3 错误语义

建议区分：

- **400/422**：请求字段缺失、格式错误、模板配置非法；
- **409**：引擎、部署方式、空间或服务化组合不支持；
- **503**：当前环境未启用 Workspace Hosting，暂时无法创建 Application Coding。

错误响应应包含稳定的错误码，例如：

```text
OPENAPI_BOT_TEMPLATE_INVALID
OPENAPI_BOT_COMBINATION_UNSUPPORTED
OPENAPI_APPLICATION_CODING_UNAVAILABLE
```

不要返回内部依赖名称、堆栈或私有服务地址。

## 8. 202 异步授权流程

当前创建接口可能返回：

```http
202 Accepted
```

这意味着不能只修改 POST 请求模型，否则授权完成后可能丢失模板参数。

### 8.1 必须保证的参数链路

```text
POST /openapi/v1/bots
  → 生成授权申请
  → 用户完成 Passport 授权
  → auth-status 查询并回传模板参数
  → ISSUED 后恢复完整 BotCreateSpec
  → 创建 Bot
```

`auth-status` 需要能够恢复以下字段：

```text
bot_name
bot_desc
engine_type
bot_type
space_id
template_type
template_config
```

其中 `engine_type` 是 `body.engine` 解析后的内部字段，`space_id` 仍保存 `resolve_current` 得到的 numeric id（`int | None`）。`cluster_name` 只在 POST 请求期做 `validate_engine_cluster` 校验，不进入异步恢复参数。

### 8.2 推荐实现

本期不新增 `create_intent` 表，继续复用现有 `auth-status` echo 流程：

- `BotAuthStatusPoll` 增加可选的 `template_type` / `template_config` 字段；
- `auth-status` 根据轮询请求恢复完整 `BotCreateSpec`；
- POST 和 auth-status 使用同一套模板 DTO、组合校验和 Workspace Hosting 绑定检查；
- 保留现有 `bot_id`、`space_id` 等字段，兼容已有客户端；
- 普通 Bot 的授权流程和字段行为不变。

该方案的边界是：模板参数仍由客户端在 auth-status 中回传，不保证服务重启后的 pending 意图恢复，也不新增并发 claim 机制。若后续需要跨进程恢复、严格并发幂等或审计，再单独建设持久化 intent。

### 8.3 幂等要求

本期不新增 `Idempotency-Key` 公共请求头。继续沿用现有 `bot_id`、Passport 授权状态和创建逻辑处理重复查询；严格的跨请求幂等、创建意图持久化和并发 claim 不在本期范围。

如果后续新增 `Idempotency-Key`，需要另行更新公共 OpenAPI 契约、错误码、客户端接入文档和兼容性评估，不能只在实现计划中增加。

## 9. Workspace Hosting 与 DIMA 前置条件

Application Coding 的核心资源不是普通 Bot 记录，而是一个由 Workspace Hosting 管理的开发工作区。

创建链路沿用现有存储和调用方式：

```text
校验模板配置
  → 确认 Workspace Hosting 已绑定
  → 进入现有 `create_bot` 链路（不重排普通 Bot 的既有顺序）
  → 在 Application Coding 分支创建/关联 hosted workspace
  → 将 workspace 标识写入现有 `ac_templates.ext.dima_space_id`
  → 完成 Bot 与模板记录
```

本期不新增独立的 `hosted_workspace_resource` 表，`ac_templates.ext.dima_space_id` 继续作为已创建 workspace 的关联记录。workspace 创建接口需要提供明确的成功/失败结果；如果远端调用超时且结果不确定，本期返回失败并记录 request_id，交由调用方重试或现有人工补救流程处理，不引入后台 reconcile。

如果 hosted workspace 创建失败：

- 整体创建失败；
- 不返回成功的 Bot 创建结果；
- 不允许留下没有可用 workspace 的 Application Coding Bot；
- 记录可检索的失败原因和 request_id；
- 对已有清理路径覆盖到的 Bot、模板和设备按现有逻辑清理；不承诺本期为所有部分成功场景增加原子补偿；
- workspace 已创建但本地模板持久化失败时记录 request_id 和 workspace_id，返回失败并支持人工补救。

社区部署未绑定 Workspace Hosting 时，必须 fail closed。不能用空实现伪装成成功，也不能依赖调用方自行补写 DIMA workspace id。

### 9.1 与现有 `create_bot` 顺序的取舍

`bot_service.create_bot` 现状是先落库 Bot，再创建 workspace/template，再分配设备。本期不重排普通 Bot 的公共创建顺序，避免影响现有 `claude_code`、`aicoding` 和服务化路径。

Application Coding 通过两点保证失败语义：

- router preflight 在 Passport 之前复用现有 hosting 绑定检查；
- `create_bot` 内 workspace 创建失败时，Application Coding 分支直接失败，不允许返回一个没有 workspace 的成功 Bot；已存在的局部清理按现有路径执行，不新增通用补偿编排。

普通 Bot（未传 `template_type`）不调用 workspace hosting，路径保持不变。

## 10. Ready 语义与对外可用性

Application Coding 不是 workspace 创建成功就立即可用。还需要等待仓库初始化/`.repos/` clone 等异步动作完成。

因此：

```text
binding.status = ACTIVE
```

不一定代表 Application Coding 已可对话或可执行任务。

对 Application Coding 应同时检查：

```text
binding.status == ACTIVE
ext.start_status == SUCCEEDED
```

兼容历史数据时：

- 显式 `PENDING`/`FAILED` 仍然阻塞；
- 老数据没有 `start_status` marker，但 binding 已 ACTIVE 时，可按兼容规则放行；
- 新创建的 Application Coding 必须写入明确的 start marker，避免新旧数据语义混用。

建议 OpenAPI 的异步状态中区分：

```text
AUTHORIZED
CREATING
WORKSPACE_PREPARING
READY
FAILED
```

只有 `READY` 才表示调用方可以继续使用 Application Coding Bot。

### 10.1 状态暴露点与契约兼容

为不破坏现有 `POST /{bot_id}/auth-status` 返回 `PENDING`/`ISSUED` 的契约（外部 tenant 已依赖），上面 `AUTHORIZED/CREATING/WORKSPACE_PREPARING/READY/FAILED` 是**内部概念状态机**，对外只通过 `GET /openapi/v1/bots/{bot_id}/status` 暴露 `READY`：

- `auth-status` 仍只回 `PENDING`/`ISSUED`：`ISSUED` 触发 `create_bot` 落库返回 `bot status=PENDING`，**不新增** `WORKSPACE_PREPARING` 之类中间态到 `auth-status`；
- applicationCoding bot 落库后 workspace 仍在 prepare，这段“未就绪”由 `GET /bots/{id}/status` 的 `is_ready` 表达（复用 `is_bot_ready`：`binding.status==ACTIVE` 且 `ext.start_status==SUCCEEDED` 才为 `true`）；
- `READY` 对等于 `is_ready==true`，不向 `BotAuthStatus` 新增对外状态枚举；
- 历史 applicationCoding 数据按 §10 兼容规则放行（binding 已 ACTIVE 但无 start_status marker）。

即：创建进度查询走 `auth-status`（到 `ISSUED` 为止），就绪查询走 `status`，两者不混入彼此契约。

## 11. 服务化、本地、更新和删除边界

### 11.1 服务化

第一期不开放：

```text
applicationCoding + service
```

原因：

- 当前 `aicoding` 不在服务化引擎集合中；
- Application Coding 的 workspace 与服务 Bot 发布版本如何绑定尚未定义；
- 预发、线上、回滚、多实例、容器回收、健康检查的资源语义尚未定义；
- 下线后 workspace 是否保留、冻结或销毁也没有稳定契约。

因此不能仅在前端隐藏服务化按钮；后端组合策略也必须拒绝该组合。

### 11.2 本地

第一期不开放本地 Application Coding：

- 本地没有 Workspace Hosting/DIMA 的对应托管链路；
- 本地资源目录与 hosted workspace 的生命周期不同；
- 当前组合策略已经拒绝 `aicoding` 本地；
- 不能通过修改前端枚举绕过后端拒绝。

### 11.3 更新

第一期支持：

- 更新普通 Bot 基础信息，沿用现有规则；
- Application Coding 模板配置更新必须经过模板校验；
- workspace/repository 变更应由 Workspace Hosting 负责，不允许 OpenAPI 直接写内部路径；
- 更新失败不得覆盖原有可用配置。

第一期不支持：

- 将普通 Bot 在线转换为 Application Coding；
- 将 Application Coding 转换为普通 Bot；
- 将 Application Coding 迁移为服务 Bot。

如需转换，采用新建 Bot + 迁移业务数据的显式流程，不在本次 OpenAPI 更新接口中隐式完成。

### 11.4 删除

Application Coding 非服务 Bot 继续沿用非服务 Bot 删除逻辑，但删除前必须处理现有模板配置中的 hosted workspace：

1. 校验调用方权限；
2. 从 `ac_templates.ext` 读取 `dima_space_id`；
3. 先通过 Workspace Hosting Service/Client 删除或回收 hosted workspace；如果当前 client 尚无对应方法，只补齐该现有 service 的删除薄封装，不新增资源表；
4. workspace 删除成功（或明确不存在）后，沿用现有模板绑定、设备和 Bot 删除逻辑；
5. workspace 删除失败或结果不确定时返回错误，不得静默返回成功。

本期不新增 `DELETING`、`DELETE_PENDING_CLEANUP`、`DELETED` 资源状态，不新增 workspace reconcile。由于没有状态表或 Saga，删除不提供跨远端 workspace、Bot、设备和 Passport 的原子一致性：若 workspace 已删除而后续既有 Bot 删除步骤失败，Bot 可能暂时保留但可重试；该边界必须记录日志并在实现/验收中明确。

服务 Bot 删除仍必须走发布生命周期服务。即使未来开放 Application Coding 服务化，也不能复用普通 `DELETE /bots/{id}` 绕过 `RELEASED`/`UPGRADED` 历史检查。

## 12. 前端展示边界

OpenAPI 是公共服务契约，前端工作台的枚举不能作为唯一控制面。

前端应根据后端能力矩阵展示：

- `Claudecode-原生`：按现有云端、本地、服务能力展示；
- `Claudecode-AIcoding`：仅云端非服务；
- `Claudecode-应用 coding`：第一期仅云端个人非服务；
- 本地 Application Coding、服务 Application Coding：不展示且后端同步拒绝。

前端隐藏只是用户体验优化，真正的安全边界必须在后端：

```text
前端过滤 ≠ 能力授权
后端组合策略 + 依赖能力检查 = 最终准入
```

## 13. 测试计划

### 13.1 契约测试

覆盖：

- `template_type`/`template_config` 正常解析；
- 未知字段仍被拒绝；
- `template_config` 单独传入被拒绝；
- `template_config` 中的历史扁平字段和 `bot_template_config` 嵌套字段原样透传；
- `dima_space_id`、`template_uid` 等服务端字段被拒绝；
- 不支持模板类型被拒绝；
- OpenAPI 文档/schema 与实际请求一致。

### 13.2 组合策略测试

至少覆盖：

| 场景 | 预期 |
| --- | --- |
| `claude_code` + applicationCoding + 云端个人非服务 | 通过 |
| `aicoding` + applicationCoding + 云端个人非服务 | 拒绝；`aicoding` 是既有引擎值，不作为本期 Application Coding 的表达 |
| applicationCoding + 本地 | 拒绝 |
| applicationCoding + 服务 | 拒绝 |
| applicationCoding + 团队空间 | 第一期拒绝 |
| 非 Coding engine + applicationCoding | 拒绝 |
| 普通 Bot不传模板 | 兼容通过 |

### 13.3 Workspace Hosting 测试

- Workspace Hosting 未绑定时，创建前返回 503；
- workspace 创建失败时不返回成功 Bot；已有清理路径覆盖到的资源按现有逻辑清理，不把全量原子补偿作为本期承诺；
- workspace 标识正确写入 `ac_templates.ext.dima_space_id`；
- workspace 删除失败时删除接口返回错误；
- device 或 owner relationship 失败时不误报创建成功，且不把“全部资源已补偿清理”作为一期承诺；
- `dima_space_id`/workspace 标识正确写入现有 `ac_templates.ext`；
- 创建成功后 ready 状态不会早于 workspace 初始化完成。

### 13.4 异步授权测试

- POST 返回 202 后，授权成功仍保留完整模板参数；
- auth-status 重复查询不重复创建 Bot；
- 授权失败不产生 Bot；
- 模板配置较大或包含嵌套字段时不会在持久化/恢复中丢失；
- 转换函数不改变 key 层级，不把扁平字段错误包装到 `bot_template_config`；
- 授权回调重放具备幂等性；

### 13.5 路由与运行时测试

- `claude_code + normalCC` 仍路由到 claude_code adapter；
- `claude_code + applicationCoding` 路由到 aicoding adapter；
- `aicoding + applicationCoding` 被拒绝；`aicoding` 不传 `template_type` 时仍按既有 AI Coding 引擎路径回归；
- Skill/MCP、workspace、readiness 不因模板路由回归。

### 13.6 删除测试

- 非服务 Application Coding 删除会清理 hosted workspace；
- workspace 清理失败时删除不返回成功；
- 服务 Bot 普通删除仍被拒绝；
- `RELEASED`、`UPGRADED` 历史不会被误删；
- 下线后生成的新草稿不会误删整个服务 Bot。

## 14. 分期实施计划

### Phase 0：能力探测与契约准备

1. 复用 Workspace Hosting Service 的绑定检查，不新增独立 capability probe；
2. 增加 `template_type` 和 `template_config` 的契约定义；
3. 定义稳定错误码；
4. 补齐组合策略单测。

### Phase 1：开放 Application Coding 非服务创建

1. OpenAPI 接收模板参数；
2. 创建授权异步链路透传并恢复完整模板参数；
3. 创建前完成组合和 Workspace Hosting 检查；
4. 创建 workspace、模板记录和 Bot；
5. 以 `READY` 作为对外可用状态；
6. 开放云端个人非服务组合；
7. 完成契约、集成和回归测试。

### Phase 2：评估团队空间

只有在以下条件具备后再评估：

- workspace owner 与团队空间 owner 的映射规则；
- 团队成员、共同编辑和 workspace 权限的一致性；
- Bot 迁移、授权和删除的血缘规则；
- 团队 workspace 的回收与审计能力。

### Phase 3：评估服务化

只有在以下条件具备后再评估：

- Application Coding 发布版本与 workspace snapshot 的绑定；
- 预发/线上 workspace 隔离；
- 回滚、下线、重启和多实例语义；
- `RELEASED`/`UPGRADED` 历史资源清理；
- 服务 Bot 对话、健康检查和容器指标适配。

## 15. 风险、回滚与兼容性

### 15.1 主要风险

- OpenAPI 参数增加但异步授权链路未保存，导致模板丢失；
- Workspace Hosting 依赖不可用，产生半成品 Bot；
- `ACTIVE` 被误认为 Application Coding 已 ready；
- 把 Application Coding 误做成独立引擎，造成多个模块枚举不一致；
- 删除时只删 Bot 记录，遗留 DIMA workspace；本期先处理 `dima_space_id`，失败即返回；但在无状态表/Saga 前提下，不承诺跨资源删除原子性，后续步骤失败时允许保留可重试的 Bot 记录；
- 通过前端隐藏但后端未拒绝，导致非法组合可被直接调用。

### 15.2 回滚策略

- 通过关闭/移除 Workspace Hosting 绑定，使 Application Coding 创建前返回 503；
- 保留普通 Bot 与现有 `claude_code`/`aicoding` 创建逻辑；
- 已创建的 Application Coding Bot 不回滚为普通 Bot；遗留 workspace 按现有人工补救/运维流程处理；
- 任何新增模板字段均保持向后兼容，未传模板字段的请求行为不变；
- auth-status 未回传模板参数时拒绝 Application Coding 创建，不静默降级为普通 Bot。

### 15.3 数据兼容

- 现有普通 Bot 不增加隐含模板；
- `template_type` 为空仍表示普通 Bot；
- 历史 `applicationCoding` 数据按 readiness 兼容规则处理；
- 不对已有 `engine` 值做批量重写；
- 不新增 `claudecode-app` 数据值，避免后续迁移成本。

## 16. 预计修改范围

本次实施预计涉及以下边界，具体文件以实现时实际代码为准：

```text
src/backend/src/agentclaw/community/adapters/http/openapi_v1/bots/schemas.py
src/backend/src/agentclaw/community/adapters/http/openapi_v1/bots/router.py
src/backend/src/agentclaw/community/core/bot_management/create_flow.py
src/backend/src/agentclaw/community/core/bot_management/services/bot_service.py
src/backend/src/agentclaw/community/core/bot_management/readiness.py
src/backend/src/agentclaw/community/core/bot_inventory/policies/combo_policy.py
src/backend/tests/community/adapters/http/openapi_v1/test_bots_endpoints.py
相关 workspace hosting、授权参数透传和集成测试文件
```

如果实际改动触及架构边界，还必须同步更新相应的契约文档、上下文边界元数据和架构测试，而不能只修改 Python schema。

## 17. 最终结论

1. **可以支持 Application Coding。**
2. 第一阶段只支持：**云端 + 个人空间 + 非服务 Bot**。
3. 不新增 `claudecode-app` 引擎值。
4. 使用：
   ```text
   engine = claude_code
   template_type = applicationCoding
   ```
5. `claude_code + applicationCoding` 延续历史逻辑，运行时路由到 aicoding adapter。
6. OpenAPI 必须补齐 `template_type`、`template_config`，并保证 202 异步授权流程不丢参数；本期复用 auth-status echo，不新增 intent 表。
7. Workspace Hosting 未绑定或不可用时，创建前失败；workspace 创建失败时沿用现有清理路径，不新增 workspace 资源表。
8. Application Coding 的可用状态必须等待 workspace 初始化完成，不能仅以 `ACTIVE` 判断。
9. 本地、团队、服务 Application Coding 第一阶段全部拒绝；这既要前端不展示，也要后端组合策略强校验。
10. 普通服务 Bot 删除继续走发布生命周期；`RELEASED`、`UPGRADED` 属于正式发布历史，不得被普通删除逻辑误删。

本决策的核心原则是：**沿用历史上“引擎负责 runtime、模板负责业务模式”的模型，先开放后端已经具备真实闭环的最小组合，再按 workspace、协作和服务生命周期逐步扩展。**
