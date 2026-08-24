# `agentclaw.community.core.work_orders`

Owns work-order lifecycle, recipient-scoped notifications, fixed event-to-message
mapping, conditional approval transitions, and the atomic Space-join approval
unit of work.

## Context Boundary

```yaml
purpose: "Own approval work orders, recipient notifications, and transactional Space-join decisions."
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
consumes:
  - "WorkOrderRepositoryProtocol (core.repository) — persistence and transactional state changes"
  - "SpaceRepositoryProtocol and SpaceAccessService — Space existence, membership, and OWNER authorization"
consumed_by:
  - "adapters/http/openapi_v1/work_orders — public work-order and notification operations"
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.core.repository
  - agentclaw.community.core.spaces
  - agentclaw.community.plugin_api.models
  - agentclaw.community.utils.avernet_tenant_guard
  - agentclaw.community.utils.env_utils
```

### Change impact

Event values, statuses, titles, and content templates are persisted public
semantics. Rename or wording changes require coordinated client and data
compatibility review. Approval state and result-notification creation are one transaction and
must not be split across best-effort writes. The unified Service API does not
perform business-object mutation; the owning business module handles that
step according to its own transaction boundary.

## Stable enum contract

The following values are wire or persistence contracts. They must not be
renamed, repurposed, or written with values outside their enum without a data
and client compatibility plan.

| Contract | Values |
| --- | --- |
| Work-order status | `PENDING`, `APPROVED`, `REJECTED` |
| Approver status | `PENDING`, `APPROVED`, `REJECTED`, `CANCELLED` |
| Persisted notification category | `APPROVAL`, `NOTICE` |
| List category filter | `ALL`, `APPROVAL`, `NOTICE` |
| List query type | `PENDING_FOR_ME`, `INITIATED_BY_ME`, `PROCESSED_BY_ME` |
| Declared business type values | `SPACE_JOIN`, `BOT_COLLABORATOR`, and `SKILL_COLLABORATOR`; the unified Service API also accepts business-module-defined `biz_type` values. |

`ALL` is a query-only filter and must never be persisted as a notification
category. `WorkOrderEventType` is also a persisted whitelist. Approval events are
classified centrally in `APPROVAL_EVENT_TYPES` and currently include
`SPACE_JOIN_APPLIED`, `BOT_COLLABORATOR_APPLIED`,
`SKILL_COLLABORATOR_APPLIED`, `HUMAN2BOT_FRIEND_APPLIED`, and
`BOT2BOT_FRIEND_APPLIED`; all reviewed/member-added/public-order events are
classified as `NOTICE`. This phase implements the `SPACE_JOIN` handler.
`SKILL_COLLABORATOR` is currently supported for generic creation, persistence,
querying, and response pass-through; it does not add a Skill-specific approval
side effect.

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

`SPACE_JOIN_REVIEWED` deliberately uses one event value for both outcomes;
the associated work-order status selects the approved or rejected template.

## Notification inbox and badge semantics

- `PENDING_FOR_ME` contains pending approval notifications and unread notices.
- `PROCESSED_BY_ME` contains approved/rejected approval notifications and read notices.
- `pending_approval_count` counts distinct pending work orders for which the
  recipient has a `PENDING` approver record.
- `unread_notice_count` counts unread `NOTICE` notifications only.
- `badge_count` is `pending_approval_count + unread_notice_count`; notification
  read state never removes a still-actionable approval from the badge.
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

The numeric codes and fixed messages are enums in
`adapters/http/openapi_v1/errors_work_order.py`; the centralized
`responses.py` mapping binds those values to concrete domain exceptions.
Changing either value is an OpenAPI contract change rather than an internal
refactor.
