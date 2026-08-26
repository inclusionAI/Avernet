# 系分 Spec：Catalog Search 接入 BCS `/v2/bots/search`

## 已确认的对齐规则

- 对外接口仍为 `GET /openapi/v1/bots/catalog/search`，不新增或暴露 `binding_id`、实例、设备、`ext`、token 或环境字段。
- Backend 将当前请求的 `search`、`page`、`page_size` 映射为 BCS 的 `q`、`offset=(page-1)*page_size`、`limit=page_size`，并固定传 `tc_bot=true`；不传 visibility、status、好友关系、caller 或认证头。
- BCS 的 `bot_uuid` 固定按 `<bot_id>:<entity_id>` 解析。每条必须是 `actor_kind=bot` 且二元组非空、不重复；否则整个请求 fail closed 为 `502000`。
- BCS 返回当前页后，Backend 使用现有 tenant-scoped ORM `list_public_bots_by_owner_bot_pairs` 按精确 `(bot_id, entity_id)` 查询公开 Bot，恢复 BCS 排序，只返回能 join 的记录。
- `total` 是当前 BCS 页的 join 数量。例如 BCS 当前页 20 条、Backend 命中 10 条时，返回 10 条且 `total=10`。不跨 BCS 页聚合，也不二次切页。
- Backend 仍是 `PublicBot` 对外字段的唯一权威；BCS 原始响应不得进入 HTTP 响应或日志。
- Legacy `/api/v1/bot-public/search` 保持 Backend-only；Discover 和 `bot_discover_service.py` 不改。

## 验收

| 用例 | 预期 |
|---|---|
| `page=2,page_size=20,search=agent` | BCS 收到 `q=agent,offset=20,limit=20,tc_bot=true`。 |
| BCS `tc_bot=true` | BCS 仅返回由 TeamClaw Backend onboard 的 owner-suffixed Bot；正常数据下该页与 Backend join 数量相等。 |
| BCS UUID `bot-1:owner-1` | Backend 只按 `("bot-1", "owner-1")` 查询当前租户公开 Bot。 |
| 同 bot_id、不同 entity_id | 不得跨 owner join。 |
| BCS 20 条、Backend 命中 10 条 | 返回 10 条，`total=10`。 |
| BCS 5xx、错误 JSON、Human、重复或不合法 UUID | 返回固定 `502000`，不退化为 Backend-only。 |
| 旧 Search / Discover | 保持原有行为。 |
