# BCN A2A 适配待办

更新日期：2026-11-04。

本文持续记录已确认的后续适配需求，供范围讨论、排期和实施验收使用。待办不代表当前已支持的能力；对客接入约定见 [BCN A2A 注册与调用指南](../../bcn-a2a-integration-guide.zh-CN.md)，实现范围及限制见 [接口参考](../../bcn-a2a-api-reference.zh-CN.md)。后续需求按编号追加。

## 待办列表

| 编号 | 事项 | 状态 |
| --- | --- | --- |
| A2A-001 | BCN 下行 A2A Client 支持 JSON-RPC 绑定 | 已实现，通过本地合同验证 |
| A2A-002 | AgentCard 支持 Data-only Extension 的规范化接入与使用 | 已实现原始数据保留及来源扩展消费，通过本地验证 |
| A2A-003 | 基于请求 metadata 传递 userId 等调用上下文 | 待定义字段与信任规则、待实施 |
| A2A-004 | A2A 管理接口按路径 bot_id 选择对象，支持 source_agent_id 找回身份 | 已实现，通过本地合同验证 |
| A2A-005 | 解除固定下行 token 依赖，本期支持无应用层凭据调用 | 已实现新入口匿名准入，兼容入口保留 Bearer；本地验证通过 |
| A2A-006 | Card 更新不再要求 expected_revision | 已实现，通过本地合同验证 |
| A2A-007 | 非流式 Agent 接入：文本问答、长任务查询和取消 | 已实现，通过本地合同验证 |
| A2A-008 | 来源平台 Agent 标识及固定扩展 URI | 声明、校验、持久化已验证；独立检索接口待需求确认 |
| A2A-009 | Provider AgentCard 管理接口 v0.1 合同 | 已实现，通过本地合同验证 |

## A2A-001：支持 JSON-RPC 绑定

### 背景与目标

此前 BCN 下行适配器仅支持 A2A 的 HTTP+JSON/SSE 和 gRPC 绑定，无法调用只提供 JSON-RPC 绑定的 Agent。

增加 JSON-RPC 绑定，使这些 Agent 可以通过现有 Provider/Bot 注册流程接入，并复用 BCN 的会话、运行跟踪、文本转换、工具扩展和审批处理。保留现有 HTTP+JSON 与 gRPC 能力。

### 实施范围

1. **Card 校验与接口选择**：识别 `supportedInterfaces` 中的 JSON-RPC 声明，按 Card 的偏好顺序和平台出站策略选择可用接口。
2. **请求与响应封装**：实现 JSON-RPC 2.0 的 `jsonrpc`、`id`、`method`、`params` 请求包装，以及 `result` / `error` 响应处理和请求 ID 校验。
3. **五个当前下行操作**：支持 SendMessage、SendStreamingMessage、GetTask、CancelTask、SubscribeToTask。流式操作使用 JSON-RPC 绑定规定的 SSE 消息格式。
4. **共享转换逻辑**：在绑定层解码为统一 A2A 对象，复用现有 Task/Context 关联、Artifact 文本投影、工具和 exec 审批逻辑，避免复制一套领域规则。
5. **鉴权与扩展协商**：遵循 A2A-005 的本期无应用层凭据接入范围，沿用 A2A 版本及扩展协商；保留出站地址安全限制、响应大小和超时限制。
6. **失败与恢复**：将 JSON-RPC 协议错误映射到统一错误语义，接入已有 GetTask + SubscribeToTask 恢复流程。不得因绑定失败而盲目切换接口、重复提交可能产生副作用的请求。
7. **文档与测试**：更新接入指南的兼容范围、Card 示例、接口示例和错误说明，并增加绑定合同测试与端到端验证。

### 版本边界

本项首先针对当前对齐的 **A2A 1.0 系列 JSON-RPC 绑定**。JSON-RPC 2.0 是 RPC 包装协议版本，不等于 A2A 版本。

增加 JSON-RPC 不自动意味着兼容 A2A 0.3。旧版方法名、Card、消息和事件模型的兼容，需要另行确认范围、单独记录待办；不要把旧版 `message/stream` 等格式混入 1.0 绑定。

本项针对 **BCN → Agent 的下行 Client**。BCN 对外作为 A2A Server 的能力不纳入本项。

### 验收标准

- 仅声明兼容 JSON-RPC 接口的 Agent 可以注册，并由 BCN 正确选中和调用。
- 多绑定 Card 按声明顺序选择当前可用接口；HTTP+JSON 和 gRPC 现有行为不退化。
- 五个操作具备请求、成功响应和错误响应合同测试；覆盖 Task 回复、直接 Message 回复及流式事件。
- 流式文本、终态、断流后查询与重新订阅、取消关联均可通过 BCN 完成闭环。
- 已协商的工具观察、exec 审批可复用现有处理；不支持的必需扩展仍明确拒绝。
- 非法包装、请求 ID 不匹配、超大响应、超时和鉴权失败均得到明确处理，不被误判为成功。
- 使用独立的 JSON-RPC Agent 服务完成端到端验证，并记录实际协议版本和验证结果。

### 参考

- [A2A 1.0.1 JSON-RPC 绑定](https://a2a-protocol.org/v1.0.1/specification/#9-json-rpc-protocol-binding)
- [当前 A2A 下行适配器边界](../../../apps/bcs/crates/external-clients/bcs-a2a-http/CONTEXT.md)

## A2A-002：AgentCard 支持 Data-only Extension

### 背景与目标

通过 A2A 的纯数据扩展，为 AgentCard 增加标准字段之外的业务描述信息。扩展放在 `capabilities.extensions[].params`，由带版本的 URI 标识，不改变普通 A2A 请求和执行流程。

当前 BCN 已保存完整原始 Card JSON，能够保留并返回可选扩展及其 params。本项重点是定义字段语义、明确校验与读取规则，并根据确认的业务需求使用这些数据，而不是重新实现一套 Card 存储。

### 实施范围

1. **定义扩展规范**：确定 BCN 使用的扩展 URI、版本、params Schema、字段含义和兼容策略；破坏性变更使用新 URI 版本。具体业务字段待后续确认，分类、标签、语言等仅作为候选示例。
2. **使用标准声明结构**：通过 `uri`、`description`、`required`、`params` 声明；纯数据扩展使用 `required:false`。不在 Card 顶层任意增加业务字段，也不引入非标准的扩展 type 字段。
3. **保留与校验**：URL 和内联 Card 注册、PUT 更新、手动及周期刷新均完整保留扩展。对 BCN 明确支持的扩展版本按约定校验 params；未知可选扩展保持原样，不宣称已理解，不影响普通调用。未知必需扩展仍按现有规则拒绝。
4. **读取与消费**：确保有权限的 Card 查询能返回扩展数据；需要业务展示、搜索或筛选的字段另行确认后纳入实施，不默认对任意 params 建立搜索索引。
5. **存储与权限边界**：继续使用完整 agent_card 存储，默认不按每个业务属性新增数据库列。扩展中的身份、组织或权限声明不能覆盖 BCN 可信的 Provider、Owner、可见性及授权信息。
6. **文档与合同验证**：补充 Card 声明示例、字段规范、版本升级说明，以及注册、更新、刷新、读取全过程的保真测试。读取纯描述数据不要求引入新的运行时 RPC 或调用流程。

### 验收标准

- 发布一份明确的扩展字段规范和可使用的 Card 示例；具体字段由需求确认后填写。
- 同一扩展通过 URL 与内联方式登记后，嵌套对象、数组及未知可选字段不会在保存和读取中丢失。
- PUT 更新、URL 刷新遵循 A2A-006 的并发语义；扩展数据随完整 Card 一起更新。
- 已支持版本的非法 params 返回可理解的校验错误；未知可选扩展可保留，不被误解析成已支持版本。
- 未理解该扩展的客户端仍能进行正常 A2A 文本调用；现有工具和 exec 扩展不受影响。
- 如确认展示或检索需求，验证对应字段的实际消费效果及现有访问控制；仅能原样返回 Card 不算完成业务消费能力。

### 参考

- [A2A 扩展类型与声明](https://a2a-protocol.org/v1.0.1/topics/extensions/)
- [当前 Card 注册与保存约定](minimal-card-registration-v1.md)

## A2A-003：基于 metadata 传递调用上下文

### 背景与目标

BCN 调用 Agent 时，需要传递 userId 等业务上下文，供 Agent 或 MobileAgentBase 适配层识别业务用户。使用标准 `SendMessageRequest.metadata` 承载版本化扩展，不向 A2A 请求顶层或 protobuf 定义增加私有字段。

这属于每次调用的上下文扩展，与 A2A-002 的 Card 纯描述数据不同。字段可传递不代表身份已可信；身份来源和授权规则需与字段协议一起确定。

### 实施范围

1. **字段规范**：确定扩展 URI 和版本，在请求 metadata 的 URI 命名空间下传递 `userId`。其他字段按实际需求确认，不预先承诺整包透传；明确类型、必填条件、长度、缺省行为及字段含义。
2. **声明与协商**：AgentCard 声明支持的上下文扩展，调用时通过 A2A-Extensions 协商。对依赖用户身份才能执行的业务，缺少必需上下文或不支持扩展时明确拒绝，不回退为共享或默认用户。
3. **可信来源**：BCN 从已认证并获授权的调用上下文生成字段，不直接信任用户消息、模型生成参数或任意外部 metadata。确认是否复用公司网关/RPC 的可信身份机制，或采用可验证的短期断言；本待办不预先指定 JWT 或另建签名体系。
4. **接收与映射**：Java SDK 提供上下文解析及验证扩展点。验证成功后，MobileAgentBase adapter 将 userId 映射至其私有 `SendMessageRequest.userId`，其他宿主按自身业务上下文接口接入。
5. **跨绑定一致性**：HTTP+JSON、gRPC 及 A2A-001 新增的 JSON-RPC 使用相同 metadata 结构与语义，不因绑定不同改变身份来源。
6. **任务生命周期**：明确上下文与 Bot、租户、Session/Task 的关联及后续调用规则。GetTask、SubscribeToTask 等没有相同请求 metadata 字段的方法，应通过鉴权和已建立的任务关联校验权限，不新增私有标准字段。明确审批续交、重试和身份变化时的处理。
7. **数据保护与文档**：仅传必要字段，不把身份凭据或敏感上下文写入普通聊天历史、工具展示和常规日志；补充接入示例及验证失败说明。

### 验收标准

- 提供字段 Schema、扩展声明和请求示例，明确 userId 的来源、接收方信任规则及缺失处理。
- 已认证用户发起调用后，目标 Agent 获得正确上下文；MobileAgentBase 映射保持一致。
- 不同用户、租户及任务的上下文不会串用；普通输入或外部 metadata 无法覆盖可信 userId。
- 缺少必需身份、身份验证失败、上下文冲突或扩展未协商时，不以默认用户继续执行。
- 流式发送、审批续交及后续任务查询/订阅/取消维持一致的授权边界。
- 各支持绑定通过合同测试；不依赖此扩展的普通文本 Agent 保持原有行为。

### 待确认

- userId 之外实际需要传递的字段，以及各字段的权威来源。
- 身份信任机制、跨 Agent 委托范围和凭据生命周期。
- 依赖该上下文的业务与可选使用该上下文的业务如何声明。

### 参考

- [A2A 扩展机制](https://a2a-protocol.org/v1.0.1/topics/extensions/)
- [A2A 认证与授权](https://a2a-protocol.org/v1.0.1/specification/#7-authentication-and-authorization)

## A2A-004：对外使用 Provider/Bot 名称

### 可行性与边界

Provider 使用稳定的英文接入标识，Bot 名称统一来自 AgentCard.name，不再要求用户维护另一份 Bot 名称。远端 A2A 地址来自 Card 的 supportedInterfaces[].url 及可选 tenant，不从名称生成。

内部仍使用 provider_id、bot_uuid 和可信绑定执行授权、存储、取消及任务关联。本项只隐藏 Provider/Bot 内部标识，不移除 A2A taskId/contextId/messageId 或 BCN run_id/session_id，也不改变已有身份凭据。

### 对外约定

- provider_name 使用小写 ASCII 字母、数字、连字符或下划线，以字母开头，长度 1～64，校验规则为 `^[a-z][a-z0-9_-]{0,63}$`。在部署环境内唯一，注册后保持稳定；不满足规则的输入明确拒绝，不静默转换。
- agent_name 直接取 AgentCard.name，支持中文，在同一 Provider 内唯一。注册请求不再单独填写 agent_name，也不新增并列展示名字段。明确 Unicode、大小写、空白和长度校验，并与数据库唯一索引保持一致；不静默改写原始 Card.name，接受后保证存储及响应中的 Bot 名称与它一致。
- 显式更新、手动刷新和周期刷新均将 Bot 名称与 Card.name 同步，名称、Card 及来源原子写入。新名称与其他 Bot 冲突时，拒绝整个更新并保留旧配置；自动刷新记录明确失败原因。
- 更新路径中的 bot_id 选择当前 Bot，新 Card.name 决定展示名称。成功改名后调用方继续使用原 bot_id；名称不作为管理定位依据。改名和软删除后名称立即释放，允许其他 Bot 使用；已有运行仍关联稳定内部 ID。唯一性覆盖同一环境、同一 Provider 下所有有效 Bot，包括非 A2A Bot，不建立历史名称保留表。
- A2A 接入方无需提供或记录 provider_id、bot_uuid、provider_bot_ref。服务端将名称映射到内部身份；Provider/ref 的生成或映射不得破坏 AgentPass 等既有身份规则。

对客指南采用独立名称入口，避免将现有 ID 路由静默改为双重含义：

| 操作 | 对外名称接口 |
| --- | --- |
| 注册 Agent | POST /a2a/agents；body 提交 Card URL 或完整 Card、owners 等注册字段，不传 provider_name 或 agent_name |
| 查询登记 Card | GET /a2a/agents/{bot_id}/agent-card；ID 在路径中；Provider 从管理 token 获取 |
| 更新登记 Card | PUT /a2a/agents/{bot_id}/agent-card；JSON body 提交完整 agent_card_url 或 agent_card，或仅 visibility |
| 刷新登记 Card | POST /a2a/agents/{bot_id}/agent-card:refresh；空 body 或 {}；Provider 从管理 token 获取 |
| 发起 BCN 异步调用 | POST /a2a/providers/{provider_name}/bots/{bot_name}/chat-async |
| 查询 BCN 运行 | GET /a2a/chat/runs/{run_id} |
| 取消 BCN 运行 | POST /a2a/chat/runs/{run_id}/cancel |

注册及 Card 管理接口通过唯一绑定 Provider 的管理 token 确定归属，路径、query 和 body 均不要求 provider_name；GET 中文参数由 HTTP 客户端编码。业务调用仍显式选择目标 Provider，路径中的 Bot 名称按单个路径段编码。这些是 BCN 管理及调用 API，不是远端 Agent 的标准 A2A RPC。Card 来源二选一、管理凭据与调用凭据的既有语义保持一致；更新不再要求 expected_revision，见 A2A-006。

### 实施范围与验收标准

1. 定义名称解析与唯一性约束，校验 Provider 英文标识；处理历史非法 Provider 名称、Bot 重名及 Bot/Card 名称不一致，给出迁移方案，不能任意选取第一条或静默重命名。
2. **token 确定管理范围**：注册和 Card 管理先验证 Provider 管理 token，解析唯一有效 Provider，再在其范围内按 bot_id 选择对象，复用已有用例。缺失、失效、禁用或无法确定唯一 Provider 的凭据明确拒绝；不得根据请求参数猜测或切换归属。新管理接口不接受 provider_name 作为范围覆盖字段，意外传入时明确拒绝。业务调用凭据与管理凭据仍分离，跨 Provider 同名 Bot 不能串用权限。
3. 注册从 URL 拉取的 Card 或内联 Card 中获取名称；缺失、非法、同 Provider 重名均拒绝。禁止通过另一个管理入口单独改名而破坏 Bot 名称与 Card.name 的一致性。
4. 对外注册和 Card 管理响应暴露 bot_id，保留 provider_name、agent_name 用于展示；更新及刷新成功返回最新名称。检查错误信息、分页、发现及运行状态 DTO，避免暴露 provider_id、bot_uuid 或 provider_bot_ref；审计和内部日志可保留内部 ID。
5. 发起调用后，查询和取消仍使用 run_id，运行归属依据内部 ID。旧 ID 接口保持兼容；涉及 A2A Bot 的名称写入必须维护相同一致性约束。
6. 验证中文 Card.name 的注册、查询、展示和调用全链路，以及显式更新、手动刷新、周期刷新触发改名；重名冲突无部分写入，旧名称不误路由，已有任务关联不变。并发注册/改名、慢刷新与显式更新竞争需有合同测试。
7. **发现、搜索与列表一致性**：注册、显式更新、手动刷新和周期刷新成功后，Bot 发现结果、搜索结果、列表及详情展示的名称均与已保存的 AgentCard.name 一致，不再从另一份独立展示名取值。按 Bot 名称搜索时使用同步后的名称；改名后新名称可命中，旧名称不再作为该 Bot 的当前名称命中（其他描述字段中包含旧名称的全文搜索结果不受此约束）。如存在缓存或独立搜索索引，需定义并验证更新、失效与可见时限，避免长期保留旧名；权限过滤保持不变。
8. **一致性验收**：分别通过注册、PUT、手动刷新和周期刷新触发名称变更，验证 Card 查询、Bot 详情、发现、列表及名称搜索结果一致。重名或 Card 校验失败时，各入口继续返回旧名称和旧 Card；覆盖跨 Provider 同名及无权限用户不可发现、不可搜索的场景。
9. **管理范围验收**：不传 provider_name 可完成注册、查询、更新和刷新；不同 Provider 的同名 Bot 始终按 token 归属隔离。覆盖伪造 provider_name、错误用途 token、失效凭据及 Provider 禁用；响应继续返回可信 provider_name。SDK 和示例不再要求重复填写 Provider 名称。跨 Provider 发现、搜索及业务调用的显式目标和权限检查保持不变。
10. 评估名称索引与解析成本；一次解析后沿用内部 ID，不在每个流式事件上重复查名称。同步 SDK、接口合同和端到端测试。

## A2A-005：本期支持无应用层凭据调用

### 背景与目标

解除 A2A 注册与运行时对 Provider downlink_bcs_to_provider 凭据的强制依赖。本期接入范围收缩为允许 BCN 无应用层凭据调用的 Agent，不要求用户准备 Agent 调用鉴权配置。

Provider 管理 token 继续用于注册和管理 Bot，BCN 调用方的运行凭据也保持原有用途；两者均不能转发给远端 Agent。

### 实施范围

1. **解除强制依赖**：调整 A2A 注册、Card 校验、可用性判断和运行入口，不再为了接入而创建固定下行 token。旧 webhook Provider 的配置及鉴权行为保持兼容。
2. **准入规则**：按 A2A 安全声明语义验证存在无需凭据的可满足路径。对客示例使用 securityRequirements:[]；只有必须提供 Bearer、OAuth 等凭据才能调用的 Agent，本期明确拒绝注册或更新。不能忽略其要求或在 401/403 后降级重试。
3. **运行一致性**：发送、查询、订阅、取消、审批续交及恢复请求均不自动携带 Provider 下行 token 或管理凭据。Card 拉取同样不附带 Agent 调用凭据。网络访问限制保留，不把“无应用层凭据”解释为必须开放公网。
4. **已有配置兼容**：本项不直接删除已有凭据记录。实施前确认已登记 Bearer A2A Bot 的兼容及迁移方案；不得静默移除其运行鉴权或将其自动改成匿名。旧兼容链路与本期新接入范围明确区分。
5. **文档与后续范围**：从接入信息表移除调用鉴权配置，Card 示例改为无需应用层鉴权。Bearer、OAuth、mTLS 或服务网格身份对接后续按实际需求实施，本项不承诺这些方式已可用。A2A-003 的可信用户上下文仍独立定义，普通 metadata 不因此成为可信身份。

### 验收标准

- 无 Provider 下行 token 的合规匿名 Agent 可注册、发送消息、查询、订阅和取消。
- 实际出站请求不泄露或自动携带 Provider 管理 token、调用方运行 token 或无关下行凭据。
- Card 声明必需鉴权且没有匿名可满足路径时明确拒绝；401/403 不触发凭据猜测、匿名降级或初始提交重放。
- 更新和刷新 Card 时同样校验准入要求，失败不覆盖原配置。
- 验证旧 webhook 不退化，并记录已有 Bearer A2A 配置的兼容或迁移结果；补充注册、更新及五个方法的合同测试。

## A2A-006：Card 更新不再要求 expected_revision

### 背景与目标

简化 Agent 信息更新：调用方直接提交新的 agent_card_url 或完整 agent_card，不必先查询 revision，也不依赖 expected_revision 完成更新。此项修改 BCN 管理接口的合同，不改变远端 A2A 协议。

### 实施范围

1. **请求合同**：移除更新接口对 expected_revision 的必填校验和客户端版本匹配要求，同步请求类型、接口文档及客户端调用。旧接口若保留版本校验，明确作为兼容路径；不得让新的对客更新接口隐式依赖该字段。
2. **更新语义**：显式 PUT 仍是完整替换，先校验再原子写入；并发显式更新以最后成功写入为准，不按请求发起时间排序。调用方接受并发更新可能覆盖之前内容的语义，不再返回要求重新查询版本的冲突。
3. **刷新竞争**：后台及手动 URL 刷新不得用旧来源的拉取结果覆盖已成功提交的新配置，包括切换为内联 Card 的情况。可保留内部 revision/CAS 或等效保护；取消客户端版本参数不等于取消服务端并发保护。过期刷新结果应丢弃，手动刷新需明确报告未应用，不能伪报已更新。
4. **存储边界**：本项不要求删除 agent_card_revision 数据库列或内部版本记录，也不要求改变 AgentCard.version；它们与客户端 expected_revision 是不同概念。保持来源二选一、鉴权、Card 校验及运行中任务绑定语义。

### 验收标准

- 不传 expected_revision 即可更新 URL 或直接提交完整 Card，无需预先 GET；非法 Card 不覆盖原配置。
- 并发 PUT 的最终记录来自最后成功写入，Card、Bot 名称和来源保持一致，不产生部分更新；名称冲突时整个更新失败。
- 慢刷新与 URL 更换、内联 Card 替换并发时，旧结果不能覆盖新配置。
- 补充更新接口及刷新竞争的合同测试；明确旧客户端携带 expected_revision 时的兼容处理，更新接入文档及实际接口参考。

## A2A-007：非流式 Agent 接入

### 背景与目标

允许未声明 streaming:true 的 Agent 接入。首期支持文本问答、长任务查询和取消，通过 SendMessage 提交、GetTask 轮询跟踪，不要求 Agent 实现流式发送或订阅。

### 实施范围

1. **校验与方法选择**：解除 streaming:true 的统一准入要求，保留协议、文本能力、鉴权及必需扩展校验。根据已验证 Card 的能力选择执行路径：支持 streaming 时保持现有流式路径，否则使用非流式路径。任务建立后保持该运行的跟踪方式，不因 Card 刷新或请求失败盲目切换、重新提交。
2. **初始提交**：非流式路径调用 SendMessage，设置 configuration.returnImmediately:true。直接返回 Message 时按完整文本完成；返回终态 Task 时处理最终结果；返回未完成 Task 时保存可信关联并进入轮询。未返回 Task 的 Message-only 响应不提供远端任务查询和取消。
3. **任务跟踪**：使用 GetTask 查询状态与 Artifacts，不调用 SendStreamingMessage 或 SubscribeToTask。定义轮询间隔、单次查询超时及连续查询失败重试上限，等待和查询均受整轮运行预算约束，避免忙轮询或无限等待；成功查询后重置连续查询失败计数。
4. **文本处理**：复用现有 Task 关联和 Artifact 文本投影；快照只补齐未投递的后缀，重复快照不能重复展示。最终快照用于收尾；仍不支持修改已输出前缀。更新频率取决于 Agent 保存快照的频率和 BCN 轮询间隔，不承诺逐 token 输出。
5. **失败语义**：区分提交失败、提交结果未知与任务查询暂时失败。初始提交只有在协商支持幂等扩展后才允许按既有规则重试；无幂等承诺时不能因超时重新执行。查询失败不能被误判为任务完成，沿用既有失去跟踪、超时及会话阻塞策略。
6. **取消**：从可信运行关联取得远端 taskId，调用 CancelTask；不能把停止本地轮询视为远端已取消。取消失败或任务仍非终态时明确处理，不能返回虚假的取消成功；终态后停止轮询并复用既有收尾逻辑。
7. **绑定与文档**：HTTP+JSON、gRPC 及 A2A-001 的 JSON-RPC 复用同一非流式执行逻辑。更新接入指南的能力要求、方法选择和限制说明，补充合同测试与端到端验证。

### 范围边界

- 本项只承诺文本问答、长任务查询和取消，不纳入非流式工具过程展示、exec 审批或通用 ask_user。
- GetTask 不保证包含全部中间工具事件，不将轮询快照宣称为完整事件回放。
- 非流式路径收到 INPUT_REQUIRED 或 AUTH_REQUIRED 时，明确报告本路径暂不支持对应恢复，不当作成功完成，也不无限轮询；远端任务仍可能处于等待状态，按既有取消及失去跟踪策略处理。
- 如后续支持审批，需另行验证 Task 快照能够保存等待信息和审批回执，再实现续交及恢复轮询。
- 不包含 Push Notification、多实例执行协调或 A2A 0.3 兼容。

### 验收标准

- streaming 为 false 或未声明的合规文本 Agent 可以注册；支持流式的 Agent 保持原有行为。
- 非流式普通调用只使用 SendMessage、GetTask 和必要的 CancelTask，不尝试流式发送或订阅。
- 覆盖直接 Message、立即终态 Task、持续运行后完成/失败的 Task；重复快照不重复投递文本，终态正确收尾。
- 覆盖查询暂时失败后恢复、失败上限、整轮超时和初始提交结果未知；有副作用的初始请求不会盲目重发。
- 覆盖运行中取消、取消拒绝及完成与取消竞争，确保远端 taskId 关联正确。
- INPUT_REQUIRED/AUTH_REQUIRED 明确暴露能力限制，不误报成功或永久占用轮询。
- 使用仅实现非流式方法的 Agent 完成文本、长任务和取消端到端验证，并验证现有流式链路无回归。

## A2A-008：来源平台 Agent 标识

### 背景与目标

接入平台通常已有自己的 Agent ID。该标识不替代 BCN bot_uuid，也不是可改名的 Card.name。将其作为 A2A-002 的具体 Data-only Extension，让有权限的其他平台读取 Card 后能够定位来源平台中的 Agent。

### 建议合同

使用 capabilities.extensions[].params 承载 agentId 及可选 party，required:false。本期不引入 sourceNamespace；同一 BCN 部署内通过响应外层的 provider_name + agentId 区分来源 Agent，要求 agentId 在同一 Provider 下唯一。agentId 为来源平台的原始标识字符串，1～256 个 Unicode 字符，无首尾空白或控制字符，保留大小写及前导零。脱离 BCN 响应单独分发 Card 时，不承诺 agentId 全局唯一；如未来同一 Provider 接入多个 ID 冲突的来源，再单独设计命名空间。

扩展声明（URI 已确定，字段规则见 [来源扩展 v1 规范](extensions/source-agent/v1.md)）：

```json
{
  "uri": "https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/source-agent/v1.md",
  "required": false,
  "params": {
    "agentId": "agent-001",
    "party": "first_party"
  }
}
```

### 来源分类 party

复用同一来源扩展，在 params 内新增可选字符串 party，不新增扩展 URI。agentId 仍按原规则填写；party 不提供时表示未分类，不默认归为三方。

| 值 | 含义（以所属 Provider 为参照） |
| --- | --- |
| first_party | 一方：Provider 自身提供的 Agent |
| second_party | 二方：与该 Provider 有直接合作关系的合作方提供的 Agent |
| third_party | 三方：该 Provider 接入的其他外部 Agent |

- Provider 按自身组织及直接合作关系声明分类；服务端对已支持的本扩展仅接受上述枚举，非法值返回 INVALID_CARD。
- party 是来源分类，不是权限或可信等级。Card 自声明不得改变 Provider 归属、授权或自动显示已认证标识；如用于认证展示或准入，必须另行基于平台登记信息核验。
- 注册、PUT、手动及周期刷新均保存和返回 party；允许在合法 Card 更新时修改或移除 party，移除表示未分类。agentId 的稳定性约束不延伸到 party。
- 验收覆盖三种枚举、缺省、非法值、更新/移除及未知可选扩展保留；party 变化不改变 agentId、Provider 归属和已有任务关联。不自动为其建立索引或授权规则。

### 扩展 URI 发布规则

- 固定使用 `https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/source-agent/v1.md`，沿用 Avernet 仓库文档地址作为扩展标识的约定，不占用 A2A 官方扩展命名空间。
- URI 标识扩展规范，不标识某个 Provider、Agent 或部署实例。所有接入平台及开发、预发、生产环境使用同一固定值，不由接入方自行替换。
- URI 对应仓库中的字段规范，不要求部署运行时接口，也不要求调用时访问。文档、SDK 和校验逻辑使用同一常量。
- 发布后保持稳定；只有扩展字段结构或含义发生不兼容变化时升级版本，Agent 软件升级不改变扩展 URI。
- 本地规范已确定；仓库文档合入并发布前，不宣称该 URL 已可公开访问。旧占位 URI 不作为正式扩展的别名。

### 实施范围

1. **身份区分**：BCN 内部 ID 用于存储和授权，Card.name 用于名称展示及选择，provider_name + agentId 用于 BCN 范围内的来源平台关联和精确检索；三者不可互相替代。来源 ID 不改变远端 A2A 调用地址或 Task/Context 关联。
2. **注册合同**：保持 agent_card_url 与 agent_card 二选一。URL 模式从拉取的 Card 提取扩展；内联模式从提交的 Card 提取；不重复增加请求顶层的 external_id 字段，不静默修改远端 Card 或覆盖签名内容。该扩展可选，无来源标识的 Agent 仍可注册；声明后必须符合字段合同。
3. **保存与返回**：完整原始 Card 只保留来源平台身份扩展（agentId、party），不注入 BCN Provider 归属信息。BCN 查询响应外层返回 provider_name、agent_name 和 agent_card；provider_name 从可信 Bot → Provider 注册关联取得，不采信 Card 的自声明。这里的“只保留”针对身份扩展，不删除其他标准字段或扩展。注册、PUT、手动及周期刷新保持一致。不在 Card 顶层自定义 id，不复用 AgentCard.provider 表示 BCN Provider，不生成带 BCN 登记信息的增强版 Card，也不改写原始 Card 签名内容。

   查询响应结构示意（agent_card 省略其他必填字段）：

   ```json
   {
     "provider_name": "order-service",
     "agent_name": "订单助手",
     "agent_card": {
       "name": "订单助手",
       "capabilities": {
         "extensions": [{
           "uri": "https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/source-agent/v1.md",
           "required": false,
           "params": {
                      "agentId": "agent-001",
    "party": "first_party"
           }
         }]
       }
     }
   }
   ```

4. **查询与索引**：明确区分“其他平台读 Card 后在来源平台查询”与“在 BCN 按来源 ID 查询”。前者直接消费扩展；后者提供带权限过滤的精确筛选，跨 Provider 查询指定 provider_name 和 agentId；若提供 Provider 管理范围内的查询，则 Provider 从管理 token 取得，仅需 agentId。只有确认 BCN 查询需求后再建立派生索引，避免默认逐行扫描大 JSON；索引与 Card 同步更新。具体接口待确定。
5. **唯一性与信任**：注册及变更必须通过对应 Provider 管理权限校验，Card 自声明不能授予身份或访问权限。同一 Provider 内相同 agentId 不重复注册；不同 Provider 可以使用相同 agentId，不自动合并、转移所有权或覆盖既有 Bot。
6. **变更与兼容**：Bot 改名不改变来源 ID；已登记来源标识保持稳定，常规 Card 刷新不得静默更换或移除，变更需明确迁移流程。首次为旧 Bot 补充标识可在校验授权和冲突后执行。失败时保留原 Card、名称和索引。

### 验收标准

- URL 与内联注册均可读取相同来源标识，Card 查询完整保留；不携带该扩展的既有 Agent 不受影响。
- 不同 Provider 的相同 agentId 不会串用；来源 ID 不替代 BCN 内部身份，也不作为权限凭证。
- Card 改名后来源 ID 保持不变；刷新、重复注册、非法或冲突声明不产生部分写入和错误映射。
- 有权限的其他平台从响应外层取得 BCN provider_name，从 Card 扩展取得来源标识；两者分别来自可信注册关联和原始 Card，不互相覆盖。验证 Card 自声明的 Provider 信息不能改变响应外层归属，刷新不会向 Card 注入 BCN Provider 信息。
- 如提供 BCN 检索，验证精确匹配、权限过滤、重复结果处理及索引一致性。

### 参考

- [A2A Data-only Extension](https://a2a-protocol.org/v1.0.1/topics/extensions/)

## A2A-009：Provider 管理接口 v0.1 合同

注册路径统一为 POST /a2a/agents，对外使用 Agent 资源名称；内部仍映射为 BCN Bot。对外 Agent 管理接口统一使用路径 bot_id：GET/PUT `/a2a/agents/{bot_id}/agent-card`，POST `.../agent-card:refresh`，Location 采用 ID 查询资源地址；响应中的 agent_name 仍等于 Card.name。GET `/a2a/agents?source_agent_id=...` 在当前 Provider 下精确找回 ID，返回 items（0 或 1 项）。内部 Bot 模型及旧 Bot API 的 bot_name 保持不变，BOT_* 错误码本次不改。新管理入口不接受 bot_name 别名，避免双字段冲突。

独立合同见 [Provider AgentCard 管理接口 v0.1](../../bcn-provider-agent-card-api-v0.1.zh-CN.md)。这是目标合同，不代表现有部署已符合。

- 与 A2A-004/005/006 协同实施四个管理接口，不要求请求或响应携带版本号，统一成功对象、错误码、X-Request-Id、输入大小及严格管理请求字段校验。
- 文档版本与 A2A 协议版本独立；旧 ID API 保持兼容，不静默改变旧调用方语义。
- 实现合同中所有者/摘要限制、URL 获取规则、响应不签发运行 token、当前有效名称唯一及数据一致性；不能仅改文档宣称通过。
- 补充 HTTP 合同测试，覆盖认证、重复注册、URL/内联切换、改名、刷新竞争、错误及失败不部分写入。
- 验证 Agent 管理接口仅按路径 bot_id 定位，旧 name 请求拒绝、旧管理路由不保留别名；Provider 隔离、Card 改名同步和旧 Bot API 兼容行为不变。
- 验证非流式及 JSON-RPC 等依赖能力；上线前提供联调验收记录。来源 ID 扩展在正式 URI 发布前不作为基础接口必需字段。

- 注册推荐直接提交完整 agent_card；按独立合同 2.2 的逐字段要求实现校验和保留行为，并覆盖技能 ID 唯一性、可选能力不启用、技能鉴权限制及签名不等于验真的测试。未知可选 Card 数据不能因管理请求严格校验而丢失。

## 后续补充

后续确认的事项从 A2A-010 开始追加，分别记录背景、实施范围和验收标准。


### 2026-11-04 实施核对

本轮按 v0.1 接入 `POST /a2a/agents` 及查询、更新、刷新三个入口；Provider 只从管理 token 解析。
四个成功响应均包含 `provider_name`、`agent_name`、`bot_id`、`agent_card_url`、`agent_card`，不返回运行凭据。
旧 ID 管理入口保持兼容，旧名称注册路径不作为新对客入口。
JSONRPC 采用 A2A 1.0 PascalCase 方法及 SSE 响应封装；不兼容 0.3 的旧方法名。
来源扩展使用已确定 URI，字段校验与注册、Card 更新的事务约束一起实现；不新增名称唯一索引或缩短字段。
本轮 134 项不同测试通过，覆盖领域校验、HTTP 管理合同、真实 SQLite 注册/更新事务、来源 ID 并发约束、Card 仓储、运行关联、SSE 订阅解析、JSON-RPC 五个方法及协议错误处理。修订后的 5 项鉴权测试与直连配置的 JSON-RPC 测试均通过。

MySQL 8.4 的 39 个完整迁移及仓储合同测试通过，包括来源 ID 并发约束、事务回滚、Card 搜索和组织候选分页。组织候选 JOIN 的排序规则冲突已修复，无需改写迁移或数据库列。

`cargo check -p bcs`、源文件行数检查和 `git diff --check` 通过。完整架构门禁仍报告既有依赖白名单及导入边界问题，未修改规则绕过检查。本机庞大构建目录和系统代理读取曾显著拖慢测试启动，但上述结论均来自实际完成的测试。

这些是本地验证结论，不代表已发布、部署或完成真实远端 Agent 的生产联调。A2A-003、来源 ID 独立检索以及其他未确认的消费者功能仍属后续需求。
