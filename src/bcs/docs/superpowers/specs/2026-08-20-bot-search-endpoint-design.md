# Bot Search 接口设计

> 状态：修订待确认（本次仅更新 spec，确认后再改代码）
> 日期：2026-08-20；修订：2026-08-24
> 范围：为好友申请页面提供 bot 列表查询接口，支持名称模糊搜索 + 可见性/好友策略过滤 + 分页 + friend 状态。

## 1. 背景

迁移到边权限后，好友申请页面的 bot 列表需从 BCS 获取（所有 tc bot 已 onboard 到 BCS）。需一个查询接口支持：
- 按名称模糊搜索
- 按其他关键字（可见性、状态等）过滤
- 分页
- 返回 friend 状态（来自 edge_grants）

## 2. 现有接口评估

| 端点 | 名称搜索 | 过滤 | 分页 | 认证 | is_friend | 结论 |
|---|---|---|---|---|---|---|
| `GET /bots` | ❌ | onboarded, active_only | offset/limit | Bearer 强制 | ❌ | 不满足 |
| `GET /bots/paged` | ❌ | user_id | offset/limit | 无 | ❌ | 不满足 |
| `GET /bots/discover` | ✅ `q` | skill[], visibility, role, org_code | ❌ 无分页 | Bearer 强制 | ✅ 读老表 | 部分满足，无分页+auth+is_friend |
| `POST /bots/query` | ❌ | bot_uuids[] (仅 ID) | ❌ | Bearer | ❌ | 不满足 |

**结论：现有接口不能直接满足，需要新接口。**

## 3. 新接口设计

### 3.1 基本信息

```
GET /bots/search
```

### 3.2 查询参数（全部可选，可任意组合）

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `bot_uuids` | string | — | 精确 Bot UUID 集合，逗号分隔，最多 100 个；与其他可见性、状态及 viewer 条件共同生效 |
| `q` | string | — | 名称/简介模糊搜索（contains，大小写不敏感） |
| `visibility` | string | — | Bot 可见性过滤，支持单值或多值 OR：`public` / `protected` / `private`，推荐逗号分隔如 `visibility=public,protected`；非法值 → 400 |
| `user_visibility` | string | — | 用户侧可见/可加好友策略过滤，支持单值或多值 OR：`public` / `protected` / `private`，推荐逗号分隔如 `user_visibility=public,protected`；非法值 → 400 |
| `status` | string | — | 状态过滤：`online` / `hidden`；非法值 → 400 |
| `viewer_actor_type` | string | — | 好友关系视角 actor 类型：`human` / `bot`；必须和 `viewer_actor_id` 同传 |
| `viewer_actor_id` | string | — | 好友关系视角 actor id；必须和 `viewer_actor_type` 同传 |
| `friendship` | string | `all` | 相对显式 viewer 的好友关系过滤：`all` / `friends` / `non_friends`。`all` 表示不过滤；`friends` 仅好友；`non_friends` 仅非好友。`friends`/`non_friends` 必须同时传 viewer |
| `tc_bot` | bool | — | TC（TeamClaw backend）bot 过滤：`true` 仅返回 backend 过来的 bot，`false` 仅返回 native bot，缺省不过滤（见 §3.6.6） |
| `offset` | int | 0 | 分页起始 |
| `limit` | int | 20 | 每页数，取值范围 `1..=100`，越界 → 400 |

### 3.3 认证

- **Bearer（可选）** — 任何 actor（人或 bot）
- 无 Bearer → 仅返回 `visibility=public` 的 bot
- 有 Bearer → 返回可见 bot 列表；Bearer 只影响可见性范围和排除调用者自身
- 好友关系不从 Bearer/请求身份隐式计算；仅当显式传 `viewer_actor_type + viewer_actor_id` 时返回 `is_friend` 字段。
- `friendship` 是查询过滤条件，默认 `all`；`friendship=friends/non_friends` 必须提供显式 viewer。

### 3.4 响应

```json
{
  "items": [
    {
      "bot_uuid": "20260421_gfdsz5vi:85020",
      "name": "研发助手",
      "summary": "代码审查专家",
      "visibility": "public",
      "user_visibility": "protected",
      "friend_ext": {
        "no_check_scope_friend_deps": ["dep-1"],
        "view_scope_user_friend_deps": [],
        "view_scope_agent_friend_deps": []
      },
      "friend_check_in_strategy": "APPROVAL",
      "status": "online",
      "is_friend": false,
      "actor_kind": "bot",
      "is_online": true
    }
  ],
  "total": 42,
  "offset": 0,
  "limit": 20
}
```

> `user_visibility` / `friend_ext` / `friend_check_in_strategy` 直接透出 Bot 当前好友策略配置；缺省值按现有 bot 配置模型兜底。
> `is_friend` 是响应字段，不再作为查询参数名使用；仅在显式传 `viewer_actor_type + viewer_actor_id` 时返回，未传 viewer 时该字段缺省（`skip_serializing_if`）。
> `status` 来自 `ActorStatus`（`online`/`hidden`），`is_online` 来自 `effective_dynamic_status`（`active`/`offline`）。

### 3.5 错误码

| HTTP | code | 说明 |
|---|---|---|
| 400 | BadRequest | 参数非法：`limit` 越界、`visibility`/`user_visibility`/`status`/`viewer_actor_type`/`friendship` 取值非法、viewer 参数未成对传入、`friendship=friends/non_friends` 缺少 viewer |
| 401 | Unauthorized | Bearer 提供但解析失败 |
| 500 | Internal | 服务内部错误 |

### 3.6 行为规则

1. **名称搜索**：`q` 匹配 `capabilities.name` 或 `capabilities.summary`（contains，大小写不敏感，trim 后空串视为不过滤）。
2. **Bot 可见性过滤（`visibility`）**：
   - 支持多值 OR，推荐格式：`visibility=public,protected`。为降低接入成本，代码实现可同时兼容重复 query：`visibility=public&visibility=protected`。
   - 无 Bearer → 强制有效范围为 `public`，即使请求传 `visibility=protected/private` 也不能扩大匿名可见范围。建议实现为“请求过滤值 ∩ 匿名允许值 `{public}`”；交集为空则返回空列表。
   - 有 Bearer → 默认返回 `public` + `protected`；若传 `visibility` 则按传入集合过滤（可包含 `private`）。
3. **用户侧可见性过滤（`user_visibility`）**：
   - 支持单值/多值 OR，取值同 `visibility`：`public` / `protected` / `private`。
   - 该字段对应好友添加策略里的用户侧可见/可加好友配置，用于筛选“对用户侧是否可见/可添加”的 bot。
   - 不传则不过滤。
4. **viewer 校验**：`viewer_actor_type` 和 `viewer_actor_id` 必须同时传或同时不传；`viewer_actor_type` 仅允许 `human` / `bot`；`viewer_actor_id` trim 后不能为空。
5. **friend 过滤（`friendship`）**：
   - `friendship=all` 或不传 → 不按好友关系过滤，返回好友 + 非好友。
   - `friendship=friends` → 仅返回与 viewer 已是好友的 bot。
   - `friendship=non_friends` → 仅返回与 viewer 非好友的 bot。
   - `friends` / `non_friends` 必须同时传 `viewer_actor_type + viewer_actor_id`；`all` 不要求 viewer。
   - 为避免“`is_friend` 不传到底是 false 还是不过滤”的歧义，查询参数不再使用 `is_friend`；`is_friend` 仅保留为响应字段。
6. **排序**：按 `name` 升序（无 name 的排最后；可扩展为多字段排序参数）。
7. **is_friend 响应判定**：handler 调 `ConnectService::list_friends(viewer_actor_id)` 取好友 actor_id 集合，按 `bot_uuid` 是否在集合内判定；仅显式 viewer 存在时返回 `is_friend` 字段。

### 3.6.6 TC bot 过滤（`tc_bot`）

- **判据**：`tc_bot` 过滤依据是持久化的 **owner-suffixed `bot_uuid`**——`bot_uuid` 形如 `<prefix>:<staff_no>` 且后缀 == `created_by` 所有者。这是 backend 经 `POST /admin/bots/{bot_uuid}/ensure`（服务凭证，非用户 JWT）onboard 的 bot 的持久化标记，与删除流程 `is_owner_suffixed_bot_id_for_staff`（"TC bot must be deleted from TC"）同源。
- **不依赖** `ProviderBotBinding`（那只是下行模式 bot 的绑定，backend 过来的 bot 不在该记录里）。
- `tc_bot=true` → 仅留 owner-suffixed bot；`false` → 仅留 native（WebSocket 自注册）bot；缺省 → 不过滤。纯内存判定，无需额外 repo 查询。

### 3.7 复用性

| 复用 | 来源 |
|---|---|
| bot 列表查询 | 新增 `BotQueryService::search_bots`（基于 `BotRegistryCoreService::list_active`，携带 `status` + `effective_dynamic_status`） |
| friend 状态 | `ConnectService::list_friends(viewer_actor_id)` |
| caller 解析 | `caller_actor_id_from_headers`（现有，可选） |
| TC bot 判据 | `is_tc_bot(&RegisteredBot)`（后缀 == `created_by`，见 `bcs-bot/src/application/bot.rs`） |

## 4. 兼容性 & 可扩展性

### 兼容性
- `GET /bots/search` 新路径，不 modifies 任何现有端点。
- 老 `/bots/discover` 保持不变（老前端继续用）。
- 响应结构独立，不依赖老 `{success,data}` envelope。
- 查询参数 `is_friend` 拟由 `friendship` 替代；若线上已有调用方使用 `is_friend`，实现时建议短期兼容读取并记录 deprecation 日志，但正式文档只推荐 `friendship`。

### 可扩展性
| 未来需求 | 扩展方式 |
|---|---|
| 按 skill 过滤 | 新增 `skill` query param |
| 按 domain 过滤 | 新增 `domain` query param |
| 按 tag 过滤 | 新增 `tag` query param |
| 多字段排序 | 新增 `sort` param（如 `sort=-created_at,name`） |
| 仅返回可加好友的 bot | 使用 `user_visibility` + `friend_check_in_strategy` / 后续新增更明确的 `addable=true` param |
| 分页游标 | 新增 `cursor` param（与 offset/limit 并存） |

所有扩展都是新增 query param，不破坏已有参数和响应结构。

## 5. 实现改动点

| 层 | 改动 | 文件 |
|---|---|---|
| **wire** | `BotSearchQuery`（含 `visibility` 多值、`user_visibility`、`viewer_actor_type`、`viewer_actor_id`、`friendship`、`tc_bot`）+ `BotSearchEntry` response struct 增加 `user_visibility` / `friend_ext` / `friend_check_in_strategy`；crate root re-export | `bcs-protocol/src/http/bots.rs`、`http/mod.rs`、`lib.rs` |
| **handler** | `search_bots` handler：参数校验（limit/visibility/user_visibility/status/viewer/friendship → 400）、caller 解析、可见性规则、显式 viewer 的 friendship 后过滤 + 分页、`BotSearchEntry` 组装 | `bcs-http/src/routes/bots.rs` |
| **router** | 注册 `.route("/bots/search", get(routes::bots::search_bots))` | `bcs-http/src/router.rs` |
| **service** | `BotQueryService::search_bots(SearchBotsCommand) -> BotSearchResult` 基于 `list_active()` 过滤 `q`/`visibility[]`/`user_visibility[]`/`status`/`tc_bot` + name 排序，经 `bot_to_query_entry` 带 status/online/friend policy 字段；`is_tc_bot` helper | `bcs-service-api/.../bot_query.rs`（trait）、`bcs-bot/src/application/bot.rs`（impl） |
| **friend** | `friendship` 在 handler 按显式 `viewer_actor_id` 调 `ConnectService::list_friends` 取集合判定；响应字段仍叫 `is_friend` | 复用现有，无新增 |
| **store** | 无改动（复用 `bcs-bot-store` 现有 query 能力） | — |
| **测试** | handler 契约测试 + `search_bots` 服务级测试 | `bcs-http/tests/bots_contract.rs`、`bcs-bot/tests/bot_search_contract.rs` |

> 注：早期 WIP 复用了 `BotDiscoveryService::discover_bots`，但其 `BotDiscoveryEntry` 丢弃 `status`/`is_online`，无法满足 §3.4 响应与 `status` 过滤，故改为新增 `search_bots` 直接查询 `list_active()`。另顺手修复了 WIP 误从 `/bots/{id}`、`/bots/query`、`/bots/status` 响应移除 `status` 字段的回归。

## 6. 不做的事

- 不修改 `/bots/discover`（老接口保持不变）。
- 不加 OpenAPI V1 适配层（v2 独立路径，后续需 v2 OpenAPI 时再加）。
- 不加 `cursor` 分页（先用 offset/limit，未来扩展）。
- 不做复杂全文搜索（先用 SQL LIKE，未来可换 FTS/ES）。

## 6.1 待确认点

1. `visibility` / `user_visibility` 多值传法：本文档推荐逗号分隔，并建议实现兼容重复 query。若希望只支持一种，建议只保留逗号分隔，改动最小。
2. `is_friend` 查询参数兼容：正式推荐新参数 `friendship=all|friends|non_friends`。如果确认没有线上调用方依赖 `is_friend`，实现时可直接删除/不支持旧 query；否则短期兼容旧参数但不在公开文档展示。
3. `friendship=all` 且传 viewer 时：本文档建议返回全部并附带每个 item 的 `is_friend`；不传 viewer 时返回全部但不带 `is_friend`。

## 7. 示例调用

```
# 搜索名称含"研"的 bot
GET /bots/search?q=研

# 搜索所有 public 或 protected bot，第二页
GET /bots/search?visibility=public,protected&offset=20&limit=20

# 搜索 user_visibility 为 public 或 protected 的 bot
GET /bots/search?user_visibility=public,protected

# 以 bot-viewer 视角搜索非好友的 protected bot
GET /bots/search?visibility=protected&viewer_actor_type=bot&viewer_actor_id=bot-viewer&friendship=non_friends
  Authorization: Bearer <bot_token>

# 仅返回 TeamClaw backend 过来的 bot
GET /bots/search?tc_bot=true

# 在推荐候选中复用相同的 Catalog 可见性规则
GET /bots/search?bot_uuids=bot-a:owner-1,bot-b:owner-2&viewer_actor_type=human&viewer_actor_id=owner-1

# 无认证 → 仅返回 public bot
GET /bots/search?q=助手
```
