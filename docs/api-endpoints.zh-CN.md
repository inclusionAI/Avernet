# Gateway v1 对外 API 端点总览

> 本文档是平台对外 API(Gateway v1)的端点总览,面向后端团队与集成方,作为各端并行开发的统一契约参考。
> 契约的权威定义以网关服务生成的 OpenAPI 文档(`/openapi.json`)为准;设计背景与任务拆分见
> `src/gateway/specs/2026-07-22-gateway-v1-api-definition/`(spec / plan / tasks)。

## 定位与背景

我们正在将平台开放给第三方开发者:集成方以**已认证用户**的身份调用网关,在平台上创建并配置
Agent,构建自己的 Agent 产品。现有第一方 API 携带了大量实现细节概念(cron、服务号"阶段"、
引擎透传报文等),不适合作为对外契约。v1 对外 API 是一套全新设计的干净契约,采用
**定义先行(definition-first)** 的方式交付:网关先定义完整契约并生成 OpenAPI,网关、后端与
客户端三方即可并行开发。

运行时职责划分:

- **网关**:定义契约、生成 OpenAPI、鉴权并转发;实际流量中网关将下游响应**原样透传**,不自己构造响应体。
- **后端**:实现各端点的业务逻辑,并负责产出符合约定的响应信封(envelope)。

## 全局约定

### 基础路径

所有端点位于 **`/openapi/v1`** 之下。

### 响应信封(Envelope)

除文件下载等二进制流外,所有 JSON 响应统一使用信封结构,四个字段**全部必填**(`data` 可为
`null` 但必须出现):

```json
{
  "code": 200000,
  "message": "OK",
  "data": { ... },
  "request_id": "..."
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | int | 6 位状态码 = HTTP 状态码(3 位)+ 业务子码(3 位) |
| `message` | string | 人类可读信息,统一为英文 |
| `data` | T \| null | 业务数据;出错或无数据时为 `null`(字段仍存在) |
| `request_id` | string | 链路追踪 id,与响应头 `X-Trace-Id` 一致 |

已定义的标准码:`200000`(OK)、`201000`(Created)、`202000`(Accepted)、`204000`(No Content)。

### 分页

列表端点统一使用查询参数 `page`(从 1 开始)与 `page_size`(默认 20,最大 100),
返回 `Page[T]` 结构:

```json
{ "total": 123, "items": [ ... ] }
```

### 认证与身份模型

- 调用方是一个**已认证用户(user principal)**:`{type, tenant, scopes, subject}`;
  资源归属解析为该用户在其租户内的实体 id。
- 调用者身份完全来自 principal,**接口不接受 `entity_id` 之类的身份参数**。
- 每个端点在 OpenAPI 中通过 `x-avernet-security` 扩展声明鉴权要求(当前统一为
  `first_party_user`);具体 scope 词表暂缓定义。
- 第三方 **app-principal**(应用凭证代表终端用户)是后续网关层能力,不在 v1 后端契约内。

### 通用负载

| 结构 | 形状 | 用途 |
| --- | --- | --- |
| `Deleted` | `{ "deleted": true }` | 删除操作的返回 |
| `NameCheck` | `{ "name": "...", "exists": false }` | 重名检查的返回 |

### 关键设计决策

- 不再有 `stage` / 服务号概念;Agent 始终以其**在线定义(live definition)** 寻址,未来的
  不可变版本/快照模型可无破坏地叠加。
- 定时任务对外为 **`routines`**(不叫 cron),触发器是嵌套对象,便于将来增加非定时触发类型。
- 文件与链接统一为 **`resources`** 抽象(如语雀文档就是一个 link 类型资源),不按来源分接口。
- `container` 概念改名为 `cluster_name`。
- 会话/聊天(conversations)依赖引擎而非后端,v1 暂缓。

## 资源组一览

v1 共 **7 个资源组**:

| 资源组 | 前缀 | 职责 |
| --- | --- | --- |
| bots | `/openapi/v1/bots` | Agent 的全生命周期管理 |
| identity | `/openapi/v1/identity` | Agent 的身份/行为文件 |
| resources | `/openapi/v1/resources` | 统一的知识资源(文件 / 链接 / 文件夹) |
| mcp | `/openapi/v1/mcp` | MCP 工具市场与服务器配置 |
| routines | `/openapi/v1/routines` | 定时/例行任务 |
| skills | `/openapi/v1/skills` 及 bot 子资源 | 技能目录与 Agent 已安装技能 |
| channels | `/openapi/v1/channels` | 外部渠道绑定(如钉钉) |

### bots — Agent 管理

创建可能走 Passport 两阶段授权:直接创建成功返回 `201`,需要用户授权时返回 `202`,
随后轮询授权状态。

| 方法 | 路径 | 说明 | 成功响应 |
| --- | --- | --- | --- |
| POST | `/openapi/v1/bots` | 创建 Agent(body:`bot_name, bot_desc, engine, cluster_name, bot_type, payload`) | 201 `Envelope[Bot]` 或 202 `Envelope[BotAuthPending]` |
| GET | `/openapi/v1/bots/{bot_id}/auth-status` | 轮询创建授权状态 | `Envelope[BotAuthStatus]` |
| GET | `/openapi/v1/bots` | 列表(query:`keyword, engine, status, page, page_size`) | `Envelope[Page[Bot]]` |
| GET | `/openapi/v1/bots/{bot_id}` | 获取详情 | `Envelope[Bot]` |
| PUT | `/openapi/v1/bots/{bot_id}` | 更新(不可改 `engine`) | `Envelope[Bot]` |
| DELETE | `/openapi/v1/bots/{bot_id}` | 删除 | `Envelope[Deleted]` |
| POST | `/openapi/v1/bots/{bot_id}/restart` | 重启 | `Envelope[Bot]` |
| GET | `/openapi/v1/bots/{bot_id}/status` | 获取运行时状态 | `Envelope[BotStatus]` |
| GET | `/openapi/v1/bots/check-name` | 重名检查(query `name`) | `Envelope[NameCheck]` |
| GET | `/openapi/v1/bots/ceiling` | 获取创建配额上限 | `Envelope[Ceiling]` |
| GET | `/openapi/v1/bots/{bot_id}/passport` | 获取 Agent Passport | `Envelope[Passport]` |
| GET | `/openapi/v1/bots/{bot_id}/engine-config` | 读取引擎配置 | `Envelope[EngineConfig]` |
| PUT | `/openapi/v1/bots/{bot_id}/engine-config` | 写入引擎配置 | `Envelope[EngineConfig]` |

### identity — 身份/行为文件

`file_type` 限定为固定白名单(枚举)。

| 方法 | 路径 | 说明 | 成功响应 |
| --- | --- | --- | --- |
| GET | `/openapi/v1/identity/bot/{bot_id}` | 列出 Agent 的身份文件(含是否存在) | `Envelope[IdentityFileList]` |
| GET | `/openapi/v1/identity/bot/{bot_id}/{file_type}` | 读取单个文件 | `Envelope[IdentityFile]` |
| PUT | `/openapi/v1/identity/bot/{bot_id}/{file_type}` | 写入单个文件(body `content`) | `Envelope[IdentityFileRef]` |

### resources — 知识资源

`type` 为 `file` / `link` / `folder`,统一寻址,不按来源区分。

| 方法 | 路径 | 说明 | 成功响应 |
| --- | --- | --- | --- |
| GET | `/openapi/v1/resources` | 列表(分页) | `Envelope[Page[Resource]]` |
| POST | `/openapi/v1/resources` | 创建(body `type: file\|link\|folder`) | `Envelope[Resource]` |
| GET | `/openapi/v1/resources/{resource_id}` | 获取详情 | `Envelope[Resource]` |
| PUT | `/openapi/v1/resources/{resource_id}` | 更新 | `Envelope[Resource]` |
| DELETE | `/openapi/v1/resources/{resource_id}` | 删除 | `Envelope[Deleted]` |
| GET | `/openapi/v1/resources/{resource_id}/download` | 下载 | 二进制流(**不走信封**) |
| GET | `/openapi/v1/resources/{resource_id}/preview` | 预览 | `Envelope[Preview]` |
| GET | `/openapi/v1/resources/check-name` | 重名检查 | `Envelope[NameCheck]` |
| POST | `/openapi/v1/resources/upload` | 上传文件(multipart) | `Envelope[Resource]` |

### mcp — MCP 工具

| 方法 | 路径 | 说明 | 成功响应 |
| --- | --- | --- | --- |
| GET | `/openapi/v1/mcp/servers` | 列出市场中的 MCP 服务器 | `Envelope[Page[McpServer]]` |
| GET | `/openapi/v1/mcp/servers/{server_code}` | 获取服务器详情 | `Envelope[McpServerDetail]` |
| GET | `/openapi/v1/mcp/servers/{server_code}/permissions` | 查询调用者对该服务器的权限 | `Envelope[McpPermission]` |
| GET | `/openapi/v1/mcp/tenants` | 列出租户 | `Envelope[list[McpTenant]]` |
| GET | `/openapi/v1/mcp/servers/{server_code}/config` | 读取调用者的统一服务器配置 | `Envelope[McpConfig]` |
| PUT | `/openapi/v1/mcp/servers/{server_code}/config` | 写入调用者的统一服务器配置 | `Envelope[McpConfig]` |

### routines — 例行任务

触发器为嵌套对象:`trigger: {type: "schedule", cron: "..."}`;未来可增加其他触发类型而不破坏契约。

| 方法 | 路径 | 说明 | 成功响应 |
| --- | --- | --- | --- |
| GET | `/openapi/v1/routines` | 列表(query `bot_id, status`) | `Envelope[Page[Routine]]` |
| POST | `/openapi/v1/routines` | 创建(body `bot_id, name, trigger, command`) | `Envelope[Routine]` |
| GET | `/openapi/v1/routines/{routine_id}` | 获取详情 | `Envelope[Routine]` |
| PATCH | `/openapi/v1/routines/{routine_id}` | 局部更新 | `Envelope[Routine]` |
| DELETE | `/openapi/v1/routines/{routine_id}` | 删除 | `Envelope[Deleted]` |
| POST | `/openapi/v1/routines/{routine_id}/run` | 立即执行一次 | `Envelope[RoutineRun]` |
| GET | `/openapi/v1/routines/{routine_id}/runs` | 执行历史 | `Envelope[Page[RoutineRun]]` |

### skills — 技能

v1 采用简化的"目录 + Agent 已安装技能"模型(后端更丰富的 skill-set 模型是否上探为一等公民仍待定)。

| 方法 | 路径 | 说明 | 成功响应 |
| --- | --- | --- | --- |
| GET | `/openapi/v1/skills` | 技能目录列表 | `Envelope[Page[Skill]]` |
| GET | `/openapi/v1/skills/{skill_id}` | 技能详情 | `Envelope[SkillDetail]` |
| GET | `/openapi/v1/bots/{bot_id}/skills` | 列出 Agent 已安装技能 | `Envelope[list[BotSkill]]` |
| POST | `/openapi/v1/bots/{bot_id}/skills` | 为 Agent 安装技能 | `Envelope[BotSkill]` |
| DELETE | `/openapi/v1/bots/{bot_id}/skills/{skill_id}` | 卸载技能 | `Envelope[Deleted]` |

### channels — 渠道

v1 支持的创建类型为钉钉(`dingding`)。

| 方法 | 路径 | 说明 | 成功响应 |
| --- | --- | --- | --- |
| GET | `/openapi/v1/channels` | 列表(query `bot_id`) | `Envelope[Page[Channel]]` |
| POST | `/openapi/v1/channels` | 创建(type `dingding`) | `Envelope[Channel]` |
| GET | `/openapi/v1/channels/{channel_id}` | 获取详情 | `Envelope[Channel]` |
| PUT | `/openapi/v1/channels/{channel_id}` | 全量更新 | `Envelope[Channel]` |
| PATCH | `/openapi/v1/channels/{channel_id}` | 启用/停用切换(`status`) | `Envelope[Channel]` |
| DELETE | `/openapi/v1/channels/{channel_id}` | 删除 | `Envelope[Deleted]` |

## 当前状态与后续

- **已合入(PR #345)**:全局契约基础 —— 信封 / 分页 / 鉴权标记(`x-avernet-security`)、
  路由聚合器与 `UserPrincipal` 身份模型;各资源组端点按组在后续 PR 中陆续落地。
- **契约先行**:落地阶段的网关 handler 为桩实现,交付物是生成的 OpenAPI 文档;后端随后按本契约
  实现业务逻辑并产出信封。
- **暂缓范围**:conversations/chat(依赖引擎)、协作者与编辑锁、render-screens、bot-public、
  版本/快照 API、scope 词表,以及第三方 app-principal 接入。
