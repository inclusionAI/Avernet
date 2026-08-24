# Catalog Search BCS Adapter：本地回归报告

## 范围

- Worktree：`/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog`
- 分支：`feat/openapi-bot-public-catalog`
- 基线：`origin/dev_refactory_collaboration`
- 本轮验证对象：Catalog Search 对 BCS `GET /v2/bots/search` 的 `q`、`offset`、`limit`、`tc_bot=true` 映射，以及 BCS `bot_uuid` 到当前租户 Backend `(bot_id, entity_id)` 的精确 inner join。
- 未运行 Engine 回归：本次没有 Engine 或 relay 改动，启动 `claude_code` 本地环境与变更无关。
- 未修改生产代码、`.superpowers/`、未提交、未推送、未部署。

## 结果汇总

**PASS** — 与本次改动直接相关的功能、OpenAPI 契约和架构门禁均通过。

| 检查 | 结果 | 证据 |
|---|---:|---|
| Catalog BCS adapter、应用服务、Router、endpoint 回归 | PASS | 118 passed，9.23s |
| 分层与 DI 架构门禁 | PASS | 71 passed，6.36s |
| OpenAPI schema、错误响应、显式身份和统一响应文档门禁 | PASS | 79 passed（6 + 8 + 20 + 45） |
| Ruff 规则 / unused import | PASS | `ruff check` 目标源码与测试均通过 |
| Python 编译 | PASS | `compileall -q` 目标模块通过 |
| Diff 完整性 | PASS | 工作区与 `origin/dev_refactory_collaboration...HEAD` 均通过 `git diff --check` |

## 执行命令

```bash
cd src/backend

DEPLOY_PROFILE=test .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/community/core/bot_public/test_bot_catalog_metadata_service.py \
  tests/community/core/bot_public/test_bot_public_service.py \
  tests/community/adapters/http/openapi_v1/bot_public/test_bot_public_router.py \
  tests/community/endpoints/test_openapi_bot_public_catalog.py

DEPLOY_PROFILE=test .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/community/architecture/test_module_boundaries.py \
  tests/community/architecture/test_http_adapter_layer_is_http_only.py \
  tests/community/architecture/test_core_no_concrete_plugin_imports.py \
  tests/community/architecture/test_protocol_contracts.py \
  tests/community/architecture/test_service_api_conformance.py \
  tests/community/architecture/test_build_injector_composition_root_only.py

DEPLOY_PROFILE=test .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/community/adapters/http/openapi_v1/test_schema_docs.py \
  tests/community/adapters/http/openapi_v1/test_openapi_error_schema.py \
  tests/community/adapters/http/openapi_v1/test_explicit_user_id.py \
  tests/community/adapters/http/openapi_v1/test_responses.py

.venv/bin/ruff check \
  src/agentclaw/community/core/bot_public/catalog_metadata.py \
  src/agentclaw/community/core/bot_public/services/bot_catalog_metadata_service.py \
  src/agentclaw/community/core/bot_public/services/bot_public_service.py \
  src/agentclaw/community/di/modules/bot_public_module.py \
  tests/community/core/bot_public/test_bot_catalog_metadata_service.py \
  tests/community/core/bot_public/test_bot_public_service.py

.venv/bin/python -m compileall -q \
  src/agentclaw/community/core/bot_public/catalog_metadata.py \
  src/agentclaw/community/core/bot_public/services/bot_catalog_metadata_service.py \
  src/agentclaw/community/core/bot_public/services/bot_public_service.py \
  src/agentclaw/community/di/modules/bot_public_module.py

git diff --check
git diff --check origin/dev_refactory_collaboration...HEAD
```

## 已覆盖行为

- BCS 固定路径、当前页参数映射及空 `search` 不传 `q`。
- 仅接受 `actor_kind=bot` 和非空、不重复的 `<bot_id>:<entity_id>`；HTTP 失败、错误 JSON、非法响应和重复记录均 fail closed。
- 以 BCS 页为顺序，按精确复合键查询 Backend；同 `bot_id` 的不同 `entity_id` 不会交叉 join。
- `total` 与 `items` 都是当前 BCS 页的实际 join 数；不会退化为 Backend-only 搜索。
- BCS 不可用映射固定 `502000`，不泄露上游细节；公共投影继续清除敏感字段。
- Router 维持 Protocol 注入，BCS HTTP client 通过 DI 的 BCN qualifier 提供；旧 Search 与 Discover 的既有逻辑未受本次验证范围影响。

## 非阻断提示与边界

- 测试过程仅出现项目既有 Pydantic/Starlette deprecation warnings，未出现失败。
- 额外运行的 `ruff format --check` 提示 4 个本轮已修改文件可自动重排。项目要求的 `ruff check` 已通过，且本轮不为消除该提示做无关格式化改动；此项不作为回归阻断。
- 本地验证通过 `LocalHttpClient` 测试缝验证报文与错误映射；未对真实 BCS 环境发请求，因此真实 BCS 连通性和凭据应在部署环境单独验收。
