---
agent: tc-engine-regression
status: completed
created: 2026-08-22T20:48:00+08:00
iteration: 1
---

# Backend 定向回归报告

## 范围

- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog`
- Branch: `fix/openapi-bot-catalog-relax-discover-search`
- 覆盖本次 Discover 的 allowlist DTO/projection 放宽，以及 Catalog Search 的 BCS 当前页精确 `(bot_id, entity_id)` live join。
- 未修改业务源码、未创建提交、未触碰 `.superpowers/` 或既有 QA report。

## 结果汇总

**PASS（定向功能、隔离和静态门禁）**

| 检查 | 结果 | 摘要 |
|---|---|---|
| Backend 定向 pytest | PASS | `148 passed`，11.28s；覆盖 Discover schema/projection、Search 精确 join/BCS 顺序/total/non-public Bot，以及 repository tenant scope。 |
| 架构 pytest | PASS | `15 passed`，2.74s；repository contract、HTTP adapter 仅 HTTP 职责、core 无 FastAPI import。 |
| Ruff | PASS | 改动生产与测试文件均为 `All checks passed!`。 |
| diff whitespace | PASS | `git diff --check` 无输出。 |

## 实际命令

```bash
cd src/backend
DEPLOY_PROFILE=test uv run pytest \
  tests/community/adapters/http/openapi_v1/bot_public/test_bot_public_router.py \
  tests/community/core/bot_public/test_bot_public_service.py \
  tests/community/repository/bot/test_bot_tenant_raw_sql_and_threads.py -v

DEPLOY_PROFILE=test uv run pytest \
  tests/community/architecture/test_repository_contracts.py \
  tests/community/architecture/test_http_adapter_layer_is_http_only.py \
  tests/community/architecture/test_no_fastapi_in_core.py -v

DEPLOY_PROFILE=test uv run ruff check \
  src/agentclaw/community/adapters/http/openapi_v1/bot_public/router.py \
  src/agentclaw/community/adapters/http/openapi_v1/bot_public/schemas.py \
  src/agentclaw/community/core/bot_public/services/bot_public_service.py \
  tests/community/adapters/http/openapi_v1/bot_public/test_bot_public_router.py \
  tests/community/core/bot_public/test_bot_public_service.py \
  tests/community/repository/bot/test_bot_tenant_raw_sql_and_threads.py

git -C /Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog diff --check
```

## 关键验证结论

- Discover 可接受本次列出的 legacy JSON 值，且仍只输出 allowlisted 字段；`score` 仍为数值字段，推荐服务不可用仍固定映射为 `502000`。
- Search 使用 `list_bots_by_owner_bot_pairs`，测试确认其调用 page size 覆盖 BCS 去重地址数、non-public Bot 可 join、同 `bot_id` 不同 `entity_id` 不会误匹配，输出恢复 BCS 顺序并以 joined 数作为 `total`。
- 被复用的 repository 实现仍包含 ORM tenant guard、`is_delete == 0` 与当前环境 `self._env()` 条件；本次定向 integration pytest 验证 tenant scope 与 non-public 行为。现有该方法没有独立的 deleted/cross-environment fixture 断言，静态审阅确认这两个约束未改动且仍在查询条件中。

## 限制

- 未启动或修改 claude-code engine：本变更仅涉及 Backend HTTP DTO、应用服务和 repository 只读查询，且本次任务明确不启动 engine，因此 engine 回归不适用。
- 依据 `002-code-report.md`，三份既有完整源模块的聚合行覆盖率为 88%（含未关联历史分支），低于 spec 所述的完整模块 90% 门槛；本次未为抬高历史覆盖率新增无关测试。变更行为由上述定向测试覆盖，但变更行覆盖率及远端 ACI 尚未执行，结论为 ACI PENDING。
- pytest 有项目既有 Pydantic/Starlette deprecation warnings；无本次失败或 error。

## 结论

定向 Backend 回归与质量检查通过；可进入代码评审。完整模块覆盖率门槛和远端 ACI 需由后续独立门禁确认。
