# Friend Connect Approval and Work-Order Notification Design

Date: 2026-08-22
Status: Draft — pending confirmation before implementation
Scope: BCS friend connect lifecycle + Backend WorkOrder notification/approval integration

## 1. Problem

Friend add currently supports H→A and A→A connect decisions in BCS, but the
approval/notification behavior is not yet complete for the new WorkOrder inbox
model.

Required behavior:

1. H→A and A→A friend add must auto-approve when the target bot config says no
   approval is required.
2. If approval is required but the target bot config has an approval-free
   department list, then the applicant's department must be checked. If the
   applicant department is in that list, the request is also auto-approved.
3. If neither auto-approval condition applies, a friend-add station notification
   / approval work order must be sent to the target bot's owner/reviewer.
4. WorkOrder approval vs notice semantics must be driven by backend-created
   `event_type`, not by frontend `item_type`.
5. Notice-type work orders must not pass `approver_user_ids`, must pass
   `notification_recipient_user_ids`, must not enter pending approval, and must
   not show approve/reject buttons.

## 2. Terms

| Term | Meaning |
| --- | --- |
| H→A | Human actor adds a bot actor as friend. |
| A→A | Bot actor adds another bot actor as friend. |
| target bot | The bot being added, i.e. `to_bot` in BCS `create_connect`. |
| applicant | The actor initiating friend add. Human for H→A, bot for A→A. |
| approval work order | `NotificationCategory.APPROVAL`; actionable inbox item with approve/reject. |
| notice notification | `NotificationCategory.NOTICE`; informational inbox item without approve/reject. |
| `friend_check_in_strategy` | Existing internal bot attribute controlling friend approval strategy. |
| `friend_ext` | Existing internal bot attribute JSON extension used for department metadata. |

## 3. Existing Code Observations

### 3.1 Backend WorkOrder

Observed files:

- `src/backend/src/agentclaw/community/core/work_orders/models.py`
- `src/backend/src/agentclaw/community/core/work_orders/services/work_order_service.py`
- `src/backend/src/agentclaw/community/core/repository/implementations/work_orders/work_order.py`
- `src/backend/src/agentclaw/community/adapters/http/openapi_v1/work_orders/schemas.py`

Existing event values include:

```text
SPACE_JOIN_REVIEWED
SPACE_MEMBER_ADDED
BOT_COLLABORATOR_REVIEWED
BOT_MEMBER_ADDED
HUMAN2BOT_FRIEND_REVIEWED
BOT2BOT_FRIEND_REVIEWED
HUMAN2BOT_PUBLIC_ORDER_CREATED
HUMAN2BOT_PUBLIC_ORDER_COMPLETED
BOT2BOT_PUBLIC_ORDER_CREATED
BOT2BOT_PUBLIC_ORDER_COMPLETED
```

The model also currently contains approval events:

```text
HUMAN2BOT_FRIEND_APPLIED
BOT2BOT_FRIEND_APPLIED
```

Current category mapping already encodes the key rule:

```python
EVENT_CATEGORIES = {
    WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED: NotificationCategory.APPROVAL,
    WorkOrderEventType.HUMAN2BOT_FRIEND_REVIEWED: NotificationCategory.NOTICE,
    WorkOrderEventType.BOT2BOT_FRIEND_APPLIED: NotificationCategory.APPROVAL,
    WorkOrderEventType.BOT2BOT_FRIEND_REVIEWED: NotificationCategory.NOTICE,
    WorkOrderEventType.HUMAN2BOT_PUBLIC_ORDER_CREATED: NotificationCategory.NOTICE,
    WorkOrderEventType.HUMAN2BOT_PUBLIC_ORDER_COMPLETED: NotificationCategory.NOTICE,
    WorkOrderEventType.BOT2BOT_PUBLIC_ORDER_CREATED: NotificationCategory.NOTICE,
    WorkOrderEventType.BOT2BOT_PUBLIC_ORDER_COMPLETED: NotificationCategory.NOTICE,
}
```

Current implementation is still Space-join centric:

- `WorkOrderService.create_space_join_request(...)`
- `WorkOrderService.approve(...)`
- `WorkOrderService.reject(...)`
- `WorkOrderRepository.create_space_join_request(...)`
- `WorkOrderRepository.review_space_join(...)`

There is no generic `create_work_order(...)` implementation yet in the observed
code, even though the desired business shape is generic.

### 3.2 BCS Friend Connect

Observed files:

- `crates/services/bcs-edge-permission/src/lib.rs`
- `crates/contracts/bcs-domain/src/edge_permission.rs`
- `crates/services/bcs-edge-permission-store/src/lib.rs`
- `crates/service-api/bcs-service-api/src/application/v1/internal_bot_attributes.rs`

Current BCS connect logic already gates by the confirmed mapping:

| Config | Current meaning |
| --- | --- |
| `visibility` | Whether bot can be added/collaborated with by actors. `private` rejects. |
| `user_visibility` | Human-side addability. `private` rejects H→A. |
| `friend_check_in_strategy` | Whether friend add requires approval. `OPEN` auto path; other strategies currently pending. |

Current `BotActorConfig` includes:

```rust
pub struct BotActorConfig {
    pub bot_id: String,
    pub env: String,
    pub visibility: String,
    pub status: String,
    pub created_by: Option<String>,
    pub user_visibility: String,
    pub friend_check_in_strategy: String,
}
```

But it does not yet expose `friend_ext`, so BCS cannot evaluate approval-free
department lists without extending this narrow read model.

## 4. Target Behavior

### 4.1 Direction Support

Only these connect directions are valid:

| Direction | Valid | Notes |
| --- | --- | --- |
| H→A | Yes | Human adds bot. |
| A→A | Yes | Bot adds bot. Creates reciprocal edge(s) on approval. |
| H→H | No | Reject. |
| A→H | No | Reject. |
| self-add | No | Reject. |

### 4.2 Addability Gate

Before any approval/notification side effect, BCS must determine whether the
request is allowed to be created at all.

Reject immediately when:

1. caller equals target.
2. direction is not H→A or A→A.
3. target bot does not exist.
4. target bot `status == hidden`.
5. target bot `visibility == private`.
6. caller is human and target bot `user_visibility == private`.

These rejections must not create WorkOrder records or notifications.

### 4.3 Idempotency Gate

Before creating WorkOrder side effects:

1. If friendship already exists, return approved/idempotent result and do not
   create duplicate WorkOrder notifications.
2. If a pending connect already exists in the same direction, return pending
   result with existing request ids and do not create duplicate WorkOrder
   notifications.

### 4.4 Request / Edge Lifecycle

The connect request record(s) and edge record(s) must have the following
terminal shapes:

| Scenario | request count | request status before decision | request status after approve | request status after reject | edge count before decision | edge status after approve | edge after reject |
| --- | --- | --- | --- | --- | --- | --- | --- |
| H→A, auto-approved | 1 | `approved` | `approved` | n/a | 1 | `approved` | n/a |
| H→A, needs approval | 1 | `pending` | `approved` | `rejected` | 0 | `approved` | none |
| A→A, auto-approved | 2 | `approved` / `approved` | `approved` / `approved` | n/a | 2 | `approved` / `approved` | n/a |
| A→A, needs approval | 2 | `pending` / `pending` | `approved` / `approved` | `rejected` / `rejected` | 0 | `approved` / `approved` | none |

Notes:

- Before approval, a required-approval request has no edge yet.
- H→A creates a single request row; A→A creates two request rows, one in each
  direction.
- A rejection must not create any edge row.
- After approval, the edge row(s) are durable and the request row(s) are marked
  `approved`.
- The reciprocal A→A request is part of the same logical friend-add operation
  and is approved/rejected together with the forward request.

### 4.5 Approval Decision

After addability and idempotency gates:

```text
if friend_check_in_strategy == OPEN:
    auto approve
elif friend_check_in_strategy == DEPT_FREE
     and applicant department is in target approval-free department list:
    auto approve
else:
    create pending approval request
```

Supported strategy values should be normalized case-insensitively:

| Stored value | Normalized meaning |
| --- | --- |
| `OPEN` / `open` | No approval needed. |
| `APPROVAL` / `approval` | Approval required. |
| `DEPT_FREE` / `dept_free` | Department allowlist can bypass approval; otherwise approval required. |

## 5. Bot Internal Attribute Contract

### 5.1 Existing Fields

From `BotInternalAttributes`:

```rust
pub struct BotInternalAttributes {
    pub user_visibility: UserVisibility,
    pub friend_ext: Map<String, Value>,
    pub friend_check_in_strategy: FriendCheckInStrategy,
}
```

### 5.2 Proposed `friend_ext` Keys

```json
{
  "department_code": "TECH",
  "no_check_scope_friend_deps": ["TECH", "AI_PLATFORM"],
  "view_scope_user_friend_deps": [],
  "view_scope_agent_friend_deps": []
}
```

Rationale:

- `department_code` describes the actor/bot's own department.
- `no_check_scope_friend_deps` is the target bot's approval-free department
  allowlist for friend add.
- `view_scope_user_friend_deps` and `view_scope_agent_friend_deps` are carried
  through as existing friend_ext metadata and are not consulted by the current
  connect decision path.
- All fields stay inside the existing `friend_ext` extension object; no new
  `bcs_bots` columns are introduced.

### 5.3 BCS Read Model Change

Extend `BotActorConfig`:

```rust
pub struct BotActorConfig {
    pub bot_id: String,
    pub env: String,
    pub visibility: String,
    pub status: String,
    pub created_by: Option<String>,
    pub user_visibility: String,
    pub friend_check_in_strategy: String,
    pub friend_ext: serde_json::Value,
}
```

`DbBotActorConfigStore` should parse it from:

```text
bcs_bots.bot_info.friend_ext
```

Missing or invalid `friend_ext` should default to `{}`.

## 6. Applicant Department Resolution

### 6.1 Desired Interface

Add a small BCS outbound seam:

```rust
#[async_trait]
pub trait ActorDepartmentRepoPort: Send + Sync {
    async fn department_code(&self, actor_id: &str, env: &str) -> Option<String>;
}
```

This keeps `ConnectService` focused on friend-add policy while allowing local,
test, and production adapters to resolve departments differently.

### 6.2 Resolution Rules

For H→A:

```text
actor_id = human_{staff_no}
department = staff department for staff_no
```

For A→A:

```text
1. read applicant bot internal attribute friend_ext.department_code
2. if missing, fallback to applicant bot created_by owner's staff department
3. if still missing, department is unknown
```

Unknown department never matches department allowlist. It falls back to manual
approval when `friend_check_in_strategy == DEPT_FREE`.

## 7. WorkOrder Creation Semantics

### 7.1 Event Category Is Authoritative

WorkOrder category must be derived from backend `EVENT_CATEGORIES[event_type]`.
Frontend `item_type=APPROVAL/NOTICE` remains a query filter only.

### 7.2 Approval Event Semantics

For friend requests that require approval:

| Direction | event_type | category |
| --- | --- | --- |
| H→A | `HUMAN2BOT_FRIEND_APPLIED` | `APPROVAL` |
| A→A | `BOT2BOT_FRIEND_APPLIED` | `APPROVAL` |

Creation requirements:

- `approver_user_ids` must be non-empty.
- `notification_recipient_user_ids` should be empty or ignored for approval
  recipient creation.
- A pending `ac_work_order` should be created.
- Approval notifications should be created for each approver.
- Inbox detail/list must expose `can_approve=true` only for eligible approvers.

### 7.3 Notice Event Semantics

For reviewed friend requests:

| Direction | event_type | category |
| --- | --- | --- |
| H→A | `HUMAN2BOT_FRIEND_REVIEWED` | `NOTICE` |
| A→A | `BOT2BOT_FRIEND_REVIEWED` | `NOTICE` |

Creation requirements:

- `approver_user_ids` must be empty.
- `notification_recipient_user_ids` must be non-empty.
- Notification must not enter the pending approval flow.
- Notification must not show approve/reject buttons.
- Notification can be stored as `ac_work_order_notification` with
  `work_order_id = NULL`, unless product explicitly requires a terminal
  `ac_work_order` row for every notice.

BCS 现阶段先把“需要审批”的 friend request 通知链路补齐；reviewed notice
可在后续接入 Backend generic create 接口后补上。

### 7.4 Proposed Generic Backend Interface

Introduce a generic WorkOrder creation method:

```python
def create_work_order(
    *,
    event_type: WorkOrderEventType,
    biz_type: WorkOrderBizType | str,
    biz_id: str,
    applicant_user_id: str,
    apply_reason: str | None,
    biz_data: dict | None,
    approver_user_ids: list[str],
    notification_recipient_user_ids: list[str],
):
    ...
```

Backend validation:

```python
category = EVENT_CATEGORIES[event_type]

if category is NotificationCategory.APPROVAL:
    if not approver_user_ids:
        raise WorkOrderNoReviewerError(...)
    create pending work order
    create APPROVAL notifications for approver_user_ids

if category is NotificationCategory.NOTICE:
    if approver_user_ids:
        raise WorkOrderInvalidRequestError(...)
    if not notification_recipient_user_ids:
        raise WorkOrderNoRecipientError(...)
    create NOTICE notifications only
```

Add `WorkOrderBizType.BOT_FRIEND`:

```python
class WorkOrderBizType(StrEnum):
    SPACE_JOIN = "SPACE_JOIN"
    BOT_FRIEND = "BOT_FRIEND"
```

`PUBLIC_ORDER` may be added separately if public-order events need generic
creation now; it is not required for friend add.

## 8. BCS ↔ Backend Integration

### 8.1 Recommended Seam

Add a BCS outbound seam for friend WorkOrder side effects:

```rust
#[async_trait]
pub trait FriendWorkOrderPort: Send + Sync {
    async fn create_friend_approval(
        &self,
        direction: FriendConnectDirection,
        request_ids: &[String],
        applicant_actor_id: &str,
        target_bot_id: &str,
        message: Option<&str>,
        env: &str,
    ) -> ServiceResult<()>;

    async fn create_friend_notice(
        &self,
        direction: FriendConnectDirection,
        request_ids: &[String],
        applicant_actor_id: &str,
        target_bot_id: &str,
        decision: FriendConnectDecision,
        env: &str,
    ) -> ServiceResult<()>;
}
```

Production adapter calls Backend's internal WorkOrder creation endpoint/service.
Local/test adapter can be Noop or recording mock.

### 8.2 Why BCS Should Not Write WorkOrder Tables Directly

The WorkOrder tables and inbox semantics are owned by Backend. BCS should not
write `ac_work_order` / `ac_work_order_notification` directly because that would
couple BCS to Backend persistence and duplicate WorkOrder category/can-approve
rules.

### 8.3 Side-Effect Failure Policy

Recommended policy:

- If BCS creates a pending friend request but cannot create the approval
  WorkOrder, the overall operation should fail and the pending request should
  not be left without an approval entry.
- If transactionality cannot span BCS DB and Backend DB, use idempotent outbox
  semantics or retryable `biz_id=request_id` idempotency in Backend.

Minimum acceptable first implementation:

- Call WorkOrder creation immediately after inserting pending/approved request.
- Backend creation must be idempotent by `(event_type, biz_type, biz_id,
  recipient_user_id)`.
- On WorkOrder creation failure, return error and emit structured log/metric.

Open implementation choice: see §12.3.

## 9. End-to-End Flows

### 9.1 H→A Pending Approval

```text
POST /friends/request
  caller = human_1001
  to_bot = bot_a

BCS:
  load target bot config
  friend_check_in_strategy = APPROVAL
  create pending permission_request
  emit approval notification to target bot owner(s)
  return pending
```

### 9.2 H→A Auto Approved by OPEN

```text
POST /friends/request
  caller = human_1001
  to_bot = bot_a

BCS:
  load target bot config
  friend_check_in_strategy = OPEN
  create approved permission_request
  create human -> bot friend edge
  return approved, auto_accepted=true
```

### 9.3 H→A Auto Approved by Department Allowlist

```text
POST /friends/request
  caller = human_1001
  to_bot = bot_a

BCS:
  target.friend_check_in_strategy = DEPT_FREE
  target.friend_ext.no_check_scope_friend_deps contains applicant dept
  create approved request + edge
  return approved, auto_accepted=true
```

### 9.4 A→A Pending Approval

```text
POST /friends/request
  caller = bot_a
  to_bot = bot_b

BCS:
  approval required
  create pending request(s)
  emit approval notification to target bot owner(s)
  return pending
```

### 9.5 A→A Auto Approved

```text
POST /friends/request
  caller = bot_a
  to_bot = bot_b

BCS:
  OPEN or DEPT_FREE allowlist hit
  create approved reciprocal connect state
  return approved, auto_accepted=true
```

## 10. Message Templates

Add friend-specific message templates in Backend WorkOrder domain.

Suggested titles:

| Scenario | Title |
| --- | --- |
| H→A pending | `Bot 好友申请待审批` |
| A→A pending | `Bot 协作好友申请待审批` |
| H→A approved | `Bot 好友申请已通过` |
| H→A rejected | `Bot 好友申请未通过` |
| A→A approved | `Bot 协作好友申请已通过` |
| A→A rejected | `Bot 协作好友申请未通过` |
| H→A auto approved notice to target owner | `Bot 好友已自动添加` |
| A→A auto approved notice to target owner | `Bot 协作好友已自动添加` |

Suggested content fields in `biz_data`:

```json
{
  "request_id": "req_xxx",
  "request_ids": ["req_xxx"],
  "from_actor_id": "human_1001",
  "from_actor_name": "张三",
  "from_actor_department_code": "TECH",
  "to_bot_id": "bot_a",
  "to_bot_name": "A Bot",
  "decision": "approved",
  "decision_reason": "auto_open | auto_dept_free | manual_approved | manual_rejected",
  "message": "optional applicant message"
}
```

## 11. Data and Idempotency

### 11.1 BCS Permission Request

`permission_requests` remains the source of truth for friend request lifecycle:

- `pending`
- `approved`
- `rejected`
- `cancelled`

### 11.2 WorkOrder Business Id

For pending approval:

```text
biz_type = BOT_FRIEND
biz_id = primary request_id
```

For A→A where BCS creates two request rows, use the forward request id as
primary `biz_id`, and include all request ids in `biz_data.request_ids`.

### 11.3 WorkOrder Idempotency

Backend should avoid duplicate notifications on retries using a uniqueness rule
or application-level idempotency:

```text
(event_type, biz_type, biz_id, recipient_user_id, env)
```

If no DB unique key is added initially, repository should at least check before
insert inside a transaction.

## 12. Open Questions Before Implementation

### 12.1 Approval-free department field name

Confirmed:

```json
friend_ext.no_check_scope_friend_deps
```

This is the approval-free department allowlist used by the friend add path.

### 12.2 Target bot reviewer source

When creating an approval work order, who should receive it?

Proposed default:

```text
target bot created_by owner user id
```

Questions:

1. Is `created_by` always the approver for friend adds?
2. Are there multiple bot owners/admins that need to receive the approval?
3. If target bot has no `created_by` because it is legacy, should we reject,
   fallback to a bot admin list, or skip WorkOrder and keep old path?

### 12.3 Cross-system transaction behavior

BCS owns `permission_requests` / `edge_grants`; Backend owns WorkOrder tables.

Question:

- Should implementation use synchronous backend call and fail the friend request
  if WorkOrder creation fails, or add an outbox/retry mechanism?

Recommended first version:

- Synchronous call with idempotent Backend create.
- Return failure if pending approval WorkOrder cannot be created.

### 12.4 Applicant department source for H→A

Question:

- Does BCS already have a production-accessible staff department source, or must
  Backend provide applicant department in the WorkOrder/Friend facade?

Recommended:

- Add BCS `ActorDepartmentRepoPort` with local noop + production adapter.
- Missing department means no DEPT_FREE match.

### 12.5 Notice recipients for auto approval

Proposed recipients:

| Direction | Recipients |
| --- | --- |
| H→A auto approved | applicant human + target bot owner |
| A→A auto approved | requester bot owner + target bot owner |
| manual reviewed | applicant side owner/user |

Please confirm whether target bot owner should receive a notice when their bot
auto-accepts a friend add.

### 12.6 Public bot behavior

`PublicNoEdge` is kept only for the runtime admission / discoverability path.
For explicit friend add (`POST /friends/request`), `OPEN` auto-approval creates a
real approved friend edge and approved request record even when the target bot
is `visibility=public`.

## 13. Implementation Plan After Confirmation

### Phase 1 — Backend WorkOrder generic creation

1. Add `WorkOrderBizType.BOT_FRIEND`.
2. Add generic `WorkOrderService.create_work_order(...)`.
3. Add repository methods for approval work order and notice notifications.
4. Add validation for approval vs notice categories.
5. Add friend event message templates.
6. Add unit tests for approval and notice creation.

### Phase 2 — BCS config and department policy

1. Extend `BotActorConfig` with `friend_ext`.
2. Parse `bot_info.friend_ext` in `DbBotActorConfigStore`.
3. Add `ActorDepartmentRepoPort` and test/noop adapter.
4. Implement `DEPT_FREE` allowlist matcher.
5. Add BCS unit tests for OPEN, DEPT_FREE hit, DEPT_FREE miss, missing dept.

### Phase 3 — BCS WorkOrder integration seam

1. Add `FriendWorkOrderPort`.
2. Add noop/recording test adapter.
3. Call approval creation when connect becomes pending.
4. Call notice creation when connect auto-approves or manual review completes.
5. Ensure duplicate create does not emit duplicate WorkOrder side effects.

### Phase 4 — Production adapter and integration tests

1. Add backend internal endpoint or client integration for generic WorkOrder
   creation.
2. Add BCS HTTP/client adapter.
3. Add integration tests for H→A and A→A pending/auto-approved flows.
4. Add failure-path test: WorkOrder creation failure does not leave invisible
   pending approval state.

## 14. Non-goals

- Do not reintroduce `human_addable` or `friend_approval` columns.
- Do not make frontend decide approval vs notice by submitting `item_type`.
- Do not let BCS write Backend WorkOrder tables directly.
- Do not globally reformat BCS Rust code.
- Do not remove old migration compatibility paths unrelated to friend approval
  notification.

## 15. Confirmation Checklist

Before code starts, confirm these points:

- [ ] Exact `friend_ext` field name for approval-free departments.
- [ ] Whether `public + OPEN` explicit friend add should create durable friend edges.
- [ ] Target bot approver source: `created_by` only or multiple owners/admins.
- [ ] Notice recipients for auto-approved friend adds.
- [ ] Whether Backend already has/wants generic `create_work_order(...)` public/internal interface.
- [ ] Whether first implementation can use synchronous BCS→Backend WorkOrder creation or needs outbox/retry.
