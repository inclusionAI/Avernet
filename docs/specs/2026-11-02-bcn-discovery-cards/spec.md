# BCN Discovery AgentCard / Group Card v1

- 状态：**Proposed / 待实现与联调**；日期：2026-11-02；更新：2026-11-03。
- 基线：仓库 `24c41c89f2bc31ba8f98a28bfe35abe9cb3eb51d`，不是仅以 PR #3196 为准。
- 所有者：BCS/BCN 负责群、成员、协作定义及持久化读契约；Discovery 服务负责检索、公开群筛选、Card 投影和搜索 HTTP/TR API；上游负责 local 编排。
- 实现语言：Python / Java 均可；本文的 SQL、JSON、YAML 和行为约束是契约，不依赖 Rust 类型、SDK 或运行时。
- 本次交付只有设计与示例，不新增数据库表、迁移、服务或可调用群端点。

## 1. 目标与边界

Discovery 接收上游 `query`，返回统一的推荐列表 `items[]`，Agent 与群混合排序、共用 `limit`。每项通过 `resourceType=agent/group` 标识业务类型，`card` 复用 **A2A 1.0 AgentCard 的字段结构**；群定义、bindings 和成员依赖 map 放在 extension 的 `params.composition` 中。依赖成员不因被群引用而成为顶层推荐项。

本期只发现 `visibility = 'public'` 的普通群中的 `manager_worker`、`state_machine`；不实现私有群 ACL、普通 chat 群、DM 群、写操作、运行态查询或 BCN 中转执行。数据库使用 MySQL 或 OceanBase **MySQL 模式**，由独立 Python/Java 服务只读访问。

**本期采用“读取已有配置并渲染”的职责边界：** 群为 public 即按公开群处理，不再检查成员公开性或 Provider 策略；manager、成员 Card、bindings 和定义版本按已有数据读取，不新增存在性、引用完整性或执行能力校验。本文假定渲染所需数据已具备；Agent 下线前归档关联群、依赖变化联动等保障机制留到后续，本期不实现，也不作为上线前置条件。Discovery 不增加 owner 审批或其他发布治理。

### 1.1 本期消费边界

| 对象 | interface | 消费方式 |
| --- | --- | --- |
| Agent | Bot 已登记的真实 A2A interface | 上游直接调用 |
| Manager–Worker 群 | `local://` + `protocolBinding=LOCAL` | 上游读取定义和群内成员 Cards 编排 |
| State Machine 群 | `local://` + `protocolBinding=LOCAL` | 上游读取 YAML、bindings 和群内成员 Cards 执行 |

**兼容性边界：LOCAL 是受控上游使用的临时占位约定，不是标准 A2A binding，也不是可调用的 A2A 服务。** 所有候选可以在 `items[]` 中混排，但 local 群必须在交给通用 A2A SDK 前分流，不能仅凭字段形状或 schema 校验结果调用。后续全部收敛为真实 A2A interface，迁移见附录 A。

- 不新增 `cardType` 或 `bcn_composition` 类型；上游不通过实现方名称选择执行方式。
- `groupKind`、`composition` 和 `revision` 是本扩展定义的业务字段，不是 A2A 标准字段。
- 短期不增加 `mode`、`direct_a2a/bcn_a2a` 等执行模式枚举；短期/长期由 `supportedInterfaces` 的 URL 与 `protocolBinding` 区分。
- 两种群都使用顶层 item 的 `resourceType=group`，用扩展中的 `groupKind` 区分；`resourceType` 只用于分类/检索，不是执行方式。
- `local` 以消费者为参照：表示消费者读取 composition 并编排成员，不表示成员模型运行在消费者机器上。

### 1.2 架构约束

HTTP 层 → Discovery 查询用例 → 只读 SQL adapter → MySQL/OB；渲染器只接受读取结果，不持有数据库连接、HTTP client 或驱动类型。数据源及 env 等由启动配置注入。

本文约定 BCS 与 Discovery 之间的只读数据映射；表/列变更需同步维护查询方，Discovery 不修改 BCS 数据。数据读取与 Card 渲染分层，不引入第二套群管理或执行逻辑。

遵循 [架构规则](../../arch/arch.rules.md)、[CI 约束](../../arch/ci.enforce.md)、[上下文边界](../../arch/context-boundary-format.md)、[协议测试规则](../../arch/protocol-contract-tests.md)。已核对 [ADR 目录](../../adr/)；未发现直接规定本 Discovery 导出格式的已接受 ADR，本文不修改已有 ADR。

## 2. Discovery API

对外采用上游搜索 Facade 的 `discover(request)` 契约，对应 HTTP `POST /api/v2/search`、`POST /api/v2/recommend`。这不是 A2A 标准方法，也不替换已有 BCS `/bots/discover` 的 `bots/count` 返回格式。本文约定 Card 投影和推荐项的消费语义，不另增 `/v1/discovery` 或对外分页协议；部署情况以实际 Facade/HTTP 契约为准。

以下为 TR DTO 的 JSON 表达；HTTP filters 使用对象，而不是 key/values 数组：

```json
{
  "query": "数据库诊断",
  "limit": 5,
  "filters": [{"key": "resource_type", "values": ["agent", "group"]}],
  "context": {
    "goal": {
      "description": "改善数据库性能",
      "constraints": {"maxCost": 100}
    },
    "task": {
      "id": "task-001",
      "description": "定位慢查询原因",
      "dependencies": ["task-000"]
    }
  }
}
```

`context.goal` / `context.task` 是开放的业务 JSON 对象，不是仅包含 `id/description` 的专用 ContextEntry 类型；上例中的 `constraints/dependencies` 仅演示业务可自定义字段。旧顶层 `goal/task` 仅作为既有调用方兼容入口。

| 输入 | 类型 / 默认值 | 规则 |
| --- | --- | --- |
| `query` | string，必填 | trim 后非空；最多 4096 个字符，与既有 Facade 校验一致 |
| `limit` | integer，缺省/null 为 5 | 正整数，超过服务端上限取上限；默认上限 20；Agent 和群共用 |
| `filters` | AgentFilter[]，缺省/null 为 [] | 最多 64 项、key 不重复；不同 key 为 AND，同 key 的 values 为 OR；空 values 无匹配 |
| `filters.resource_type` | `agent` / `group` | 不指定时混合搜索；只限制顶层推荐类型，不裁剪群依赖 |
| `context` | SearchContext，可选 | 新调用统一使用 `context`；其中 `goal/task` 均为可选开放业务 JSON 对象/`Map<String,Object>`，不限制为 `id/description`。对象内字段由业务方定义，可包含嵌套对象、数组、数字、布尔值和 null；字段缺失、null 或空对象不构成请求错误。本期 `context` 内容暂不参与搜索、模型输入或缓存键。旧顶层 `goal/task` 兼容接收，仅供既有调用方使用 |

其他 filters 仅在服务端配置支持时使用。HTTP 对应示例为 `{"resource_type":["group"]}`。服务端从认证上下文/部署配置确定 `env`，不能接受调用者任意传入数据库 env。公开是**目录可见性**，不等于匿名访问 Discovery 或成员调用免鉴权。

无匹配时的成功响应示意：

```json
{
  "success": true,
  "errorCode": "OK",
  "errorMessage": "",
  "items": [],
  "status": "ok",
  "reasons": [],
  "effective": {
    "limit": 5,
    "retrieval": {"keywordEnabled": true, "vectorEnabled": true, "rerankEnabled": true}
  }
}
```

每条 item：

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `id` | string | Agent 为 `bot_uuid`，Group 为 `group_id`；跨类型去重使用 `(resourceType,id)`，不能只看 id |
| `resourceType` | enum | `agent` / `group`；仅表示推荐对象类型，不是执行方式 |
| `providerName` | string | `resourceType=agent` 时输出，来自 `bcs_providers.name`；Group 不输出该字段 |
| `card` | ResourceCard | Agent/群使用同一公共 Card DTO；HTTP 为 JSON object，Java Facade 1.0.2+ 使用 `ResourceCard`；未知合法扩展保留，`extensions[].params` 仍为 `Map<String,Object>` |
| `score/scoreSource` | number/string 或 null | 既有搜索最终评分及来源，不是成功概率；默认 TopK 无评分时为 null |
| `scoreDetails` | object 或 null | keyword/vector/rrf/reranker 各阶段的 value/rank；沿用搜索契约，不由 SQL renderer 生成 |

HTTP 省略为空的评分字段，TR 用 null。`effective.retrieval` 的 HTTP 字段为 snake_case；开关为 true 不代表调用成功。上游按 `items` 顺序消费，不自行相加各阶段分数。

### 2.1 推荐与依赖隔离

- `items[]` **只包含独立推荐结果**，统一排序后最多返回 effective.limit 项；不再返回顶层 agents/groups、matchedAgentResourceIds 或两套 cursor。
- local 群的 `composition.agents` 为 `uuid → AgentDependency` map（见 3.5）；成员没有 item 包装、评分或推荐位次，不占用 limit。
- 同一 Agent 若独立被推荐，可同时出现在顶层和群内；群内副本不能因为顶层已有同一资源而省略。不同群也各自携带完整依赖。
- `resource_type=group` 时仍返回每群完整成员 map；同理，query 不匹配的成员也必须带上。依赖按当前 env 内的 UUID 读取，不做成员公开性、Provider 策略或推荐条件的二次筛选。
- 每个群作为一个自包含推荐项原子返回；不允许截断 bindings、YAML 或成员 map，不用其他 item 为它补缺。

### 2.2 检索与失败边界

关键词、向量、融合和重排由既有搜索链路负责；本文 SQL 是公开候选/元数据的读取方式，不定义新的跨类型评分算法。Agent 元数据沿用[既有 Card 注册/检索字段](../a2a/minimal-card-registration-v1.md)。renderer 按已召回 ID 读取元数据，不另做 label 匹配或二次过滤向量召回结果。

无匹配返回 `success=true,status=ok,items=[]`。本期不增加“完整性检查 → 省略群 → 补扫凑满 limit”的处理链；既有搜索链路的 `status/reasons` 语义保持不变。

数据库读取、JSON 解码或 Card 渲染实际发生异常时，沿用服务现有错误处理，不伪造成成功空列表，也不捏造 Card 或替换定义。公共错误码沿用 `INVALID_ARGUMENT/OVERLOADED/DEADLINE_EXCEEDED/UNAVAILABLE/INTERNAL`；业务失败为 `success=false,status=error`，网络/RPC 异常按实际传输契约处理。这不是新增业务完整性预检。

## 3. Card 公共字段

以下示例使用拟议 extension URI `https://schemas.example.com/bcn/discovery-card/v1`。该 URI 是稳定的逻辑标识，不要求作为网络文档地址被访问；上线前需冻结为项目控制的统一 URI，不能每次请求生成或由不同服务随意命名。

### 3.1 标准 AgentCard 部分

| 字段 | Agent | Manager–Worker | State Machine |
| --- | --- | --- | --- |
| `name` | 保存的 AgentCard.name | group.label；空时回退 group_id | 同左 |
| `description` | 保存的 description | `Manager–Worker 群：<name>` | `State Machine 群：<name>` |
| `version` | 保存的 AgentCard.version | `str(bcs_groups.version)` | 同左 |
| `supportedInterfaces` | 原有真实接口，保留偏好顺序 | 本期单个 LOCAL 占位接口 | 同左 |
| `capabilities` | 原样保留，包括第三方 extensions | 仅声明 BCN extension，不宣称可 streaming | 同左 |
| `defaultInputModes` | 保存的值 | `["text/plain"]` | `["text/plain"]` |
| `defaultOutputModes` | 保存的值 | `["text/plain"]` | `["text/plain"]` |
| `skills` | 保存的技能，不从群成员能力猜测 | 一项目录技能 `group.execute`，tag=`manager_worker` | 同左，tag=`state_machine` |
| `securitySchemes/securityRequirements` | 保留声明；scheme 支持 type/scheme/description/bearerFormat，requirements 为方案名到 scope 数组的映射，不含凭证值 | 本期不伪造调用鉴权配置 | 同左 |

群的 input/output/skill 是目录描述，默认以文本输入/输出展示，不承诺 LOCAL 地址提供 A2A 方法。Discovery 不据此筛除其他模态的群；具体执行能力由上游决定。

本期群固定为一个占位接口；group_id 编码为单个 path segment。该标记不是资源身份，也不是可解析的网络地址：

```json
{
  "url": "local://groups/group-mw-001",
  "protocolBinding": "LOCAL",
  "protocolVersion": "1.0"
}
```

CardInterface 使用 `url/protocolBinding/protocolVersion`，可带 `tenant`；透传的 `tenant` 不改变数据库 env。群占位接口固定使用 `local://` + `LOCAL`，不能标为真实 A2A binding。这里的 `protocolVersion=1.0` 不表示 LOCAL 地址提供 A2A 服务。

**LOCAL 是编排标记，不交给 A2A 网络客户端。** 上游读取 composition 并调用其中成员的真实接口；Agent 和长期群使用真实 A2A interface。Discovery 不检查上游是否支持该编排，也不对 LOCAL 入口做网络请求。

### 3.2 扩展容器

Agent 和群都通过 A2A extension 机制携带额外信息，但两者使用不同的 params 集合。

Agent 的来源 extension 携带第三方平台内 ID 与来源性质。资源主键位于 `items[].id`；`agentId/party` 仅放在来源 extension 的 params 中，不放在 item 或 Card 根部：

```json
{
  "uri": "https://example.org/a2a/extensions/source-agent/v1",
  "description": "来源平台标识",
  "required": false,
  "params": {
    "agentId": "third-party-agent-analyst-001",
    "party": "third_party"
  }
}
```

Local 群的 BCN extension 携带群分类、composition 和本次渲染 revision：

```json
{
  "uri": "https://schemas.example.com/bcn/discovery-card/v1",
  "description": "BCN discovery composition",
  "required": true,
  "params": {
    "groupKind": "manager_worker",
    "composition": {
      "definition": {},
      "bindings": {},
      "agents": {}
    },
    "revision": "sha256:<digest>"
  }
}
```

此块只展示字段结构，`<digest>` 和空 definition/bindings/agents 是占位内容；完整样例见第 10 节。

`capabilities.extensions` 及 extension 的 `uri/description/required/params` 使用 A2A 扩展机制；params 内业务字段由各扩展自身定义。同一 Card 中 BCN URI 最多出现一次，来源扩展 URI 的最终值需与 TR 联调冻结。Local 群 `required=true`；Agent 来源标识不改变原 A2A 调用协议，使用 `required=false`。

### 3.3 params 定义

| 路径 | 类型 | 必填条件 | 语义 |
| --- | --- | --- | --- |
| `agentId` | string | Agent 可选 | 来源平台内的 Agent ID，由提供方随 Card 注册；不是 `bot_uuid`、调用 URL 或跨平台 ID |
| `party` | string | Agent 可选 | 来源性质描述；本期按原样字符串透传，不强制枚举，例如 `first_party` / `third_party` |
| `groupKind` | enum | group | `manager_worker` / `state_machine`；Agent 不出现 |
| `composition` | object | local group | 包含 `definition`、`bindings`、`agents` 三个必填字段 |
| `composition.definition` | object | local group | 根据 `groupKind` 选择定义结构 |
| `composition.bindings` | object | local group | slot → AgentReference 数组，保留已存 bot_ids 的内容与基数 |
| `composition.agents` | `Map<string, AgentDependency>` | local group | key 为 `uuid`，value 见 3.5 |
| `revision` | string | local group | `sha256:` + 64 位小写十六进制；最终渲染群 Card 的内容摘要 |

Agent 不输出 composition；长期真实 A2A 群可整体省略 composition。本期不定义执行模式枚举，也不通过 mode 判断执行方式。Local 群渲染时填入 groupKind、composition 和 revision；真实 A2A 群不依赖这些字段进行调用。

版本区分：interface 的 `protocolVersion` 是 A2A 传输协议版本，LOCAL 例外见 3.1；extension URI `/v1` 是本契约版本；AgentCard `version` 是被描述资源的版本；群 `revision` 覆盖最终导出内容，避免仅看 group.version 而漏掉成员 Card 更新。

### 3.4 身份与 bindings

- Agent item 的 `id` 是已有 bot_uuid；Group item 的 `id` 是已有 group_id。binding.uuid 只是群内引用名，取值也是 bot_uuid。
- 这些 ID 是当前 BCS/env 作用域内的不透明字符串，不保证是 UUID，也不是 A2A URL 或跨环境全局 ID。上游只做精确匹配，不解析前缀或据此拼接调用地址。
- `providerName` 是注册来源平台名称，不是 A2A AgentCard 根字段。注册侧可在 Card 输入元数据中附带 providerName 并由 Facade 提升到 `items[].providerName`；BCN 输出路径仍以 `bcs_providers.name` 为权威投影来源，且不输出 `provider_id`、完整 Provider config 或凭证。独立 Agent item 与群内 AgentDependency 都携带该字段。
- `agentId` 与 `party` 是来源平台提供的扩展语义，存放在来源 extension 的 `params` 中，不是 item 或 Card 的顶层字段。它们描述来源侧身份/性质，不能替代 `bot_uuid`，也不参与群绑定或检索排序。
- **底层绑定引用就是已有 `bot_uuid`**，不创建第二套 Bot ID；不是 provider_bot_ref、agent_code、driver_bot 的别名，也不是数据库自增 id。

```json
{
  "analyst": [{
    "uuid": "bot-analyst-001"
  }]
}
```

AgentReference 仅包含必填字段 `uuid`，不重复携带成员 revision、`url` 或其他资源标识。数组保留现有 `bot_ids` 的基数：不能把多值绑定悄悄取第一项。相同 Bot 可被多个 slot 引用，在当前群的 composition.agents map 中只保存一份依赖。

bindings 按已保存的 slot 与 `bot_ids` 映射，保留数组顺序、基数和 UUID 原值，不 trim、不按节点引用限制为单 Bot，也不根据 YAML participants/required 做检查。空数组按原值输出。成员内容摘要只保存在 `agents[uuid].revision`，不做版本匹配校验。

Discovery 不检查 manager 数量、slot 声明、成员归属、引用存在性或依赖是否齐全；这些不属于本期读取/渲染职责。不按同名 Agent 搜索替代引用。

### 3.5 群内依赖 agents map

选择 **`composition.agents: Map<uuid, AgentDependency>`**，而不是数组或 slot→Card：bindings 已用 `uuid` 引用，map 可直接索引、群内按身份去重，也不会把角色绑定和成员元数据混在一起。执行顺序来自 definition，不来自 map 的键顺序；不另增 `agentCardsById` 字段。

`AgentDependency` 结构为：

```json
{
  "providerName": "platform_x",
  "revision": "sha256:<member-card-digest>",
  "card": {
    "name": "...",
    "supportedInterfaces": []
  }
}
```

- key 为 binding 的 uuid，即 BCN 内的 bot_uuid；`card` 是与独立 Agent 相同格式的完整 AgentCard，不是 URL、引用或搜索 item；不附 score/scoreSource。
- `providerName` 描述成员来源平台；第三方 `agentId` 仍然从 `card` 的来源 extension params.agentId 读取，并可将 party 专属字段随来源扩展原样透传。
- `AgentDependency.revision` 是该成员 Card 的内容摘要；同一 uuid 在一个群中只保存一份依赖及其摘要。
- 按 bindings 中的 uuid 收集并去重依赖，构造 map；不再额外比较 key 集合或检查引用闭包。不依赖顶层 items、不跨群补齐，不加入未引用成员；本期只读取 Bot Card，不扩展递归群依赖。
- Python 用字典、Java 用 Map；调用方通过 `agents[reference.uuid]` 查找。Discovery 不新增重复 key 审查或逐成员可导出性判断。
- 群内按 uuid 去重；群间、群与独立推荐之间保留必要副本。单次批量读取的成员数据可在内存中复用，序列化时完整内联。

因此单张 LOCAL 群 Card 携带定义、绑定和成员调用信息；这是输出结构，不代表 Discovery 已验证该群可执行。成员调用沿用已有鉴权要求。

## 4. Manager–Worker 群

顶层 item 使用 `resourceType=group`，扩展 `groupKind=manager_worker`；本期 interface 为 `local://` + `LOCAL`。

### 4.1 definition

```json
{
  "managerSlot": "manager",
  "workerSlots": ["worker:bot-worker-001"],
  "instruction": "由 manager 分解任务，分配给 worker 后汇总结果。"
}
```

| 字段 | 类型 | 规则 / 来源 |
| --- | --- | --- |
| `managerSlot` | string，必填 | 固定 `manager`；按已存 role=`manager` 的成员生成绑定，不重复检查数量 |
| `workerSlots` | string[]，必填 | 每个 role=`worker` 的成员一个 slot，命名 `worker:<bot_uuid>`；按原始 ID 的 UTF-8 字节序排序 |
| `instruction` | string，可选 | 非空 `bcs_groups.context`；不从聊天记录、opening message 或 service_spec 拼接 |

`workerSlots` 可以为空，不补默认 worker。按已有 role 映射 manager/worker，不用 `driver_bot` 替代角色，也不把 observer、driver、consultant 自动转为 worker。本期不增加 actor_kind/mode 或角色完整性检查。

非空 `context` 原样作为 instruction，不需要 owner 批准或内容审批。

### 4.2 上游消费说明

上游读取 instruction、manager/worker slots 及依赖 Cards，自行实现任务分配、成员调用和结果汇总。BCN 的 `bcs_assign_task` / `bcs_task_complete` 及 Session 上下文不会因返回 Card 自动迁移；如何适配由上游决定，不作为 Discovery 返回群的前置条件。

本 spec 不规定新的 manager 控制消息、调度/重试策略或适配器认证流程，也不输出没有来源的 `maxRounds/maxParallelWorkers/workerFailurePolicy/completionPolicy`。

## 5. State Machine 群

顶层 item 使用 `resourceType=group`，扩展 `groupKind=state_machine`；本期 interface 为 `local://` + `LOCAL`。

### 5.1 definition

| 字段 | 类型 | 来源 / 规则 |
| --- | --- | --- |
| `definitionId` | string，必填 | 当前群绑定的 default_definition_id |
| `definitionVersion` | integer，必填 | 当前群绑定的 default_definition_version；禁止查 latest 替换 |
| `mediaType` | string，必填 | 固定 `application/yaml` |
| `content` | string，必填 | 定义表的原始 authoring `yaml_text`，保持内容，不重新序列化 |
| `sha256` | string，必填 | `SHA-256(UTF-8(content))`，64 位小写十六进制，不带前缀 |

本期 **内联 YAML**：读取原始 `yaml_text` 字符串，不解析 YAML、不重新序列化、不访问 `external_uri`。Discovery 不检查语法、节点类型、运行时版本、图结构或上游执行支持范围。

数据库 `content_hash` 来源于 normalized JSON，不能作为 YAML 的 `sha256`；后者按实际输出字符串计算。本期读取路径假定原始 YAML 已保存，不增加 normalized/blob 转译、运行快照回退或缺失数据修复逻辑。

### 5.2 YAML 透传与执行边界

定义来源沿用[现有 authoring YAML](../../../apps/bcs/crates/tools/bcs-cli/bcs-coordination/references/custom-collaboration-schema.md)，Discovery 不另造格式或限定字段。下面只是一个示例，不是支持范围的白名单：

```yaml
name: 数据库诊断与复核
participants:
  analyst:
    required: true
  reviewer:
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      diagnose:
        kind: bot_task
        assignee: {type: bot_binding, binding: analyst}
        instruction: 分析 SQL 并形成诊断结论。
        transitions:
          complete: {targets: [review]}
      review:
        kind: bot_task
        assignee: {type: bot_binding, binding: reviewer}
        instruction: 复核诊断并输出最终报告。
        final_output: true
```

**BCN 的配置或运行时支持范围不等于上游支持范围。** loop/v2、HumanInput、judge、自定义节点和未知字段均随原始 YAML 透传，不据此过滤群或报“不支持”。由上游决定如何解释与执行。

### 5.3 bindings 渲染

读取 `bcs_group_runtime_bindings.participant_bindings_json`，当前结构示意：

```json
{
  "analyst": {"source": "manual", "bot_ids": ["bot-analyst-001"], "extensions": {}},
  "reviewer": {"source": "manual", "bot_ids": ["bot-reviewer-001"], "extensions": {}}
}
```

逐个读取保存的 slot 与 `bot_ids`，映射为 `{uuid}` 数组；引用的 Bot Card 放进 agents map。保留 slot 名、UUID 原值和数组顺序/基数，不检查 source、extensions、slot 声明、required、成员归属或 auto 模式。

本 DTO 的绑定投影只使用 slot 与 `bot_ids`，不以 source/extensions 的内容作为拒绝返回条件。不从 participant role 顺序或 driver_bot 推断绑定，也不解析 YAML 中的历史 `bot_id` 来补写绑定。

## 6. 数据来源与映射

| Card / 筛选 | 表与列 | 说明 |
| --- | --- | --- |
| 群公开性 | bcs_groups.visibility | 仅选择 public |
| 有效群记录 | bcs_groups.record_status | 选择 active |
| 群类别范围 | bcs_groups.group_kind | 选择 normal；不是 Card.groupKind |
| Card.groupKind | bcs_groups.group_strategy | manager_worker / state_machine |
| 群 ID / 名称 / 版本 | group_id / label / version | 直接映射资源标识与展示字段 |
| manager 提示 | bcs_groups.context | 非空时原样映射为 instruction |
| 成员 / 角色 | bcs_group_participants | 使用独立成员表，不读旧 participants JSON |
| 当前状态机配置 | bcs_group_runtime_bindings | active + current sentinel，见 7.3 |
| YAML / 绑定版本 | bcs_collaboration_definitions | 按已存 definition_id + version 读取 yaml_text |
| AgentCard | bcs_bots.agent_card | 原样使用已保存的完整 Card，不重新获取或验证 |
| Bot 归属 | bcs_bots.provider_id | 使用现有 Bot-owned Provider 关联 |
| providerName | bcs_providers.name | 仅取名称，不读取 config/disabled/collaboration_scope |

所有读取都限定当前 env。公开群直接使用已存成员和绑定，不再检查成员 public、删除/在线状态、Provider 策略或 manager/依赖完整性；独立推荐 Agent 沿用既有 Agent 搜索规则，不把群依赖当作独立推荐重新筛选。

本期是可复用群定义目录，沿用 active record 条件，不按最近 Session 的完成/关闭状态过滤，也不新增生命周期判断。Agent 下线或数据变更后的关联群处理不在本期实现。

## 7. 只读查询流程

### 7.1 读取约定

- 使用 MySQL-compatible driver / JDBC 和只读访问，每条关联带 env。
- 数据源按已完成现有迁移的 schema 使用，包括 Bot-owned Provider 与 agent_card 列；不新增 schema 扫描、迁移审批或新旧表双读逻辑。
- 群、绑定、定义和成员 Card 使用普通只读事务批量读取，读取后结束事务再渲染；不新增跨请求快照、CAS 或刷新状态机。Discovery 不访问 Card/YAML URL，不探活或调用 Agent。
- DB/连接/超时异常正常向上返回，不转成成功空列表。

以下 `?` 是逻辑绑定参数，Python/Java adapter 转换为驱动占位方式；动态 IN/OR 只生成 placeholder，值通过参数绑定。

### 7.2 第一步：读取已召回的公开群

检索和排序由既有搜索链路提供；renderer 只批量读取召回的 group_id，不再实现一套 label LIKE 检索或 keyset 补扫：

```sql
SELECT group_id, label, version, group_strategy, context
FROM bcs_groups
WHERE env = ?
  AND visibility = 'public'
  AND record_status = 'active'
  AND group_kind = 'normal'
  AND group_strategy IN ('manager_worker', 'state_machine')
  AND group_id IN (?, ?, ...);
```

按上述条件取得群元数据后，再读取相关成员与定义。返回顺序沿用搜索结果；不把 participants JOIN 到候选查询后再 LIMIT，以免截断成员或挤占推荐名额。

### 7.3 第二步：批量成员与当前状态机绑定

```sql
SELECT group_id, bot_uuid, role
FROM bcs_group_participants
WHERE env = ? AND group_id IN (?, ?, ...)
ORDER BY group_id, bot_uuid;
```

成员表仅对候选 manager-worker 群读取，用于角色映射；不为导出资格检查额外读取 actor_kind/mode。状态机依赖直接来自下述绑定。

只对候选 state_machine 群读取：

```sql
SELECT group_id, group_version, default_definition_id,
       default_definition_version, participant_bindings_json
FROM bcs_group_runtime_bindings
WHERE env = ?
  AND group_id IN (?, ?, ...)
  AND record_status = 'active'
  AND next_group_version = 2147483647
ORDER BY group_id, group_version DESC;
```

沿用当前 store 的 sentinel 语义，每群按 group_version DESC 取首条 current 行。不比较 `group_version` 与 `bcs_groups.version`，也不另加重复 current、绑定缺失或版本完整性检查。

不读 runtime sessions、runs、run snapshots；`auto_start_on_service_invocation` 是 BCN 内部启动策略，不影响 local 用户显式执行，不直接转成上游自动执行开关。

### 7.4 第三步：批量精确版本定义

去重 `(definition_id, definition_version)`，分块查询：

```sql
SELECT definition_id, version, yaml_text
FROM bcs_collaboration_definitions
WHERE env = ? AND record_status = 'active'
  AND ((definition_id = ? AND version = ?)
    OR (definition_id = ? AND version = ?));
```

OR 项由批量大小生成。使用已保存的 definition_id/version 作为查询键，不改查 latest，也不另做定义版本正确性或缺失/重复检查。本期没有 normalized_json/blob fallback。

### 7.5 第四步：批量成员 Bot 和 Provider

从 manager/worker 角色、状态机绑定及独立 Agent 检索结果收集 Bot UUID，按当前 env 去重、分块：

```sql
SELECT bot_uuid, provider_id, agent_card
FROM bcs_bots
WHERE env = ? AND bot_uuid IN (?, ?, ...);
```

按已保存 Card 直接组装，不再校验成员可见性、删除状态、Card 有效性或接口可达性。`agent_card_url` 是 Card 文档地址，不是调用入口，本请求不访问该地址。

Provider 仅用于获取名称：

```sql
SELECT provider_id, name
FROM bcs_providers
WHERE env = ? AND provider_id IN (?, ?, ...);
```

不读取 disabled/config/collaboration_scope，也不增加 Provider 可用性或授权复核。

### 7.6 第五步：渲染

```text
ranked_candidates = search_candidates(query, filters)
rows = read_public_candidates_and_dependencies(env, ranked_candidates)
end_read_transaction()

# 原样使用保存的 Card，并为群依赖计算内容 revision。
member_cards = wrap_saved_agent_cards(rows.bots, rows.providers)
# rendered_cards 以 (resourceType, id) 为 key，避免跨类型 ID 冲突。
rendered_cards = render_independently_recommended_agents(ranked_candidates, member_cards)
for group in rows.groups:
    if group.strategy == manager_worker:
        definition, slots = map_manager_worker_roles(group, rows.participants)
    else:
        binding = rows.current_bindings[group.group_id]
        source = rows.definitions[(binding.definition_id, binding.definition_version)]
        definition = wrap_raw_yaml(source)
        slots = map_saved_bot_ids(binding)
    bindings = render_uuid_references(slots)
    agents = inline_dependencies(slots, member_cards)
    rendered_cards[("group", group.group_id)] = render_group_card(group, definition, bindings, agents)

items = assemble_items_in_search_order(ranked_candidates, rendered_cards, effective_limit)
return search_response(items)
```

这是数据读取/映射示意，不包含成员公开性检查、角色/引用完整性检查、YAML 解析、执行能力校验或 Card 二次验证。bindings 的 JSON 解码只是为了读取 slot 与 bot_ids；不存在额外的业务预检步骤。

内部按 UUID 批量去重读取，再将每群依赖内联到自己的 agents map；不把 member_cards 追加到顶层 items。混合排序及评分元数据沿用搜索链路，renderer 不伪造评分，也不增加坏数据补扫。

### 7.7 内容 revision 与原样透传

1. Agent：原样使用完整第三方 Card，保留未知字段、扩展、skills、interfaces、agentId/party 和源 signatures；不注入 BCN extension 或修改签名内容。独立 Agent item 不输出 revision；providerName 放在 Card 外层，来自 Provider 表。
2. 群：固定标准字段与 extension，workerSlots 和无语义的集合排序，YAML content 保持原始字符串；bindings 数组顺序保留，agents map 键顺序无语义。
3. 先对每个成员的最终 AgentCard 按 RFC 8785 JCS 规范生成 UTF-8 字节并计算 SHA-256，得到 AgentDependency.revision，并将它与完整成员 Card 一起内联到 agents map；bindings 仅保存 uuid 引用。最后对整张群 Card 只去掉顶层 signatures 和当前群扩展 params.revision，按同样规范计算并填入 `revision="sha256:"+hex`。**不得递归删除内嵌成员的 revision**；群 hash 覆盖完整 map、bindings、定义和接口。
4. 不把当前时间、请求 ID、数据库读取顺序写入 Card；相同内容得到相同 revision。revision 仅标识内容变化，不是授权或签名。
5. 不新增验签、重签或签名信任体系。不改变源 Card 内容时保留签名；若其他功能修改被签内容，不能继续宣称原签名有效。
6. 源字段若不能用约定的 JSON/JCS 表达，返回明确数据错误；Python/Java 不能各自用不同浮点/排序规则近似计算。

## 8. 查询成本与异常处理

使用服务现有的请求 limit、批量查询大小、超时和响应大小配置；不在本协议增加多层固定“安全预算”或调整配额的审批要求。按候选批次读取相关数据，避免逐群、逐成员查询的 N+1；不为填满 limit 新增补扫循环。查询性能与部署容量在实现时验证，不作为群的发布资格。

群内 YAML、bindings、agents 作为完整字段输出，不为满足响应大小而截断其中内容。读取或序列化实际触及服务资源上限时，沿用服务的资源超限错误处理，不为此新增群可导出性状态。

| 情况 | 外部行为 |
| --- | --- |
| 非法 query/limit/filters | 沿用 INVALID_ARGUMENT |
| 无匹配推荐项 | success=true，items=[]，status=ok |
| DB 不可用/读取超时 | 沿用 UNAVAILABLE / DEADLINE_EXCEEDED，不能返回成功空列表 |
| 实际解码或渲染异常 | 沿用现有 INTERNAL 等错误处理，不增设业务完整性预检或自动修复 |
| 服务资源超限 | 沿用现有 OVERLOADED 等错误处理，不返回被截断的群定义或依赖 |

不增加导出专用业务判断和错误分类，既有搜索的降级 envelope 不变。

## 9. 公开范围与调用边界

- public 群按本 spec 输出 context、YAML、bindings 和依赖 Cards；不增加 owner 确认、内容审批、敏感信息扫描或扩展白名单。
- 不额外拼入 Provider config、session_token、群消息等内部字段；第三方 Card 原样透传，不因接口为内网地址而拒绝返回。
- Discovery 不增加 URL 审批、接口探活、签名或二次鉴权；实际调用继续遵循既有鉴权和网络策略。LOCAL 编排不会自动获得 BCN 的凭证、Session 或工具上下文。
- 不要求上游每次执行前重新发现，不以 revision 承诺即时撤销调用权限；缓存或后续持久化 Card 的更新机制另按实际需求定义。

## 10. 完整示例与验证边界

- [Agent 示例](examples/agent.json)：原样保存的远端 Card，providerName 放在外层。
- [Manager–Worker 群](examples/manager-worker.json)：一个 manager、一个 worker，完整内嵌 agents map。
- [State Machine 群](examples/state-machine.json)：analyst → reviewer，完整内嵌 agents map。
- [原始 YAML](examples/state-machine.yaml)：与 State Machine Card.content 完全相同。
- [完整 Discovery 响应](examples/discovery-response.json)：统一 items 含一个独立推荐 Agent 和两种群；群依赖只内嵌，bindings 仅引用 uuid，内容摘要保留在依赖与群级别。对应 query=`数据库诊断`、limit=5；被推荐的 analyst 也作为状态机依赖保留完整副本。评分仅为示意。
- [来源核对与本次验证](research.md)：仓库事实、设计选择、未验证边界。

示例使用保留示例域名/虚构 ID，不是线上地址或实际注册快照。静态示例检查不代表 SDK conformance 或上游可执行性；Discovery 不因此新增请求期 Card validator 或执行能力探测。

## 11. 实施验收

验收验证读取与字段映射，不把静态样例断言变成请求期业务校验。

| 层次 | 必须覆盖 |
| --- | --- |
| 字段/序列化 | items/resourceType/providerName；agentId/party 及未知字段保留；群 groupKind/composition/revision 输出 |
| LOCAL 分流 | local:// + LOCAL 交给上游编排器，不向群占位地址发送网络请求 |
| 筛选边界 | 群按 env/public/active/normal/两种 strategy 读取；不二次检查成员公开性、删除/在线状态或 Provider 策略 |
| manager 映射 | 按保存的 manager/worker 角色生成 slots；不以 driver 替代角色；context 原样输出；不新增角色数量/模式预检 |
| state machine 映射 | 沿用 current sentinel 取首条；按已存 definition_id/version 读取；yaml_text 原样输出，不与群版本比较、不解析或校验执行支持范围 |
| YAML 透传 | 含 loop/v2、HumanInput、judge、自定义/未知字段的已存 YAML 仍原样输出；不新增内容审批或语法审核 |
| bindings/依赖 | 每条引用仅含 uuid；UUID 原值、顺序和基数保留，包括多值/空数组；按 UUID 组装 map；不做 slot/required/引用闭包预检；不因 query/filter 裁剪成员 |
| revision | 成员 revision 仅保存在 agents map 的依赖对象；群 hash 覆盖内嵌内容；YAML sha256 来自输出字符串；不同无语义行序结果一致 |
| 推荐隔离 | 同成员多 slot 在群内去重；顶层与跨群保留副本；依赖不占推荐 limit、不成为额外推荐 |
| 查询与异常 | 参数化 SQL、批量读取、limit 与排序；异常不伪装成功空列表；沿用服务超时/大小限制，不新增补扫或专项错误分类 |
| 数据库适配 | Python/Java 对目标 MySQL/OB 验证查询、Unicode 和 JSON 读取，确认同一输入的渲染结果一致 |

Python/Java 可使用同一组语言无关 fixtures 验证字段映射。上游执行器适配与端到端运行由对应调用方验证，不作为 Discovery 公开返回群的额外门槛。

本次仅修改设计文档并检查静态示例；**未运行真实 MySQL/OB SQL、A2A SDK validator、上游编排或生产集成测试**。本期不新增服务实现，也不把文档检查当作线上验证。

## 附录 A：后续演进与兼容性

| 字段/行为 | 短期 | 长期 |
| --- | --- | --- |
| item.id/resourceType、groupKind | Agent/Group 的身份与类型 | 保持稳定；resourceType=group 不意味着 local |
| supportedInterfaces | Agent 真实；群 local:// + LOCAL | 全部使用真实 A2A 地址和 binding |
| composition（含 agents map） | local 群必填，自包含 | 可整体省略；普通调用不依赖定义或成员 |
| 调用、状态、取消、凭证 | 上游管理成员调用 | BCN + Group-as-a-Bot 对外承担 |

长期 remote 群只用本扩展携带身份/分类、无需上游参与编排时，extension `required=false`，使普通 A2A 客户端无需理解群内部结构即可调用。

切换 URL 或 protocolBinding 必须产生新 revision。已开始的 local 执行固定其旧定义/成员快照，不在中途自动迁移；新调用使用新 Card。真实 A2A 调用失败时不自动回退 local，避免重复任务。

长期上游统一按 A2A 处理，不需要感知 BCN、中转路径或群内部结构。`bot_uuid` / `group_id` 保持不透明，不能据此拼接调用地址。普通 Agent 从直连 URL 改为 BCN 代理 URL 仍是真实 A2A 调用；群由 Group-as-a-Bot 暴露同样的 A2A 接口。

真实 interface 示意（地址由部署配置/登记信息提供，不是硬编码路由要求）：

```json
{
  "url": "https://agents.example.com/a2a/groups/group-mw-001",
  "protocolBinding": "HTTP+JSON",
  "protocolVersion": "1.0"
}
```

这是**执行契约变化**，不只是改 URL：同步替换 LOCAL binding 为真实 A2A binding、省略不再需要的 composition，并发布新 revision。先上线真实 A2A 执行能力，再切换 Card；不能用只返回“不支持”的假服务冒充完成迁移。旧 local 执行完成且旧缓存失效后可删除上游 LOCAL 兼容分支。

本次从原提案的 agents/groups、双游标和假 HTTPS interface 切换为 items、群内依赖 map、LOCAL，属于待实现契约修订，不宣称已部署兼容。Discovery 投影方、搜索 HTTP/TR DTO 文档和上游执行入口需同步升级；不新增 cardType，不修改数据库 schema，也不在同一新响应中保留两套返回结构。
