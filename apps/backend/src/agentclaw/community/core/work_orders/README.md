# `agentclaw.community.core.work_orders`

Owns work-order lifecycle, recipient-scoped notifications, fixed event-to-message
mapping, conditional approval transitions, required pre-decision business callbacks,
and the atomic Space-join approval unit of work.

## Context Boundary

```yaml
purpose: "Own approval work orders, notifications, dispatch to business-owned approval handlers, required decision callbacks, and transactional Space-join decisions."
provides:
  - WorkOrderService
  - WorkOrderNotificationService
  - WorkOrderModel
  - WorkOrderNotificationModel
  - WorkOrderApproverModel
  - WorkOrderStatus
  - WorkOrderDecision
  - WorkOrderApproverStatus
  - WorkOrderBizType
  - WorkOrderEventType
  - NotificationCategory
  - WorkOrderQueryType
  - WorkOrderItemType
  - WorkOrderMessageTitle
  - WorkOrderMessageContent
  - WorkOrderNotificationDetail
  - WorkOrderNotificationBadgeSummary
  - SkillCollaboratorApprovalHandlerProtocol
  - WorkOrderDecisionCallbackDispatcher
  - WorkOrderCallbackCredential
consumes:
  - "WorkOrderRepositoryProtocol (core.repository) — persistence and transactional state changes"
  - "SpaceRepositoryProtocol and SpaceAccessService — Space existence, membership, and OWNER authorization"
  - "SkillCollaboratorApprovalHandlerProtocol — Skill-owned manual review policy"
  - "SkillEditorRequestRepositoryProtocol — Skill-owned admission in the AUTO creation transaction and Grant write in the completion transaction"
  - "Qualified BCN HttpClient Plugin API — required friend-request approval callbacks"
consumed_by:
  - "adapters/http/openapi_v1/work_orders — public work-order and notification operations"
internal_dependencies:
  - agentclaw.community.core.bot_collaborator.manager_sync.notifications
  - agentclaw.community.core.base
  - agentclaw.community.core.repository
  - agentclaw.community.core.spaces
  - agentclaw.community.plugin_api
  - agentclaw.community.log
  - agentclaw.community.utils.avernet_tenant_guard
  - agentclaw.community.utils.env_utils
  - agentclaw.community.utils.work_no
```

### Change impact

Event values, statuses, titles, and content templates are persisted public
semantics. Rename or wording changes require coordinated client and data
compatibility review. Approval state and result-notification creation are one transaction and
must not be split across best-effort writes. For AUTO, local Space/Bot/Skill
business writes join the same approval-and-notice transaction. Registered
external decision callbacks run before local AUTO completion; callback failure
records FAILED and a failure notice together. If a callback has already
succeeded but local persistence fails, the work order remains PROCESSING for
reconciliation rather than falsely reporting FAILED. An external side effect
cannot be rolled back by the local database transaction.
Unregistered event types keep the existing local-only approval behavior.

## AUTO events and result recipients

Both `POST /openapi/v1/bots/work-orders/events` and
`POST /api/v1/work-orders/events` accept MANUAL, AUTO and NOTICE events.
Existing authentication and user delegation remain required. There is no new
module whitelist or AUTO authorization check: callers must validate business
eligibility before choosing AUTO. A delegated user can also request AUTO on
these routes, so deployments must control access at the business/gateway boundary
if direct user-selected AUTO is undesirable.

MANUAL requires `approver_user_ids` and empty `recipient_user_ids`. AUTO uses only
nonempty `recipient_user_ids` for success/failure notices; IDs are trimmed, blanks
removed and duplicates removed before writing. `approver_user_ids` is ignored
for AUTO; send `[]`. No human approver row, task or approval right is created.
NOTICE still requires recipients and no applicant or approvers.

```json
{
  "event_category": "APPROVAL",
  "approval_mode": "AUTO",
  "biz_type": "BOT_FRIEND",
  "biz_id": "friend-1",
  "event_type": "HUMAN2BOT_FRIEND_APPLIED",
  "applicant_user_id": "user-001",
  "approver_user_ids": [],
  "recipient_user_ids": ["user-001", "user-002"],
  "title": "Friend request",
  "biz_data": {"request_ids": ["request-1"]}
}
```

The applicant must match the authenticated/delegated user. A registered friend
callback runs before local completion and result notices. Return status is
APPROVED or FAILED, with the created result-notification IDs; local persistence
failure after confirmed remote success raises an error instead of a false FAILED.
Migration: old AUTO callers must move notification IDs from `approver_user_ids`
to `recipient_user_ids`; empty recipients fail before any write. There is no
legacy-field fallback.

Generic routes still reject Skill collaborator APPROVAL events in both modes;
use the Skill editor-request endpoint, which owns qualification, Grant and
duplicate checks. Its internal AUTO call now sends recipients in the same field.
Skill creation checks admission in its transaction; completion rechecks binding
and membership and commits the Grant, terminal state and notices atomically.
Skill NOTICE delivery remains available through generic events.

## Bot editor request auto-approval

`GET/PATCH /openapi/v1/bots/{bot_id}/editor-request-policy` reads or replaces
`{"auto_approve": true|false}`. Both operations require the authenticated Bot
Owner and an available Team Space Bot. Address the owner with `entity_id`
(`owner_id` remains a deprecated alias); omission selects the authenticated user.
The PATCH body requires a strict boolean and rejects unknown fields. App-only
callers without user delegation are refused.

The policy is stored in `ac_bots.ext.editor_request_auto_approve`, defaults to
false, and needs no database migration. Only the JSON boolean `true` enables
auto-approval. Writes merge with existing ext fields under the Bot row lock.
The legacy generic Bot update also prevents collaborators from setting this
Owner-only field; unrelated legacy updates keep their current behavior.

`POST /openapi/v1/bots/{bot_id}/editor-requests` retains its eligibility checks:
the applicant must be an active member of the Team Space, must not be the
Owner or an existing editor, and must have no pending request for this Bot.
The policy is evaluated inside the creation transaction under the same Bot row
lock. If enabled, creation atomically writes an APPROVED order, an ADMIN
collaborator relation, and a result NOTICE to the applicant. It creates no
pending approver or Owner approval notification. `biz_data.approval_mode="auto"`
and the review remark identify a policy decision; `reviewer_user_id` is null,
not a fabricated human approval. The collaborator operator is the authorizing
Bot Owner. The existing collaboration-changed callback runs after commit.
No edit lock is acquired, stolen, or released by this operation.

The existing response shape is unchanged (`work_order_id`, `work_order_no`,
`status`); clients must handle both PENDING and APPROVED. Existing pending
orders remain pending when enabled, and disabling the policy does not revoke
any granted access. Repeated applications continue to return the existing
already-pending/already-editor errors. Manual review and other work-order
business types keep their existing workflows.

Bot editor applications request ADMIN access. Manual approval, policy auto-approval
and generic AUTO completion grant ADMIN when creating a collaborator relation.
Existing collaborator relations and Space member roles are not rewritten; direct
editor CRUD defaults are unchanged.

## Stable enum contract

The following values are wire or persistence contracts. They must not be
renamed, repurposed, or written with values outside their enum without a data
and client compatibility plan.

| Contract | Values |
| --- | --- |
| Work-order status | `PENDING`, `PROCESSING`, `APPROVED`, `REJECTED`, `FAILED` |
| Approver status | `PENDING`, `APPROVED`, `REJECTED`, `CANCELLED` |
| Persisted notification category | `APPROVAL`, `NOTICE` |
| List category filter | `ALL`, `APPROVAL`, `NOTICE` |
| List query type | `PENDING_FOR_ME`, `INITIATED_BY_ME`, `PROCESSED_BY_ME` |
| Supported business type | `SPACE_JOIN`, `BOT_COLLABORATOR`, `SKILL_COLLABORATOR`, and `BOT_FRIEND`; Skill policy uses its business-owned handler, while callback-backed types use decision callbacks. |

`ALL` is a query-only filter and must never be persisted as a notification
category. `WorkOrderEventType` is also a persisted whitelist. Approval events are
classified centrally in `APPROVAL_EVENT_TYPES` and currently include
`SPACE_JOIN_APPLIED`, `BOT_COLLABORATOR_APPLIED`,
`SKILL_COLLABORATOR_APPLIED`, `HUMAN2BOT_FRIEND_APPLIED`, and
`BOT2BOT_FRIEND_APPLIED`; all reviewed/member-added/public-order events are
classified as `NOTICE`. `HUMAN2BOT_FRIEND_APPLIED` and
`BOT2BOT_FRIEND_APPLIED` opt into the BCN friend-decision callback; other
generic approval events remain local-only until explicitly registered.

## Space-join message templates

Space-join notification titles are persisted as stable, language-independent
`WorkOrderTitleKey` values. The OpenAPI adapter translates the known keys into
Chinese display copy and also recognizes historical Chinese titles and the
former `SPACE_JOIN APPROVED` / `SPACE_JOIN REJECTED` formats. Unknown custom
titles pass through unchanged.

`content` and `biz_data` have separate ownership. Notification `content` comes
from `ac_work_order_notification.content`; work-order `biz_data` comes from
`ac_work_order.biz_data`. Generic OpenAPI event inputs accept a JSON object or
`null`, persist the object as JSON text, and deserialize the same object on
read. The adapter never derives one field from the other or reconstructs either
payload based on `biz_type`. Historical scalar or plain-text rows are exposed
under `legacy_value` so the response remains object-shaped without losing data.

| Scenario | Event | Category | Persisted title | API title | Content |
| --- | --- | --- | --- | --- | --- |
| Waiting for review | `SPACE_JOIN_APPLIED` | `APPROVAL` | `SPACE_JOIN_PENDING` | `空间加入申请待审批` | `用户「{applicant_name}」申请加入空间「{space_name}」，请及时处理。` |
| Approved | `SPACE_JOIN_REVIEWED` | `NOTICE` | `SPACE_JOIN_APPROVED` | `空间加入申请已通过` | `你加入空间「{space_name}」的申请已通过。` |
| Rejected | `SPACE_JOIN_REVIEWED` | `NOTICE` | `SPACE_JOIN_REJECTED` | `空间加入申请未通过` | `你加入空间「{space_name}」的申请未通过。拒绝原因：{review_remark}` |
| Added directly | `SPACE_MEMBER_ADDED` | `NOTICE` | `你已被添加到空间` | `你已被添加到空间` | `你已被添加到空间「{space_name}」。` |
| Removed from Space | `SPACE_MEMBER_REMOVED` | `NOTICE` | `你已被移出空间` | `你已被移出空间` | `你已被移出空间「{space_name}」。` |

`SPACE_JOIN_REVIEWED` deliberately uses one event value for both outcomes;
the associated work-order status selects the approved or rejected template.

## Notification inbox and badge semantics

- `PENDING_FOR_ME` contains pending approval notifications and all unread notices.
  Notice membership does not depend on the associated order's status or presence.
- `PROCESSED_BY_ME` contains terminal approval notifications and read notices.
  APPROVED, REJECTED and FAILED are terminal for approvals; PROCESSING is not.
  Reading a notice moves it between inbox views on the next list query.
- `INITIATED_BY_ME` with ALL/APPROVAL is an order projection: one row per order,
  sorted by the order's modification time and ID, with no attached notification.
  The existing applicant / legacy AUTO reviewer visibility predicate is retained.
  The HTTP adapter consequently emits `WORK_ORDER_<id>`, APPROVAL, no notification
  ID/read state, and `can_approve=false`. Receiving/reading result notices cannot
  hide, duplicate or reorder an initiated order. NOTICE retains the existing
  recipient-scoped associated-notification projection.
- Compatibility: these are deliberate list-membership changes, not a schema
  migration. Consumers of initiated ALL must use work-order IDs for detail,
  rather than assuming every row has a notification ID. No frontend/BCN change
  is included. Unread result notices remain pending until read, including historical notices.
- `pending_approval_count` counts distinct pending work orders for which the
  recipient has a `PENDING` approver record.
- `unread_notice_count` counts unread `NOTICE` notifications only.
- `badge_count` is `pending_approval_count + unread_notice_count`; notification
  read state never removes a still-actionable approval from the badge. It is a
  reminder count, not the size of a filtered or paginated list. Approval counting
  additionally requires a pending approver record; that policy is unchanged.
- `unread_count` retains the historical count of all unread notifications for
  compatibility and is not used to calculate `badge_count`.

Approval remarks are optional: an omitted or blank value is persisted as
`null`. Rejection remarks remain required after trimming and are limited to 512
characters for both operations.

## OpenAPI error contract

The OpenAPI adapter maps only concrete work-order exceptions. Public messages
are fixed strings and must never be replaced with `str(exc)`, because exception
text may contain internal identifiers or implementation details. Access to a
notification belonging to another recipient is deliberately indistinguishable
from an absent notification.

| Business code | HTTP | Exception | Fixed public message |
| --- | --- | --- | --- |
| `400201` | 400 | `WorkOrderInvalidReasonError` | `Invalid application reason` |
| `400202` | 400 | `WorkOrderInvalidRemarkError` | `Invalid review remark` |
| `403201` | 403 | `WorkOrderAccessDeniedError` | `Forbidden` |
| `404201` | 404 | `WorkOrderNotFoundError` | `Not found` |
| `404202` | 404 | `WorkOrderNotificationNotFoundError` | `Not found` |
| `409201` | 409 | `WorkOrderAlreadyPendingError` | `A pending application already exists` |
| `409202` | 409 | `WorkOrderAlreadyProcessedError` | `The work order has already been processed` |
| `409203` | 409 | `WorkOrderApplicantAlreadyMemberError` | `Applicant is already a space member` |
| `409204` | 409 | `WorkOrderNoReviewerError` | `The space has no available approver` |
| `409205` | 409 | `WorkOrderJoinNotAllowedError` | `The space does not accept join requests` |
| `409208` | 409 | `WorkOrderSkillEditorRequestNotAllowedError` | `The Skill does not accept editor requests` |
| `409209` | 409 | `WorkOrderSkillApplicantAlreadyEditorError` | `Applicant already has Skill editor access` |
| `502201` | 502 | `WorkOrderCallbackError` | `Upstream work-order callback failed` |

The numeric codes and fixed messages are enums in
`adapters/http/openapi_v1/errors_work_order.py`; the centralized
`responses.py` mapping binds those values to concrete domain exceptions.
Changing either value is an OpenAPI contract change rather than an internal
refactor.

### AUTO completion and failure boundaries

- Repository creation returns PROCESSING, matching the stored row. The service
  does not claim that row again. The event-status PROCESSING value is internal;
  public event routes return terminal APPROVED/FAILED, never PROCESSING.
- Skill and non-Skill wrappers lock and validate the row before business writes,
  then share `_finish_auto_approval_in_session` for APPROVED/SYSTEM/timestamps
  and result notices. It uses the caller's transaction, does not commit, and
  contains no business callback or external message delivery.
- A registered friend callback must succeed before local completion. Confirmed
  callback success followed by a persistence error propagates without marking
  FAILED or dispatching again. AlreadyProcessed also propagates without a
  failure transition. Other execution failures retain the existing FAILED flow;
  a failed failure-record write propagates rather than fabricating success.
- Failure remarks contain a bounded exception-class summary, not raw external
  responses or credentials. No new exception column or schema migration is added.
- No new recovery job, request-level deduplication or distributed transaction is
  introduced. A crash after creation can leave PROCESSING. A remote success with
  a lost response may still be reported FAILED by the existing callback contract;
  a database commit error may have an uncertain outcome. Do not blindly retry
  an external callback on these errors. Notification rows do not prove delivery
  to an external messaging channel.

## Execution diagnostics and partial failure

Bot collaboration synchronization runs **after** the local transaction commits.
Generic AUTO, dedicated Bot policy AUTO, and manual approval use the same
best-effort post-commit hook: synchronization failure is logged but does not
replace the committed APPROVED response with an error. Database write errors
still propagate; this hook never owns persistence. There is no durable retry
queue or guarantee of eventual synchronization in this change.

Space-join approvals use the same best-effort post-commit posture via
``notify_space_membership_changed``: manual review submits a Space-membership
hint with the full refs the owner check already resolved, generic AUTO
completion submits the validated numeric refs (the background resolver
extends them while the Space exists), and neither a rejection, a terminal
FAILED state nor a finalize failure ever notifies.

A confirmed external decision followed by a local persistence failure raises
`WorkOrderLocalFinalizeError` (public HTTP 500, business code `500201`, fixed
message: external decision completed; local persistence failed; reconcile before
retrying). It retains the work-order ID and original cause internally. A known
transaction rollback leaves MANUAL PENDING or AUTO PROCESSING; a commit error
can have an uncertain outcome and must be checked, not assumed rolled back.
No FAILED transition or second remote call is attempted in this branch.
Already-processed conflicts retain their existing error semantics.

AUTO callback timeout still records FAILED per the existing local contract, but
the failure remark/notice explicitly says the external outcome may need
reconciliation. It does not claim the remote request was rejected. Retrying a
MANUAL order after remote success/local rollback can repeat the remote call:
BCN idempotency/state reconciliation must be confirmed separately.

Structured phase logs include `work_order_id` (null before creation), `phase`,
`outcome`, `env`, `duration_ms`, and exception class on failures. Relevant phases
are `event_create`, `auto_callback`, `auto_finalize`, `auto_failure_record`,
`manual_review`, `manual_callback`, `manual_finalize`, `bot_request_create`,
`bot_post_commit_sync`. Space manager hints use the shared manager-sync
notification diagnostics rather than a separate work-order execution phase. Creation completion
logs supply the newly allocated ID;
confirmed remote/local failures carry `remote_confirmed=true`, and failed Bot
sync carries `local_committed=true`. Completion logs describe database/API
phases, **not** external message delivery. Callbacks additionally log the remote
request ID and HTTP/business codes, but not response bodies, messages, review
reason bodies, credentials, or raw exception text in their diagnostic records.

Reproduce with `tests/community/core/repository/implementations/test_work_order_sqlite_journeys.py`:
real application services and repositories, per-test file-backed SQLite, only
external boundaries mocked. This is not a production-database concurrency test
or live BCN integration test.


List regression journeys: `tests/community/core/repository/implementations/test_work_order_sqlite_listing.py`
exercise real file SQLite, service listing and the HTTP list converter together,
including recipients, initiated orders, terminal/unread results, read transitions,
independent notices, pagination and environment isolation. External BCN execution
is simulated, not proof of remote delivery or remote idempotency.

The title converter now renders PROCESSING/FAILED explicitly (for example,
`好友申请处理中（自动审批）` / `好友申请处理失败（自动审批）`) rather than
falling back to `新的系统通知`. It uses the persisted approval mode; it does not
infer AUTO from a free-form remark or notification payload. The separate Bot
editor policy path's legacy mode-marker inconsistency remains excluded from this
list-only change and is captured by a characterization test.

See `SQLITE_VERIFICATION.md` for the end-to-end matrix and remaining failure risks.
