# Task 3：Todo 详情和 Product Progress 投影

> 上级计划：[`../tasks.md`](../tasks.md)
> Spec：[`../spec.md`](../spec.md)
> Design：[`../design.md`](../design.md)
> 可独立交付：是，但必须在 Task 2 完成后执行
> 前置：Todo 已有稳定 `product_collet_id` 和 runtime kind resolver
> 可并行：可与 Task 4 的通知基础设施部分并行

## 1. 背景和用户价值

Product Runtime 提供 Collet、Rollout、StageRollout 和 Session Messages，但这些不是用户直接理解的产品语言。用户关心的是：

- 当前任务进行到哪个固定阶段；
- 每个阶段是否未开始、进行中、等待处理、失败或完成；
- Bot 做了哪些子步骤；
- 当前卡点和下一步是什么；
- 产生了哪些可预览/下载产物；
- 是否重试过、当前是第几次尝试；
- 运行服务不可用时，哪些信息仍可信。

本任务以 Todo Stage 快照为骨架，把 Product Runtime 事实投影为稳定 Backend VO，再由 Frontend 展示。Frontend 不直接解析协议 DTO 或 provider messages。

## 2. 本任务范围

包含：

- Product Progress Core model/VO；
- Progress query service/protocol；
- `query_collet_rollout()`；
- current rollout/revision/stopped 解析；
- Todo Stage 与 StageRollout 映射；
- `query_stage_rollout_details()` 按需查询；
- Session Messages 防御解析和子步骤 Summary；
- Runtime unavailable/partial data；
- 当前尝试和历史尝试展示；
- 现有 Artifact preview/download 对接；
- Backend HTTP endpoint；
- Frontend mapper/hook/page；
- Legacy/Product 分流和回归测试。

不包含：

- Product Runtime 内部推进；
- 实现 query 方法；
- 修改协议 DTO；
- Human message 发送；
- 站内通知；
- Dima 导入；
- 创建 Profile/Collet。

## 3. 开始前调查

执行并记录：

```bash
git status --short --branch
git grep -n "query_collet_rollout"
git grep -n "query_stage_rollout_details"
git grep -n "TodoDetail"
git grep -n "artifact" apps/frontend-nextgen/src/pages/Lab/Todos apps/backend/src/agentclaw/community/core/task/todos
```

阅读：

```text
Task 2 runtime kind resolver 和 Collet binding
ProductProfileService contracts.py（只读）
公开 ColletRollout/StageRollout DTO 定义（只读）
apps/backend/src/agentclaw/community/core/task/todos/services/todo_service.py
Todo detail/events/interactions/artifacts 现有服务和 HTTP routes
apps/frontend-nextgen/src/domain/todos/
apps/frontend-nextgen/src/services/backendApi/todos/
apps/frontend-nextgen/src/hooks/useTodoDetail.ts
apps/frontend-nextgen/src/pages/Lab/Todos/TodoDetailPage.tsx
相关 Frontend tests
```

先确认是否已有等价 Progress VO/service。若已有，扩展并迁移，不并存两个产品协议。

## 4. 输入和输出

### 输入

```text
authenticated user
tenant/env/space
todo_id
可选 expanded_stage_code
可选 attempt/rollout selector（必须受产品规则限制）
```

### 输出

稳定 Product Progress VO：

```json
{
  "todo_id": "todo-001",
  "status": "in_progress",
  "runtime_kind": "product_collet",
  "runtime_unavailable": false,
  "runtime_error_code": null,
  "current_stage_code": "code_development",
  "rollout_revision": 3,
  "stopped": false,
  "stages": [
    {
      "code": "requirement_analysis",
      "name": "需求分析",
      "status": "completed",
      "summary": "已形成需求理解与验收项",
      "current_step": null,
      "steps": [],
      "outputs": [],
      "blocker": null,
      "attempt_count": 1,
      "updated_at": "..."
    },
    {
      "code": "code_development",
      "name": "代码开发",
      "status": "in_progress",
      "summary": "正在完成实现和验证",
      "current_step": "执行 focused tests",
      "steps": [],
      "outputs": [],
      "blocker": null,
      "attempt_count": 2,
      "updated_at": "..."
    }
  ]
}
```

Runtime 失败时：

```json
{
  "todo_id": "todo-001",
  "runtime_kind": "product_collet",
  "runtime_unavailable": true,
  "runtime_error_code": "product_progress_unavailable",
  "stages": [
    {
      "code": "requirement_analysis",
      "name": "需求分析",
      "status": "unknown",
      "steps": [],
      "outputs": []
    }
  ]
}
```

Todo 静态 Stage 仍返回，不能整体 500 或全部显示未开始。

## 5. 候选修改文件

Backend：

```text
apps/backend/src/agentclaw/community/core/task/todos/models.py
apps/backend/src/agentclaw/community/core/task/todos/*progress*
apps/backend/src/agentclaw/community/core/task/todos/services/
apps/backend/src/agentclaw/community/adapters/http/openapi_v1/task/todos/router.py
apps/backend/src/agentclaw/community/adapters/http/openapi_v1/task/todos/schemas.py
apps/backend/src/agentclaw/community/api/task/
apps/backend/src/agentclaw/community/di/modules/task/todo_bindings.py
apps/backend/tests/community/core/task/todos/
apps/backend/tests/community/adapters/http/openapi_v1/task/todos/
apps/backend/tests/community/architecture/
```

Frontend：

```text
apps/frontend-nextgen/src/domain/todos/models.ts
apps/frontend-nextgen/src/domain/todos/status.ts
apps/frontend-nextgen/src/services/backendApi/todos/
apps/frontend-nextgen/src/hooks/useTodoDetail.ts
apps/frontend-nextgen/src/pages/Lab/Todos/TodoDetailPage.tsx
apps/frontend-nextgen/src/pages/Lab/Todos/components/
apps/frontend-nextgen/test/domain/todos/
apps/frontend-nextgen/test/services/backendApi/todos/
apps/frontend-nextgen/test/hooks/useTodoDetail.test.tsx
apps/frontend-nextgen/test/pages/Lab/TodoDetail.test.tsx
```

## 6. TDD 场景清单

### 6.1 Runtime query

- 使用 Todo `product_collet_id` 调 query；
- ActorContext 来自认证用户；
- 使用 `current_rollout_id` 而非列表顺序；
- 读取 RolloutList `revision/stopped`；
- 缺少元数据时返回明确 unavailable；
- 空 rollout 有明确 empty/provisioning 表达；
- Product service timeout 返回部分静态进度；
- Legacy Todo 不调用 Product service。

### 6.2 Stage mapping

- Todo Stage 名称、顺序来自快照；
- Runtime 未知 stage 不覆盖产品阶段；
- 产品阶段没有 Runtime 数据时保持 unknown/not_started（按可证事实）；
- 当前 Stage 正确；
- 多次尝试计算 `attempt_count`；
- 默认展示最新尝试；
- 历史尝试可按需展开；
- 未知协议 status fail closed。

### 6.3 Details/messages

- limit 默认 50且范围 1–200；
- 默认只请求当前 Stage；
- 展开指定 Stage 才请求该 Stage details；
- `order=provider-defined` 不盲排序；
- `complete=None/False` 不宣称完整；
- malformed message 被安全忽略并留诊断计数；
- 错误/产物/文本消息白名单解析；
- Summary 失败返回基础事实；
- 相同 fingerprint 命中缓存；
- 新消息生成新 fingerprint。

### 6.4 HTTP/Frontend

- Backend VO schema 稳定；
- unknown status 不能 Frontend 默认成 pending；
- runtime unavailable 有明确 Banner/空态；
- 阶段骨架仍显示；
- current step、blocker、attempt count 展示；
- 历史尝试按需加载；
- artifact preview/download 成功和错误态；
- Legacy detail 回归。

## 7. 实施步骤

### Step 1：定义 Product Progress Core model

Backend Core model建议：

```text
TodoProgress
TodoStageProgress
TodoProgressStep
TodoProgressOutput
TodoProgressBlocker
TodoStageAttemptSummary
```

每个字段必须是产品语义，不直接复用协议 DTO。至少定义：

```text
stage code/name/order
product stage status
summary/current step
steps
outputs
blocker
attempt count
updated time
message completeness
runtime unavailable/error code
```

避免在 VO 暴露 `Any`、原始 provider payload 或内部 class repr。

### Step 2：集中状态 mapper

读取真实协议 enum，建立 exhaustive mapping。建议 mapper 返回：

```python
@dataclass(frozen=True)
class StageStatusProjection:
    status: TodoStageProgressStatus
    pending_action_kind: str | None
    terminal: bool
```

要求：

- 对每个已知 enum 写测试；
- 未知值抛稳定映射错误，service 转为 unavailable/unknown；
- 不在 Frontend 再映射协议状态；
- Todo 六状态与 Stage 进度状态分开，避免为 Stage 强塞 Todo 全局状态。

### Step 3：实现 Rollout result adapter

建立一个 Todo 内部 adapter，将 `list[ColletRollout]` 防御转换为内部 snapshot：

```text
revision
current_rollout_id
stopped
rollouts
```

算法：

1. 检查结果是否提供元数据；
2. 校验 revision/current ID 类型；
3. 按 ID查找当前 Rollout；
4. 找不到时返回 contract mismatch；
5. 不按 index/时间猜当前项；
6. 只保留产品映射需要字段。

### Step 4：实现 Stage 对齐

以 Todo Stage snapshots 为主表：

```text
for todo_stage in ordered_todo_stages:
    matching_rollouts = runtime stage rollouts by stable stage code
    current/latest attempt = 按明确 attempt/revision 信息选择
    projection = map(todo_stage, attempts)
```

如果协议 Stage 没有与 Todo code 可对应的稳定字段，停止并反馈合同缺口，不用中文 name 模糊匹配。

Runtime 返回额外 stage：

- 记录诊断；
- 不默认插入用户可见阶段；
- 如果是协议系统阶段，只有明确产品映射后才展示为子步骤。

### Step 5：按需查询 Stage details

默认：

```text
current Stage only
```

展开参数只能选择 Todo 已有 Stage，并经过权限校验。

调用：

```python
query_stage_rollout_details(
    stage_rollout_id,
    context=actor_context,
    limit=validated_limit,
)
```

limit：

- 默认 50；
- Backend clamp/validate 1–200；
- Frontend 不可要求无限量；
- 超过范围返回 400 或使用明确 clamp 规则并测试。

### Step 6：Message normalization

先归一化再摘要。Normalized message建议只包含：

```text
message_id（如可靠）
kind
safe_text
timestamp（如可靠）
sequence（如可靠）
artifact reference（如合法）
error code/message（脱敏）
actor type/name（白名单）
```

处理规则：

- 不认识类型：计数并忽略正文；
- 超长文本：Backend 安全截断，但 artifact 正文走 preview；
- HTML/Markdown：按 Frontend 安全渲染合同处理；
- 敏感字段：不传；
- 时间缺失：不制造时间；
- 顺序不可靠：保留 provider order并标记不完整。

### Step 7：Summary 和子步骤

先实现确定性规则摘要，确保无需额外服务也可用：

- 明确开始消息 → 进行中步骤；
- 明确完成消息 → completed step；
- 明确 error → blocker；
- artifact → output；
- 普通文本 → 有界 detail。

如果项目已有安全的 Summary service，可在规则事实之上生成自然语言 summary，但必须：

- 输入为 normalized messages；
- 输出失败可 fallback；
- 缓存按 fingerprint/version；
- 不把模型总结当作状态真相；
- 不阻塞 Progress 基础响应。

### Step 8：Artifact 对接

复用现有 Todo Artifact service/endpoint：

- Progress output只返回 artifact ID/kind/name/summary；
- Preview 通过已有鉴权 endpoint；
- Download 通过已有安全响应；
- 验证 artifact 与 Todo/Stage 归属；
- 外部链接按既有 allowlist/safe link策略；
- 不在 Progress payload 内嵌大文件内容。

### Step 9：Progress service

建议协议：

```python
class TodoProductProgressServiceProtocol(Protocol):
    async def get_progress(
        self,
        todo_id: str,
        *,
        actor: AuthenticatedActor,
        expanded_stage_code: str | None = None,
        limit: int = 50,
    ) -> TodoProgress: ...
```

流程：

1. 鉴权读取 Todo；
2. 读取 ordered stages；
3. resolve runtime kind；
4. Legacy 调现有 projection或返回既有 detail；
5. Product pending 返回 provisioning；
6. Product Collet query rollout；
7. map stages；
8. details on demand；
9. summary/cache；
10. 返回 VO。

Runtime 异常只影响 runtime 部分，Todo 基本详情 endpoint 不应整体失败。

### Step 10：HTTP endpoint

建议：

```text
GET /openapi/v1/collaboration/tasks/todos/{todo_id}/progress
```

Query：

```text
expanded_stage_code optional
message_limit optional, 1..200
```

错误：

```text
404 todo_not_found
403 todo_not_visible
409 invalid_runtime_binding
200 + runtime_unavailable for transient Product service failures
```

是否 transient failure 返回 200 部分数据应与现有 HTTP 规范保持一致；关键是不伪装未开始、不泄漏 500 内部错误。

### Step 11：Frontend domain 和 mapper

定义严格类型：

```text
TodoProgress
TodoStageProgress
TodoProgressStep
TodoProgressOutput
```

Mapper：

- 校验 required fields；
- unknown status 抛可见协议错误；
- 不读取 protocol DTO字段；
- nullable/partial 明确处理；
- 不把 missing 转为 completed/not_started。

### Step 12：Frontend hook

扩展或拆分 `useTodoDetail`：

- 基础 detail 与 progress 请求可独立失败；
- 去重同 Todo 请求；
- 切换 Todo取消旧请求；
- expanded stage 按需加载；
- runtime unavailable 支持 retry；
- 不无限 polling；若已有刷新策略则复用。

### Step 13：Frontend 页面

页面优先级：

1. Todo 标题、来源、负责人、执行方；
2. 固定阶段轴；
3. 当前阶段 summary/current step；
4. blocker/待处理动作；
5. 子步骤；
6. outputs preview/download；
7. attempt count/history；
8. runtime unavailable 降级提示。

要求：

- 当前阶段突出；
- 未加载 details 与“没有步骤”区分；
- summary 是摘要，不冒充原始事实；
- 历史尝试默认折叠；
- Legacy Todo 保持现有交互。

### Step 14：性能和缓存

- 首屏只查当前 Stage details；
- 列表页不调用 Product detail；
- cache key稳定；
- service timeout有界；
- Frontend避免重复请求；
- 增加 query latency/error metrics；
- 不在一个请求串行查询所有阶段的 messages。

### Step 15：文档

更新 Todo README：

- Stage snapshot 为产品骨架；
- Runtime 只提供事实；
- Progress unavailable语义；
- Frontend 不解释 DTO；
- details按需查询和 limit。

## 8. 失败和降级

| 失败 | 用户展示 | 后端行为 |
| --- | --- | --- |
| Collet provisioning | 运行初始化中 | 不查 rollout |
| query rollout timeout | 运行进度暂不可用 | 返回静态 stages + error code |
| current rollout缺失 | 运行数据异常 | fail closed + metric |
| stage mapping缺失 | 单阶段状态未知 | 保留其他阶段 |
| details失败 | 子步骤暂不可用 | 基础状态仍返回 |
| malformed messages | 部分消息未识别 | 忽略危险字段 + diagnostic count |
| Summary失败 | 显示基础事实 | fallback，不阻塞 |
| Artifact preview失败 | 产物暂不可预览 | 允许按既有策略下载/重试 |

## 9. 测试执行

Backend：

```bash
cd apps/backend
DEPLOY_PROFILE=test uv run --no-sync pytest -q \
  tests/community/core/task/todos \
  tests/community/adapters/http/openapi_v1/task/todos \
  tests/community/architecture
```

Frontend：

```bash
cd apps/frontend-nextgen
npm run check:open-core
npm run typecheck
npm test -- --runInBand \
  test/domain/todos \
  test/services/backendApi/todos \
  test/hooks/useTodoDetail.test.tsx \
  test/pages/Lab/TodoDetail.test.tsx
```

完整：

```bash
cd apps/backend && bash scripts/ci_test.sh
cd apps/frontend-nextgen && npm run ci
```

卫生：

```bash
git diff --check
git diff --check --cached
```

## 10. 验收 Checklist

- [ ] 协议层目录无修改；
- [ ] Progress VO 不暴露协议 DTO/Any；
- [ ] Todo Stage 快照决定名称和顺序；
- [ ] 使用 `current_rollout_id`，不使用列表顺序；
- [ ] revision/stopped 正确返回；
- [ ] unknown status fail closed；
- [ ] details limit 在 1–200；
- [ ] 默认只查询当前 Stage；
- [ ] provider-defined order被防御处理；
- [ ] incomplete messages不宣称完整；
- [ ] Summary失败有 fallback；
- [ ] Runtime unavailable仍显示静态 stages；
- [ ] Artifact preview/download复用现有能力；
- [ ] Frontend不解释 ProductProfileService DTO；
- [ ] 历史尝试默认折叠、按需查询；
- [ ] Legacy detail回归通过；
- [ ] Backend/Frontend focused和完整 CI状态已报告。

## 11. 与其他任务的边界

本任务只读 Runtime，不发送 Human 输入、不创建通知。Task 4 可以复用：

```text
current rollout/revision adapter
runtime kind resolver
progress refresh hook
```

但 Task 3 不应提前创建 interaction 表或 notification publisher。

## 12. Agent 停止条件

- 协议 Rollout 没有稳定 stage identity，无法与 Todo stage code 对齐；
- 结果无法取得 current rollout/revision；
- 必须读取协议内部 Repository 才能展示进度；
- Session Messages 含敏感内容但仓库无可用过滤规则；
- 现有 Artifact API 无法验证 Todo归属；
- 当前分支已有不同 Progress VO 且无法安全兼容；
- 需要前端直接解释 provider payload才能实现；
- 实现要求一次读取所有 stages/messages，存在不可接受 N+1/延迟风险。
