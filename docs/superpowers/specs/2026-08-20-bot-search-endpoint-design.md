# Bot Search 接口设计

> 状态：待确认
> 日期：2026-08-20
> 范围：为好友申请页面提供 bot 列表查询接口，支持名称模糊搜索 + 其他关键字过滤 + 分页 + friend 状态。

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
GET /v2/bots/search
```

### 3.2 查询参数（全部可选，可任意组合）

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `q` | string | — | 名称/简介模糊搜索（contains，大小写不敏感） |
| `visibility` | string | — | 可见性过滤：`public` / `protected` / `private` |
| `status` | string | — | 状态过滤：`online` / `hidden` |
| `is_friend` | bool | — | friend 状态过滤：`true` / `false`（需 Bearer 认证） |
| `offset` | int | 0 | 分页起始 |
| `limit` | int | 20 | 每页数（最大 100） |

### 3.3 认证

- **Bearer（可选）** — 任何 actor（人或 bot）
- 无 Bearer → 仅返回 `visibility=public` 的 bot，`is_friend` 字段不返回
- 有 Bearer → 返回可见 bot 列表 + 附带 `is_friend` 状态（从 `edge_grants` 读取）

### 3.4 响应

```json
{
  "items": [
    {
      "bot_uuid": "20260421_gfdsz5vi:85020",
      "name": "研发助手",
      "summary": "代码审查专家",
      "visibility": "public",
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

### 3.5 错误码

| HTTP | code | 说明 |
|---|---|---|
| 400 | BadRequest | 参数非法（如 limit > 100） |
| 401 | Unauthorized | Bearer 提供但解析失败 |
| 500 | Internal | 服务内部错误 |

### 3.6 行为规则

1. **名称搜索**：`q` 匹配 `name` 或 `summary`（contains，大小写不敏感）。
2. **可见性过滤**：
   - 无 Bearer → 强制 `visibility=public`（忽略用户传入的 visibility 参数）。
   - 有 Bearer → 默认返回 `public` + `protected`（可见的）；若传 `visibility` 参数则按参数过滤。
3. **friend 过滤**：`is_friend=true` → 仅返回已是好友的 bot；`is_friend=false` → 仅返回非好友的 bot。无此参数 → 不过滤。
4. **排序**：按 `name` 升序（可扩展为多字段排序参数）。
5. **is_friend** 判定：`EdgeGrantRepo::has_friend_edge(caller, bot_uuid, env)`。

### 3.7 复用性

| 复用 | 来源 |
|---|---|
| bot 列表查询 | `BotQueryService::list_bots_paged`（现有）或 `BotRegistryCoreService::list_active` |
| friend 状态 | `ConnectService::list_friends(caller)` 或直接 `EdgeGrantRepo::has_friend_edge` |
| caller 解析 | `caller_actor_id_from_headers`（现有） |

## 4. 兼容性 & 可扩展性

### 兼容性
- `GET /v2/bots/search` 新路径，不 modifies 任何现有端点。
- 老 `/bots/discover` 保持不变（老前端继续用）。
- 响应结构独立，不依赖老 `{success,data}` envelope。

### 可扩展性
| 未来需求 | 扩展方式 |
|---|---|
| 按 skill 过滤 | 新增 `skill` query param |
| 按 domain 过滤 | 新增 `domain` query param |
| 按 tag 过滤 | 新增 `tag` query param |
| 多字段排序 | 新增 `sort` param（如 `sort=-created_at,name`） |
| 仅返回可加好友的 bot | 新增 `human_addable=true` param |
| 分页游标 | 新增 `cursor` param（与 offset/limit 并存） |

所有扩展都是新增 query param，不破坏已有参数和响应结构。

## 5. 实现改动点

| 层 | 改动 | 文件 |
|---|---|---|
| **wire** | `BotSearchQuery` struct + `BotSearchEntry` response struct | `bcs-protocol/src/http/bots.rs` |
| **handler** | `search_bots` handler | `bcs-http/src/routes/bots.rs`（同文件追加） |
| **router** | 注册 `.route("/v2/bots/search", get(routes::bots::search_bots))` | `bcs-http/src/router.rs` |
| **service** | 复用 `BotQueryService::list_bots_paged`（现有）；`is_friend` 通过 `ConnectService::list_friends` 批量判断 | 无新增 trait |
| **store** | 无改动（复用 `bcs-bot-store` 现有 query 能力） | — |
| **测试** | handler 单测 + E2E | `bcs-http` tests + `edge_permission.sh` |

## 6. 不做的事

- 不修改 `/bots/discover`（老接口保持不变）。
- 不加 OpenAPI V1 适配层（v2 独立路径，后续需 v2 OpenAPI 时再加）。
- 不加 `cursor` 分页（先用 offset/limit，未来扩展）。
- 不做复杂全文搜索（先用 SQL LIKE，未来可换 FTS/ES）。

## 7. 示例调用

```
# 搜索名称含"研"的 bot
GET /v2/bots/search?q=研

# 搜索所有 public bot，第二页
GET /v2/bots/search?visibility=public&offset=20&limit=20

# 搜索非好友的 protected bot
GET /v2/bots/search?visibility=protected&is_friend=false
  Authorization: Bearer <bot_token>

# 无认证 → 仅返回 public bot
GET /v2/bots/search?q=助手
```