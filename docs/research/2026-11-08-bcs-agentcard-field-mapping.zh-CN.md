# BCS AgentCard 填写说明、字段映射与注册中心改造评估

日期：2026-11-08。性质：research（现状梳理 + 问题评估），不替代
[Provider AgentCard 管理接口 v0.1](../bcn-provider-agent-card-api-v0.1.zh-CN.md)、
[Minimal AgentCard registration v1](../specs/a2a/minimal-card-registration-v1.md) 等合同。

基准：A2A 1.0.1 `AgentCard`
（<https://a2a-protocol.org/v1.0.1/specification/#441-agentcard>）。
代码引用以 `apps/bcs/crates/` 为根。

## 0. 结论速览

1. BCS 目前是 **A2A Client + Card 登记处**：保存 Provider 提交的完整原始 Card，用它选择下行接口、做发现检索；
   **不是 A2A Server，也不对外发布标准 AgentCard**。`GET /bots/{id}/agent-card` 返回的是登记的原始 Card，
   走 Bot 查看权限（`adapters/http/bcs-http/src/routes/bot_agent_card.rs` 头注释明确写了这一点）。
2. Card 只落三列：`bcs_bots.agent_card` / `agent_card_url` / `agent_card_revision`；
   Bot 的 `name` 与 `Card.name` 强同步，`summary` 只在注册时取 `Card.description`，其余字段只活在 Card JSON 里。
3. 自定义扩展有三个：来源扩展 `source-agent/v1`（纯数据，`agentId`/`party`）、工具观察扩展 `tools/v1`、
   以及引用的社区扩展 `experimental-ext-structured-input`（exec 审批）。
4. 若 BCS 要作为注册中心对外提供 AgentCard，需要先定"目录模式"还是"代理模式"。现状直接透出原始 Card，
   会把远端真实地址暴露给外部、绕开 BCS 的鉴权/路由/审计，且 legacy（WS/OpenClaw）Bot 根本没有 Card。
   详见第 4、5 节。

## 1. 入口与校验链路

| 入口 | 校验 | 说明 |
| --- | --- | --- |
| `POST /a2a/agents`、`PUT /a2a/agent-card`、`POST /a2a/agent-card/refresh`（v0.1 名称入口） | `validate_management_card`（`contracts/bcs-domain/src/a2a_management.rs`）+ SDK `select_card_with_bindings`（`external-clients/bcs-a2a-http/src/card.rs`）+ 出站地址策略 | 只接受匿名可调用的 Card |
| `POST /providers/{id}/bots`、`PUT /providers/{id}/bots/{uuid}/a2a`、`.../a2a/card:refresh`（legacy ID 入口）、v2 `/register` | 仅 SDK `select_card_with_bindings` + 地址策略 + `a2a_authentication` | 保留单一静态 Bearer 支持；**不跑** `validate_management_card` |
| 周期刷新（10s tick、300s round、每批 16 个 URL Card） | 当前 Card 能通过管理校验时才对新 Card 强制管理校验（`application/bcs-app-a2a-bot/src/lib.rs` `commit`） | 失败保留上次快照 |

所有写入都会在存储层再跑 `validate_source_transition`（`services/bcs-bot-store/src/a2a.rs`）和来源 agentId 冲突检查（`a2a_source.rs`）。

## 2. 字段填写说明 + BCS 映射

图例：**A2A** = A2A 1.0.1 是否必填；**BCS** = BCS v0.1 入口要求；**映射** = 与 BCS 原有逻辑（Bot 行 / BotCapabilities / 发现 / 下行）的对应关系。

### 2.1 顶层字段

| 字段 | A2A | BCS 要求 | 映射到 BCS 原有逻辑 |
| --- | --- | --- | --- |
| `name` | 必填 | 2～64 个 Unicode 码点，无首尾空白/控制字符，区分大小写，同 Provider 内唯一（`a2a_names::card_bot_name`） | **= Bot 名称**：写入 `bcs_bots.name` 和 `BotCapabilities.name`；作为 `agent_name`、`/a2a/providers/{p}/bots/{bot_name}/chat-async` 的路径段；PUT/刷新时原子改名；非 Card 渠道改名被拒（`agent_identity.rs` `protect_provider_identity`）；查询/发现结果里 `capabilities.name` 用 Card.name 覆盖（`bot_cards.rs`） |
| `description` | 必填 | 非空；未传 `summary` 时作为摘要，需 ≤4096 码点 | 注册时 → `BotCapabilities.summary`（仅一次）；发现文本检索命中字段 |
| `version` | 必填 | 非空 | 无映射，仅保存。注意与 BCS `agent_card_revision`（并发版本）无关 |
| `supportedInterfaces[]` | 必填 | ≥1 项，按偏好排序 | **下行路由**：每次新投递按顺序选第一个"绑定已启用 + 协议 1.0 + 地址策略通过"的接口；已接受的 Task 冻结接口，不随刷新改投。可用性批量查询只投影该字段（`a2a.rs` `availability_many`）。替代了 legacy 的 `webhook_url` |
| `capabilities` | 必填 | 对象，可为 `{}` | 见 2.3 |
| `defaultInputModes` | 必填 | 必须含 `text/plain` | BCS 只发文本，不做其它模态转换 |
| `defaultOutputModes` | 必填 | 必须含 `text/plain` | BCS 只消费文本 Artifact |
| `skills[]` | 必填 | 可为 `[]`；见 2.4 | **替代 legacy `capabilities.skills`**：Card Bot 的 `--skills` 发现选择器只匹配 Card `skills[].id/name`；文本检索匹配 `id/name/description/tags`（`a2a_search.rs`）。`BotCapabilities.skills/domains/scopes` 对 A2A Bot 通常为空，不从 Card 派生 |
| `provider` | 可选 | 本期无需填写；填了须含 `organization` + http(s) `url` | **不采信**：归属来自管理 token 对应的 BCS Provider，Card.provider 不赋予归属/可见性/权限 |
| `documentationUrl` | 可选 | 绝对 http(s) | 仅保存返回 |
| `iconUrl` | 可选 | 绝对 http(s) | 仅保存返回；**未与 BCS Bot avatar 打通**（avatar 走注册命令的 `avatar` 字段） |
| `securitySchemes` | 可选 | 本期建议省略；声明的方案须被引用合法 | 下行鉴权：匿名 → 不带 Authorization；legacy 入口支持唯一一个 HTTP Bearer，值来自 Provider 凭据库（`PROVIDER_BEARER`），**不在 Card 中** |
| `securityRequirements[]` | 可选 | 推荐 `[]`；v0.1 入口要求存在匿名路径（空数组、空对象或空 `schemes`） | 同上。格式为 1.0 proto JSON：`[{"schemes":{"<name>":{"list":[...]}}}]` |
| `signatures[]` | 可选 | 对象数组，不验签 | 原样保存；BCS 不改写 Card，因此签名内容不被破坏 |

不接受用旧版顶层 `url` / `preferredTransport` / `protocolVersion` 代替 `supportedInterfaces`（0.3 不自动兼容）。

### 2.2 `supportedInterfaces[]` 每项

| 字段 | A2A | BCS 要求 | 映射 |
| --- | --- | --- | --- |
| `url` | 必填 | 标准绑定须为绝对 URL；生产用 HTTPS；必须通过出站策略 | 下行实际调用地址（替代 legacy webhook）。gRPC 按原字符串做 mesh 白名单比较；自定义绑定被投影为 `custom-a2a://route/<hex>` 仅用于选择，持久化仍是原值 |
| `protocolBinding` | 必填 | `HTTP+JSON` / `JSONRPC` / `GRPC`（大小写严格），或部署安装的自定义绑定名（`plugin-api/bcs-a2a-binding-api`） | 选择 Client 实现；其他绑定被忽略但不能是唯一接口 |
| `protocolVersion` | 必填 | 被选中的接口必须为 `1.0` | 版本协商 |
| `tenant` | 可选 | 非空 | 原样随请求透传 |

### 2.3 `capabilities`

| 字段 | BCS 行为 |
| --- | --- |
| `streaming` | `true` → SendStreamingMessage + SubscribeToTask 恢复；否则 SendMessage + GetTask 轮询 + CancelTask。tools/structured-exec 两个 required 扩展要求 `streaming=true` |
| `pushNotifications` | 保留，BCS 不配置回调 |
| `extendedAgentCard` | 保留，BCS 不调用 GetExtendedAgentCard；登记的 Card 必须自足 |
| `extensions[]` | `uri` 绝对 URI 且 Card 内不重复；`required=true` 且 BCS 不支持 → 拒绝；未知可选扩展原样保留 |

BCS 识别的扩展：

| URI | 类型 | BCS 消费 |
| --- | --- | --- |
| `https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/source-agent/v1.md` | 纯数据，`required` 必须显式 `false` | `params.agentId`（1～256 字符，同环境同 Provider 唯一，登记后不可改/删）；`params.party` ∈ `first_party`/`second_party`/`third_party`（可改可删）。只用于唯一性约束；**没有按 agentId 检索的接口，也不进入发现结果的结构化字段** |
| `https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/tools/v1.md` | 运行时扩展，需 A2A-Extensions 协商 | 工具 start/result 观察，投影到 BCS 会话的工具展示 |
| `https://github.com/a2aproject/experimental-ext-structured-input/tree/main/v1` | 运行时扩展 | exec 审批（Structured Input）；`required=true` 时还校验 params 兼容 |

### 2.4 `skills[]` 每项

| 字段 | A2A | BCS 要求 | 映射 |
| --- | --- | --- | --- |
| `id` | 必填 | 非空，Card 内唯一 | 发现 `--skills` 精确匹配（忽略大小写）；对应 legacy `Skill.name` 的角色 |
| `name` | 必填 | 非空 | 同上，也参与文本检索 |
| `description` | 必填 | 非空 | 文本检索 |
| `tags` | 必填 | 字符串数组，可为 `[]` | 文本检索；语义上接近 legacy `capabilities.domains`，但**未合并** |
| `examples` | 可选 | 字符串数组 | 仅保存 |
| `inputModes`/`outputModes` | 可选 | 字符串数组 | 不做按技能的模态过滤 |
| `securityRequirements` | 可选 | v0.1 入口要求存在匿名路径 | 同顶层 |

### 2.5 Card 之外的注册字段（BCS 自有，非 A2A）

| 字段 | 来源 | 映射 |
| --- | --- | --- |
| `provider_name` / `provider_id` | 管理 token 绑定 | Bot 归属、凭据、出站策略 |
| `bot_id`（`bot_uuid`） | BCS 生成 | 稳定内部 ID，改名不变 |
| `owners[0]` | 注册请求 | Human 所有者 / 授权链 |
| `summary` | 注册请求，缺省取 Card.description | `BotCapabilities.summary`，**之后 Card 更新不再同步** |
| `visibility` | A2A 注册固定 `protected` | 协作可见性（好友才能拉群） |
| `agent_card_url` | 注册/更新请求 | 刷新来源；内联 Card 时为 NULL |
| `agent_card_revision` | BCS | CAS 并发版本 |

## 3. 现有实现的问题与风险

按影响从高到低：

1. **两套准入标准并存。** legacy ID 入口只跑 SDK 校验，不跑 `validate_management_card`，因此可以存进缺
   `description`/`version`、skill 无 `tags`、重复 skill id 等的 Card；周期刷新也只在"当前 Card 本身合规"时才强制新 Card 合规。
   结果是登记库里 Card 质量不一致，一旦对外发布就会把不合规 Card 发给外部消费者。
2. **`summary` 与 `description` 漂移。** 摘要只在注册时取一次，PUT/刷新"不修改 owners、summary"。
   `name` 强同步而 `summary` 不同步，前端列表和 Card 内容可能长期不一致。
3. **Card skills 与 legacy capabilities 是两套模型。** Card Bot 的 `capabilities.skills/domains` 为空；
   `discover` 有 Card 分支，但其它只读 `BotCapabilities.skills/domains` 的消费方看不到 Card 技能
   （本文未逐一排查这些消费方，改造前需 grep 确认影响面）。
   反过来 legacy Bot 没有 Card，发现结果里 `agent_card` 为空。
4. **名称唯一性只是服务端预检。** 文档已承认：没有 DB 唯一索引，并发注册和 legacy 改名路径仍可能产生重名；
   名称解析遇歧义时报错。来源 `agentId` 唯一性依赖 Provider 行锁 + JSON 扫描（MySQL `JSON_TABLE`，SQLite 依赖写串行），
   没有派生列/索引，Provider 下 Bot 数增长后成本线性上升。
5. **发现检索直接扫 Card JSON。** MySQL 用 `JSON_SEARCH(LOWER(agent_card) ...)`，每次都要解析完整 Card；
   作为注册中心对外开放检索后这是热点路径，需要 skill/tag 的投影或索引。
6. **`iconUrl` 与 BCS avatar、`provider` 与 BCS Provider 均未打通**，对外展示时需要明确以谁为准。
7. **`party` / `agentId` 只校验不消费**：无检索接口，发现结果也没有结构化字段，调用方只能自己解析 `capabilities.extensions`。
8. **`GET /bots/{id}/agent-card` 返回远端真实接口地址。** 内部使用无妨，但对外暴露会泄露 Provider 内网/mesh 地址，
   并让外部客户端绕过 BCS 直连远端 Agent（第 4 节）。
9. **安全声明能力有限**：只支持匿名或单一静态 Bearer，OAuth2/mTLS/APIKey 等被拒；`signatures` 不验签。
   作为注册中心时"Card 是否可信"无法回答。

## 4. BCS 作为注册中心对外提供 AgentCard 的映射

先确定模式，两者 Card 映射完全不同：

### 4.1 模式 A：目录模式（Registry / pass-through）

BCS 只是目录，外部客户端拿到 Card 后直连远端 Agent。

- 对外 Card：**原样返回 Provider 原始 Card**（保护签名），不改 `supportedInterfaces`。
- BCS 自有元数据不塞进 Card，而是放在外层注册项（registry entry）里：

```json
{
  "provider_name": "order-service",
  "bot_id": "bcn-bot-example",
  "agent_name": "订单助手",
  "visibility": "protected",
  "source_agent": {"agent_id": "order-agent-001", "party": "first_party"},
  "agent_card_revision": 3,
  "agent_card": { "...原始 Card..." }
}
```

- 适用：Provider 本身就面向外部开放、地址可公开、鉴权由远端自己负责。
- 缺点：BCS 的权限、路由、审计、会话、工具观察全部失效；只适合可见性为 public 的 Agent。
- legacy WS Bot 无法进入此模式（无可直连的 A2A 端点）。

### 4.2 模式 B：代理模式（Gateway，推荐）

BCS 自己作为 A2A Server，对外发布"BCS 版" Card，调用经 BCS 转发。这与 BCS "协调平面 / 路由归 BCS" 的实体归属一致。

| 对外 Card 字段 | 取值规则 |
| --- | --- |
| `name` | Bot 名称（= 原 Card.name）。跨 Provider 可重名，若对外是全局目录，需要 `provider_name/agent_name` 形式或在外层给出全局唯一键 |
| `description` | 原 Card.description（legacy Bot 用 `capabilities.summary`） |
| `version` | 原 Card.version（legacy Bot 可用 BCS 构建版本或 `agent_card_revision`） |
| `supportedInterfaces` | **重写为 BCS 的入口**，例如 `https://<bcs>/a2a/v1/agents/{provider_name}/{agent_name}`，`protocolBinding` 为 BCS 对外开放的绑定，`protocolVersion: "1.0"`；或统一 URL + `tenant=<bot_id>`。绝不透出远端 url/tenant |
| `provider` | **由 BCS 注入**可信的 Provider 信息（organization = Provider 名称，url = Provider 主页），不沿用自声明值 |
| `capabilities.streaming` | BCS 对外是否支持流式（BCS 总能对外流式，即便下行是非流式也可适配；需要明确策略） |
| `capabilities.pushNotifications` | 按 BCS 对外能力，目前应为 `false` |
| `capabilities.extendedAgentCard` | 可设为 `true`：匿名只给公开目录 Card，认证后按 BCS 可见性/好友关系返回扩展 Card |
| `capabilities.extensions` | 只保留 BCS 对外能**端到端**支持的扩展（tools、structured-input 需要 BCS 透传协商）；`source-agent` 可保留（纯数据）或移到外层 |
| `defaultInputModes`/`defaultOutputModes` | 与 BCS 实际能力取交集，目前为 `["text/plain"]` |
| `skills` | 原 Card.skills；legacy Bot 由 `capabilities.skills` + `domains`（→`tags`）合成 |
| `securitySchemes`/`securityRequirements` | **声明 BCS 自己的鉴权**（Bearer token / OAuth / Gateway 签名的 `X-Avernet-Principal`），与远端 Agent 的鉴权无关 |
| `iconUrl` | BCS avatar 优先，其次原 Card.iconUrl |
| `documentationUrl` | 原值 |
| `signatures` | 原签名因字段被重写而失效，必须**去掉原签名并由 BCS 用自身密钥重签**（JWS），否则外部验签失败 |

发布入口建议：

- `GET /.well-known/agent-card.json`：BCS 自身作为一个"目录 Agent"的 Card（技能如 `discover_agents`）。
- `GET /a2a/v1/agents/{provider_name}/{agent_name}/.well-known/agent-card.json`（或带 `tenant` 的统一路径）：单个 Bot 的代理 Card。
- `GET /a2a/v1/agents?q=&skill=&party=`：目录检索，返回注册项列表（外层元数据 + 代理 Card）。

## 5. 建议改造点

按优先级：

1. **先定模式（A/B）并写 ADR**：这是"BCS 是否作为 A2A Server"的不可逆决策，`todo.zh-CN.md` 中 A2A-001 明确把"BCN 对外作为 A2A Server"列为未纳入范围。
2. **新增"对外 Card 投影"层**，与登记原始 Card 分离：原始 Card 继续作为下行配置真相，对外 Card 由纯函数从
   (原始 Card, Bot 行, Provider, BCS 对外能力) 派生；不要原地修改 `bcs_bots.agent_card`。
   放在 `core`（纯映射）+ HTTP adapter（路由）中，遵守现有分层。
3. **统一准入标准**：legacy ID 入口和刷新也执行 `validate_management_card`（鉴权部分可按入口放宽），
   或在对外发布前做一次合规过滤，不合规 Card 不发布。
4. **legacy Bot 合成 Card**：由 `BotCapabilities` 生成（name/summary/skills/domains），使目录覆盖全部 Bot，
   否则注册中心只能看到 A2A Provider Bot。
5. **可见性和鉴权映射**：对外目录按 BCS `visibility`（public/protected/private）+ 好友关系过滤；
   protected Bot 只在 extended Card / 认证检索中出现；private 不发布。
6. **summary 同步策略**：要么 Card 更新时同步 `summary`（未显式设置过时），要么明确 summary 独立并在对外 Card 中只用 description。
7. **结构化索引**：为 `source agentId`、`party`、skill id/tag 增加派生投影（新编号迁移，不改已有迁移），
   支撑目录检索与唯一性，替代全量 JSON 扫描；同时补 `(env, provider_id, name)` 唯一约束（需先清理存量重名）。
8. **签名**：对外 Card 由 BCS 签名；若模式 A 直接透传，至少在外层注明"BCS 未验签"。
9. **`source-agent` 消费**：在发现/目录结果中输出结构化 `source_agent`，并提供按 `(provider_name, agentId)` 查询。
10. **安全声明扩展**：目录模式下需要允许远端声明 OAuth2/APIKey 等（仅登记、BCS 不调用时不必拒绝），
    与"BCS 下行能否调用"的准入分开判断。

## 6. 本文依据

- 合同：`docs/bcn-provider-agent-card-api-v0.1.zh-CN.md`、`docs/specs/a2a/minimal-card-registration-v1.md`、
  `docs/specs/a2a/extensions/source-agent/v1.md`、`docs/specs/a2a/extensions/tools/v1.md`、`docs/specs/a2a/todo.zh-CN.md`
- 校验：`contracts/bcs-domain/src/a2a_management.rs`、`contracts/bcs-domain/src/a2a_names.rs`、`external-clients/bcs-a2a-http/src/card.rs`
- 存储/检索：`services/bcs-bot-store/src/a2a.rs`、`a2a_source.rs`、`a2a_search.rs`
- 应用/核心：`application/bcs-app-a2a-bot/src/{lib,management,registration}.rs`、
  `services/bcs-bot/src/core/{a2a_provider_registration,agent_identity}.rs`、`services/bcs-bot/src/application/bot_cards.rs`
- 路由：`adapters/http/bcs-http/src/router.rs`、`routes/bot_agent_card.rs`
- 自定义绑定：`plugin-api/bcs-a2a-binding-api/src/lib.rs`

本文为源码阅读结论，未运行测试或实际注册验证。
