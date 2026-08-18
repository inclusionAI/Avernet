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
  - WorkOrderStatus
  - WorkOrderBizType
  - WorkOrderEventType
  - NotificationCategory
  - WorkOrderQueryType
  - WorkOrderItemType
  - WorkOrderMessageTitle
  - WorkOrderMessageContent
  - WorkOrderNotificationDetail
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
compatibility review. Approval, membership creation, and result-notification
creation are one transaction and must not be split across best-effort writes.

## Stable enum contract

The following values are wire or persistence contracts. They must not be
renamed, repurposed, or written with values outside their enum without a data
and client compatibility plan.

| Contract | Values |
| --- | --- |
| Work-order status | `PENDING`, `APPROVED`, `REJECTED` |
| Persisted notification category | `APPROVAL`, `NOTICE` |
| List category filter | `ALL`, `APPROVAL`, `NOTICE` |
| List query type | `PENDING_FOR_ME`, `INITIATED_BY_ME`, `PROCESSED_BY_ME` |
| Supported business type | `SPACE_JOIN` |

`ALL` is a query-only filter and must never be persisted as a notification
category. `WorkOrderEventType` is also a persisted whitelist. This phase
implements only the `SPACE_JOIN` handler; the remaining event values reserve
the names defined by the system design for later business handlers.

## Space-join message templates

Titles and content are generated only from `WorkOrderMessageTitle` and
`WorkOrderMessageContent`, then the final rendered text is stored on the
notification row. Clients display the stored text directly.

| Scenario | Event | Category | Title | Content |
| --- | --- | --- | --- | --- |
| Waiting for review | `SPACE_JOIN_APPLIED` | `APPROVAL` | `空间加入申请待审批` | `用户「{applicant_name}」申请加入空间「{space_name}」，请及时处理。` |
| Approved | `SPACE_JOIN_REVIEWED` | `NOTICE` | `空间加入申请已通过` | `你加入空间「{space_name}」的申请已通过。` |
| Rejected | `SPACE_JOIN_REVIEWED` | `NOTICE` | `空间加入申请未通过` | `你加入空间「{space_name}」的申请未通过。拒绝原因：{review_remark}` |
| Added directly | `SPACE_MEMBER_ADDED` | `NOTICE` | `你已被添加到空间` | `你已被添加到空间「{space_name}」。` |

`SPACE_JOIN_REVIEWED` deliberately uses one event value for both outcomes;
the associated work-order status selects the approved or rejected template.

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
