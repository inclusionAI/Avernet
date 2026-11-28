# Task 2：Dima 导入、创建 Todo 并创建 Product Collet

> 上级计划：[`../tasks.md`](../tasks.md)
> Spec：[`../spec.md`](../spec.md)
> Design：[`../design.md`](../design.md)
> 可独立交付：是，但必须在 Task 1 完成后执行
> 前置：BotTeam Revision 已稳定绑定 `product_profile_id`
> 后续依赖者：Task 3、Task 4 Product Interaction

## 1. 背景和用户价值

Dima 工作项进入 Todo 后，用户需要在 Todo 内持续查看和介入，而不是只复制一条静态记录。要启动 Product Runtime，Todo 必须绑定基于指定 BotTeam Revision 创建的 Collet。

当前代码已具备 Dima 来源绑定和 BotTeam Todo 创建雏形，但 BotTeam + source 因缺少原子事务被明确拒绝；创建后仍可能进入旧 Context dispatch。本任务打通完整主链路：

```text
Dima → Todo 聚合 → Product Collet → 可运行 Product Todo
```

完成后：

- 同一 Dima 工作项不会重复生成 Todo；
- Todo、来源、负责人和阶段不会部分落库；
- Collet 创建失败不会丢失已导入 Todo；
- 重试只补偿 Collet，不会重复导入；
- Product Todo 不会误走旧 Context 运行链路。

## 2. 本任务范围

包含：

- Todo nullable `product_collet_id`；
- Revision/Profile/Collet runtime kind 判定；
- Todo + SourceBinding + participants + stages 原子创建；
- 移除 BotTeam + source 的临时拒绝并替换为真实事务能力；
- Dima 最多 20 条的逐条导入结果；
- `create_product_collet()` 调用；
- `ensure_product_collet()` 幂等补偿；
- Scheduler Product/Legacy 分流；
- 手工 BotTeam Todo 复用 Collet 创建；
- Repository、HTTP 和 service 测试。

不包含：

- Product Runtime 内部如何启动/推进 Rollout；
- Rollout Worker/Progress ingress；
- 进度页面；
- Product Interaction；
- 通知；
- Dima 双向同步；
- 单 Bot Todo 迁移。

## 3. 开始前调查

执行并记录：

```bash
git status --short --branch
git grep -n "source is not supported for a Bot-team Todo"
git grep -n "create_todo_with_stages"
git grep -n "ac_todo_source_binding"
git grep -n "collab_context_id"
git grep -n "TodoScheduler"
```

阅读：

```text
apps/backend/src/agentclaw/community/core/task/todos/services/todo_product_creation_service.py
apps/backend/src/agentclaw/community/core/task/todos/services/todo_template_creation_service.py
apps/backend/src/agentclaw/community/core/task/todos/scheduler.py
apps/backend/src/agentclaw/community/core/task/todos/models.py
apps/backend/src/agentclaw/community/core/repository/protocols/task/todos.py
apps/backend/src/agentclaw/community/core/repository/implementations/task/todos/
Dima import/source service 和对应 HTTP router
Task 1 增加的 Profile binding 和 runtime config
ProductProfileService contracts.py（只读）
```

先确认当前分支是否已存在：

```text
product_collet_id
ensure_product_collet
create_product_collet
atomic source persistence
```

存在同义能力时复用，不重复建表或 service。

## 4. 输入和输出

### 输入

每条 Dima 导入至少需要：

```text
source work item stable ID
source container/project ID
whitelisted source snapshot
target space_id
selected BotTeam revision_id
selected owner_id/owner_name
authenticated importer
```

手工 BotTeam Todo 输入沿用现有创建协议。

### 输出

单条成功：

```json
{
  "source_work_item_id": "DIMA-001",
  "status": "imported",
  "todo_id": "todo-001",
  "product_runtime_status": "ready"
}
```

重复：

```json
{
  "source_work_item_id": "DIMA-001",
  "status": "duplicate",
  "todo_id": "todo-001",
  "product_runtime_status": "ready"
}
```

Todo 已创建但 Collet 暂时失败：

```json
{
  "source_work_item_id": "DIMA-002",
  "status": "failed",
  "todo_id": "todo-002",
  "error_code": "product_runtime_unavailable",
  "retryable": true
}
```

批量 HTTP 总体成功不代表每条成功，Frontend 按单条 status 展示。

## 5. 候选修改文件

```text
apps/backend/src/agentclaw/community/core/task/todos/models.py
apps/backend/src/agentclaw/community/core/task/todos/product_creation_models.py
apps/backend/src/agentclaw/community/core/task/todos/product_creation_protocol.py
apps/backend/src/agentclaw/community/core/task/todos/services/todo_product_creation_service.py
apps/backend/src/agentclaw/community/core/task/todos/services/todo_template_creation_service.py
apps/backend/src/agentclaw/community/core/task/todos/scheduler.py
apps/backend/src/agentclaw/community/core/task/todos/README.md
apps/backend/src/agentclaw/community/core/repository/protocols/task/todos.py
apps/backend/src/agentclaw/community/core/repository/implementations/task/todos/models.py
apps/backend/src/agentclaw/community/core/repository/implementations/task/todos/todo_repository.py
apps/backend/src/agentclaw/community/adapters/http/openapi_v1/task/todos/
apps/backend/src/agentclaw/community/api/task/
apps/backend/src/agentclaw/community/di/modules/task/todo_bindings.py
apps/backend/tests/community/core/task/todos/
apps/backend/tests/community/repository/task/todos/
apps/backend/tests/community/adapters/http/openapi_v1/task/todos/
apps/backend/tests/community/architecture/
```

Frontend 若当前 Dima 导入结果未支持 runtime status，只做最小 schema/展示修改，不开始 Task 3 进度界面。

## 6. TDD 场景清单

### 6.1 聚合原子创建

- Todo、SourceBinding、participant bindings、Stage snapshots 同时成功；
- Stage insert 失败时 Todo 和 SourceBinding 都回滚；
- Source unique 冲突返回 existing Todo ID；
- 同一来源并发导入只存在一个 Todo；
- BotTeam + source 不再触发临时 unsupported 错误；
- 无 source 的手工 BotTeam Todo 仍能创建；
- tenant/env/space 来源 identity 正确隔离。

### 6.2 Collet binding

- 新字段可空，历史 Todo 可读；
- `NULL → id` 成功；
- `id → same id` 幂等；
- `id A → id B` 冲突；
- 不写 `collab_context_id`；
- 不创建/复用 `ac_todo_execution` 作为 Collet binding。

### 6.3 ensure_product_collet

- Revision 有 Profile、Todo 无 Collet时调用 create；
- 已有 Collet时不重复调用；
- 历史 Revision 无 Profile时 not_applicable；
- 明确 runtime pending 不回退旧 Context；
- request ID 重试稳定；
- ActorContext 来自认证 Human；
- 协议成功、本地 CAS失败后相同请求可恢复；
- 不同 Collet ID冲突并 fail closed。

### 6.4 Dima 批量

- 最多 20 条；21 条被请求校验拒绝；
- 每条独立事务；
- 第 2 条失败不回滚第 1/3 条；
- duplicate 返回既有 Todo；
- duplicate 且 Todo 无 Collet时补偿同一个 Todo；
- 不回写 Dima；
- 只保存白名单 source metadata。

### 6.5 Scheduler

- Product pending Todo 调 `ensure_product_collet()`；
- Product Todo 有 Collet后不调用旧 Context dispatch；
- Legacy Todo 行为保持；
- 矛盾 binding 记录错误且不自动选择路径；
- Collet 创建失败后 Todo 保持可补偿，不被删除或误标完成。

## 7. 实施步骤

### Step 1：增加 Product runtime binding

在 Todo Core record、Repository record/ORM、mapper 中增加：

```python
product_collet_id: int | None
```

数据库 column nullable。增加 tenant/env 查询索引。不要增加强制非空或历史回填。

对外 Todo VO 可增加：

```text
runtime_kind
product_runtime_status
```

是否直接暴露 Collet ID取决于现有产品安全合同；无 UI 用途时不必暴露原始 ID。

### Step 2：集中实现 runtime kind resolver

不要把 if/else 分散在 scheduler、detail、interaction。新增纯函数/值对象：

```python
resolve_todo_runtime_kind(todo, revision) -> TodoRuntimeKind
```

覆盖：

```text
product_collet
product_runtime_pending
legacy_context
invalid_binding
```

单元测试完整 truth table。后续 Task 3/4复用。

### Step 3：扩展 Repository 原子边界

当前 `create_todo_with_stages()` 如果无法包含 source，选择其一：

1. 向方法增加显式 `source_binding: ... | None`；或
2. 新增 `create_product_todo_aggregate()`。

选择标准：

- 若现有调用者均可安全兼容，可扩展；
- 若语义会变得模糊，新建聚合方法；
- 不能在 service 层嵌套多个 Repository transaction 假装原子；
- 不能先 pre-check 后分两次 insert，数据库 unique constraint 必须闭合并发竞态。

事务内顺序建议：

```text
insert Todo
insert SourceBinding（可选）
insert/保存 participant bindings
insert Stage snapshots
return complete Todo record
```

任何一步失败整体 rollback。

### Step 4：修复 BotTeam + source 创建服务

删除临时错误：

```text
source is not supported for a Bot-team Todo until atomic source persistence is available
```

但只能在 Step 3 原子能力和测试完成后删除。

创建时固定：

```text
template_id = bot_team_id
template_version_id = revision_id
template_name_snapshot
participant_bindings_json.todo_owner
stage snapshots
source binding
```

后续编辑 BotTeam 不影响 Todo。

### Step 5：实现 Collet content builder

纯函数：

```python
build_product_collet_content(todo, source, stages) -> str
```

内容应稳定、有界、可测试，至少包含：

- Todo 标题；
- Todo 目标/描述；
- 选定 Revision 的产品说明；
- Dima 来源摘要（如有）；
- 验收条件摘要（如有）。

不得包含：

- execution token/credential；
- 完整未过滤 Dima 响应；
- 前端临时字段；
- 用户无权查看的附件正文；
- 依赖当前时间导致重试内容变化的字段。

### Step 6：实现 Collet request ID builder

```python
build_collet_request_id(tenant, env, todo_id) -> str
```

建议：

```text
todo:collet:{tenant}:{env}:{todo_id}:v1
```

同一 Todo 所有重试复用，不能按重试次数生成新 ID。

### Step 7：实现 ensure_product_collet

建议新建专职 service/protocol，或放入已有 Todo Product Creation service 但保持方法职责清晰：

```python
async def ensure_product_collet(
    todo_id: str,
    *,
    authenticated_actor: ...,
) -> EnsureProductColletResult
```

算法：

1. 在 tenant/env/space 权限下读取 Todo；
2. 读取 pinned Revision；
3. resolve runtime kind；
4. 已有 Collet：返回 ready；
5. Legacy：返回 not_applicable；
6. invalid：返回稳定冲突错误；
7. 取得 Revision `product_profile_id`；
8. 构造稳定 content/request ID/ActorContext；
9. 调 `create_product_collet()`；
10. 校验返回稳定 ID；
11. CAS 绑定；
12. 返回 ready。

ActorContext：

- 用户触发导入/手工创建时使用当前认证 Human；
- Scheduler 补偿时必须使用仓库已有受支持的后台 actor 模式；
- 如果没有合法后台 actor，先只实现显式补偿并停止确认 Scheduler 方案，禁止伪造 Human。

### Step 8：Dima 批量 orchestration

伪代码：

```python
for item in request.items[:20]:
    try:
        existing = find_by_source_identity(...)
        if existing:
            runtime = ensure_collet_if_applicable(existing)
            result = duplicate(existing.id, runtime)
            continue

        todo = create_product_todo_aggregate(...)
        runtime = ensure_product_collet(todo.id)
        result = imported_or_retryable_failure(todo.id, runtime)
    except DomainError as exc:
        result = failed(item.id, stable_error(exc))
```

关键点：

- pre-check 只用于友好响应，数据库 unique constraint 才是最终防线；
- unique conflict 必须查询并返回既有 Todo；
- 单条错误只影响单条；
- 不用一个跨 20 条的大事务；
- 记录每条的 source ID和 Todo ID，日志不记录完整 Dima 内容。

### Step 9：手工 BotTeam Todo 复用

手工创建 BotTeam Todo 后调用同一 `ensure_product_collet()`，不复制另一套 create logic。

如果同步等待协议会影响创建 API SLA，可返回：

```text
product_runtime_status=provisioning
```

并由明确补偿机制继续；但本地 Todo 一旦有 Profile 就不得回退旧 Context dispatch。

### Step 10：Scheduler 分流

在旧 dispatch 前调用 runtime kind resolver：

```text
legacy_context → existing behavior
product_runtime_pending → ensure_product_collet
product_collet → skip legacy dispatch
invalid_binding → log/metric + no dispatch
```

不要在 Product 分支实现 Rollout Worker。

### Step 11：HTTP 和 Frontend 最小适配

HTTP：

- 批量最多 20 条 schema 校验；
- 返回 per-item result；
- 稳定 error_code/retryable；
- duplicate 带既有 Todo ID；
- 不返回内部异常文本。

Frontend（仅必要范围）：

- 展示 imported/duplicate/failed；
- failed + todo_id 时说明 Todo 已创建、运行初始化待重试；
- 不因单条失败丢失其他结果；
- 不开始实现完整 Progress。

### Step 12：文档和监控

更新 Todo README：

- Product/Legacy runtime split；
- Source atomic boundary；
- Collet binding；
- Scheduler 不负责 Product Runtime worker。

增加结构化指标/日志：

```text
todo_product_collet_ensure_total{result}
todo_dima_import_total{status}
```

## 8. 幂等、并发和补偿

### 来源幂等

Identity 继续使用现有合同：

```text
avernet_tenant + env + space_id + source_type + source_item_id
```

source container 不参与 identity，移动 Dima 项目不能产生第二个 Todo。

### Collet 幂等

```text
request_id = Todo identity + operation version
```

协议成功、本地失败：相同 ID重试并 CAS。

### 并发导入

- 两个请求同时导入同一来源；
- 一个本地事务成功；
- 另一个命中 unique conflict；
- conflict 方查询既有 Todo；
- 两者对同 Todo 使用同 Collet request ID；
- 最终只绑定一个 Collet ID。

### 补偿状态

可以使用 VO/查询动态表达：

```text
ready
provisioning
retryable_failure
not_applicable
binding_conflict
```

不必为了运行初始化另造 Todo 第七状态。Todo 六状态保持不变。

## 9. 测试执行

Focused：

```bash
cd apps/backend
DEPLOY_PROFILE=test uv run --no-sync pytest -q \
  tests/community/core/task/todos/test_todo_product_creation_service.py \
  tests/community/core/task/todos/test_todo_template_creation_service.py \
  tests/community/repository/task/todos \
  tests/community/adapters/http/openapi_v1/task/todos \
  tests/community/architecture
```

如 scheduler 有独立测试，必须包含。然后：

```bash
cd apps/backend
bash scripts/ci_test.sh
```

涉及 Frontend 时：

```bash
cd apps/frontend-nextgen
npm run check:open-core
npm run typecheck
npm test -- --runInBand test/services/backendApi/todos test/pages/Lab/Todos.test.tsx
```

最后：

```bash
git diff --check
git diff --check --cached
```

## 10. 验收 Checklist

- [ ] 协议层目录无修改；
- [ ] Task 1 Profile binding 被复用；
- [ ] `product_collet_id` nullable 且无历史回填；
- [ ] Collet ID未写旧 Context/Execution 字段；
- [ ] 本地 Todo 聚合原子创建；
- [ ] BotTeam + source 临时拒绝已由真实能力替代；
- [ ] 同一来源并发只创建一个 Todo；
- [ ] 每批最多 20，逐条独立结果；
- [ ] Todo 成功、Collet 失败时 Todo 保留；
- [ ] duplicate 可对既有 Todo 补偿 Collet；
- [ ] request ID稳定；
- [ ] CAS 三种结果测试；
- [ ] Product Todo 不走旧 Context dispatch；
- [ ] Legacy Todo 回归通过；
- [ ] Scheduler 不实现 Product Runtime worker；
- [ ] tenant/env/space 隔离测试；
- [ ] focused/完整 CI状态如实报告。

## 11. 与其他任务的边界

本任务输出稳定能力：

```text
Todo.product_collet_id
resolve_todo_runtime_kind()
ensure_product_collet()
atomic Product Todo aggregate creation
```

Task 3只能基于这些能力查询进度；Task 4只能基于这些能力互动。不要在本任务生成虚假的进度或会话记录。

## 12. Agent 停止条件

- Task 1 尚未提供稳定 Profile ID；
- Collet 返回没有稳定 ID；
- 现有来源唯一约束与文档 identity 不一致；
- Repository 无法把 Todo/Source/Stages 放进同一事务；
- Scheduler 没有合法后台 ActorContext，且任务要求后台创建 Collet；
- 当前分支已有另一个 Collet binding 字段或同义 runtime 表；
- Product Todo 必须修改协议层才能启动；
- 新分流会让已有 Context Todo 停止运行；
- 数据库迁移需要破坏性修改或历史回填。
