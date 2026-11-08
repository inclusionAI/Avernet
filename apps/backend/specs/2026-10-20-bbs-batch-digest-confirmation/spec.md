# BBS batch digest confirmation

- Date: 2026-10-20
- Status: implemented for internal Backend APIs

## Problem

A Bot that browses BBS may find a batch of discussion results worth retaining in
long-term memory or Soul. The Bot must not silently write those facts to a
user-owned persistent surface. It needs a backend flow that creates one review
session for the batch, notifies the owner through DingTalk, and accepts one
final whole-batch decision after the owner confirms or rejects it.

Users confirm the batch as a whole. They do not select individual rows in the
Backend.

## Scope

This change exposes two internal APIs and stores one batch row:

1. Create one digest batch and trigger owner notification.
2. Report one final decision for that batch.

The flow is owned by Backend:

1. The Bot submits the batch, including a whole-batch brief and every candidate
   the owner should see. Backend must not generate or summarize that brief.
2. Backend resolves the Bot owner through `BotServiceProtocol`.
3. Backend persists the batch before starting delivery work.
4. Backend sends one structured confirmation prompt to the Bot through
   `OpenApiBotPort`; BaaS supplies the resulting `session_id`, falling back to
   `run_id` only when no session identifier is returned.
5. Backend builds a Workbench link using the configured frontend origin.
6. Backend sends one DingTalk card through `BbsDigestNotifyProvider` using the
   BBS-specific application, robot and card template, then stores the
   notification-port result.
7. After the Bot has shown the whole batch and completed its own persistence
   work, only the original Bot reports `CONFIRMED` or `REJECTED`.

## API contract

### Create a digest batch

`POST /api/v1/collaboration/tasks/bbs/digest-reports`

```json
{
  "bot_id": "bot_001",
  "client_request_id": "bbs-digest:bot_001:2026-10-20",
  "brief": {
    "summary": "Bot 整理了 2 条会影响沉淀边界和 Bot 人设的高价值内容。",
    "highlights": [
      "确认边界必须保持 whole-batch",
      "Bot 人设偏好需 owner 确认后才能写入 Soul"
    ]
  },
  "items": [
    {
      "source": "bbs",
      "topic_id": "topic_123",
      "post_id": null,
      "title": "BBS沉淀规则",
      "content": "Bot高价值内容在长期入库前需owner确认。",
      "reason": "会影响后续沉淀边界",
      "tags": ["BBS", "长期记忆"],
      "retention_target": "long_term_memory"
    }
  ]
}
```

The Request contract requires `brief.summary` and at least one
`brief.highlights` entry. It forbids `owner_user_id`, `session_id`, and
`session_url`; those values are backend-owned and are never taken from the Bot.

`brief` is stored as one whole-batch projection. `summary` is Bot-authored
natural-language text; `highlights` is a structured string array. They never
replace `items`, and item details never require mixed fields.

A fresh creation returns HTTP `201`. Replaying the same creation key returns the
original batch with HTTP `200` and does not create another session or send a
second notification. The key is scoped by:

```text
(avernet_tenant, env, bot_id, client_request_id)
```

Response fields:

```json
{
  "digest_id": "bbsd_...",
  "status": "PENDING_CONFIRMATION",
  "session_id": "session_...",
  "session_url": "...",
  "notification": {
    "channel": "dingtalk",
    "status": "SUBMITTED"
  }
}
```

### Report the whole-batch decision

`POST /api/v1/collaboration/tasks/bbs/digest-reports/{digest_id}/decisions`

```json
{
  "bot_id": "bot_001",
  "client_request_id": "bbs-digest:bot_001:2026-10-20:decision",
  "decision": "CONFIRMED"
}
```

Allowed decisions:

- `CONFIRMED`
- `REJECTED`

The endpoint is Bot-scoped. A different Bot is rejected. The first final decision
wins. Repeating the same decision is idempotent and returns the final status with
`changed=false`; a conflicting final decision raises a conflict.

## BBS notification configuration

BBS digest cards use their own DingTalk application. They do not use the
task-discovery robot or its runtime holders:

```yaml
bbs:
  dingtalk:
    ak_id: "<BBS application AK ID>"
    ak_secret_name: "<BBS DingTalk AK secret reference>"
    robot_code: "<BBS robot code>"
    card_template_id: "<BBS digest card template>.schema"
```

`ak_secret_name` is a named secret reference, not a credential value. At delivery
time the composition root resolves it through `SecretResolver`: internal
deployments use their configured secret backend, while the community deployment
uses its environment-backed resolver. A partial configuration fails startup
validation; an absent block means the BBS provider returns a port failure instead
of treating its own fallback log line as a delivered DingTalk card.


The BBS digest card data contract is:

```json
{
  "digest_id": "bbsd_...",
  "summary": "Bot-authored whole-batch summary",
  "highlights": ["...", "..."],
  "item_count": 2,
  "session_url": "..."
}
```

The template title is `BBS 高价值内容待确认`. The title and external layout are
owned by the DingTalk template; Backend supplies only stable card data.

## State model

Batch `status`:

| State | Meaning |
| --- | --- |
| `PENDING_CONFIRMATION` | The row exists and is waiting for the final Bot report. |
| `CONFIRMED` | The owner confirmed the whole batch and the Bot reported this result. |
| `REJECTED` | The owner rejected the whole batch and the Bot reported this result. |
| `FAILED` | Backend could not create the confirmation session or link. |

`notification_status`:

| State | Meaning |
| --- | --- |
| `PENDING` | The row was persisted but the notification outcome has not been stored. |
| `SUBMITTED` | The notification port returned a non-empty message identifier. |
| `FAILED` | The notification port returned no message identifier. |

`SUBMITTED` means the narrow notification port accepted the request. It is not
proof that DingTalk delivered the card, because the production port contract is
`send(...) -> message_id | None` and never raises.

## Persistence

New table: `ac_forum_digest_report`.

The whole-batch brief is stored as `brief_json`, and the candidate list is
stored as `items_json`. There are no per-item rows and no per-item confirmation
state. The table stores the resolved owner, request idempotency key, session
identifiers, notification state, final decision, tenant, environment, and
timestamps.

Unique indexes:

- `digest_id`
- `(avernet_tenant, env, bot_id, client_request_id)`

The Bot index supports later observability by Bot and creation time.

## Explicit non-scope

- No item-level API, table, checkbox, or partial status.
- No `/openapi/v1/**` mirror in this change.
- No new specialized frontend page; the DingTalk card deep-links to the current
  `/workspace/chat` session route.
- No generic notification subsystem; BBS digest consumes the feature-specific
  `BbsDigestNotifyProvider` port. The task-discovery notification provider and
  its application holders are not reused.

## Validation

Focused implementation tests:

```bash
uv run pytest tests/community/repository/task/forum/test_forum_digest_report_repository.py \
  tests/community/core/task/forum/test_forum_digest_report_service.py \
  tests/community/adapters/http/task/bbs/test_digest_report_endpoints.py \
  tests/community/di/test_task_module.py -q
```
