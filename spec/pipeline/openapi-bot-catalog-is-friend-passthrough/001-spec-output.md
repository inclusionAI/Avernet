---
agent: tc-code
status: completed
created: 2026-08-23T00:00:00+08:00
iteration: 1
---

# Catalog Search `is_friend` 透传 Spec 与实施计划

## 目标

当 BCS `GET /v2/bots/search` 的某个目录项提供布尔字段 `is_friend` 时，`GET /openapi/v1/bots/catalog/search` 在该 Bot 的精确 join 成功后原样返回该字段；BCS 未提供该字段时，OpenAPI 不返回该字段。

## 范围与约束

- `is_friend` 的唯一事实源是当前 BCS 页的对应目录项。Backend 不查询、推导、覆盖或删除该值。
- 保持 BCS 请求不携带 headers 的既有行为；不转发 Gateway principal、Cookie、Bearer 或原始认证头。因此本次不改变 BCS 是否实际提供 `is_friend` 的条件。
- 仅接受 BCS 契约中的 JSON boolean。字段缺失为合法的“不透出”；字段存在但不是 boolean 时，沿用当前 BCS metadata 的 fail-closed 语义，Search 返回既有 `502000`，不返回部分数据。
- `is_friend` 必须按精确 `(bot_id, entity_id)` 关联；同 `bot_id` 的不同 owner 不能串值。
- 不改变 `tc_bot`、关键词、分页、排序、tenant/current-env/soft-delete join、Search 的 `public` 筛选语义、Discover、旧接口、认证或日志格式。
- 公开 HTTP DTO 仍使用显式 allowlist；除新增 `is_friend` 外，不放行任何 BCS 原始字段或 Backend 内部字段。

## 领域与契约变更

| 层 | 变更 |
|---|---|
| BCS metadata port | `BotCatalogMetadata.is_friend: bool | None = None`，`None` 表示上游未提供。 |
| BCS adapter | 从合法 BCS item 提取可选 bool，保留现有固定相对路径、`tc_bot=true`、无 headers 和 fail-closed 规则。 |
| Catalog service | exact join 时仅把 metadata 中非 `None` 的值放入对应 Backend item。 |
| OpenAPI adapter | `PublicBot.is_friend: bool | None = None`，仅从 joined service record 显式投影；`response_model_exclude_none` 省略缺失值。 |
| 文档/schema | 增加可选 boolean 字段与来源说明。 |

## 实施计划

### Task 1：先锁定 BCS metadata 行为（TDD）

**文件：**

- 修改：`src/backend/tests/community/core/bot_public/test_bot_catalog_metadata_service.py`
- 修改：`src/backend/src/agentclaw/community/core/bot_public/catalog_metadata.py`
- 修改：`src/backend/src/agentclaw/community/core/bot_public/services/bot_catalog_metadata_service.py`

- [x] 新增一个 BCS item 包含 `is_friend: false` 的测试，断言 metadata 保留 `False`，而不是按真假值丢失。
- [x] 新增参数化测试，断言存在的非 bool `is_friend` 触发 `BotCatalogMetadataUnavailableError`。
- [x] 先运行这两个测试，确认当前实现分别因丢失字段和未拒绝错误类型而失败。
- [x] 为 metadata 值对象增加 optional bool；adapter 仅在字段缺失时使用 `None`，否则校验 bool 并原样写入。
- [x] 重跑 metadata adapter 测试。

### Task 2：锁定 exact join 与 OpenAPI allowlist（TDD）

**文件：**

- 修改：`src/backend/tests/community/core/bot_public/test_bot_public_service.py`
- 修改：`src/backend/tests/community/adapters/http/openapi_v1/bot_public/test_bot_public_router.py`
- 修改：`src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py`
- 修改：`src/backend/src/agentclaw/community/adapters/http/openapi_v1/bot_public/schemas.py`
- 修改：`src/backend/src/agentclaw/community/adapters/http/openapi_v1/bot_public/router.py`

- [x] 新增服务测试：BCS `is_friend: false` 只进入其 exact joined item。
- [x] 新增 Router 测试：`is_friend: false` 被公开响应保留；service record 没有该字段时，响应中该字段省略，且原有敏感字段仍不可见。
- [x] 先运行上述测试，确认当前实现失败。
- [x] 在 service 的 BCS 顺序 join 中按 metadata address 添加非 `None` 的 `is_friend`；在 `PublicBot` 和 `_public_bot` 中显式添加可选字段。
- [x] 重跑 Router 与 service 测试。

### Task 3：同步公开契约并验证

**文件：**

- 修改：`src/backend/docs/openapi-v1/bot-public-catalog-api.zh-CN.md`
- 修改：`src/gateway/configs/schemas/bots.openapi.json`
- 修改：`spec/pipeline/openapi-bot-catalog-is-friend-passthrough/002-code-report.md`

- [x] 更新前端文档，声明该字段仅在 BCS 当前项提供时出现，并保持 caller-relative 语义。
- [x] 用 Backend OpenAPI 输出的 `PublicBot` schema 更新 gateway schema 的最小语义差异，并为 optional boolean 写/更新断言。
- [x] 运行 metadata、service、router 定向 pytest，Router OpenAPI schema 断言，Ruff、unused-import 检查、架构门禁和 `git diff --check`。
- [x] 审计相对基线的 diff，只保留上述合同透传所需变更；不提交已有 `.superpowers/` 或其他未跟踪文件。

## 兼容性与风险

- 这是向后兼容的响应字段新增：旧客户端可忽略，新客户端必须按 optional 字段处理。
- `is_friend` 是调用者相关的 BCS 派生状态；本次只透传实际 BCS 返回值，不在 Backend 使用它做授权、过滤或持久化。
- BCS 当前 adapter 不携带 Bearer，若上游因该条件不返回字段，HTTP 响应将继续省略 `is_friend`；这不是 Backend 删除。
