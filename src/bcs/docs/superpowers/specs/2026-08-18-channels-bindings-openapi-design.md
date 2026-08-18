# `/channels/bindings` 全 CRUD 经 BCS OpenAPI 对外开放 — 设计

- 日期:2026-08-18
- 状态:Draft(待评审;假定见 §10,无阻断性问题)
- 分支:`channels_binding_openapi`

## 1. 背景与目标

`/channels/bindings` 现在只存在于 BCS 的"遗留/管理面"`bcs-http` 适配器(`src/bcs/crates/adapters/http/bcs-http/src/routes/channel.rs:125`),5 个方法全人-only(靠 `require_staff_no` → `AuthPluginChain`,无 `staff_no` 返回 401):

| 方法 | legacy handler | 共享 `ChannelService` 方法 |
|---|---|---|
| `POST /channels/bindings` | `create_binding`(`:122`) | `create_binding(cmd)` |
| `GET /channels/bindings` | `list_bindings`(`:147`) | `list_bindings()` |
| `GET /channels/bindings/by-target` | `list_bindings_by_target`(`:163`) | `list_bindings_by_target(target, channel_type)` |
| `PATCH /channels/bindings/{id}` | `set_binding_status`(`:181`) | `set_binding_status(id, active)` 或 `update_binding_config(id, config)` |
| `DELETE /channels/bindings/{id}` | `delete_binding`(`:226`) | `delete_binding(id)` |

它们**不在** `bcs-api-http` 版本化 OpenAPI 适配器、不在 `api-contracts/v1/`、不在发布的 `bcn.openapi.json` —— 这就是要补的口子。

本次目标:为 `/channels/bindings` **全 5 方法**增加版本化 OpenAPI 支持:
- 公开路径挂在已强制的 **collaboration** 前缀下:`/openapi/v1/collaboration/channels/bindings`(及 `{id}`、`/by-target` 子路径);
- 仅支持以"人"身份调用,鉴权走 `bcs-api-http` 现有标准流水线(`verify_principal` + 默认 `IdentityPolicy::HumanOnly`),与其它 openapi 端点同一套;
- **应用层逻辑只保留一份**:legacy 与 V1 共用同一 `ChannelService` 实现,5 方法都已在 trait 上、`BcsChannelService` 都已实现;
- V1 与 legacy 仅在**身份提取 + 严格 schema + 错误封装映射**这三处 adapter 级差异,**权限/身份逻辑全部留在 adapter,app 服务不带 actor**。

## 2. 核心决策:合并 app 层(两 adapter 共用一个 `ChannelService`,5 方法共享)

理由来自 `src/bcs/CLAUDE.md` 官方分层规则:

| 规则(已核实) | 说明 |
|---|---|
| "Delivery adapters call only `bcs_service_api::application`." | V1 adapter 调 `ChannelService` 正是此层正确用法 |
| `ChannelService` 在 `bcs_service_api::application::channel`(`application/channel.rs:155`),是 `application::*Service` | 它**就是** application 服务(非 `*CoreService`),两 adapter 共用是教科书分层 |
| "HTTP state exposed to route handlers must expose application services, not core services or ports." | `ApiState` 暴露 `Arc<dyn ChannelService>` 完全合规 |
| "Delivery adapters handle only application errors. They must not match or expose core errors directly." | `ChannelUseCaseError`(在 `application/channel.rs:19`)是 application 错误,V1 在边界映射,合规 |
| "`application::*Service` must not be a re-export or thin alias of `core::*CoreService`" | 合并**不新建**任何 `*Service`(V1 路由直接调 `ChannelService`),无 thin-alias 之嫌 |

→ **一份 `ChannelService` 实现(`BcsChannelService`),两个 delivery adapter 共用全部 5 方法**。两个 adapter 调同一个 `Arc<dyn ChannelService>` 实例。**不新建** `bcs-app-channel` crate、不新建 `application/v1/channel` trait。

**"权限在 adapter"已由现状保证**:5 个 legacy handler 全在 adapter 内 `require_staff_no` 把人-only 闸;除 POST 的 `created_by`(纯数据归因,app 只存不验)外,PATCH/DELETE/by-target/list 的 trait 方法签名**根本不带 caller/actor** —— 共享 `ChannelService` 实现今天**没有任何 per-caller 鉴权**。所谓"权限逻辑"= 人-only 这道闸,100% 在 adapter。合并不改变这一形状,V1 把同闸换成 `verify_principal`+`require_human`,app 服务继续 authz-free。详见 §9 的"未来归属鉴权"约束。

合并顺手解决的好处:
1. **跨适配器 TOCTOU 自动消失**:同一 `Arc<dyn ChannelService>` 实例 → 同一把 `binding_admin_lock`(`lib.rs` 内 `Mutex<()>`)→ 两 adapter 并发写天然互斥;无需共享锁提取、无需 store 唯一约束。
2. **避开 thin-alias 禁令**:无"V1 服务包 legacy 服务"的薄壳。
3. **省一整个 crate** + **零新增 app 代码**:5 方法已在 trait 上、impl 已实现(且 `list`/`list_by_target` 已逐条脱敏,V1 读出即安全)。

## 3. 对外暴露面

| # | gateway 外部路径 | 方法 | 对应共享 `ChannelService` 方法 | V1 严格 schema |
|---|---|---|---|---|
| 1 | `/openapi/v1/collaboration/channels/bindings` | POST | `create_binding(cmd)` | `CreateChannelBindingRequest`(deny_unknown_fields) |
| 2 | 同上 | GET | `list_bindings()` | 无 body/query |
| 3 | `/openapi/v1/collaboration/channels/bindings/by-target` | GET | `list_bindings_by_target(target, channel_type)` | query(§8.3) |
| 4 | `/openapi/v1/collaboration/channels/bindings/{id}` | PATCH | `set_binding_status(id, active)` **或** `update_binding_config(id, config)` | `UpdateChannelBindingRequest`(oneOf,§8.4) |
| 5 | `/openapi/v1/collaboration/channels/bindings/{id}` | DELETE | `delete_binding(id)` | 无 body |

> PATCH {id} 在 legacy 是双语义(`UpdateBindingRequest{ active?:bool, config?:Value }`,按"恰好一个"分派到 status 或 config)。V1 严格 schema 用 `oneOf`/`minProperties=1 maxProperties=1` 表达 active XOR config,契约比 legacy 更清。

## 4. 架构与数据流

```
调用方 ──(X-Avernet-Principal JWT)──> gateway /openapi/v1/collaboration/channels/bindings[...]
   │  route_security: user:required(5 条,§7 application.yaml)
   ▼
bcs-api-http /openapi/v1/collaboration/channels/bindings[...]  (protected_router 的 collaboration nest 内 .merge(channel::router()))
   │  verify_principal 中间件:验签 → 注入 Extension<AuthenticatedCaller> + RequestId
   │  (路由默认 IdentityPolicy::HumanOnly → app-only fail-closed 401)
   ▼
route::channel::{create_binding | list_bindings | list_bindings_by_target | update_binding | delete_binding}
   │  1. 抽 State<ApiState>、Extension<AuthenticatedCaller>、Extension<RequestId>
   │     [+ Json/Query/Path 按 op 抽,rejection→400 invalid_request]
   │  2. require_authenticated_user(&caller)? —— 人-only 闸(权限逻辑全在此);user=None→forbidden 403
   │  3. POST 派生 created_by = user.id.clone()(其余 4 op 只把门,不取 id)
   │  4. 调 state.channel_service.<对应 5 方法之一>(同一 Arc<dyn ChannelService>)
   │  5. err? ChannelUseCaseError → ApplicationError → application_error_response
   ▼
成功:Envelope::success(code, message, data, request_id) → 201/200
```

5 方法的入参/出参/状态码见 §8、错误见 §6。`ChannelService` 内部业务逻辑(validate_target / 冲突检查 / 持久化 / `redact_config` / `redact_bindings`)与 legacy 走**同一份**,无重复。

## 5. 组件 / 文件改动面

| 层 | 文件 | 动作 |
|---|---|---|
| 错误类型 | `src/bcs/crates/service-api/bcs-service-api/src/application/channel.rs:19` | `ChannelUseCaseError` 加 `Conflict(String)` 变体(§6) |
| 共享 app 实现 | `src/bcs/crates/services/bcs-channel/src/lib.rs:1380` | `create_binding` 冲突返 `Conflict` 而非 `InvalidParams`(list/list_by_target/set_status/update_config/delete 不动) |
| legacy 错误映射 | `src/bcs/crates/adapters/http/bcs-http/src/routes/channel.rs:282`(`channel_error`) | 新 `Conflict` 变体 → `HttpAdapterError::Conflict` → 409 |
| V1 openapi 路由(新) | `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/channel.rs` + 登记 `routes/mod.rs` | `pub fn router() -> Router<ApiState>`:5 方法全挂(POST list/POST create/GET list/GET by-target/PATCH {id}/DELETE {id});**`ChannelUseCaseError`→`ApplicationError` 映射放这里**(对照 legacy `channel_error` 在 `routes/channel.rs:282`) |
| V1 DTO(新) | `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/channel.rs` | `CreateChannelBindingRequest` + `UpdateChannelBindingRequest`(oneOf)+ `ListBindingsByTargetQuery` + `ChannelBindingDto`(出参,套 `Envelope`);`From<ChannelBinding>` impl |
| 路由挂载(改) | `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/mod.rs:15`(`protected_router`) | 在已有 `Router::new().nest("/openapi/v1/collaboration", …)` 内 `.merge(routes::channel::router())`(走既有 collaboration 前缀,**无需新增前缀**) |
| ApiState 槽(改) | `src/bcs/crates/adapters/http/bcs-api-http/src/v1/common/state.rs:16` | 增 `channel_service: Arc<dyn ChannelService>` 槽 + `with_channel_service` builder |
| bootstrap 装配(改) | `src/bcs/crates/bootstrap/bcs/src/server.rs` `build_openapi_v1_state`(L1384 一带) | 接收 `build_channel_runtime`(L664 一带)已构造的 `Arc<dyn ChannelService>`,塞进 `ApiState` |
| 契约片段(新) | `src/bcs/api-contracts/v1/openapi/channel-bindings.yaml` | 3 个 path object:`/channels/bindings`(post create + get list)、`/channels/bindings/by-target`(get)、`/channels/bindings/{id}`(patch + delete);每 op 标 `x-avernet-security`(`user:required`)+ schema `$ref` + `x-error-codes` |
| 契约登记(改) | `src/bcs/api-contracts/v1/openapi.yaml` | `paths:` 加 3 条 `$ref`(三个 path object) |
| 共享模型(看情况) | `src/bcs/api-contracts/v1/shared.yaml` 或 `domain-models.yaml` | 若无则加 `ChannelBinding`/`BindingTarget`/`Visibility`/`GroupChatScope`/`BindingStatus` schema |
| 网关路由安全(改) | `src/gateway/configs/application.yaml` `route_security`(L111-179 一带) | 5 条 `user: required`:`POST /openapi/v1/collaboration/channels/bindings`、`GET …/channels/bindings`、`GET …/channels/bindings/by-target`、`PATCH …/channels/bindings/{id}`、`DELETE …/channels/bindings/{id}` |
| 发布物(再生) | `src/gateway/configs/schemas/bcn.openapi.json` | 跑 `src/bcs/scripts/dump_openapi.py` 重生成 |

> **不动契约校验器** `validate_openapi_contract.py`:collaboration 前缀本就是它认的公开前缀。
> **不增网关新 domain**:挂在已有 collaboration domain 下,`build_served_openapi` 的 per-domain 合并无需新增条目。

## 6. 错误处理与状态映射

| 场景 | HTTP | 来源 |
|---|---|---|
| 无/无效 principal | 401 | `verify_principal` → `ErrorResponse::unauthenticated`(5 op 同) |
| 已认证但非人 caller(app-only,`user=None`) | **403** | handler `require_authenticated_user(&caller)?` → `ApplicationError::forbidden("…")` → 403(与 invitation 一致) |
| POST:body 畸形/未知字段/`account_ref` 空 | 400 | `invalid_request`(`ApplicationError::InvalidInput`) |
| PATCH:body 非 oneOf(同传/全无 active+config) | 400 | `invalid_request`(handler 内 `"active or config is required"`/`"update active and config separately"`) |
| GET by-target:query 缺失/畸形(`target_id` 空) | 400 | `invalid_request` |
| POST:target bot/group 不存在 | 404 | `ChannelUseCaseError::NotFound` → `ApplicationError::not_found` |
| PATCH/DELETE:`{id}` 不存在 | 404 | 同上 |
| POST:`account_ref` 已有 active binding | **409** | `ChannelUseCaseError::Conflict`(新)→ `ApplicationError::conflict` |
| `ChannelUseCaseError::Internal(ServiceError::Conflict(_))` | 409 | → `ApplicationError::conflict`(与 legacy `channel_error` 对齐) |
| 其余 `Internal(ServiceError::*)`(provider-config 校验等,见 §10.4) | 500 | → `ApplicationError::internal`(`error_code`="internal_error",详情记日志+`request_id`,body 脱敏);legacy 侧由 `HttpAdapterError::Service` 经 `status()` 再分(见 §10.4,V1 在 channels CRUD 路径范围有限,impl 对齐) |
| store / 其它内部错(含 GET list/by-target) | 500 | `Internal`;详情记日志 + `request_id`,响应脱敏(`bcs-api-http .../common/error.rs`) |

全部以 `Envelope` 错误形状返回(`{code, message, data:{error_code}, request_id}`)。POST 成功 201、其余成功 200。

**顺带修 legacy 一个既存 bug**:legacy `channel_error` 把 dup-active-binding(`InvalidParams("active binding already exists...")`)映射成 400。加 `Conflict` 变体并映射成 409 后,legacy 与 V1 一致(400→409,仅此一种场景)。作为 bugfix 一并合入。

## 7. 身份与授权(回答"与之前鉴权逻辑相同")

5 op 均走 `bcs-api-http` 标准 openapi 鉴权流水线,**不**复用 legacy `require_staff_no`/`AuthPluginChain`:
- `X-Avernet-Principal` 网关签名 JWT → `verify_principal` 验签(HS256,要求 `exp/iat/aud/iss`);
- 注入 `Extension<AuthenticatedCaller>` + `RequestId`;
- 路由**默认** `IdentityPolicy::HumanOnly`;契约 + 网关 `route_security` 5 条均 `user: required`;
- **人-only 在 adapter 层显式落实**:每个 V1 handler 调 `require_authenticated_user(&caller)?`(authorization.rs);`caller.user=None`(app-only)→ `ApplicationError::forbidden` → **403**(与 invitation 一致;**不是** Python openapi_v1 面的"401 与无凭证不可辨")。缺/坏 principal 由 `verify_principal` 拦在更前面 → **401**。
- POST 的 `created_by = require_authenticated_user(&caller)?.id.clone()`(`AuthenticatedUserIdentity.id`=`v1/user.rs:8,16`),与 legacy(从 `principal.user_id` 映射 `staff_no`)同源,标识符一致;其余 4 op 调 `require_authenticated_user(&caller)?` 仅把门,不取 id。
- **权限逻辑全在 adapter**(上述 helper 在每个 V1 route handler 里),共享 `ChannelService` 不带 actor、不做 per-caller 鉴权(见 §9 未来约束)。

效果等同 legacy("仅人"),但走 openapi 契约/网关流水线。

## 8. 输入 / 输出 schema(严格)

`ChannelBindingDto`(出参,套 `Envelope`)同 legacy `BindingResponse` 字段:`id, channel_type, account_ref, target, group_chat_scope, outbound_visibility, env, status(active|disabled), created_by, config`。**`config` 是服务端脱敏版**:POST/GET list/by-target 出参都经脱敏(`redact_config` `lib.rs:1430` / `redact_bindings` `:1436`;单测 `binding_response_uses_service_redacted_config`、`list_bindings_redacts_provider_config`、`list_bindings_by_target_filters_and_redacts_provider_config` 均押着)。

**8.1 POST create 输入(`CreateChannelBindingRequest`,deny_unknown_fields)**:不暴露 `env`(死字段);字段 `channel_type`(str)、`account_ref`(str)、`target`(`{bot:{bot_id}}`|`{group:{group_id}}`)、`group_chat_scope?`(`conversation_shared`|`per_sender`)、`outbound_visibility`(`full_transcript`|`lead_only`)、`config`(provider 自定义 JSON)。出参 `ChannelBindingDto` → 201。

**8.2 GET list**:无入参。出参 `Vec<ChannelBindingDto>` → 200。

**8.3 GET by-target 输入(query)**:target 判别 + id + 可选 `channel_type`;query 字段名实施时照 legacy `ListBindingsByTargetQuery`/`normalize_target_query`(`bcs-http/src/routes/channel.rs:163,170`)对齐(见 §10.2)。出参 `Vec<ChannelBindingDto>` → 200(已脱敏)。

**8.4 PATCH `{id}` 输入(`UpdateChannelBindingRequest`,oneOf)**:严格表达"active 与 config 恰一非一":
- either `{active: bool}` → `set_binding_status(id, active)`;
- or `{config: Value}` → `update_binding_config(id, config)`;
- 同传/全无 → 400(与 legacy `set_binding_status` handler `:191-200` 同语义)。
出参 `Envelope` 空数据(对照 legacy `{ok:true}`)→ 200。

**8.5 DELETE `{id}`**:无 body。出参 `Envelope` 空数据 → 200。

## 9. 范围 / 非目标与未来约束

- **做**:全 5 方法(POST create / GET list / GET by-target / PATCH {id} 双语义 / DELETE {id})开放;挂 collaboration 前缀;人-only;合并 app 层一份实现;补 `ChannelUseCaseError::Conflict`。
- **不做**:抽正式 `ChannelBindingCoreService`(等 sibling 操作成族再做);改 legacy `bcs-http` 路由处理逻辑(只动 `channel_error` 加 Conflict 映射一例);从 `CreateBindingCommand` 删 `env` 死字段(保留,最小改动);GET list/by-target 的 per-caller 范围过滤(legacy 本就是 admin 全量,V1 对齐;若要按 caller 过滤,改 app 契约,属另议)。
- **未来约束(本次不做,但定调)**:**per-binding 归属鉴权**(只有 `created_by` 能 PATCH/DELETE 自己的 binding)若将来要做,**必须放 adapter 层** —— adapter 先读 binding 再比对 `created_by` 才调 mutate。为此 trait 需补**读方法 `get_binding(id)`**(本次不加)。**绝不**给 `set_binding_status`/`delete_binding` 加 `actor` 参数 —— 那会把鉴权漏进 app,违背"权限在 adapter 层"的定调。

## 10. 假定 / 待最终化

1. `ApiState.channel_service` 槽默认**必填**(bootstrap 总是注入);如需渐进发布再改 optional。
2. `ChannelBinding`/`BindingTarget`/`Visibility`/`GroupChatScope`/`BindingStatus` 契约 schema,及 **GET by-target 的 query 字段名**,实施时据 `src/bcs/api-contracts/v1/shared.yaml`/`domain-models.yaml` 与 legacy `ListBindingsByTargetQuery`/`normalize_target_query`(`bcs-http/src/routes/channel.rs:163,170`)确认/对齐。
3. `created_by` 字段已定:`require_authenticated_user(&caller)?.id.clone()`(`AuthenticatedUserIdentity.id`,与 legacy staff_no 同源)—— 无需实施时再确认。
4. `provider.validate_config` 失败经 `provider_error` 落到 `ChannelUseCaseError::Internal(ServiceError::*)`,具体 HTTP status(400/409/500 之一,多见 400)实施时按 legacy `channel_error` 既有走向确认,V1 映射对齐不另造语义。

## 11. 测试

- **V1 路由层**:5 op 各跑 identity-policy(app-only caller→**403**、无 principal→401);POST happy(201+脱敏+`created_by`)+ 错误(404/409/400/500);GET list happy(200+逐条脱敏)+500;GET by-target(query 畸形→400,happy+已脱敏);PATCH happy(active 路径/config 路径分别 200)+oneOf 分派(同传/全无→400)+NotFound→404;DELETE happy(200)+NotFound→404。
- **legacy 回归**:dup-active-binding 由 400 → 409(新增)。
- **契约**:4 条 path 过 `validate_openapi_contract.py`;对齐 `bcs-api-http` 现有 openapi 路由的 inventory/identity 测试。
- **pre-push**:默认 lint/SAST;`OCB_PRE_PUSH_RUN_CI=1` 走 bcs 模块 gate(CLAUDE.md / AGENTS.md)。

## 12. 决策日志

| # | 决策 | 理由 |
|---|---|---|
| D1 | 公开路径挂 `/openapi/v1/collaboration/channels/bindings`(+ `{id}`、`/by-target`) | 走既有 collaboration 前缀 → 无需动校验器、无需网关注册新 domain,改动面最小 |
| D2 | 范围 = **全 5 方法**(POST create / GET list / GET by-target / PATCH {id} / DELETE {id}) | 用户确认一起做;5 方法已在 trait 上、impl 已实现(且 list 路径已脱敏),零新增 app 代码;只多 DTO/contract/test/route_security |
| D3 | 鉴权用 `bcs-api-http` 标准 `verify_principal` + `IdentityPolicy::HumanOnly`,**不复用** legacy `require_staff_no`,5 op 同 | 必须参与 openapi 契约/网关流水线;效果等同(均拒非人);POST 的 `created_by` 源同 `user_id` |
| D4 | **合并 app 层**:V1 与 legacy 共用同一 `ChannelService`(5 方法),**权限/身份全留 adapter** | 官方分层明令"delivery adapter 只调 application";`ChannelService` 即 application 服务,共用是规定用法;legacy 现 5 handler 本就 adapter-only 鉴权、app 不带 actor,合并不改此形状;平行 V1 app 会重复或陷 thin-alias |
| D5 | 加 `ChannelUseCaseError::Conflict` 变体,冲突 → 409 | 让 V1 不靠 string 嗅探判冲突;顺带修 legacy 把 dup-binding 返 400 的 bug |
| D6 | `env` 死字段保留(不删)+ V1 DTO 不暴露 | 最小改动;impl 用 `self.env`,`cmd.env` 死字段无害;V1 严格 schema 下客户端传 `env`→400 |
| D7 | per-binding 归属鉴权若做,**必须在 adapter 层 + 需补 trait `get_binding(id)` 读方法**,**不得**给 status/delete 加 `actor` 参数 | 守"权限在 adapter 层"定调,防止鉴权漏进 app;本次不做,仅定调 |

## 13. 实施期补正(写计划时核实而来,以此为准,覆盖前文相悖处)

1. **`x-avernet-security = { user: required, app: required }`**(collaboration 惯例,同 `invitations.yaml`),非仅 `user: required`。§3 / §5 以此为准。"仅人"语义 = 必须有 user principal;`app: required` 是人所用 app 随行身份。
2. **网关 `route_security` 无需新增条目**:`src/gateway/configs/application.yaml` L238 `/openapi/v1/collaboration/**` 已 enforce `user: required + app: required`,5 条新路径被此前缀覆盖。§5"加 5 条 route_security"作废。
3. **bootstrap threading 用 reorder 方案**:三个 call-site(`server.rs` default L1966、in-memory L3353、prod L3966)把 `build_channel_runtime(...)` 提前到 `build_openapi_v1_state(...)` 之前,把 `channel_runtime.service.clone()` 作新尾参传入 `build_openapi_v1_state`;fn 内末尾 `.with_channel_service(channel_service)`(对照 `.with_bot_service`,L1492)。`ApiState` 加 `channel_service: Option<Arc<dyn bcs_service_api::application::channel::ChannelService>>` + `with_channel_service` builder(对照 `with_bot_service`,L55);槽为 None 时 handler fail-closed 返 `ApplicationError::internal("Channel V1 service is not configured")`(对照 `bot.rs` 的 `service(&state,&request_id)?`)。
4. **契约 schema 全部 inline 进 `channel-bindings.yaml`**:`BindingTarget`/`ChannelBinding`/`BindingStatus`/`Visibility`(channel-scope)/`GroupChatScope` 在 `shared.yaml`/`domain-models.yaml` 均**不存在**(已 grep 确认);勿与既有 `BotVisibility`/`GroupVisibility` 撞名。
5. **每个 2xx 响应必须是 envelope**(validator `ENVELOPE_FIELDS = {code,message,data,request_id}` 押着,见 `validate_openapi_contract.py:26,181`):create → `code: const 20100` 的 `CreatedChannelBindingEnvelope`(data=`ChannelBinding`);list/by-target → `code: const 20000` 的 `ChannelBindingPageEnvelope`(data=`ChannelBindingPage{items:[...]}`);PATCH/DELETE → `code: const 20000`、data=`null` 的 envelope。错误体 `$ref ../shared.yaml#/ErrorEnvelope`;500 走 `$ref ../shared.yaml#/InternalErrorResponse`。
6. **`src/bws/tests/openapi/test_dump_openapi.py`** 硬编码 `len(operations)==34`(加 5 op → 39)与精确 `COLLABORATION_TAGS` 列表 —— 必改;新增 tag `Collaboration / Channels` 需同步扩 `COLLABORATION_TAGS` + `openapi.yaml` `tags:`。这是易漏的 load-bearing 测试。
7. operationId 全 snake_case 且禁含 `collaboration`/`bcn`/`openapi`/`_v1_`(validator `:154-156`):`create_channel_binding` / `list_channel_bindings` / `list_channel_bindings_by_target` / `update_channel_binding` / `delete_channel_binding`。
8. 重生命令(repo root):`uv run --with pyyaml python src/bcs/scripts/dump_openapi.py src/gateway/configs/schemas/bcn.openapi.json`;校验经 `src/bcs/tests/openapi/` 的 pytest(`test_dump_openapi.py`/`test_*_v1_contract.py` 内调 `validate_contract`)。
