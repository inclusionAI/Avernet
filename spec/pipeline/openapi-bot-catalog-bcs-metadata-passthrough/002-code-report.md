---
agent: tc-code
status: completed
created: 2026-08-23T00:00:00+08:00
iteration: 1
---

# 编码报告：Catalog Search BCS 元信息透传

## Worktree 信息

- 路径：`/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog`
- 分支：`fix/openapi-bot-catalog-relax-discover-search`

## 改动

| 范围 | 说明 |
|---|---|
| BCS metadata adapter | 对每个已通过 existing physical-Bot 校验的 BCS 当前页 item，读取 `visibility`、`is_online`、`actor_kind`、`is_friend`、`friend_ext`、`friend_check_in_strategy` 和 `user_visibility`。未变更 `/v2/bots/search` 请求参数、headers 或日志。 |
| Catalog Search join | 保持 BCS 顺序和 `(bot_id, entity_id)` 精确 inner join；只将非 `null` 的点名字段写入同一项的 Backend Bot。 |
| HTTP/OpenAPI | `PublicBot` 与显式 Router projection 增加七个 optional 字段；`friend_ext` 保留 BCS 返回的内部键，不接受其他未点名的 BCS 原始字段。 |
| 文档/schema | 更新前端中文文档和 Gateway `bots.openapi.json`；发布 schema 中 `PublicBot`、`DiscoveredPublicBot` 的七个字段逐项与 Backend 动态 OpenAPI 一致。 |
| 测试 | 覆盖 false、嵌套 `friend_ext`、exact-address 隔离、Router allowlist、字段缺失省略和 OpenAPI 声明。 |

## 安全与兼容性

- 这七个字段是用户明确要求公开的固定 allowlist；`bot_uuid`、分页字段及其余 BCS 原始字段继续不返回。
- `friend_ext` 作为明确的公开字段按 BCS 原值透传，不删除嵌套键；Backend 不基于它做授权、筛选或持久化。
- `is_friend` 继续仅接受 BCS JSON boolean；其余新增字段不做值枚举或内容改写。
- 不新增日志。现有 request ID、失败类别和计数日志不记录这些可能与调用者相关的 metadata 值。

## 验证

- RED：4 个新增定向用例先失败，原因分别为 metadata 没有字段、join 不写入、Router 不投影、OpenAPI 不声明。
- GREEN：4/4 新增定向用例通过。
- `DEPLOY_PROFILE=test uv run pytest tests/community/adapters/http/openapi_v1/bot_public/test_bot_public_router.py tests/community/core/bot_public/test_bot_catalog_metadata_service.py tests/community/core/bot_public/test_bot_public_service.py -v`：163 passed。
- 架构门禁：15 passed。
- Ruff、Pyflakes、Gateway schema JSON、动态/发布 OpenAPI 字段对比和 `git diff --check`：通过。

## Diff 审计

- 仅修改 Catalog Search metadata、join、HTTP allowlist、对应测试、中文文档和 Gateway OpenAPI schema。
- 除下述 Iteration 2 的固定路径迁移外，未修改 Discover、legacy Search、BCS 请求参数/headers、分页/排序或既有日志；未处理工作区既有 `.superpowers/` 和其他未跟踪文件。

## Iteration 2：BCS 路径迁移

- 将受控 BCS client 的固定相对路径从 `/v2/bots/search` 切换为 `/bots/search`；`q`、`offset`、`limit`、`tc_bot=true`、timeout 和无 headers 行为未改变。
- TDD：先把路径断言改为 `/bots/search`，确认旧实现失败；切换后 metadata adapter 16 项测试、Ruff、Pyflakes 和 `git diff --check` 通过。
