---
agent: tc-code
status: completed
created: 2026-08-23T00:00:00+08:00
iteration: 1
---

# 编码报告：Catalog Search `is_friend` 透传

## Worktree 信息

- 路径：`/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog`
- 分支：`fix/openapi-bot-catalog-relax-discover-search`
- 基线：当前分支 `HEAD`；未创建新 worktree。

## 改动

| 范围 | 说明 |
|---|---|
| BCS metadata contract/adapter | 增加 optional `is_friend`，仅接受 bool；缺失则保持 `None`，错误类型沿用 fail-closed。 |
| Catalog Search service | 在既有 BCS 顺序、精确 `(bot_id, entity_id)` join 上，把非 `None` BCS 值写入同一项。 |
| OpenAPI allowlist | `PublicBot`/`DiscoveredPublicBot` 增加 optional boolean，并仅从 service record 显式投影。 |
| 文档与 schema | 前端中文文档和 Gateway schema 描述 BCS caller-relative 的可选字段。 |
| 测试 | 覆盖 `false` 保留、字段缺失省略、错误类型 fail-closed、exact join 与 OpenAPI schema。 |

未新增或修改日志：现有请求 ID、失败类别和结果数日志已覆盖该链路；记录 caller-relative 好友状态本身没有排障必要且会扩大日志暴露面。

## 安全与兼容性

- BCS 的 `is_friend` 是唯一事实源；Backend 不重新查询、推导、过滤或覆盖。
- 不传递 Gateway/Bearer/Cookie 到 BCS，固定相对路径与现有 HTTP client 不变。
- 仅 BCS item 实际提供 bool 时才对外出现；旧客户端可忽略该新增 optional 字段。
- 无关的 OpenAPI generator 漂移已剔除，Gateway schema 只保留该字段的两个组件变更。

## 验证

- RED：新增定向测试在实现前 5 failed / 6 passed，失败原因分别为字段丢失、错误类型未拒绝、join 未关联、HTTP 未投影、schema 缺字段。
- GREEN：11 项定向 TDD 测试通过。
- `DEPLOY_PROFILE=test uv run pytest tests/community/adapters/http/openapi_v1/bot_public/test_bot_public_router.py tests/community/core/bot_public/test_bot_catalog_metadata_service.py tests/community/core/bot_public/test_bot_public_service.py -v`：159 passed。
- 架构门禁：15 passed。
- `ruff check`、`pyflakes`、Gateway schema JSON 校验与 `git diff --check`：通过。

## 未触碰的文件

- `.superpowers/`
- `spec/pipeline/openapi-bot-public-bcs-join/005-qa-report.md`

以上文件为工作区既有未跟踪内容，不属于本次实现，未纳入报告变更范围或提交范围。
