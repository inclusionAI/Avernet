# Task 4：Human 站内通知与 Product Interaction

> 上级计划：[`../tasks.md`](../tasks.md)
> Spec：[`../spec.md`](../spec.md)
> Design：[`../design.md`](../design.md)
> 可独立交付：通知基础设施可独立；Product Interaction 依赖 Task 2
> 前置：Product Todo 已有 `product_collet_id`；推荐复用 Task 3 rollout adapter

## 1. 背景和用户价值

Todo 只有在需要 Human 时能被及时触达、进入后能完成处理、处理结果能继续驱动运行，才形成闭环。

当前产品要求：

- Bot 请求信息、固定验收点、中断/失败、Verify 和完成等事件可触发站内通知；
- 通知点击进入 Todo 并定位最后一个互动框；
- 所有空间成员可查看和互动，但通知只发给当前负责人或明确 Human recipient；
- 评论/发送后形成一个对话框；
- 只有最后一个对话框可继续输入；
- 消息发送后，在平台响应前不能再次发送；
- 等待期间可以提出停止；
- Human 输入和平台同步响应必须可追溯；
- Product Todo 不能继续依赖旧 Context 的 `user_interaction.posted`。

本任务分为两个紧密相关但可分步交付的部分：

```text
4A Todo 站内通知
4B Product Interaction
```

## 2. 本任务范围

### 2.1 4A 通知

- TodoNotificationPublisherProtocol；
- 复用现有站内 Inbox/WorkOrder Repository 的生产 Adapter；
- 通知事件、recipient、delivery key；
- 幂等、未读数、已读和 deep link；
- 失败补偿；
- Frontend 通知跳转/定位。

### 2.2 4B 互动

- Product Interaction 本地持久化；
- 互动列表和发送接口；
- `query_collet_rollout()` 获取 current rollout/revision；
- `interactive()`；
- synchronous result 保存；
- single-flight；
- `delivery_unknown` 补偿；
- 最后一个互动框；
- 发送锁定；
- 等待期间停止请求；
- Legacy/Product 分流。

不包含：

- 钉钉通知；
- 修改协议或新增 stop API；
- Product Runtime 内部重试/推进；
- 通知所有空间成员；
- 任意历史对话迁移；
- Dima 回写；
- Frontend 直接访问协议服务。

## 3. 开始前调查

执行并记录：

```bash
git status --short --branch
git grep -n "ForumNotificationPublisher"
git grep -n "merge_notice"
git grep -n "user_interaction.posted"
git grep -n "pending_action"
git grep -n "interactive(" apps/backend/src
```

阅读：

```text
apps/backend/src/agentclaw/community/core/task/forum/notifications/
apps/backend/src/agentclaw/community/core/repository/implementations/work_orders/notification.py
apps/backend/src/agentclaw/community/core/repository/protocols/work_orders.py
apps/backend/src/agentclaw/community/core/task/todos/services/todo_service.py
Todo 现有 interactions/events HTTP routes
Task 2 runtime kind resolver/product_collet binding
Task 3 rollout adapter（若已完成）
ProductProfileService contracts.py（只读）
AdjustTypeEnum 定义（只读）
Frontend Todo detail/interaction components
Frontend notification/inbox navigation components
```

先搜索是否已有：

```text
ac_todo_product_interaction
TodoNotificationPublisherProtocol
product interaction service
interaction_id/request_id/delivery_unknown
```

如有等价实现，复用并补齐，不创建同义表。

## 4. 输入和输出

### 4.1 通知输入

```text
Todo ID/space
notification type
current owner or explicit recipient
stage/rollout/action identity
title/summary
deep-link focus target
```

输出：

```text
published / already_published / skipped / failed
```

### 4.2 互动输入

```json
{
  "message": "请基于最新验收意见重新执行",
  "reply_to_interaction_id": null
}
```

actor/tenant/env 不能从 body 接受，必须来自认证上下文。

### 4.3 互动输出

```json
{
  "interaction_id": "int-001",
  "delivery_state": "succeeded",
  "request_message": "请基于最新验收意见重新执行",
  "response": {
    "type": "RE_DO",
    "message": "已重新执行相关阶段"
  },
  "cancel_requested": false,
  "created_at": "...",
  "updated_at": "..."
}
```

列表同时返回：

```json
{
  "items": [],
  "can_send": false,
  "blocking_interaction_id": "int-001"
}
```

## 5. 候选修改文件

Backend：

```text
apps/backend/src/agentclaw/community/core/task/todos/*notification*
apps/backend/src/agentclaw/community/core/task/todos/*interaction*
apps/backend/src/agentclaw/community/core/task/todos/services/todo_service.py
apps/backend/src/agentclaw/community/core/task/todos/models.py
apps/backend/src/agentclaw/community/core/task/todos/README.md
apps/backend/src/agentclaw/community/core/repository/protocols/task/todos.py
apps/backend/src/agentclaw/community/core/repository/implementations/task/todos/
apps/backend/src/agentclaw/community/core/repository/protocols/work_orders.py（仅在通用能力确有缺口时）
apps/backend/src/agentclaw/community/core/repository/implementations/work_orders/notification.py
apps/backend/src/agentclaw/community/adapters/http/openapi_v1/task/todos/
apps/backend/src/agentclaw/community/api/task/
apps/backend/src/agentclaw/community/di/modules/task/todo_bindings.py
apps/backend/tests/community/core/task/todos/
apps/backend/tests/community/repository/task/todos/
apps/backend/tests/community/adapters/http/openapi_v1/task/todos/
apps/backend/tests/community/architecture/
```

Frontend：

```text
apps/frontend-nextgen/src/domain/todos/
apps/frontend-nextgen/src/services/backendApi/todos/
apps/frontend-nextgen/src/hooks/useTodoDetail.ts
apps/frontend-nextgen/src/pages/Lab/Todos/TodoDetailPage.tsx
apps/frontend-nextgen/src/pages/Lab/Todos/components/
现有 notification/inbox navigation 目录
apps/frontend-nextgen/test/domain/todos/
apps/frontend-nextgen/test/services/backendApi/todos/
apps/frontend-nextgen/test/hooks/
apps/frontend-nextgen/test/pages/Lab/
```

## 6. TDD 场景清单

### 6.1 通知 Publisher

- Human action required 创建一条通知；
- 同 delivery key重试不重复；
- 已读通知重试后仍已读；
- 新 action version创建新通知；
- recipient 是当前 owner/明确 Human；
- 所有空间成员可看 Todo但不会全部收到通知；
- recipient 缺失时 skipped并记录原因；
- tenant/env/space 不串数据；
- deep link包含 Todo和 focus；
- Inbox失败不回滚 Runtime事实；
- 补偿重试可成功。

### 6.2 Interaction Repository

- 创建 sending interaction；
- tenant/env/interaction ID唯一；
- Todo 行锁下只允许一个 sending/unknown；
- 并发第二条返回 interaction_in_flight；
- 保存 current rollout/revision；
- 保存 Human request和平台 response；
- 状态转换合法；
- 非法逆向转换拒绝；
- 历史按创建时间稳定读取；
- 只有最后一个 interaction可回复；
- completed/cancelled Todo不能新发。

### 6.3 Product service 调用

- 使用 Todo Collet query current rollout；
- 使用 `current_rollout_id`；
- expected_revision来自 RolloutList；
- request_id等于 interaction ID；
- ActorContext来自认证 Human；
- `DO_NOTHING/OPTIMIZE/RE_DO/SHUTDOWN`逐项映射；
- unknown adjust type fail closed；
- stale revision保存明确失败；
- timeout进入 delivery_unknown；
- 相同 interaction补偿复用 request ID；
- SHUTDOWN 才映射 cancelled。

### 6.4 Cancel/stop

- sending时可标记 cancel_requested；
- stop 使用新的确定性 request ID；
- 未确认 stop不显示 cancelled；
- 明确 SHUTDOWN后 cancelled；
- 重复 cancel幂等；
- interaction 已完成后的 cancel按产品规则拒绝或转为新 stop意图，必须有测试；
- 不调用不存在的协议 stop API。

### 6.5 Frontend

- 只有最后一个互动框有输入；
- sending/unknown时禁止发送；
- 请求发出后立即显示 pending bubble；
- 同步 response显示在同一对话框；
- 失败/unknown有清楚状态；
- 等待期间显示停止入口；
- stop未确认显示“停止处理中/未确认”；
- 通知 deep link定位最后互动框；
- Legacy Todo继续现有 interaction；
- Product Todo不写旧 Context event。

## 7. 4A 实施步骤：站内通知

### Step 1：定义 Todo notification domain event

建议：

```python
@dataclass(frozen=True)
class TodoNotificationEvent:
    delivery_key: str
    notification_type: TodoNotificationType
    todo_id: str
    space_id: str
    recipient_id: str | None
    title: str
    summary: str
    stage_code: str | None
    stage_rollout_id: int | None
    rollout_revision: int | None
    action_version: str
    occurred_at: datetime
```

类型至少：

```text
human_action_required
bot_information_required
execution_interrupted
verification_required
todo_completed
```

不要把 BBS Forum event直接作为 Todo event。

### Step 2：定义 Publisher Protocol

```python
class TodoNotificationPublisherProtocol(Protocol):
    def publish(self, event: TodoNotificationEvent) -> TodoNotificationPublishResult: ...
```

结果区分：

```text
published
already_published
skipped
failed
```

Core 不 import具体 WorkOrder/Inbox Repository。

### Step 3：实现生产 Adapter

参考 BBS 社区通知做法，但只复用基础设施：

```text
Todo domain event
→ Todo adapter
→ WorkOrderRepositoryProtocol.merge_notice() 或等价现有能力
```

Adapter负责：

- 映射 Todo title/summary/action URL；
- 以 delivery key幂等 upsert；
- 保持 read state；
- 更新未读计数按现有机制；
- 不把 Todo Core耦合到 Forum protocol。

如果 `merge_notice()` 不能保证 delivery key幂等，先扩展通用 Repository 的明确幂等能力并加通用测试；不要在 Todo service中先查再写造成竞态。

### Step 4：确定 recipient

顺序：

1. 事件明确 recipient；
2. Todo 当前 `todo_owner`；
3. 无 recipient → skipped，记录诊断。

不要 fallback到所有空间成员或创建人，除非产品 Owner另行确认。

空间所有成员仍可在主动进入 Todo后互动，这与通知 recipient 不冲突。

### Step 5：delivery key

集中 builder：

```text
{todo_id}:{notification_type}:{rollout_revision}:{stage_rollout_id-or-none}:{action_version}
```

输入为空时使用稳定 sentinel，不用当前时间。

测试：相同事实 key相同；新 rollout/action key不同。

### Step 6：接入触发点

在 Todo 产品事实已经成功持久化后发布通知：

```text
pending Human action persisted → publish
interaction response asks Human → publish
execution interrupted fact persisted → publish
verification required fact persisted → publish
Todo completed fact persisted → publish
```

通知不能先于事实写入，否则点击后可能看不到对应内容。

发布失败：

- 记录 failed delivery；
- 不回滚事实；
- 由补偿扫描按事实 + delivery key重试。

### Step 7：通知 deep link和已读

URL仅保存产品路由和稳定 ID，不保存私有域名：

```text
/todos/{todo_id}?focus=interaction&interaction_id={latest-or-action}
```

Frontend：

- 进入后重新鉴权；
- 定位 Todo和最后互动框；
- 只有成功打开/用户动作后按现有规则标记已读；
- 已读状态不因 publisher重试恢复。

### Step 8：补偿

优先复用现有 job/scheduler机制。扫描：

```text
存在待通知 Todo事实
AND delivery key无 published记录
```

重发相同 key。补偿必须有上限、退避和指标，不能每次扫描无限重复增加事件。

## 8. 4B 实施步骤：Product Interaction

### Step 1：确认/建立本地互动表

如果无等价表，创建 `ac_todo_product_interaction`，字段和索引遵循 Design。

Repository protocol至少提供：

```python
create_interaction_single_flight(...)
get_interaction(...)
list_interactions(todo_id, ...)
mark_delivery_unknown(...)
complete_interaction(...)
fail_interaction(...)
request_cancel(...)
```

所有方法显式 tenant/env。

状态转换建议：

```text
sending → succeeded
sending → delivery_unknown
sending → failed
sending → cancel_requested
cancel_requested → succeeded/cancelled/delivery_unknown/failed
delivery_unknown → succeeded/failed/cancel_requested
delivery_unknown → delivery_unknown（幂等）
```

禁止：

```text
succeeded → sending
cancelled → sending
failed → succeeded（除非是同 request ID补偿且有明确协议结果；需单独方法和审计）
```

### Step 2：定义 Product Interaction Core model/VO

Core：

```text
TodoProductInteraction
TodoProductInteractionResponse
TodoProductInteractionDeliveryState
TodoProductInteractionPage
```

VO不暴露内部异常、原始协议对象或 ActorContext。

### Step 3：单飞创建

Repository临界区：

1. 锁 Todo row；
2. 校验 Todo runtime kind=`product_collet`；
3. 校验 Todo非 completed/cancelled；
4. 校验无 sending/delivery_unknown；
5. 校验 reply target是最后一个允许回复的 interaction；
6. 创建 interaction ID；
7. 保存 sending；
8. commit。

不要持有数据库事务等待协议网络调用。

### Step 4：查询当前 Rollout

调用 `query_collet_rollout()`：

- 使用 Todo `product_collet_id`；
- 使用认证 Human ActorContext；
- 读取 `current_rollout_id` 和 `revision`；
- 不按列表顺序猜；
- 保存到 interaction；
- 找不到 current rollout时 interaction进入明确 failed，不发送。

如果 Task 3 已有 adapter，直接复用，不复制解析逻辑。

### Step 5：调用 interactive

```python
result = await service.interactive(
    current_rollout_id,
    request_message,
    context=actor_context,
    request_id=interaction_id,
    expected_revision=revision,
)
```

要求：

- message做长度、空白和内容安全校验；
- request ID不因重试变化；
- expected revision保存后再调用；
- 不从 Frontend接受 rollout ID/revision；
- 不把 owner当当前 actor。

### Step 6：保存同步响应

事务更新：

```text
delivery_state=succeeded
response_type=result.type
response_message=result.message
updated_at
```

随后集中映射动作：

| 动作 | Todo 产品处理 |
| --- | --- |
| `DO_NOTHING` | 保持/继续执行，刷新 Progress |
| `OPTIMIZE` | 显示优化已接受，等待/展示新尝试 |
| `RE_DO` | 显示重做已接受，等待/展示新尝试 |
| `SHUTDOWN` | 记录明确 stop fact并将 Todo映射 cancelled/终止 |

协议 response message 是平台回复，应持久化并展示，但需要长度和安全渲染处理。

### Step 7：超时和明确失败

明确业务错误：

```text
sending → failed
```

超时/连接断开/结果未知：

```text
sending → delivery_unknown
```

Frontend在 unknown时：

- 显示“发送结果确认中”；
- 不允许新消息；
- 允许请求停止；
- 不提供“再发一次”生成新 interaction。

补偿 worker/service：

- 读取同 interaction；
- 使用同 request ID、同 message、同 expected revision重试；
- 成功保存同一条 response；
- stale revision不能自动换新 revision重放 Human 消息。

### Step 8：stale revision

如果协议拒绝 expected revision：

1. 保存 failed + `stale_rollout_revision`；
2. 解除发送锁；
3. 刷新 Progress；
4. Frontend提示状态已变化，请基于最新信息重新确认；
5. 用户重新发送会创建新的 interaction ID。

### Step 9：等待期间停止

Cancel endpoint：

```text
POST /todos/{todo_id}/product-interactions/{interaction_id}/cancel
```

流程：

1. 鉴权并确认 interaction属于 Todo；
2. `request_cancel()` 幂等标记；
3. 生成 stop request ID：`{interaction_id}:shutdown:v1`；
4. 当前调用已返回时，基于最新 rollout/revision发送停止意图；
5. 当前调用 unknown时，按产品策略排队，在不制造并发冲突的条件下发送；
6. 只有明确 `SHUTDOWN` 才 cancelled；
7. 否则显示 stop pending/unconfirmed。

由于协议没有独立 stop API，停止意图文本必须由 Todo 产品层固定生成，不让 Frontend拼协议指令。

### Step 10：Legacy/Product 分流

现有 interaction endpoint若继续复用：

```text
runtime kind product_collet → TodoProductInteractionService
runtime kind legacy_context → existing user_interaction.posted flow
runtime kind product_runtime_pending → 409 runtime_not_ready
runtime kind invalid_binding → 409 invalid_runtime_binding
```

Product Todo绝不能：

- 要求 `collab_context_id`；
- 写旧 Context event；
- 由旧 Scheduler resume；
- 使用 `todo_execution_id` 作为 interaction ID。

### Step 11：HTTP API

建议：

```text
GET  /openapi/v1/collaboration/tasks/todos/{todo_id}/product-interactions
POST /openapi/v1/collaboration/tasks/todos/{todo_id}/product-interactions
POST /openapi/v1/collaboration/tasks/todos/{todo_id}/product-interactions/{interaction_id}/cancel
```

稳定错误：

```text
interaction_in_flight
runtime_not_ready
invalid_runtime_binding
stale_rollout_revision
interaction_delivery_unknown
interaction_not_cancellable
interaction_not_found
```

不要直接返回协议异常文本。

### Step 12：Frontend interaction UI

对话框模型：

- 每条 interaction是一个 Human request + platform response；
- 历史框只读；
- 最后一个框在 `can_send=true`时提供输入；
- 没有 interaction时页面底部提供初始输入；
- 发送后立即禁用；
- response返回后追加在同一框；
- unknown/failed/cancel pending有不同视觉状态；
- stop明确成功前不显示已取消。

不要让 Frontend自行根据 response文本猜动作类型。

### Step 13：通知与互动联动

- 需要 Human处理时发布通知；
- Human发送成功后，相关旧通知可按现有产品规则标记处理/已读；
- 平台响应再次要求 Human时创建新 action version和新通知；
- 相同 response重试不得重复通知；
- 完成通知跳到详情顶部或最终结果，而不是可输入框。

### Step 14：可观测性

指标：

```text
todo_notification_publish_total{type,result}
todo_product_interaction_total{state,response_type}
todo_product_interaction_inflight
todo_product_interaction_unknown_age_seconds
```

日志关联 ID：

```text
todo_id
interaction_id
product_collet_id
rollout_id
expected_revision
notification_delivery_key
```

不记录完整 message。

### Step 15：文档

更新 Todo README：

- Product Interaction本地事实；
- single-flight；
- delivery unknown；
- SHUTDOWN映射；
- Todo notification boundary；
- Legacy/Product分流。

## 9. 幂等、并发和补偿

### Notification

- delivery key稳定；
- adapter upsert/merge；
- 已读不回退；
- 后续 action version新通知；
- 失败由事实扫描补偿。

### Interaction

- interaction ID=协议 request ID；
- 同一 interaction重试不新增行；
- Todo行锁阻止并发；
- unknown保持输入锁；
- stale revision不自动重放；
- stop有独立确定 request ID；
- 未明确 SHUTDOWN不 cancelled。

## 10. 测试执行

Backend focused：

```bash
cd apps/backend
DEPLOY_PROFILE=test uv run --no-sync pytest -q \
  tests/community/core/task/todos \
  tests/community/repository/task/todos \
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

## 11. 验收 Checklist

### 通知

- [ ] Todo Core有独立 Notification Publisher Protocol；
- [ ] 未依赖 ForumNotificationPublisherProtocol；
- [ ] 复用现有 Inbox/WorkOrder基础设施；
- [ ] recipient只为 owner/明确 Human；
- [ ] delivery key稳定且并发幂等；
- [ ] 已读不会因重试变未读；
- [ ] Runtime成功不因通知失败回滚；
- [ ] 补偿可重发；
- [ ] deep link重新鉴权并定位正确。

### Interaction

- [ ] 有本地可追溯存储；
- [ ] 未创建重复同义表；
- [ ] single-flight并发测试通过；
- [ ] request ID=interaction ID；
- [ ] current rollout/revision来自协议元数据；
- [ ] expected revision已传；
- [ ] Human和platform response都保存；
- [ ] timeout进入 delivery_unknown；
- [ ] unknown时禁止新消息；
- [ ] stale revision不自动重放；
- [ ] 只有 SHUTDOWN映射 cancelled；
- [ ] stop未确认有明确状态；
- [ ] Product Todo不写旧 Context event；
- [ ] Legacy interaction回归通过；
- [ ] Frontend只有最后一个框可输入；
- [ ] Backend/Frontend focused和完整 CI状态已报告。

## 12. 与其他任务的边界

- 复用 Task 2 runtime kind和 Collet binding；
- 推荐复用 Task 3 Rollout result adapter；
- 通知基础设施可先做，但触发点必须在产品事实持久化之后；
- 不在本任务实现 Progress Summary；互动完成后只触发刷新。

## 13. Agent 停止条件

- 当前站内通知基础设施没有稳定幂等 identity且改造会影响多个领域；
- WorkOrder/Inbox权限模型与 Todo recipient冲突；
- 合并代码已有 Product Interaction表但语义不同；
- `interactive()` 不支持 request ID或 expected revision；
- Rollout结果无法取得 current rollout/revision；
- 协议 AdjustTypeEnum与文档动作完全不匹配；
- 停止需求只能通过新增协议 API完成；
- 无法在 Repository保证 single-flight；
- 互动消息包含无法安全展示/存储的敏感数据；
- Legacy/Product无法可靠分流。
