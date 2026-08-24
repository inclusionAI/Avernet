---
agent: tc-code
status: planned
created: 2026-08-23T00:00:00+08:00
iteration: 1
---

# Catalog Search BCS 元信息透传 Spec 与实施计划

## 目标

`GET /openapi/v1/bots/catalog/search` 对 BCS 当前页精确 join 成功的 Bot，透传 BCS 实际提供的下列可选字段：

- `visibility`
- `is_online`
- `actor_kind`
- `is_friend`
- `friend_ext`
- `friend_check_in_strategy`
- `user_visibility`

字段不存在或值为 `null` 时，OpenAPI 响应省略该字段。`friend_ext` 不删除其内部键，由前端决定如何使用；除此之外不新增全量 BCS 原始响应透传。

## 范围与约束

- 仅 Catalog Search 改动；Discover、旧 `/api/v1/bot-public/search`、BCS 请求 URL/参数/headers、分页、排序、tenant/current-env/soft-delete join 和既有日志格式不变。
- Backend 仍以精确 `(bot_id, entity_id)` 做 inner join；字段只能写入 BCS 当前项对应的 Backend Bot，不能跨 owner 串值。
- BCS `actor_kind == "bot"` 是既有 physical Bot join 前置校验，保持不变；透传值不会改变该校验。
- 除现有 `is_friend` 必须为 JSON boolean 的保护外，对新增字段不做枚举、内容或嵌套键删除/改写；仅在上游实际返回非 `null` 值时透传。
- HTTP DTO/Router 仍逐字段显式投影。只公开本 Spec 点名的字段，不把 `bot_uuid`、分页字段或其他 BCS 原始字段变为对外契约。
- 新增字段均为 optional；既有客户端不受影响。

## 对外模型

```ts
type PublicBot = {
  // 既有 Backend 字段省略
  visibility?: unknown;
  is_online?: unknown;
  actor_kind?: string;
  is_friend?: boolean;
  friend_ext?: unknown;
  friend_check_in_strategy?: unknown;
  user_visibility?: unknown;
};
```

`visibility`、`is_online`、`friend_ext`、`friend_check_in_strategy` 和 `user_visibility` 保留 BCS 值形状；本次不在 Backend 推测其可选值。`actor_kind` 已由 BCS physical-Bot 校验限定为 `"bot"`。

## 实施计划

### Task 1：先锁定 BCS metadata 保留行为（TDD）

**文件：**

- 修改：`src/backend/tests/community/core/bot_public/test_bot_catalog_metadata_service.py`
- 修改：`src/backend/src/agentclaw/community/core/bot_public/catalog_metadata.py`
- 修改：`src/backend/src/agentclaw/community/core/bot_public/services/bot_catalog_metadata_service.py`

- [x] 新增一个 BCS item 同时携带六个新增字段的测试，断言 metadata 不丢失 `false`、空对象或嵌套 `friend_ext` 键。
- [x] 先运行该测试，确认当前 metadata 未保存这些字段而失败。
- [x] 为 metadata 增加可选字段；adapter 只从点名字段读取值，不更改 BCS 请求，保留 existing `is_friend` boolean 和 `actor_kind == "bot"` 校验。
- [x] 重跑 metadata adapter 测试。

### Task 2：锁定 exact join 和 HTTP allowlist（TDD）

**文件：**

- 修改：`src/backend/tests/community/core/bot_public/test_bot_public_service.py`
- 修改：`src/backend/tests/community/adapters/http/openapi_v1/bot_public/test_bot_public_router.py`
- 修改：`src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py`
- 修改：`src/backend/src/agentclaw/community/adapters/http/openapi_v1/bot_public/schemas.py`
- 修改：`src/backend/src/agentclaw/community/adapters/http/openapi_v1/bot_public/router.py`

- [x] 新增服务测试：只将 BCS metadata 值写入同 `(bot_id, entity_id)` 的 joined Bot，另一个同 `bot_id` owner 不携带该值。
- [x] 新增 Router 测试：全部点名字段（含 `false`、空对象和嵌套 `friend_ext`）被返回；未点名 BCS/Backend 字段仍不可见；缺失字段被省略。
- [x] 先运行测试并确认因 HTTP DTO/allowlist 缺字段而失败。
- [x] 在既有 join 中只添加 metadata 中非 `None` 的点名字段，并在 schema/Router 逐字段投影。
- [x] 重跑服务与 Router 测试。

### Task 3：同步契约与质量门禁

**文件：**

- 修改：`src/backend/docs/openapi-v1/bot-public-catalog-api.zh-CN.md`
- 修改：`src/gateway/configs/schemas/bots.openapi.json`
- 创建：`spec/pipeline/openapi-bot-catalog-bcs-metadata-passthrough/002-code-report.md`

- [x] 更新前端文档，标记这七个字段仅适用于 Catalog Search，且仅在 BCS 当前页提供时出现。
- [x] 以 Backend OpenAPI schema 为语义来源，对 Gateway schema 只写入新增字段的最小变更。
- [x] 运行所有直接相关 pytest、架构门禁、Ruff、unused-import、JSON 和 `git diff --check`；审计 diff 不含纯格式化或无关文件。

## 验收标准

- BCS 当前 item 的全部点名字段可在精确 joined Search item 中看到，`false`、`{}` 和嵌套 `friend_ext` 不丢失。
- BCS item 没有某字段时，响应中没有该字段；没有重新查询或以 Backend 值替代。
- `friend_ext` 内部键保持原样；不点名字段、`bot_uuid`、BCS 分页字段及既有 Backend 敏感字段继续不返回。
- 全部直接相关测试和质量检查通过。

## Iteration 2：BCS Search 路径迁移

### 目标

将 Catalog Search 的固定 BCS 相对路径从 `/v2/bots/search` 切换为 `/bots/search`。

### 范围与验证

- 只修改 `BcsBotCatalogMetadataService` 的固定相对路径、它的请求断言和前端接入文档。
- `q`、`offset`、`limit`、`tc_bot=true`、timeout、无 headers、错误映射、join 和所有 metadata 透传行为保持不变。
- 先更新路径断言并确认旧实现失败；再修改路径并运行 Catalog metadata adapter 测试、Ruff、Pyflakes 和 `git diff --check`。
