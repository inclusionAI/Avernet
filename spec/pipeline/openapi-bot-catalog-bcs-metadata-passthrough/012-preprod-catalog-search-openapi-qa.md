---
agent: tc-browser-interface-test
status: passed
created: 2026-08-23T00:00:00+08:00
---

# Catalog Search OpenAPI 预发认证态 QA

## 范围

- 目标：预发 Gateway 的 `GET /openapi/v1/bots/catalog/search`。
- 参数：`page=1`、`page_size=20`，未传关键词。
- 方法：在 `bb-browser` 的 Gateway 同源 Swagger 临时页内，以浏览器现有认证态发起 relative fetch；Cookie 和响应原文均未读取、复制或记录，临时标签已关闭。

## 结果

| 检查 | 结果 | 证据 |
|---|---|---|
| HTTP / 业务码 | PASS | HTTP 200、`200000`。 |
| 统一响应信封 | PASS | 存在 `code`、`message`、`data`、`request_id`；`data.items` 为数组且 `data.total` 为整数。 |
| 当前页与总数 | PASS | 当前页 13 条，`total` 为 13。 |
| 公开条目模型 | PASS | 每项均具备公开模型必填字段。 |
| 敏感字段泄露 | PASS | 顶层公开条目未发现 `binding_id`、数据库 `id`、`device_id`、`ext`、token 或环境字段。 |
| BCS 可选字段投影 | PASS | 当前页的 13 项均含 `visibility`、`is_online`、`actor_kind`；`is_friend`、`friend_ext`、`friend_check_in_strategy`、`user_visibility` 当前均未出现，符合可选字段的省略语义。 |

## 结论

预发 Catalog Search OpenAPI 已可用，响应信封、分页数值与公开字段 allowlist 符合契约。该浏览器测试不读取或记录 Bot 原始记录，因而不做逐条 `(bot_id, entity_id)` 的跨服务明细比对；该精确 join 已由本地契约测试覆盖。
