---
agent: tc-code
status: completed
created: 2026-09-04T16:43:24+08:00
iteration: 2
---

# 编码报告

## Worktree 信息
- 路径: /Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/caller-instance-self-restart
- 分支: rebase/caller-instance-self-restart-on-REL20260904
- 最终 PR 基线: github/REL20260904@3c3aeaf109638ea5e1d917a31dd0fd38d971e5ac
- 复用既有 worktree，未创建新 worktree；保留并更新 `001-spec-output.md` 的 iteration 2 架构约束。

## Iteration 2 修复摘要

1. **恢复单一 Service API 权威**
   - `get_authorized_caller_connection()` 已声明在 owning core Protocol。
   - `api/expert_chat_instance_service.py` 已恢复为基线直接 re-export，同一 Protocol 对象同时供 Concrete、DI 和 Router 使用。
   - Protocol/Concrete pair 已加入 conformance registry，并增加公开 API 与 core Protocol 对象同一性断言。
2. **移除未声明 core 内部依赖**
   - core Service 不再 import `core.access`。
   - Router 从可信认证身份和既有 `super_admin()` 解析 `is_super_admin`，显式传给 Service。
   - Service 继续集中执行管理员兼容、本人匹配、精确实例存在和有效 `bot_uuid` 领域规则。
3. **恢复 Endpoint 测试分层**
   - Endpoint 文件移除全部 `unittest.mock`、`AsyncMock`、`patch` 和 direct-router 测试，仅保留真实 world/DI 用例。
   - request/success/denied/failed 日志和敏感哨兵测试迁至 `tests/community/api/expert_chat/test_router.py`。
4. 未修改 BaaS、schema、Repository 查询语义或既有 lifecycle 状态机。

## 改动文件列表
| 文件路径 | 改动类型 | 说明 |
|----------|----------|------|
| src/backend/src/agentclaw/community/core/expert_chat/expert_chat_instance_service_protocol.py | 修改 | 在 owning core Protocol 声明 actor-aware 方法及显式管理员角色参数 |
| src/backend/src/agentclaw/community/core/expert_chat/services/expert_chat_instance_service.py | 修改 | 使用显式 `is_super_admin`，移除 `core.access` import并保留领域授权规则 |
| src/backend/src/agentclaw/community/adapters/http/expert_chat/router.py | 修改 | 从认证配置解析管理员角色并传入 Service；保留安全结构化日志 |
| src/backend/tests/community/architecture/test_service_api_conformance.py | 修改 | 注册 Protocol/Concrete pair并锁定 API/core 同一对象与签名 |
| src/backend/tests/community/api/expert_chat/test_router.py | 修改 | 承载允许 mock 的 Router 日志、角色传递和敏感值测试 |
| src/backend/tests/community/endpoints/test_expert_chat_caller_connection.py | 修改 | 仅保留真实 world/DI Endpoint 用例 |
| src/backend/tests/community/core/expert_chat/services/test_expert_chat_instance_service.py | 修改 | 使用显式 `is_super_admin` 验证管理员、自有实例与拒绝路径 |
| src/backend/specs/caller-instance-self-restart/001-spec-output.md | 修改 | 补充 iteration 2 架构与参数约束 |
| src/backend/specs/caller-instance-self-restart/002-code-report.md | 修改 | iteration 2 编码报告 |

## TDD / 回归 RED 证据

### RED 1：API/core Protocol 双权威

```bash
uv run pytest \
  tests/community/architecture/test_service_api_conformance.py::test_expert_chat_instance_api_reexports_owning_core_protocol \
  -q
```

结果：`1 failed`。公开 API 派生 Protocol 与 owning core Protocol 不是同一对象，断言按预期失败。

### RED 2：两个 Backend 全量回归门禁

```bash
uv run pytest \
  tests/community/framework/test_no_mock_in_endpoint_tests.py::test_no_mock_or_patch_in_endpoint_tests \
  tests/community/architecture/test_module_boundaries.py::test_declared_deps_cover_actual_imports \
  -q
```

结果：`2 failed`：
- Endpoint 文件发现 `unittest.mock`、`AsyncMock` 和 `patch`。
- `core.expert_chat` 发现未声明的 `core.access` import。

## GREEN 验证

### 两个原失败门禁

同一命令复跑结果：`2 passed`。

### 相关功能回归

```bash
uv run pytest \
  tests/community/core/expert_chat \
  tests/community/endpoints/test_expert_chat_caller_connection.py \
  tests/community/endpoints/test_expert_chat_multi_session.py \
  tests/community/api/expert_chat/test_router.py \
  tests/community/acceptance/expert_chat/test_caller_connection_api.py \
  -q -rs
```

结果：`244 passed, 14 skipped, 18 warnings in 1.12s`。
- 14 个 Acceptance 均因未设置 `RUN_ACCEPTANCE=1`、未启动 live Singlebox 而跳过。
- warnings 为既有 Pydantic/Starlette deprecation warnings。

### 架构契约与 HTTP adapter suite

```bash
uv run pytest \
  tests/community/architecture/test_service_api_conformance.py \
  tests/community/architecture/test_api_layer_is_protocols_only.py \
  tests/community/architecture/test_http_adapter_layer_is_http_only.py \
  -q
```

结果：`148 passed, 17 warnings in 1.94s`。新增 Protocol pair 和 API/core 同一性门禁均通过。

## 代码质量检查
- `ruff check --ignore F841`（全部本次改动 Python 文件）：PASS。
- `ruff check --select F401,F811,F821`：PASS，无新增未使用 import、重复 import 或未定义名称。
- `pycodestyle --select=E203,E265`：PASS。
- `git diff --check`：PASS。
- Service 测试文件既有 16 个基线 `F841` 未作无关清理；本次无新增 Ruff finding。

## Git Diff 摘要

```text
 .../community/adapters/http/expert_chat/router.py  | 129 +++++++++++++------
 .../expert_chat_instance_service_protocol.py       |  13 ++
 .../services/expert_chat_instance_service.py       |  47 +++++++
 .../tests/community/api/expert_chat/test_router.py | 141 ++++++++++++++++++++-
 .../architecture/test_service_api_conformance.py   |  16 +++
 .../services/test_expert_chat_instance_service.py  | 105 +++++++++++++++
 .../test_expert_chat_caller_connection.py          |  69 ++++++++++
 7 files changed, 479 insertions(+), 41 deletions(-)
```

## 外部系统边界日志
- 事件保持为 `expert_chat.caller_connection.request/success/denied/failed`。
- Router API 测试验证 `operator_id`、`is_super_admin`、请求参数和 `force_upgrade` 正确适配。
- 成功日志只记录 `authorized_as`、`need_poll`、`duration_ms` 等非敏感字段。
- 拒绝日志记录稳定 reason；失败日志只记录 `exception_type`。
- `SENSITIVE_CONNECTION`、`SENSITIVE_TOKEN`、`SENSITIVE_SECRET`、`SENSITIVE_CREDENTIAL` 以及 Authorization/Cookie 均不落日志。

## 回退修复记录
- 003 必修：API/core Protocol 双权威 → owning core Protocol 单一声明，API 恢复直接 re-export，并加入 conformance gate。
- 003b 回归：core 未声明 `core.access` 依赖 → Router 解析角色后显式传 `is_super_admin`，core import 删除。
- 003b 回归：Endpoint 禁止 mock/patch → 日志测试迁移到 API Router 测试目录，Endpoint 只保留真实 world/DI 用例。


## REL20260904 Rebase 与最终本地门禁

- 原功能提交 `e23e3ab58` 仅作为 topic，使用 `git rebase --onto github/REL20260904 6ecb42630227da0a2030a051659312ac88dea86c` 重放。
- Rebase 后功能提交：`abb5fa5608e10d3e648005d938f3eef35bb3ce2a`。
- `github/REL20260904..HEAD` 仅包含本任务 1 个功能提交，没有夹带 `dev` 的其他提交。
- REL 基线 Backend CI：case pass `100.00% (17127/17127)`；line coverage `88.55%`；change-line coverage `100.00% (44/44)`；`backend CI gate passed`。
- Coverage pytest：`17068 passed, 59 skipped, 0 failed`。Acceptance/live Singlebox 场景未启动，不作为本地通过证据。ouchers
