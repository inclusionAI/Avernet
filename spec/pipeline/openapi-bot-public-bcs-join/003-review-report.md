> **SUPERSEDED HISTORICAL REVIEW — DO NOT USE AS CURRENT IMPLEMENTATION OR SHIP INSTRUCTION.**
>
> This report records the rejected BCSFuse HTTP batch design only. The current binding design is
> the tenant-scoped metadata port with a fixed unavailable response in
> [004-implementation-plan.md](004-implementation-plan.md); it deliberately has no BCS HTTP
> URL, path, payload, credentials, base-URL configuration, or network call.

---
agent: tc-code-reviewer
status: completed
created: 2026-08-20T15:35:00+08:00
iteration: 2
---

# 已废弃的 BCSFuse HTTP batch 代码评审报告（历史记录）

## 评审范围

- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog`
- 分支: `feat/openapi-bot-public-catalog`
- 范围: 当前未提交的 Backend-candidates → BCSFuse `/v1/workers/batch` catalog join 改动。

## 验证证据

- `uv run pytest -q tests/community/core/bot_public/test_bot_public_service.py tests/community/adapters/http/openapi_v1/bot_public/test_bot_public_router.py tests/community/core/bot_public/services/test_sync_bot_config_uses_resolver.py --cov=...`：**100 passed**, failed=0。
- `uv run ruff check`（全部改动 Python 文件）及 `git diff --check`：通过。
- 局部覆盖率：`BotPublicService` 484/551（88%）、catalog router 52/66（79%）、新增 BCSFuse DI module 29/43（67%）。新 BCS parse 失败分支 [bot_public_service.py:1139](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog/src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py:1139) 和 [bot_public_service.py:1142](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog/src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py:1142) 未覆盖。

## 逐条评审意见

### 固定检查维度

| 维度 | 结论 | 说明 |
|---|---|---|
| 正确性 | FAIL | Backend 全量有序候选、100 条分批、join 后 total/page 已实现；但 BCS membership key 无 tenant 维度，且未验证返回的是 physical Bot。 |
| 安全性 | FAIL | Backend response 仍经 allowlist 投影且新日志只记录失败类型/计数。独立安全复核未发现置信度 >=0.8 的新增高/中危漏洞；但本 Review Spec 的强制 tenant-scoped membership 仍无法证明：BCSFuse 按全局 worker ID 查询且未携带 verified caller/tenant，不能排除跨 tenant 同 key 的错误命中。 |
| 性能 | PASS | 采用可注入 client、每批最多 100 条、单次 5 秒 timeout，未见重试或 N+1。 |
| 代码风格 | PASS | Ruff 和 `git diff --check` 通过。 |
| 测试覆盖 | FAIL | 100 个 focused cases 均通过，但改动核心 module 仅 88%，并漏掉 malformed BCS envelope/item 与 tenant collision 等行为断言。 |
| ACI 覆盖率门禁 | PENDING | 没有指定 PR base/head、远端 junit/coverage job 或 ACI report；不能宣称 PASS。 |
| 静态检查 | PASS | 未发现本次新增的 unused import/variable 或 Python whitespace 告警。 |

### ACI 覆盖率证据

- Base / Head: 未提供 / 当前 worktree 未提交改动。
- 用例: 本地 100/100（100%），skipped=0，failed=0；非远端 ACI casePassRate。
- 总行覆盖率: selected modules 839/2907（29%）；它包含大模块历史行，不能替代 change-line 指标。核心改动 module `BotPublicService` 为 484/551（88%）。
- 变更行覆盖率: PENDING；无可用 base/head/远端 coverage artifact。
- 远端 ACI job: PENDING。

### Review Spec 检查项

| 编号 | 检查项 | 结论 | 说明 |
|---|---|---|---|
| R-01 | BCS request 由 Backend tenant-scoped public candidates 派生 | PASS | [service:1049](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog/src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py:1049) 先以 tenant-scoped ORM Backend 搜索枚举，再生成 worker IDs。 |
| R-02 | canonical composite key、100 条 batch、physical Bot 校验 | FAIL | composite ID 与 colon bot ID 生成正确，batch size=100；但 BCSFuse response 不含/代码不验证 worker type，无法排除非 physical Bot，且 key 不含 tenant。 |
| R-03 | join 后分页、total、stable order | PASS | [service:1055](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog/src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py:1055) 内连接后才在 [service:1084](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog/src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py:1084) 计算 total/slice；101 条跨批测试已通过。 |
| R-04 | 5s fail-closed 固定 502、无 fallback | PASS | [service:1130](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog/src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py:1130) 使用 5 秒单次调用；异常转领域错误并由 [router:88](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog/src/backend/src/agentclaw/community/adapters/http/openapi_v1/bot_public/router.py:88) 固定映射为 502。 |
| R-05 | caller/tenant BCS 语义 | FAIL | router 的 verified principal 仅触发 admission；batch 调用只传 Content-Type。BCSFuse endpoint [worker_routes.py:285](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog/src/bcsfuse/src/interfaces/api/worker_routes.py:285) 按 `store.get_by_id` 全局查找且没有 tenant/caller dependency。 |
| R-06 | App-only 安全兼容性 | FAIL | App-only 仍可到达 Backend，但没有 BCSFuse scoped membership contract 或 authorization conformance evidence，无法与 tenant collision 隔离共同成立。 |
| R-07 | public schema/response/log | PASS | Gateway public fields未扩张；router allowlist 投影，catalog 日志仅含计数/失败类别，未记录 BCS response、worker list、keyword 或认证头。 |
| R-08 | adapter/core/DI 边界 | PASS | router 薄；HttpClient 通过 `QUALIFIER_BCSFUSE` 在 DI 注入，core 未直接构建 HTTP client 或读取环境。 |
| R-09 | 改动文件覆盖 >90% | FAIL | 核心 service 88%，router 79%，且 BCS malformed-response 分支无断言覆盖。 |

## 具体问题列表

### 问题 1: 无 tenant/caller 维度的 BCSFuse membership 可跨 tenant 错配

- 文件: [bot_public_service.py:1121](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog/src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py:1121), [worker_routes.py:285](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog/src/bcsfuse/src/interfaces/api/worker_routes.py:285)
- 严重程度: 必须修复
- 描述: Backend `(bot_id, owner_id)` 仅在 tenant 内唯一，而 BCSFuse batch endpoint 没有 tenant 或 verified-caller input，并以 `store.get_by_id(worker_id)` 全局查找。tenant A 和 B 若存在同一 pair，A 未注册 BCS worker 也可能因 B 的 worker 存在而通过 inner join。
- 建议修复方式: 使用 tenant/caller-scoped BCS contract，或将可信 tenant 纳入 BCS worker identity 与存储/查询约束；在 BCS side 增加 User+App/App-only authorization conformance tests，并增加 A/B same-pair regression。

### 问题 2: 仅以 response map key 判定 membership，未验证 physical Bot/metadata shape

- 文件: [bot_public_service.py:1137](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog/src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py:1137)
- 严重程度: 必须修复
- 描述: 代码只验证 `data` 是 dict，并把请求内 key 直接加入 available set；不验证 item 是有效 metadata 或 physical Bot。`{"success":true,"data":{"id":null}}` 会被视为命中。现有 batch response 本身也没有 worker `type`，故无法满足 Review Spec 的 physical-bot 防错配约束。
- 建议修复方式: 将 batch contract 收紧为 tenant-scoped physical-Bot membership，并逐项校验 required fields/type；无效 item/envelope 均转 `BotCatalogSearchUnavailableError`。覆盖 missing data、invalid item、human/non-Bot、requested-key mismatch。

### 问题 3: 共享 service 方法意外改变既有内部搜索 API

- 文件: [bot_public_service.py:1027](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog/src/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py:1027), [router_auth.py:117](/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog/src/backend/src/agentclaw/community/adapters/http/bot_public/router_auth.py:117)
- 严重程度: 必须修复
- 描述: `/api/v1/bot-public/search` 也调用同一个 `search_public_bots_by_keyword`；本次使它依赖 BCSFuse 且在上游失败时进入其 generic 500/error body，并非只修改 catalog `/openapi/v1/.../search`。没有该现有 API 的兼容性或 failure mapping 测试。
- 建议修复方式: 将 catalog join 编排拆成专用 service method/port，仅由 OpenAPI catalog router 调用；或明确更新 internal API 契约、固定错误映射并补齐兼容性测试。

## 整体结论

**结论: REJECT**

### 必须修复项

1. 使 BCS metadata membership 按可信 tenant/caller 隔离，并验证 physical Bot；不得以全局 worker ID 查询替代。
2. 隔离或显式兼容 `/api/v1/bot-public/search` 的既有行为。
3. 补足 BCS malformed/type、cross-tenant collision、internal route failure 与新增分支测试，使改动覆盖达到 >90%，再提供远端 ACI 证据。
