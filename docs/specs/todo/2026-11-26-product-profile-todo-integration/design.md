# Todo 产品层接入 ProductProfileService 技术设计

> 状态：Ready for implementation
> 日期：2026-11-26
> Spec：[`spec.md`](spec.md)
> 实施索引：[`tasks.md`](tasks.md)

## 1. 设计结论

本设计采用“Todo 产品聚合 + Product Runtime 引用”的方式，而不是把 Todo 迁入协议层：

1. BotTeam Revision 发布时创建 ProductProfile，Revision 只保存 `product_profile_id`；
2. Todo 创建时先原子保存产品聚合，再创建 Collet，Todo 只保存 `product_collet_id`；
3. Todo Stage 快照是详情页稳定骨架，Rollout/StageRollout 只提供运行事实；
4. Todo Backend 负责将运行事实映射为稳定 Progress VO；
5. Human 请求、协议响应和通知均由 Todo 本地持久化，保证产品可追溯；
6. 历史 Context Todo 继续旧链路，不做回填或隐式迁移；
7. ProductProfileService 只通过现有 Protocol 调用，不修改协议实现。

## 2. 分层和调用方向

```text
Frontend Todo Pages
        │ Todo HTTP VO
        ▼
OpenAPI Todo Adapter
        │ 已认证 actor + 产品请求
        ▼
Todo Product Services
├── TodoBotTeamProductProfileService
├── TodoProductCreationService / TodoProductRuntimeService
├── TodoProductProgressService
├── TodoProductInteractionService
└── TodoNotificationPublisherProtocol
        │
        ├── Todo Repositories / Product-owned tables
        │
        ├── ProductProfileServiceProtocol
        │   ├── create_product_profile
        │   ├── create_product_collet
        │   ├── query_collet_rollout
        │   ├── query_stage_rollout_details
        │   └── interactive
        │
        └── TodoNotificationPublisherAdapter
            └── existing inbox/work-order notification repository
```

强制依赖方向：

```text
Frontend → Todo HTTP → Todo Core → Protocol / Repository
```

禁止方向：

```text
Frontend → ProductProfileService DTO
Todo Core → 协议内部 Repository/L5 实现
ProductProfileService → Todo 产品模型
Todo Core → ForumNotificationPublisherProtocol
```

## 3. 权威协议合同

Todo 产品层只能依赖当前五方法：

```python
async def create_product_profile(
    name,
    composition,
    stages,
    *,
    context,
    request_id,
) -> ProductProfile

async def create_product_collet(
    profile_id,
    content,
    *,
    context,
    request_id,
) -> Collet

async def query_collet_rollout(
    collet_id,
    all_rollout=True,
    *,
    context,
) -> list[ColletRollout]

async def interactive(
    collet_rollout_id,
    interactive_context,
    *,
    context,
    request_id,
    expected_revision,
) -> InteractiveResult

async def query_stage_rollout_details(
    stage_rollout_id,
    *,
    context,
    limit=50,
) -> StageRolloutDetails
```

关键合同事实：

- `InteractiveResult` 只保证 `type` 和 `message`；
- `StageRolloutDetails.messages` 是 `list[Any]`，Todo 必须做防御解析；
- `StageRolloutDetails.order` 默认是 `provider-defined`；
- `StageRolloutDetails.complete` 可能为 `None`；
- `query_collet_rollout()` 类型标注为 list，但实际可返回带元数据的 `RolloutList`；
- `RolloutList` 提供 `revision`、`current_rollout_id`、`stopped`；
- Todo 不能假定列表最后一项是当前 Rollout。

## 4. 产品运行模式

### 4.1 集中判定

新增单一函数或值对象集中判定运行模式，例如：

```python
class TodoRuntimeKind(str, Enum):
    PRODUCT_COLLET = "product_collet"
    PRODUCT_RUNTIME_PENDING = "product_runtime_pending"
    LEGACY_CONTEXT = "legacy_context"
    INVALID_BINDING = "invalid_binding"
```

判定输入：

```text
Todo.template_version_id
Revision.product_profile_id
Todo.product_collet_id
Todo.collab_context_id
```

判定表：

| Revision Profile | Todo Collet | Context | 结果 |
| --- | --- | --- | --- |
| 有 | 有 | 任意 | `product_collet`；不得再走旧 dispatch |
| 有 | 无 | 无 | `product_runtime_pending`；创建/补偿 Collet |
| 无 | 无 | 有 | `legacy_context` |
| 无 | 无 | 无 | 单 Bot或尚未 dispatch 的 Legacy Todo，按现有规则 |
| 无 | 有 | 任意 | `invalid_binding` |
| 有 | 无 | 有 | 默认视为迁移冲突，`invalid_binding`，不得自动二选一 |

### 4.2 禁止启发式判断

不得依据以下信息判断：

- ID 是整数还是字符串；
- `collab_context_id` 是否长得像 Collet ID；
- Todo 创建时间；
- `template_id` 是否叫 BotTeam；
- Feature flag 默认值；
- status 当前是否 `not_started`。

## 5. 数据模型

### 5.1 Revision 绑定 ProductProfile

在 `ac_todo_template_version` 新增：

```sql
ALTER TABLE ac_todo_template_version
  ADD COLUMN product_profile_id BIGINT NULL
  COMMENT 'Immutable ProductProfile bound to this published BotTeam revision';
```

索引按项目已有 SQL 方言和命名规范生成，语义为：

```text
(avernet_tenant, env, product_profile_id)
WHERE product_profile_id IS NOT NULL（若数据库支持部分索引）
```

如果当前数据库不支持部分索引，则使用普通复合索引。不要增加全局唯一约束，因为协议 ID 是否跨 tenant/env 全局唯一不应由 Todo 假设。

Repository 增加：

```python
bind_product_profile_if_unbound(
    *,
    revision_id: str,
    product_profile_id: int,
    tenant: str,
    env: str,
) -> BindResult
```

CAS 语义：

- 当前为 `NULL`：写入并返回 bound；
- 当前等于输入：返回 already_bound，作为幂等成功；
- 当前为其他值：返回 conflict，不允许覆盖。

### 5.2 Todo 绑定 Product Collet

在 `ac_todo` 新增：

```sql
ALTER TABLE ac_todo
  ADD COLUMN product_collet_id BIGINT NULL
  COMMENT 'Product Runtime Collet bound to this Todo';
```

索引语义：

```text
(avernet_tenant, env, product_collet_id)
```

Repository 增加：

```python
bind_product_collet_if_unbound(
    *,
    todo_id: str,
    product_collet_id: int,
    tenant: str,
    env: str,
) -> BindResult
```

语义与 Profile CAS 相同。

禁止：

- 把 Collet ID 写入 `collab_context_id`；
- 把 Collet ID 写入 `ac_todo_execution`；
- 清空已有 Context 以切换模式；
- 对历史 Todo 回填。

### 5.3 Product Interaction 本地表

实施前先搜索是否已有等价模型。如果没有，则新增：

```text
ac_todo_product_interaction
```

建议字段：

| 字段 | 类型语义 | 说明 |
| --- | --- | --- |
| `id` | DB 主键 | 按项目约定 |
| `interaction_id` | string | 产品稳定 ID，同时作为协议 request_id |
| `todo_id` | string | Todo identity |
| `product_collet_id` | bigint | 调用时绑定的 Collet |
| `collet_rollout_id` | bigint nullable | 调用时当前 Rollout |
| `expected_revision` | bigint nullable | 调用 interactive 的 revision fence |
| `actor_id` | string | 已认证 Human |
| `actor_name` | string nullable | 展示快照 |
| `request_message` | text | Human 输入 |
| `reply_to_interaction_id` | string nullable | 可选回复关系 |
| `delivery_state` | string | 状态机字段 |
| `response_type` | string nullable | `AdjustTypeEnum` 快照 |
| `response_message` | text nullable | 同步平台回复 |
| `error_code` | string nullable | 稳定错误码 |
| `request_id` | string | 默认等于 interaction_id |
| `cancel_requested` | boolean | 用户是否请求停止 |
| `gmt_create/gmt_modified` | timestamp | 审计时间 |
| `env` | string | 环境隔离 |
| `avernet_tenant` | string | 租户隔离 |

状态：

```text
sending
succeeded
delivery_unknown
failed
cancel_requested
cancelled
```

约束和索引：

```text
UNIQUE(avernet_tenant, env, interaction_id)
INDEX(avernet_tenant, env, todo_id, gmt_create)
INDEX(avernet_tenant, env, todo_id, delivery_state)
```

同一 Todo 的单飞约束不建议只依赖普通唯一索引，因为状态是条件性的。Repository 在 Todo 行锁或等价 CAS 下检查是否已有 `sending/delivery_unknown`，然后创建新 interaction。

## 6. Task 1 设计：Revision 发布为 ProductProfile

### 6.1 发布顺序

```text
校验 BotTeam draft
→ 构造 ProductProfile 输入
→ create_product_profile(request_id)
→ 本地事务发布 Revision 并 CAS 绑定 profile ID
→ 返回已发布 Revision
```

为什么先调协议、后提交本地：

- Revision 对外一旦 published 就必须可运行；
- 先发布本地再调协议会产生“已发布但无 Profile”的新 Revision；
- 协议创建成功、本地失败可用相同 request ID 重试并重新绑定。

### 6.2 request_id

request ID 必须确定性生成并可重建，例如：

```text
todo:profile:{tenant}:{env}:{revision_id}:v1
```

要求：

- 同一 Revision 发布重试使用同一个 request ID；
- 不包含用户可编辑名称；
- 日志中可记录 hash/ID，但不得记录凭据；
- 算法版本显式为 `v1`，未来变更不隐式复用旧 key。

### 6.3 composition 配置

`MethodCompositionTemplate` 不应在 Todo service 内散落硬编码。使用产品运行配置或工厂注入：

```text
execution_ref
acceptance_ref
overall_acceptance
```

配置策略：

- 生产配置缺失时 fail closed；
- 测试通过 Fake 配置提供确定值；
- 不从 Frontend 请求接收 capability 引用；
- 不为通过测试使用协议内部测试实现。

### 6.4 stages YAML 转换

BotTeam V1 是线性节点：

```yaml
stages:
  - name: requirement_analysis
    candidates:
      - type: bot
        uuid: bot-id-1
    depends_on: []
    acceptance:
      - condition: 需求理解与验收项完整

  - name: code_development
    candidates:
      - type: bot
        uuid: bot-id-2
    depends_on:
      - requirement_analysis
    acceptance:
      - condition: 方案设计与 commit 完整
```

转换规则：

1. stage name 使用 Revision 内稳定 node code，不使用可编辑中文名作为 identity；
2. 第一个 stage `depends_on=[]`；
3. 第 N 个 stage 依赖第 N-1 个；
4. candidate 使用 node 固定的 Bot identity；
5. completion gate/验收条件转为协议 acceptance 文本；
6. 输入顺序稳定，保证重试生成同样 YAML；
7. 非法 code、重复 code、不可用 Bot 在调用协议前拒绝。

### 6.5 `approval_required` 兼容

当前 `TodoBotTeamService._to_template_definition()` 会把：

```text
node approval_required=true
```

展开为：

```text
Bot Stage
Human Stage: <node>__approval
```

这属于现有 Template/Todo Stage 的产品兼容表达，不代表 ProductProfile 必须包含 Human Stage。

Profile 生成器必须根据 BotTeam Revision 的原始产品节点生成协议 stages，或在从 Template Stage 转换时识别并过滤 `__approval` 合成节点。Human 负责人、通知、验收入口和恢复逻辑继续由 Todo 产品层实现。

### 6.6 原子发布

本地 Repository 事务至少完成：

```text
创建/确认 immutable revision
写 product_profile_id
设置 published 状态
更新 BotTeam current revision pointer（如现有模型需要）
```

并发发布同一 draft 时：

- 使用现有 version/revision 乐观锁；
- Profile CAS 只允许空值绑定；
- 发现已绑定相同 ID视为成功；
- 已绑定不同 ID返回冲突，保留证据，不自动覆盖。

## 7. Task 2 设计：Dima/Todo/Collet

### 7.1 聚合创建边界

当前 BotTeam + source 被拒绝，是因为 `create_todo_with_stages()` 没有把来源绑定放在同一事务。本设计要求扩展现有方法或增加语义清晰的新 Repository 方法：

```python
create_product_todo_aggregate(
    *,
    todo,
    stages,
    participant_bindings,
    source_binding: TodoSourceBinding | None,
) -> TodoRecord
```

同一事务写入：

```text
Todo
SourceBinding（如有）
participant bindings（可作为 Todo JSON 字段）
Stage snapshots
```

不得先创建 Todo，再在 service 中另开事务写 source。

### 7.2 Dima 导入

每批最多 20 条，service 逐条建立独立事务和独立结果：

```json
{
  "source_work_item_id": "DIMA-001",
  "status": "imported",
  "todo_id": "todo-001",
  "product_runtime_status": "ready"
}
```

Collet 暂时失败：

```json
{
  "source_work_item_id": "DIMA-002",
  "status": "failed",
  "todo_id": "todo-002",
  "error_code": "product_runtime_unavailable",
  "retryable": true
}
```

虽然批量结果为 `failed`，本地 Todo 已存在；重试必须识别既有来源绑定并对同一个 Todo 补偿 Collet，不能重新建 Todo。

### 7.3 Collet 创建

输入：

```text
profile_id = Revision.product_profile_id
content = Todo 的稳定执行内容
context = 已认证 Human ActorContext
request_id = todo:collet:{tenant}:{env}:{todo_id}:v1
```

`content` 由产品层明确拼装，至少包含：

```text
Todo 标题
Todo 描述/目标
来源摘要（如有）
负责人/参与者的产品上下文（不放授权凭据）
```

不要把完整 Dima 原始响应、内部凭据或 UI 临时字段原样传入。

### 7.4 ensure_product_collet

统一服务：

```python
async def ensure_product_collet(todo_id, *, actor_context) -> EnsureColletResult
```

步骤：

1. 按 tenant/env 查询 Todo、Revision 和 Profile；
2. 若已有 `product_collet_id`，直接返回；
3. 若 Revision 无 Profile，返回 not_applicable，不走 Product Runtime；
4. 用确定 request ID 调 `create_product_collet()`；
5. 调 Repository CAS 绑定；
6. 绑定为相同 ID时幂等成功；
7. 绑定为不同 ID时返回冲突并报警，不覆盖。

调用入口：

- Dima 导入后的单条处理；
- 手工创建 BotTeam Todo；
- Scheduler 扫描 Product Todo 的 `not_started` 分支；
- 显式补偿/运维入口（如果现有项目有此模式）。

### 7.5 Scheduler 分流

```text
Product Todo + no Collet
→ ensure_product_collet()

Product Todo + Collet
→ 不调用旧 Context dispatch；等待 Product Runtime 自身推进

Legacy Todo
→ 保持旧 Context dispatch/refresh
```

Todo 不重复实现 Product Runtime 的 Rollout Worker。

## 8. Task 3 设计：Progress 投影

### 8.1 产品接口

建议新增：

```text
GET /openapi/v1/collaboration/tasks/todos/{todo_id}/progress
```

也可以在现有 Todo detail 中嵌入 `progress`，但必须保持 service/VO 边界独立，避免详情基础信息因 Runtime 故障整体失败。

### 8.2 Progress VO

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
      "code": "code_development",
      "name": "代码开发",
      "status": "in_progress",
      "summary": "正在完成 Todo 与 Product Runtime 的接入",
      "current_step": "创建并绑定 Collet",
      "steps": [
        {
          "id": "stable-or-derived-id",
          "name": "分析现有链路",
          "status": "completed",
          "detail": "已确认旧 Context 链路",
          "occurred_at": "2026-11-26T10:00:00Z"
        }
      ],
      "outputs": [],
      "blocker": null,
      "attempt_count": 2,
      "updated_at": "2026-11-26T10:05:00Z"
    }
  ]
}
```

Frontend 只认识此 VO，不认识协议枚举或任意 message schema。

### 8.3 查询算法

```text
1. 读取 Todo + Stage snapshots + runtime kind
2. Legacy：调用现有详情投影
3. Product pending：返回固定 Stage + runtime pending
4. Product Collet：query_collet_rollout(all_rollout=True)
5. 读取 RolloutList.current_rollout_id/revision/stopped
6. 找到当前 Rollout
7. 将其 StageRollout 与 Todo Stage code 映射
8. 默认仅对当前 Stage 查询 details
9. 用户展开历史 Stage/尝试时再按需查询 details
10. 防御解析 messages，生成 summary/steps
11. 返回稳定 Progress VO
```

如果返回值没有 RolloutList 元数据：

- 不得自行用最后一项当当前 Rollout；
- 返回 `progress_unavailable` 或协议合同不满足的诊断错误；
- 实施 Agent 先用 Recording Double 固化预期，再与真实服务联调；
- 不修改协议层补字段。

### 8.4 状态映射

集中建立一个 Backend mapper，将协议状态映射到产品阶段状态，例如：

| 协议状态 | Todo Stage 展示 |
| --- | --- |
| `CREATE` | `not_started` |
| `RUNNING` | `in_progress` |
| `RUN_FINISHED` | `pending_action` 或“等待验收”，由产品上下文决定 |
| `RUN_FAILED` | `interrupted` 或带 blocker 的运行失败 |
| `ACCEPTANCE_SUCCEED` | `completed` |
| `ACCEPTANCE_FAILED` | `pending_action`/需要调整 |

最终代码必须根据真实枚举定义逐项覆盖，未知值 fail closed 并返回可诊断的 `unknown_runtime_status`，禁止默认映射为未开始。

### 8.5 Session Messages 和子步骤

`query_stage_rollout_details()` 调用要求：

- `limit` 在 1–200；
- 默认 50，按需分页/扩容但不超过 200；
- 对 `messages` 做类型和字段白名单解析；
- `order=provider-defined` 时只在消息含可靠时间/序号时排序；
- 无可靠顺序时保留 provider order，并将 `messages_complete=false`；
- `complete is not True` 时不得生成“完整过程”结论。

Summary 策略：

1. 基础事实优先：状态、消息数、最后可靠时间、明确错误、明确产物；
2. 可选 LLM/规则摘要失败时退回基础事实；
3. 缓存 key 使用 `(stage_rollout_id, message_fingerprint, summary_version)`；
4. fingerprint 由规范化、脱敏后的消息稳定字段生成；
5. Summary 不得覆盖原始事实或制造不存在的产物；
6. Frontend 可显示“摘要生成失败”，但基础进度仍可用。

### 8.6 产物

优先复用 Todo 现有 Artifact 预览/下载能力：

- Runtime message 中只保留 artifact identity/引用；
- Backend 验证 artifact 属于当前 Todo/Stage；
- Preview 返回安全、受限内容；
- Download 继续走已有权限和内容响应；
- 不把任意外部 URL 直接当可信下载地址。

### 8.7 N+1 控制

- 列表页不查询 Stage details；
- 详情首屏只查询当前 Rollout；
- 默认只查询当前 Stage details；
- 展开历史阶段/尝试时按需加载；
- Summary 有 fingerprint cache；
- Frontend 请求去重，避免同一 Todo 并发重复加载；
- Product service 超时时返回部分数据，不无限重试阻塞页面。

## 9. Task 4 设计：通知

### 9.1 独立 Protocol

新增 Todo 自己的边界：

```python
class TodoNotificationPublisherProtocol(Protocol):
    def publish(self, event: TodoNotificationEvent) -> PublishResult: ...
```

不得让 Todo Core 依赖：

```text
ForumNotificationPublisherProtocol
```

生产 Adapter 可复用现有 WorkOrder/Inbox Repository，例如 `merge_notice()`，但领域事件和去重规则归 Todo 所有。

### 9.2 通知事件

建议事件类型：

```text
human_action_required
bot_information_required
execution_interrupted
verification_required
todo_completed
```

事件至少包含：

```text
delivery_key
todo_id
space_id
recipient_id
title
summary
action_type
stage_code
stage_rollout_id
rollout_revision
occurred_at
```

### 9.3 recipient

- Todo 可见性：空间所有成员；
- 通知接收者：当前负责人或事件明确指定 Human；
- 不向空间所有成员群发每个事件；
- 无合法 recipient 时保存可诊断的 skipped reason，不猜测创建者；
- 负责人变更后的新事件通知新负责人，旧通知历史不变。

### 9.4 幂等

建议 delivery key：

```text
{todo_id}:{notification_type}:{rollout_revision}:{stage_rollout_id-or-none}:{action_version}
```

要求：

- 同 key 重试不重复增加未读数；
- 已读通知不会因重试变回未读；
- 后续不同运行尝试或动作版本使用新 key；
- 通知失败不回滚 Runtime/interaction 成功事实；
- 补偿扫描根据 Todo 待处理事实和 delivery log 重发。

### 9.5 跳转

通知 action 跳转到 Todo 详情并携带产品定位参数，例如：

```text
/todos/{todo_id}?focus=interaction&interaction_id={latest}
```

Frontend 加载详情后：

1. 验证用户仍有空间权限；
2. 定位最后一个互动框；
3. 只有最后一个框可输入；
4. 成功打开/显式操作时按现有通知规则标记已读。

## 10. Task 4 设计：Product Interaction

### 10.1 服务流程

```text
POST interaction
→ 鉴权并读取 Todo
→ 判定 product_collet runtime
→ Todo 行锁/CAS 检查单飞
→ 创建 interaction(sending)
→ query_collet_rollout()
→ 获取 current_rollout_id + revision
→ 保存 rollout/revision fence
→ interactive(request_id=interaction_id, expected_revision=revision)
→ 保存 response type/message + succeeded
→ 映射 Todo pending action/status
→ 发布必要通知
→ 返回 interaction VO
```

### 10.2 产品接口建议

```text
GET  /openapi/v1/collaboration/tasks/todos/{todo_id}/product-interactions
POST /openapi/v1/collaboration/tasks/todos/{todo_id}/product-interactions
POST /openapi/v1/collaboration/tasks/todos/{todo_id}/product-interactions/{interaction_id}/cancel
```

现有 interactions 接口可以保持 Legacy 行为；也可以由 Adapter 内部按 runtime kind 分流，但不得把两种存储混为同一领域事实。

### 10.3 单飞和并发

在创建 interaction 的同一 Repository 临界区：

1. 锁定 tenant/env 下的 Todo 行；
2. 检查 Todo 不是 completed/cancelled；
3. 检查没有 `sending/delivery_unknown` interaction；
4. 创建 `sending`；
5. 提交后再调用外部协议。

两个并发请求中只有一个成功创建，另一个返回稳定冲突错误，例如：

```text
interaction_in_flight
```

Frontend 遇到该错误刷新互动列表，不自动重发不同 request ID。

### 10.4 expected_revision

调用前从 `RolloutList.revision` 取得当前 revision，并与 `current_rollout_id` 一起写入 interaction。随后调用：

```python
interactive(
    current_rollout_id,
    request_message,
    context=actor_context,
    request_id=interaction_id,
    expected_revision=revision,
)
```

如果 revision 冲突：

- 保存 failed/stale_revision；
- 刷新进度；
- 提示用户基于最新状态重新确认；
- 不透明地用新 revision 自动重放 Human 原消息。

### 10.5 动作映射

| `AdjustTypeEnum` | 产品语义 |
| --- | --- |
| `DO_NOTHING` | 保存回复，保持/继续当前执行 |
| `OPTIMIZE` | 保存回复，显示正在优化或产生新尝试 |
| `RE_DO` | 保存回复，显示相关阶段将重做 |
| `SHUTDOWN` | 记录明确停止事实，并把 Todo 投影为 cancelled/终止 |

实际枚举值必须从当前协议定义读取，未知值保存原始安全字符串并返回 `unknown_adjust_type`，不得默认当作继续或取消。

### 10.6 超时和 delivery_unknown

协议调用超时或连接中断时，不能确定请求是否已经生效：

```text
sending → delivery_unknown
```

补偿策略：

- 使用同一个 interaction ID/request ID 重试；
- 不创建第二条 Human 消息；
- 在 unknown 未解决前保持输入锁定；
- 如果协议幂等返回同结果，保存 succeeded；
- 若得到明确失败，保存 failed 并解除锁定；
- 若长期未知，提供运维诊断，不允许用户用新 request ID盲重发。

### 10.7 等待中停止

用户在发送等待期间可以请求停止：

1. 将当前 interaction 标记 `cancel_requested`；
2. 当前调用返回或进入 unknown 后，以新的确定性 request ID 发起停止意图；
3. 停止意图仍使用现有 `interactive()`，不新增协议 stop API；
4. 只有协议明确返回 `SHUTDOWN` 才把 Todo 投影为 `cancelled`；
5. 未确认停止时显示 `interrupted` 或“停止未确认”；
6. 不伪造取消成功。

停止 request ID 示例：

```text
{interaction_id}:shutdown:v1
```

### 10.8 互动列表 VO

```json
{
  "items": [
    {
      "interaction_id": "int-001",
      "actor": {"id": "u-1", "name": "用户"},
      "request_message": "请按最新验收项重新执行",
      "delivery_state": "succeeded",
      "response": {
        "type": "RE_DO",
        "message": "已重新执行代码验证阶段"
      },
      "cancel_requested": false,
      "created_at": "...",
      "updated_at": "..."
    }
  ],
  "can_send": true,
  "blocking_interaction_id": null
}
```

Frontend 根据 `can_send` 控制最后一个输入框，但 Backend 仍必须做最终并发校验。

## 11. ActorContext 与安全

所有 ProductProfileService 调用使用服务层从认证上下文构造的 ActorContext，例如：

```python
ActorContext(
    PrincipalRef("human", authenticated_user_id),
    source="todo_product",
)
```

不得：

- 使用请求 body 的 actor ID；
- 使用 Todo owner 冒充当前调用者；
- 用系统身份绕过用户可见性，除非已有明确后台补偿合同；
- 将 tenant/env 放入用户可覆盖字段；
- 在日志记录消息全文或敏感运行信息。

后台 Scheduler/补偿需要调用协议时，应使用仓库已有、经过审计的 service actor 模式；若当前合同不支持后台 actor，实施 Agent 必须停止并反馈，而不是伪造 Human。

## 12. 幂等矩阵

| 操作 | request ID | 本地幂等 | 冲突处理 |
| --- | --- | --- | --- |
| 创建 Profile | `todo:profile:{tenant}:{env}:{revision}:v1` | Revision profile CAS | 不同 Profile ID 不覆盖 |
| 创建 Collet | `todo:collet:{tenant}:{env}:{todo}:v1` | Todo collet CAS | 不同 Collet ID 不覆盖 |
| Human 互动 | `interaction_id` | interaction unique + single-flight | 已在途返回冲突 |
| 停止互动 | `{interaction_id}:shutdown:v1` | interaction cancel state | 未确认不映射 cancelled |
| 通知 | delivery key | Inbox merge/upsert | 已读不恢复未读 |
| Summary | stage rollout + fingerprint + version | cache/upsert | 新 fingerprint 生成新摘要 |

## 13. 失败与补偿矩阵

| 场景 | 本地状态 | 补偿 | 禁止行为 |
| --- | --- | --- | --- |
| Profile 创建失败 | Revision 不发布 | 相同 request ID 重试 | 发布无 Profile 的新 Revision |
| Profile 成功、本地发布失败 | 协议可能已有 Profile | 相同 request ID 重试后 CAS | 创建新 request ID/profile |
| Todo 聚合事务失败 | 无部分聚合 | 修复后重试导入 | 保留孤立 Todo/Stage |
| Todo 成功、Collet 失败 | Todo 保留、无 Collet | `ensure_product_collet` 重试 | 删除 Todo 重新导入 |
| Collet 成功、CAS 失败 | 协议可能已有 Collet | 相同 request ID重试并 CAS | 覆盖不同 Collet ID |
| Progress query 失败 | Todo 基础信息可用 | 后续刷新 | 显示未开始 |
| Stage details 失败 | 阶段状态仍可用 | 按需重试 | 清空/伪造步骤 |
| Summary 失败 | 原始映射仍可用 | fallback/cache refresh | 阻塞详情页 |
| 通知失败 | Runtime 事实保留 | delivery 补偿扫描 | 回滚 interaction/runtime |
| interactive 明确失败 | interaction failed | 用户确认后新 interaction | 自动改变 Todo 状态 |
| interactive 超时 | delivery_unknown | 同 request ID 重试 | 用新 ID重复发送 |
| stop 未确认 | cancel_requested/unknown | 同 stop request ID补偿 | 标记 cancelled |

## 14. 迁移和兼容

### 14.1 数据库迁移顺序

1. 增加 nullable `product_profile_id`；
2. 增加 nullable `product_collet_id`；
3. 如无等价存储，创建 Product Interaction 表；
4. 部署支持读但尚不写的新代码；
5. 开启新 Revision Profile 创建；
6. 开启新 Product Todo Collet 创建；
7. 开启 Progress；
8. 开启通知和互动。

所有新增字段 nullable，不做历史回填。

### 14.2 历史数据

- 历史 Revision `product_profile_id=NULL` 合法；
- 历史 Todo `product_collet_id=NULL` 合法；
- 历史 Context Todo 使用旧字段和旧服务；
- 新代码读到矛盾绑定时 fail closed；
- 不在读路径自动创建 Profile；
- Product Todo 可在 scheduler/明确补偿入口创建 Collet，但必须有已绑定 Profile。

### 14.3 Feature rollout

如果项目使用 feature flag，应按能力分开，而不是一个总开关：

```text
profile_publish_enabled
product_collet_enabled
product_progress_enabled
product_interaction_enabled
todo_notification_enabled
```

关闭后保持已有 Product 数据可读，不能让已经创建的 Product Todo 回退旧 Context dispatch。

## 15. 测试设计

### 15.1 Recording Double

测试提供 `RecordingProductProfileService`：

- 记录五方法调用和参数；
- 可配置返回 Profile/Collet/Rollout/Details/InteractiveResult；
- 可注入 timeout、明确错误、revision conflict；
- 不导入或修改协议内部实现；
- 验证 ActorContext、request ID、expected revision 和 limit。

### 15.2 Backend 测试层次

1. Domain/service unit：转换、状态映射、幂等分流；
2. Repository real DB：事务、CAS、唯一约束、并发 interaction；
3. HTTP：鉴权、schema、错误码、Legacy/Product 分流；
4. Scheduler：Product Todo 不进入旧 Context dispatch；
5. Architecture：Todo 不依赖协议内部实现、Frontend DTO 边界保持；
6. Migration：新库和既有库升级均通过。

### 15.3 Frontend 测试

- Product progress mapper 不接受未知 status；
- Runtime unavailable 显示明确降级态；
- 只允许最后一个互动框输入；
- sending/unknown 时按钮禁用；
- cancel request 可见但未确认不显示已取消；
- artifact 预览/下载权限错误可见；
- 通知 deep link 定位正确；
- Legacy Todo 保持现有页面行为。

## 16. 可观测性

建议结构化日志/指标：

```text
todo_product_profile_create_total{result}
todo_product_collet_ensure_total{result}
todo_product_progress_query_total{result}
todo_product_interaction_total{state,type}
todo_notification_publish_total{result,type}
todo_product_compensation_backlog{kind}
```

日志关联字段：

```text
todo_id
revision_id
product_profile_id
product_collet_id
rollout_id
interaction_id
request_id
notification_delivery_key
```

不得记录：

- credential/token；
- 完整 Dima 私密内容；
- 完整 Human/Provider message；
- 未脱敏的协议 payload。

## 17. Agent 必须停止并反馈的条件

实施 Agent 遇到以下情况不得自行猜测：

1. 当前 ProductProfileService 合同与本设计列出的五方法不一致；
2. `ProductProfile`、`Collet` 或 Rollout DTO 无法取得绑定所需稳定 ID；
3. `query_collet_rollout()` 无法取得 current rollout/revision，且无已有产品合同解释；
4. composition 的生产 capability 引用没有配置来源；
5. Scheduler 后台调用要求合法 ActorContext，但仓库无受支持模式；
6. 数据库不支持所需事务或锁语义，且现有 Repository 无等价 CAS；
7. 合并分支已存在语义相同但字段/表不同的实现，存在重复建模风险；
8. 需要修改协议层才能完成 Todo 产品逻辑；
9. 迁移可能破坏历史 Context Todo；
10. 无法区分 Product Todo 与 Legacy Todo。

停止时应提交：发现的代码证据、受影响任务、可选方案和需要 Owner 决策的问题，不得先改协议层。
