# BCN Discovery AgentCard / Group Card v1

- 状态：**Proposed / 待实现与联调**；日期：2026-11-02。
- 基线：仓库 `24c41c89f2bc31ba8f98a28bfe35abe9cb3eb51d`，不是仅以 PR #3196 为准。
- 所有者：BCS/BCN 负责群、成员、协作定义及持久化读契约；Discovery 服务负责检索、公开导出策略、Card 投影和 HTTP API；上游负责 local 编排。
- 实现语言：Python / Java 均可；本文的 SQL、JSON、YAML 和行为约束是契约，不依赖 Rust 类型、SDK 或运行时。
- 本次交付只有设计与示例，不新增数据库表、迁移、服务或可调用群端点。

## 1. 目标与边界

Discovery 接收上游 `query`，返回两个数组：`agents` 和 `groups`。数组元素均采用 **A2A 1.0 AgentCard 的字段结构**，群定义放在标准 extension 的 `params` 中。

本期只发现 `visibility = 'public'` 的普通群中的 `manager_worker`、`state_machine`；不实现私有群 ACL、普通 chat 群、DM 群、写操作、运行态查询或 BCN 中转执行。数据库使用 MySQL 或 OceanBase **MySQL 模式**，由独立 Python/Java 服务只读访问。

### 1.1 短期与长期

| 对象 | 短期 `execution.mode` | 短期 interface | 短期消费方式 | 长期 |
| --- | --- | --- | --- | --- |
| Agent | `remote` | Bot 已登记的真实 A2A interface | 上游直接调用 Agent | 换成 BCN 真实 interface，仍为 `remote` |
| Manager–Worker 群 | `local` | 不可调用的占位 interface | 上游读取 manager/worker 定义并执行 | `remote`，调用 BCN Group-as-a-Bot |
| State Machine 群 | `local` | 不可调用的占位 interface | 上游读取 YAML、bindings 并执行 | `remote`，调用 BCN Group-as-a-Bot |

**兼容性边界：占位 interface 只能满足字段形状，不能让群成为真正可调用、完整符合 A2A 服务语义的 Agent。** 本期 `groups[]` 是受 BCN 扩展约束的组合定义目录，不得混入通用可调用 Agent 注册表。上游不能只做 AgentCard schema 校验后就调用它。

- `execution`、`mode`、`resourceKind`、`groupKind`、`composition` **都不是 A2A 标准字段**。
- `remote/local` 以消费者为参照：`local` 表示消费者编排，不表示成员模型运行在消费者机器上。
- 不增加 `direct_a2a/bcn_a2a` 枚举；真实访问地址及绑定协议由 `supportedInterfaces` 决定。
- `resourceKind` 是本契约的 kind 字段；两种群都填 `group`，用 `groupKind` 区分。

### 1.2 架构约束

HTTP 层 → Discovery 查询用例 → 只读 SQL adapter → MySQL/OB；渲染器只接受查询快照，不持有数据库连接、HTTP client 或驱动类型。数据源、namespace 等由启动配置注入。

直接读表是**需双方确认的版本化读契约**，不是把 BCS 所有内部表永久开放为公共 API。实施前由 BCS 与 Discovery owner 确认本文的列、语义、兼容测试和 schema readiness；不得由 Discovery 修改 BCS 数据。若实际部署边界禁止跨服务读表，应先走架构批准/限期 waiver，而不是用本文绕过架构规则。替代方案是 BCS 提供等价只读视图或服务 API。

遵循 [架构规则](../../arch/arch.rules.md)、[CI 约束](../../arch/ci.enforce.md)、[上下文边界](../../arch/context-boundary-format.md)、[协议测试规则](../../arch/protocol-contract-tests.md)。已核对 [ADR 目录](../../adr/)；未发现直接规定本 Discovery 导出格式的已接受 ADR，本文不修改已有 ADR。

## 2. Discovery API

拟新增独立服务契约 `POST /v1/discovery`，**不是 A2A 标准方法**，也不替换已有 BCS `/bots/discover` 的 `bots/count` 返回格式。

```json
{
  "query": "数据库诊断",
  "agentLimit": 20,
  "groupLimit": 20,
  "agentCursor": null,
  "groupCursor": null
}
```

| 输入 | 类型 / 默认值 | 规则 |
| --- | --- | --- |
| `query` | string，默认 `""` | trim；最多 256 个 Unicode 字符；空值列举公开候选 |
| `agentLimit` | integer，20 | 0..100；0 表示不检索独立 Agent，但仍补齐群依赖 |
| `groupLimit` | integer，20 | 0..100；0 表示不检索群；两者不能同时为 0 |
| `agentCursor/groupCursor` | string 或 null | 独立、不透明、服务端签名的游标；绑定 query、可信作用域及契约版本 |

服务端从认证上下文/部署配置确定 `env` 和资源 `namespace`，不能接受调用者任意传入数据库 env。公开是**目录可见性**，不等于匿名访问 Discovery 或成员调用免鉴权。

```json
{
  "agents": [],
  "groups": [],
  "page": {
    "matchedAgentResourceIds": [],
    "nextAgentCursor": null,
    "nextGroupCursor": null,
    "partial": false,
    "omittedGroupCount": 0
  }
}
```

- `agents[]` = 独立 Agent 检索结果 **并集** 本页群引用的成员 Cards，按 `resourceId` 去重；每个引用必须在同一响应中解析成功。
- `matchedAgentResourceIds` 只标识独立检索命中项，避免把为群补齐的成员误当作 query 命中。`agentLimit` 仅限制独立命中项。
- `groups[]` 只放群 Card；群必须包含所有执行所需 bindings，不发布“少一个 worker 但看起来成功”的卡片。
- 每组依赖闭包原子返回；如响应空间不足，整组留待下一页，不能只返回群、不返回其成员。
- 群 v1 检索是 **label 的大小写不敏感字面子串匹配**，不是语义/向量检索，不搜索 YAML 或私有运行数据。`%`、`_`、`!` 均按普通字符处理。
- Agent 检索沿用 [现有 Card 注册/检索语义](../a2a/minimal-card-registration-v1.md)：本地别名、Bot ID、Card name/description/skills；不能把全文 JSON 字符串匹配冒充字段匹配。
- 群按数据库 `id ASC` keyset 扫描；Agent 同样采用确定性 keyset。不做未经定义的相关性评分。
- 游标表示**已消费候选的位置**，不是最后一张成功渲染的 Card。坏数据被跳过时仍推进；因本页容量而推迟的候选不得被消费。
- 不返回伪精确 `total`。跨页不保持数据库事务；目录变化时允许结果变化，不承诺多页快照。
- 没有更多候选时相应 cursor 为 null；触及扫描预算但尚未扫完时保留 cursor，允许空页带 cursor。

`partial=true` 表示候选因损坏、不可导出或单项超限被省略，并给出 `omittedGroupCount`；正常分页/依赖容量导致的延后不是 partial。候选 ID、内部 SQL 错误、私有成员标识只进入受控诊断，不在公开响应中展开。

## 3. Card 公共字段

以下示例使用占位 namespace `demo` 和 extension URI `https://schemas.example.com/bcn/discovery-card/v1`。后者是**拟议标识**，上线前需冻结为项目控制的统一 URI；不能每次请求生成或由不同服务随意命名。

### 3.1 标准 AgentCard 部分

| 字段 | Agent | Manager–Worker | State Machine |
| --- | --- | --- | --- |
| `name` | 保存的 AgentCard.name | group.label；空时回退 group_id | 同左 |
| `description` | 保存的 description | `Manager–Worker 群：<name>` | `State Machine 群：<name>` |
| `version` | 保存的 AgentCard.version | `str(bcs_groups.version)` | 同左 |
| `supportedInterfaces` | 原有真实接口，保留偏好顺序 | 本期占位接口 | 本期占位接口 |
| `capabilities` | 保留原能力，合并 BCN extension | 仅声明 BCN extension，不宣称可 streaming | 同左 |
| `defaultInputModes` | 保存的值 | `["text/plain"]` | `["text/plain"]` |
| `defaultOutputModes` | 保存的值 | `["text/plain"]` | `["text/plain"]` |
| `skills` | 保存的技能，不从群成员能力猜测 | 一项目录技能 `group.execute`，tag=`manager_worker` | 同左，tag=`state_machine` |
| `securitySchemes/securityRequirements` | 保留声明，不含凭证值 | 本期不伪造调用鉴权配置 | 同左 |

群的 input/output/skill 描述的是 **local runtime 的目录契约**，不承诺占位 URL 提供对应 A2A 方法。本期只承诺文本输入/最终文本输出；需要其他模态的群应报不支持，不静默转换。

本期群固定占位接口如下；真实 URL 中的 group_id 应编码为一个 path segment：

```json
{
  "url": "https://non-invocable.invalid/groups/group-mw-001",
  "protocolBinding": "JSONRPC",
  "protocolVersion": "1.0"
}
```

上游必须先识别 BCN 扩展和 `execution.mode`，禁止对 local interface 发起连接、健康探测、重试或附加凭证。不能依赖 `.invalid` 恰好不可解析来保证安全。不理解扩展的上游必须拒绝 local 群。

### 3.2 扩展容器

```json
{
  "capabilities": {
    "extensions": [{
      "uri": "https://schemas.example.com/bcn/discovery-card/v1",
      "description": "BCN discovery identity and composition",
      "required": true,
      "params": {
        "resourceId": "bcn://demo/groups/group-mw-001",
        "resourceKind": "group",
        "groupKind": "manager_worker",
        "revision": "sha256:<digest>",
        "execution": {
          "mode": "local",
          "runtimeProfile": "bcn.manager-worker.a2a/v1"
        },
        "composition": {
          "definition": {},
          "bindings": {}
        }
      }
    }]
  }
}
```

此块只展示字段结构，`<digest>` 和空 definition/bindings 不是可通过验证的完整示例。完整样例见第 11 节。

`capabilities.extensions`、extension 的 `uri/description/required/params` 属于 A2A 扩展机制；**params 内的全部业务字段属于本 spec**。同一 Card 中 BCN URI 最多出现一次。local 群 `required=true`；remote Agent 的身份注解不改变原调用协议，使用 `required=false`。

### 3.3 params 定义

| 路径 | 类型 | 必填条件 | 语义 |
| --- | --- | --- | --- |
| `resourceId` | string URI | 全部 | 稳定身份；不是调用 URL |
| `resourceKind` | enum | 全部 | `agent` / `group` |
| `groupKind` | enum | group | `manager_worker` / `state_machine`；agent 不出现 |
| `revision` | string | 全部 | `sha256:` + 64 位小写十六进制；本次渲染快照标识 |
| `execution.mode` | enum | 全部 | `remote` / `local`；未知值拒绝 |
| `execution.runtimeProfile` | string | local | 编排解释规则的版本标识；不是 A2A protocolVersion |
| `composition.definition` | object | local group | 根据 groupKind 选择定义结构 |
| `composition.bindings` | object | local group | slot → 非空 AgentReference 数组 |

remote Agent 不输出 composition；长期 remote group 可不输出。字段缺省不能解释成 local；缺少执行模式的 BCN Card 是无效 Card。

版本区分：`protocolVersion` 是 A2A 版本；extension URI `/v1` 是本契约版本；AgentCard `version` 是被描述资源的版本；`revision` 覆盖**最终导出内容**，避免仅看 group.version 而漏掉成员 Card 更新。

### 3.4 身份与 bindings

- Agent：`bcn://<namespace>/agents/<encoded-bot_uuid>`。
- Group：`bcn://<namespace>/groups/<encoded-group_id>`。
- namespace 由配置映射到唯一数据库作用域，至少隔离部署/环境；若有多租户还必须隔离租户，不能假定数据库已有 tenant 列。
- ID 作为单个 URI path segment 百分号编码；解码后查询条件仍必须携带受信 env，不允许 URI 跨作用域选库。
- **底层引用就是已有 `bot_uuid`**，不创建第二套 Bot ID；不是 provider_bot_ref、agent_code、driver_bot 的别名，也不是数据库自增 id。

```json
{
  "analyst": [{
    "resourceId": "bcn://demo/agents/bot-analyst-001",
    "revision": "sha256:<member-card-digest>"
  }]
}
```

AgentReference 只有 `resourceId`、`revision` 两个必填字段，不再冗余放 `url/botUuid`。数组保留现有 `bot_ids` 的基数：不能把多值绑定悄悄取第一项。相同 Bot 可被多个 slot 引用，成员 Cards 去重即可。

当前 state-machine runtime 要求**被节点 assignee 引用的 slot 恰好一个 Bot**；非节点引用的 slot 可有多个。v1 遵守该限制，不将数组自动解释成 fan-out。未绑定、非 required 且未被引用的 slot 可以省略；其他缺失绑定使整组无效。成员匹配到的 revision 必须与引用完全一致。

存储绑定中的 trim/唯一性检查完成后保留数组顺序；不得按 display name 查找替代 Bot。对暂时无法解析的引用，也不能在执行时用另一个同名 Agent 自动补位。

## 4. Manager–Worker 群

`resourceKind=group`，`groupKind=manager_worker`，本期 `mode=local`，runtimeProfile 为 `bcn.manager-worker.a2a/v1`。

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
| `managerSlot` | string，必填 | 固定 `manager`；对应 role=`manager` 的成员；必须恰好一个 |
| `workerSlots` | string[]，必填 | 每个 role=`worker` 的成员一个 slot，命名 `worker:<bot_uuid>`；按原始 ID 的 UTF-8 字节序排序 |
| `instruction` | string，可选 | 非空 `bcs_groups.context`；不从聊天记录、opening message 或 service_spec 拼接 |

`workerSlots` 可以为空，表示 manager 自行完成，不能凭空补 worker。每个 manager/worker slot 的 binding 数组恰好一个元素。`driver_bot` 不一定等于 manager，禁止用 driver 替代 role 判断。observer、driver、consultant 角色不自动转成 worker。

本期执行参与者必须为 `actor_kind=bot` 且 `mode=auto`。执行角色为 Human、muted Bot 或未知模式的群不符合本期导出 profile，整组省略并诊断；不是删除这些成员后另造一个群。非执行观察者不导出身份，也不承诺复制 BCN 的观察者消息投影。

`context` 是执行提示而不是天然安全的公开简介。本 spec 的发布前提是 owner 确认公开群的 context 可外发；未确认时不能悄悄去掉有语义的 instruction 后宣称等价。

### 4.2 上游执行语义与限制

1. 上游把用户输入、instruction、可用 worker slot 交给 manager。
2. manager 决定分配任务；上游只允许引用当前 bindings 中的 worker，负责调用其真实 Agent interface。
3. worker 的终态结果/失败返回 manager；manager 决定继续分配或给出最终结果。
4. 调用者负责超时、取消、预算、并发和失败上限；不把任意一次 manager 文本回复误判成群完成。

**A2A 本身不定义 manager 的任务分配协议。** 当前 BCN 的 `bcs_assign_task` / `bcs_task_complete` 工具和 Session 语义不能仅靠复制 Card 获得。该 runtimeProfile 是拟议的上游兼容性契约：上线前必须证明 manager 的任务分配与完成信号能被上游适配器接收、转换，且不会仍然向旧 BCN Session 派发任务。

本 spec 定义发现/导出的字段，不新增一套 manager 控制消息协议，也不声称所有现有 manager Bot 已具备可移植执行能力。无兼容适配器的上游只能展示群，执行必须报 `UNSUPPORTED_RUNTIME_PROFILE`。发布 local 可执行功能的验收必须包括一次真实 manager → worker → manager 闭环。

不输出没有可靠来源的 `maxRounds/maxParallelWorkers/workerFailurePolicy/completionPolicy`。这些若以后成为跨端协议，需补来源、默认值和版本契约；不能从已退役的 `service_spec` 推导。

## 5. State Machine 群

`resourceKind=group`，`groupKind=state_machine`，本期 `mode=local`，runtimeProfile 为 `bcn.state-machine.a2a/v1`。

### 5.1 definition

| 字段 | 类型 | 来源 / 规则 |
| --- | --- | --- |
| `definitionId` | string，必填 | 当前群绑定的 default_definition_id |
| `definitionVersion` | integer，必填 | 当前群绑定的 default_definition_version；禁止查 latest 替换 |
| `mediaType` | string，必填 | 固定 `application/yaml` |
| `content` | string，必填 | 定义表的原始 authoring `yaml_text`，保持内容，不重新序列化 |
| `sha256` | string，必填 | `SHA-256(UTF-8(content))`，64 位小写十六进制，不带前缀 |
| `sourceContentHash` | string，可选 | 定义表 content_hash，仅供存储来源核对；不是 content 的校验和 |

本期 **内联 YAML**，不输出额外 definition URL，不访问 `external_uri`。大小上限 256 KiB UTF-8；只允许一个文档、拒绝重复键和自定义 tag，使用安全解析器并限制嵌套/alias 展开。

数据库 content_hash 按 normalized JSON 计算，**不能直接复制为 YAML 的 sha256**。缺失 yaml_text、只有 normalized_json/blob 的历史数据本期报 `DEFINITION_SOURCE_UNAVAILABLE`。未来可增加经兼容测试的 normalized→authoring 导出器，但不能直接把内部 JSON dump 成 YAML，或隐式读取执行快照代替群默认定义。

### 5.2 YAML 与执行 profile

采用[现有 authoring YAML](../../../apps/bcs/crates/tools/bcs-cli/bcs-coordination/references/custom-collaboration-schema.md)，不是新造一套 `states/on/guards` 格式。顶层只允许 `name/metadata/participants/runtime`；真实 Bot ID 只存在外部 bindings，不嵌入 YAML。

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

v1 最小 local profile 支持 `version=1`、acyclic、bot_task、complete 转移、分叉/汇合和唯一 final_output；节点接收原始输入及直接父节点的 artifacts。并行汇合等待全部直接父节点成功；节点失败/超时使本次执行失败，不偷偷跳过依赖。节点重试/超时遵循 YAML 的相应设置；对有副作用调用，自动重试必须有幂等保障，否则失败退出。

上游未实现的字段/行为不能被忽略。HumanInput、judge、loop/v2、runtime actor、BCN 特有 projection/扩展行为若影响执行，则本期不作为可执行 local 群发布，报 `UNSUPPORTED_DEFINITION_FEATURE`；需要它们时升级 profile 并增加 conformance。语法有效不等于上游能执行。

### 5.3 bindings 渲染

读取 `bcs_group_runtime_bindings.participant_bindings_json`，当前结构示意：

```json
{
  "analyst": {"source": "manual", "bot_ids": ["bot-analyst-001"], "extensions": {}},
  "reviewer": {"source": "manual", "bot_ids": ["bot-reviewer-001"], "extensions": {}}
}
```

校验：source 必须 `manual`；slot 必须在 YAML participants 声明；ID trim 后非空、无重复，并属于同 env 的当前群成员；执行引用必须是 auto Bot；required 或被节点引用的 slot 不得缺失。引用 slot 基数限制见 3.4。

不从 participant role 顺序推断 slot，不用 driver_bot 补缺失绑定。不支持 legacy YAML `bot_id` 回退；发现该形态应要求修复定义/绑定。未知非空 binding extensions 不丢弃后继续执行：除非已证明纯展示属性，否则视为 profile 不支持。

## 6. 数据来源与映射

| Card / 判定 | 表与列 | 说明 |
| --- | --- | --- |
| 群公开性 | bcs_groups.visibility | 必须 public；NULL/未知值拒绝 |
| 有效群记录 | bcs_groups.record_status | 必须 active |
| 群类别范围 | bcs_groups.group_kind | 必须 normal；**不是 Card.groupKind** |
| Card.groupKind | bcs_groups.group_strategy | manager_worker / state_machine |
| 群 ID / 名称 / 版本 | group_id / label / version | id 自增列只用于内部 keyset |
| manager 提示 | bcs_groups.context | 只作为已批准公开的执行 instruction |
| 真实成员 / 角色 | bcs_group_participants | 不读 bcs_groups.participants 旧 JSON |
| 当前状态机配置 | bcs_group_runtime_bindings | active + current sentinel，见 7.3 |
| YAML / 精确版本 | bcs_collaboration_definitions | env + definition_id + version + active |
| AgentCard | bcs_bots.agent_card | 完整保存的 Card；不是 agent_card_url |
| Bot 作用域/公开性 | bcs_bots.env/visibility/actor_kind/is_deleted | 不允许 Human ID 当 Bot 卡导出 |
| Bot 归属 | bcs_bots.provider_id | 采用 migration 029 后经确认的 Bot-owned projection |
| Provider 可用性/边界 | bcs_providers.disabled/config.collaboration_scope | 只读取必要策略投影，不读取或导出完整 config |

这里的“只筛选公开群”不需要 friend、owner、组织、邀请等私有 ACL 查询。但公开候选仍需通过记录完整性和导出 profile 校验。

`status`、`lifecycle_status`、`record_status` 不是同一个概念。本期是**可复用群定义目录**，沿用公开目录的 active record 边界，不按最近会话的完成/关闭状态筛掉定义，也不新增 `lifecycle_status='active'` 假定。如产品以后只发布“当前可接受 BCN 调用”的群，应单独定义该资格，不能把 Session 状态冒充访问控制。

数据库可读不等于可以外发。所有实际导出的 Bot 必须 public、非 deleted、actor_kind=bot，拥有有效已保存 A2A Card；绑定 Provider 必须存在、未 disabled、collaboration_scope 为 standard。缺省 scope 只在 config 是有效对象且字段真正缺失时视为 standard；显式 null、未知类型或未知值失败关闭。成员 Bot 离线不自动删 Card，在线状态不是接口可达性保证。

这一条复核现有公开群/Provider 边界，防止历史数据或策略变更绕过写入期约束；不另外设计私有授权系统。如果已保存 Card 含不允许外发的私有 interface/凭证，整张 Card 不可导出；不能为了凑齐群依赖而捏造替代 URL。公开性也不能给上游转授 BCN 持有的 Provider 凭证。需要由 BCN 代持凭证才能调用的成员，应等待长期代理方案，或先有合法的上游独立授权。

## 7. 只读查询流程

### 7.1 部署前检查与一致性

1. 使用 MySQL-compatible driver / JDBC 数据源、utf8mb4、只读账户；每条关联都带 env。
2. 启动时核验所需表/列与读语义。`001_init_schema.sql` 不是现状全量 schema，至少需要包含 `029_bot_provider_storage` 和 `039_bot_a2a` 的完整迁移链及相关绑定列。
3. migration 029 的列存在不等于数据已切换：确认 provider affiliation backfill 和 writer fence 已完成。未完成就停止启用该读路径，不同时猜测新旧 Provider 表。
4. 每个响应在**同一个 pinned connection 的只读一致性快照**中取得群、成员、绑定、定义及 Bot/Provider 数据；由驱动/部署适配 MySQL 与实际 OB 版本的事务配置，并通过并发更新测试证明无混读。无法提供一致性则不以 autocommit 多次 SELECT 冒充快照。
5. 取完有界数据后提交/结束读取，再解析、哈希、渲染；事务内禁止 HTTP 获取 Card/YAML、A2A 探活或模型调用。需要补扫时在预算内继续同一快照；若已结束事务，重新开始整页，不拼接不同快照。
6. DB/连接/超时错误使整个请求失败；不能把异常转成空结果。复制库可用，但可见性撤销时效须满足部署约束；本期不引入跨请求 Card 缓存。

以下 `?` 是逻辑绑定参数，Python/Java adapter 可转换为本驱动占位方式；只能动态生成 **placeholder 数量**，不能拼接 ID、query、env 或游标原文。

### 7.2 第一步：公开群候选

```sql
SELECT id, group_id, label, version, group_strategy, context
FROM bcs_groups
WHERE env = ?
  AND visibility = 'public'
  AND record_status = 'active'
  AND group_kind = 'normal'
  AND group_strategy IN ('manager_worker', 'state_machine')
  AND id > ?
  AND LOWER(COALESCE(label, '')) LIKE LOWER(?) ESCAPE '!'
ORDER BY id ASC
LIMIT ?;
```

参数依次为 env、after_id（首屏 0）、escaped_pattern、scan_batch。query 将 `!`→`!!`、`%`→`!%`、`_`→`!_`，再包成 `%query%`；空 query 可直接省略 LIKE 谓词。大小写/Unicode 行为以部署数据库 LOWER 与 collation 为准，MySQL/OB 必须通过同组样例验证。

先分页群，再读取成员。禁止直接 JOIN participants 后 LIMIT，避免一个群占多行、截断成员并导致错页。

### 7.3 第二步：批量成员与当前状态机绑定

```sql
SELECT group_id, bot_uuid, role, actor_kind, mode
FROM bcs_group_participants
WHERE env = ? AND group_id IN (?, ?, ...)
ORDER BY group_id, bot_uuid;
```

不要加 `actor_kind='bot'` 后就当作完整成员集；必须看到 Human/muted/未知角色并按导出 profile 判断。

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

采用当前 store 的 sentinel 语义，不假定 `group_version == bcs_groups.version`；普通群资料更新不一定新建 runtime binding。每群取最高 group_version 的 current 行；出现多条 current 行时发完整性告警并省略该群，不猜哪条是真正现状。无绑定、定义 ID/version 不完整时省略该群。

不读 runtime sessions、runs、run snapshots；`auto_start_on_service_invocation` 是 BCN 内部启动策略，不影响 local 用户显式执行，不直接转成上游自动执行开关。

### 7.4 第三步：批量精确版本定义

去重 `(definition_id, definition_version)`，分块查询：

```sql
SELECT definition_id, version, name, source_format,
       yaml_text, content_hash
FROM bcs_collaboration_definitions
WHERE env = ? AND record_status = 'active'
  AND ((definition_id = ? AND version = ?)
    OR (definition_id = ? AND version = ?));
```

OR 项由批量大小生成。禁止 `MAX(version)`、只按 definition_id JOIN、读取最新 run 定义。缺失或重复精确键均视为数据问题。normalized_json/blob fallback 不在本期读取路径中。

### 7.5 第四步：批量成员 Bot 和 Provider

从 manager/worker 角色、状态机绑定及独立 Agent 检索结果收集 Bot UUID，按当前 env 去重、分块：

```sql
SELECT bot_uuid, name, visibility, actor_kind, is_deleted,
       provider_id, agent_card, agent_card_revision
FROM bcs_bots
WHERE env = ? AND bot_uuid IN (?, ?, ...);
```

不要 SELECT *，不查询 session_token，不把 webhook_url 当 A2A URL。Card 不存在/无效即不满足本期直接调用条件；agent_card_url 是 Card 文档地址，不是任务调用入口，Discovery 请求中也不再次获取它。

Provider 查询只投影策略，不把可能含凭证的 config 带到应用：

```sql
SELECT provider_id, disabled,
       CASE WHEN JSON_VALID(config) = 1
            THEN JSON_TYPE(config) ELSE NULL END AS config_type,
       CASE WHEN JSON_VALID(config) = 1
            THEN JSON_TYPE(JSON_EXTRACT(config, '$.collaboration_scope'))
            ELSE NULL END AS scope_type,
       CASE WHEN JSON_VALID(config) = 1
            THEN JSON_UNQUOTE(JSON_EXTRACT(config, '$.collaboration_scope'))
            ELSE NULL END AS collaboration_scope
FROM bcs_providers
WHERE env = ? AND provider_id IN (?, ?, ...);
```

应用按第 6 节校验类型与区分大小写的精确值；SQL NULL（缺失）和 JSON null（显式 null）不得混为一谈。此 JSON 投影必须在实际 OB 版本验证，不能仅凭 MySQL 单测声称兼容。

### 7.6 第五步：渲染与引用闭包

```text
snapshot = read_public_candidates_and_dependencies(trusted_scope, cursors, budgets)
end_read_transaction()

member_cards = validate_and_render_agent_cards(snapshot.bots, snapshot.providers)
for group in snapshot.groups_in_candidate_order:
    if group.strategy == manager_worker:
        definition, slots = render_manager_worker(group, snapshot.participants)
    else:
        binding = select_current_binding(group)
        source = exact_definition(binding.definition_id, binding.definition_version)
        definition, slots = validate_and_render_state_machine(source, binding)
    assert_all_references_are_public_members_with_valid_cards(slots, member_cards)
    card = render_group_card(group, definition, bind_to_member_revisions(slots))
    validate_bcn_card_contract(card)
    append_group_and_dependency_closure_atomically_within_response_budget(card)

return agents_union, groups, candidate_progress_cursors, diagnostics_summary
```

这是行为分层示意，查询计划需先解析受限 bindings 以收集 ID；这种纯内存解析可以在快照读取期间完成，不得执行 YAML 中的工具或代码。渲染报单组数据错误可以省略并继续；不支持 profile 的群不能只去掉不认识的字段后发布。

### 7.7 revision、签名与确定性

1. Agent：以保存的完整 Card 为源，保留未知扩展、skills、真实 interfaces；移除源 `signatures` 和同 URI 的旧 BCN 注解，再注入本服务身份扩展。原始数据库 Card 不修改。
2. 群：固定标准字段与 extension，workerSlots 和无语义的集合排序，YAML content 保持原始字符串；bindings 数组顺序保留。
3. 对待发布 Card **去掉顶层 signatures 和本扩展 params.revision 字段**，按 RFC 8785 JCS 规范生成 UTF-8 字节，计算 SHA-256，再填 `revision="sha256:"+hex`。先计算成员 revision，再把它们写入群 bindings 计算群 revision。
4. 不把当前时间、请求 ID、数据库读取顺序写入 Card；相同导出内容必须得到相同 revision。原始 agent_card_revision 可作内部诊断，不代替导出 revision。
5. 修改带签名 Card 后不能保留失效的原签名。此 API 默认返回**Discovery 派生的未签名 Card**，由认证传输承载信任；不是提供者的原文签名分发接口。未来可由 Discovery 重新签名；不能冒用提供者身份。
6. 源字段若不能用约定的 JSON/JCS 表达，返回明确数据错误；Python/Java 不能各自用不同浮点/排序规则近似计算。

## 8. 资源上限、分页与失败

以下是首版安全预算，可由部署调低；提高必须补负载证据：

| 预算 | 首版上限 |
| --- | --- |
| 每批候选群 | 20 |
| 每请求扫描群候选 | 100，不是全目录永久截断 |
| 每群 participants | 256；超过则整组错误，不截断角色 |
| 每响应去重后的 Agent Cards | 256，含独立命中和群依赖 |
| 每批完整 AgentCard 读取 | 16 个；单卡 1 MiB |
| 单份 YAML / 单群 context | 256 KiB / 64 KiB UTF-8 |
| 单请求读取的 Card/YAML/context 文本总量 | 16 MiB，解析前检查字节数 |
| 响应体 | 4 MiB UTF-8 JSON；群和闭包原子计费 |

文本长度先用 OCTET_LENGTH/有界投影检查，超长值不完整传输或解析；第 7 节 SQL 展示业务列，生产 adapter 需加入此保护。关系行读取也必须有界：候选批次最多 20×256 合法成员，超量通过计数/limit+1 检测而不是无界读取后截断。

- 用批量查询，不做“每群各查成员、每 slot 各查 Bot”。若需补扫，先判断预算，不能无限循环填满 limit。
- 一个典型批次的业务 SELECT 数上界是 `3 + ceil(D/64) + ceil(B/16) + ceil(P/64)`：群候选、participants、runtime bindings，外加定义、Bot、Provider 批次；没有某类数据可省略对应查询。D/B/P 是去重定义键/Bot/Provider 数；字节预检另计。
- 例如 20 群、20 定义、100 Bot、10 Provider：上述读取 12 条业务 SELECT，另有预检/独立 Agent 搜索/事务初始化。**不是网络往返数、延迟实测或完整请求固定查询数**。
- 整页需累加每批读取字节、关系行和 SELECT 次数；错误持续出现也必须受限。触及预算保留进度游标；不要重复回到坏数据起点。
- 单群连同成员即超过响应/成员上限：省略并记 `GROUP_EXPORT_TOO_LARGE`；只是剩余页容量不足：延后该候选，cursor 不越过它。
- Query 的 `%...%` 搜索可能扫描大量行。现有 visibility/label/唯一键索引不保证覆盖 env+公开性+strategy+id；上线必须做真实 EXPLAIN 与基数压测。该 spec 不擅自修改已提交 migration；如需索引另提增量迁移。

| 情况 | 外部行为 |
| --- | --- |
| 非法 query/limit/cursor | HTTP 400；不访问其他作用域 |
| 未认证/不允许目录访问 | 401/403 |
| 无匹配公开群 | 200，groups=[]，非错误 |
| 单组缺 manager/定义/成员 Card，绑定损坏，profile 不支持 | 200，省略整组、partial=true，计数+内部原因 |
| 相应 Card 有效但成员调用未授权 | 不提供 BCN 凭证；上游须取得独立授权，不能认为发现即授权 |
| DB 不可用/读事务失败 | 503，整请求失败，不返回成功空列表 |
| 必需 schema 或 Provider read-source 尚未就绪 | readiness 失败，请求 503；不静默回退旧表 |

内部原因至少区分 `INVALID_MANAGER_ROLES`、`INVALID_BINDINGS`、`MEMBER_NOT_EXPORTABLE`、`MISSING_CURRENT_BINDING`、`DEFINITION_NOT_FOUND`、`DEFINITION_SOURCE_UNAVAILABLE`、`UNSUPPORTED_DEFINITION_FEATURE`、`GROUP_EXPORT_TOO_LARGE`。日志禁止记录完整 YAML、context、Card URL query 或凭证。

## 9. 公开导出与安全

- 现有 `visibility=public` 是候选条件，不自动证明 YAML/context/所有 extension 都无敏感信息。首发需对既有公开群进行内容外发确认；这是启用该导出服务的前置治理，不在本期添加一套私有群 ACL。
- 白名单输出仅为本 spec 字段与批准的 AgentCard 声明；不导出 owners、created_by、Human ID、Provider config、运行日志、群消息、session_token、私有 endpoint 或签名 URL 中的凭证。
- A2A Card 中 URL/security 声明是待使用的数据，不是执行指令；上游仍须按自身网络/SSRF/鉴权策略选择 interface。不能把数据库中任意 URL 自动加入可信域。
- 不得通过“删掉敏感 YAML 节点”偷偷改变群逻辑。发现不宜公开的定义应整组拒绝并修复来源。
- local 模式没有 BCN 身份/凭证代理、Session 上下文、工具权限和事件投影的自动继承。真实运行必须重新建立这些边界。
- 本期默认不缓存；上游如缓存，运行前重新发现/核对绑定 revision，不允许按陈旧 Card 绕过已撤回的公开性。

## 10. 长期迁移

| 字段/行为 | 短期 | 长期 |
| --- | --- | --- |
| resourceId/resourceKind/groupKind | 稳定身份与类型 | 保持稳定 |
| execution.mode | 群 local，Agent remote | 全部 remote |
| supportedInterfaces | Agent 真实；群占位 | BCN 提供真实 A2A interfaces |
| composition | 群必填 | 可省略；保留时仅供解释/展示 |
| runtimeProfile | local 必填 | remote 无需上游理解 |
| 调用、状态、取消、凭证 | 上游管理成员调用 | BCN + Group-as-a-Bot 对外承担 |

长期 remote 群只用本扩展携带身份/分类、无需上游参与编排时，extension `required=false`，使普通 A2A 客户端无需理解群内部结构即可调用。

切换 URL/mode 必须产生新 revision。已开始的 local 执行固定其旧定义/成员快照，不在中途自动迁移；新调用使用新 Card。remote 调用失败时不能自动回退 local，避免重复任务与权限绕过。

`groups[]` 是目录分类，不等于必须 local：长期仍可返回 `groups[]`，消费者按 mode 决定调用方式，而不是永久忽略 groups 的 interface。

## 11. 完整示例与验证边界

- [Agent 示例](examples/agent.json)：保存的远端 Card 经 BCN 身份投影。
- [Manager–Worker 群](examples/manager-worker.json)：一个 manager、一个 worker。
- [State Machine 群](examples/state-machine.json)：analyst → reviewer。
- [原始 YAML](examples/state-machine.yaml)：与 State Machine Card.content 完全相同。
- [完整 Discovery 响应](examples/discovery-response.json)：含两种群及全部依赖，revision 互相对应；对应 query=`数据库诊断`、agentLimit=0、groupLimit=20，所以独立 Agent 命中列表为空。
- [来源核对与本次验证](research.md)：仓库事实、设计选择、未验证边界。

示例使用保留示例域名/虚构 ID，不是线上地址或实际注册快照。实际的 A2A 声明校验还需固定 A2A 1.0 validator；JSON 语法通过不代表协议 conformance，更不代表占位群可调用。

## 12. 实施验收

| 层次 | 必须覆盖 |
| --- | --- |
| 字段/序列化 | agents/groups 均为 Card 数组；扩展唯一；无自定义顶层字段；同组 kind，正确 groupKind；未知 mode 拒绝 |
| A2A 边界 | 固定 1.0 schema；保留 Agent unknown fields；签名派生规则；local interface 从不调用；remote interface 不靠猜测生成 |
| 筛选 | public/private、active record、normal/DM、chat/两种策略、不同 env；与最近 Session 状态无误耦合 |
| 查询 | query trim/空值/中文/大小写/`%_!`/注入；keyset、多页坏数据、预算空页、无无限重试 |
| manager | 0/1/2 manager；driver≠manager；零/多 worker；Human/muted 执行成员；不虚构 policy 默认值 |
| state machine | current sentinel；绑定版本≠群版本；重复 current；精确定义版本；YAML 缺失；非法 YAML；hash 区分 |
| bindings | 成员缺失、跨 env、私有/删除成员；slot 未声明/required 缺失；多值不取首项；被引用 slot 必须单值 |
| Profile | DAG 顺序/并行/汇合/失败；不能忽略 loop/judge/HumanInput；不支持适配器必须明确拒绝 |
| 权限 | Provider disabled/非 standard/无效 config；缺失 scope 与 JSON null 区分；不导出凭证与 Human 身份 |
| 确定性 | 不同 DB 行序结果一致；同内容 revision 一致；成员 Card/群定义变化带动群 revision；YAML sha256 匹配 |
| 闭包/分页 | 非 query 命中成员仍补齐；去重；引用 revision 一致；容量延后不跳群；单群过大不死循环 |
| 数据库 | Python/Java 实际 driver × 目标 MySQL/OB：完整迁移后运行 SQL、参数化/Unicode/JSON/NULL/快照隔离测试 |
| 故障/成本 | 断连/超时不返回空成功；无 N+1；预检/分块/扫描预算；真实 EXPLAIN、最大页与持续损坏数据压测 |
| local 执行 | 与上游联调 manager→worker→manager 和 YAML DAG；验证远端 Bot 不再错误调用旧 BCN 工具上下文 |

实现时将以上作为语言无关 golden fixtures 和 conformance cases，Python/Java 分别跑相同输入/期望输出；数据库适配测试与 HTTP API 测试分开。新增服务需有对应 context boundary 文档与契约版本声明。

本次仅完成文档/静态示例检查；**未运行真实 MySQL/OB SQL、A2A SDK validator、上游编排或生产集成测试**。这些是实现上线前置条件，不能由本 spec 的完成替代。
