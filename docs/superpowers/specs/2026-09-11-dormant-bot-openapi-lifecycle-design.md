# Dormant Bot OpenAPI Lifecycle Design

## 1. Background

AgentClaw currently exposes dormant-Bot lifecycle behavior through three
different surfaces:

- `POST /api/bots/{bot_id}/activate` for the existing page;
- `POST /api/internal/dormant/recycle-one` for operations;
- `POST /openapi/v1/bots/{bot_id}/activate` as a partially migrated public API.

The page and public activation routes already delegate to the same
`ActivateBotService`, but their authentication and response contracts differ.
There is no formal public recycle endpoint. The internal recycle operation also
reaches into private `DormantBotService` helpers, coupling the operation facade
to scan-specific notification and audit behavior.

This design publishes two Owner-only personal-Bot lifecycle operations while
preserving the existing page, internal operations, scheduled scan, and provider
integration contracts.

## 2. Goals

- Publish an Owner-only personal-Bot recycle endpoint under `/openapi/v1`.
- Complete the existing public activation contract under the same addressing
  and authorization model.
- Resolve every Bot by the exact `(bot_id, owner_id)` pair, including
  per-owner `default` Bots.
- Return the current lifecycle status and whether the request changed it.
- Keep recycle synchronous and activation asynchronous.
- Share each lifecycle operation's Core behavior across its old and new HTTP
  adapters.
- Preserve legacy request paths and response shapes.
- Exclude Teclaw from dormant scan, recycle, and reactivation behavior.

## 3. Non-Goals

- Adding `RECYCLING` or another durable lifecycle-operation status.
- Moving activation or recycle execution to `ac_task_queue`.
- Recovering an in-flight operation after a Backend restart.
- Adding a lifecycle reconciler or a shared restart/recycle/activate lock.
- Persistently retrying Passport freeze failures.
- Strengthening physical-provider release confirmation.
- Removing or deprecating the page or internal operations endpoints.
- Adding a runtime feature flag for the public endpoints.

These reliability gaps are recorded in section 14 and belong to a separate PR.

## 4. Domain Decisions

### 4.1 Recycling and dormant governance are different operations

Owner-initiated recycling explicitly asks the platform to stop the Bot. It does
not depend on inactivity, warning history, cooldown, the exact Bot whitelist,
or the protected-owner list. It writes no dormant notification.

Scheduled dormant governance continues to own inactivity classification,
warning notifications, cooldown decisions, and dry-run behavior.

### 4.2 Personal Bot collaboration does not grant lifecycle authority

Some personal Bots support collaborators through personal Coding templates,
an explicit member-management capability, or a Team Space. Recycle and
reactivate still require `PermissionLevel.OWNER`; `ADMIN` and `MEMBER`
collaborators receive the same masked 404 as an unknown Bot.

### 4.3 Teclaw is outside this lifecycle

Teclaw is a distributed external engine with a dedicated provision flow. The
current dormant candidate query and `recycle-one` fail to exclude it, while the
generic activation path does not recreate it through `TeclawProvisionService`.
That asymmetric lifecycle is unsafe. Teclaw is therefore excluded from every
dormant lifecycle entry point rather than only from the new HTTP adapter.

## 5. Public HTTP Contract

### 5.1 Addressing and authorization

Both mutations use the existing addressed-Bot model:

```text
user_id  = authenticated actor
owner_id = owner of the addressed Bot; defaults to user_id
bot_id   = Bot id within that owner's scope
```

Their public authorization rows are:

```python
Check(PermissionLevel.OWNER)
```

Their application admission rows are:

```python
AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT
```

Handlers declare `actor_id: UserIdDep` and `owner_id: OwnerIdDep`. The status
poll endpoint adopts the same optional `owner_id` query parameter and remains
Owner-only.

### 5.2 Recycle

```http
POST /openapi/v1/bots/{bot_id}/recycle
    ?user_id=<actor_id>
    &owner_id=<bot_owner_id>
```

Successful state-changing response:

```json
{
  "code": 200000,
  "message": "OK",
  "data": {
    "bot_id": "default",
    "owner_id": "168944",
    "status": "RECYCLED",
    "changed": true
  },
  "request_id": "trace-id"
}
```

Calling recycle for an already recycled Bot returns the same HTTP 200 and
`changed=false` without repeating resource release.

### 5.3 Activate

```http
POST /openapi/v1/bots/{bot_id}/activate
    ?user_id=<actor_id>
    &owner_id=<bot_owner_id>
```

Starting or observing an in-progress activation returns HTTP 202:

```json
{
  "code": 202000,
  "message": "Accepted",
  "data": {
    "bot_id": "default",
    "owner_id": "168944",
    "status": "REACTIVATING",
    "changed": true
  },
  "request_id": "trace-id"
}
```

An already active Bot returns HTTP 200, `status=ACTIVE`, and `changed=false`.
The existing optional `BotActivateResult.message` field remains for public
schema compatibility, but new clients do not depend on it. Because activate
predates this change, its new `owner_id` and `changed` properties remain
optional in the published schema while the server always populates them. The
new recycle response may require both properties.

### 5.4 Polling

```http
GET /openapi/v1/bots/{bot_id}/status
    ?user_id=<actor_id>
    &owner_id=<bot_owner_id>
```

Callers interpret status as follows:

| State | Meaning |
| --- | --- |
| `REACTIVATING`, `PENDING` | Keep polling |
| `ACTIVE` and `is_ready=true` | Reactivation completed |
| `FAILED` | Reactivation failed |
| `RECYCLED` | Reactivation failed before start and rolled back |

### 5.5 Errors

All public failures use the standard `ErrorEnvelope` and fixed English
messages:

| HTTP | Meaning |
| --- | --- |
| 403 | `user_id` does not match the authenticated principal |
| 404 | Bot absent, wrong owner, or caller below Owner permission |
| 409 | Unsupported Bot type/engine or invalid lifecycle state |
| 502 | Upstream device operation failed |
| 500 | Unclassified persistence or invariant failure |

The caller uses the status endpoint when it needs the current state after an
error; error responses do not expose it.

## 6. State Matrix

### 6.1 Recycle

| Current state | Result |
| --- | --- |
| `ACTIVE` | Execute recycle; return 200 `RECYCLED`, `changed=true` |
| `RECYCLED` | No-op; return 200 `RECYCLED`, `changed=false` |
| Any other state | Return 409 |

### 6.2 Activate

| Current state | Result |
| --- | --- |
| `RECYCLED` | Start activation; return 202 `REACTIVATING`, `changed=true` |
| `REACTIVATING` | No-op; return 202 `REACTIVATING`, `changed=false` |
| `ACTIVE` | No-op; return 200 `ACTIVE`, `changed=false` |
| `PENDING`, `FAILED`, or any other state | Return 409 |

`PENDING` is not treated as proof of activation because creation, restart, and
historical failure paths can all produce it and there is no durable operation
record in this scope.

## 7. Core Interfaces

The two operations remain separate cohesive services:

```python
@dataclass(frozen=True)
class BotLifecycleResult:
    bot_id: str
    owner_id: str
    status: str
    changed: bool


class ActivateBotService:
    def activate(
        self,
        *,
        bot_id: str,
        owner_id: str,
        owner_name: str | None = None,
    ) -> BotLifecycleResult: ...


class RecycleBotService:
    def recycle(
        self,
        *,
        bot_id: str,
        owner_id: str,
        owner_name: str | None = None,
    ) -> BotLifecycleResult: ...
```

Neither interface accepts transport/governance concerns such as `actor_id`,
`dry_run`, `source`, or an arbitrary operations reason.

## 8. Core Behavior

### 8.1 Recycle order

```text
load exact Bot
  -> validate personal managed-cloud capability
  -> reject Teclaw
  -> apply state matrix
  -> stop_bot
  -> set RECYCLED
  -> best-effort freeze_agent_passport
  -> return result
```

Passport freeze stays after device release. Freezing first could leave an
otherwise active Bot without a usable credential when resource release fails.

### 8.2 Activate order

```text
load exact Bot
  -> validate personal managed-cloud capability
  -> reject Teclaw
  -> apply state matrix
  -> set REACTIVATING
  -> start existing daemon background thread
  -> return result
```

The thread retains the current sequence:

```text
unfreeze_agent_passport
  -> query_token
  -> start_bot
```

Unfreeze failure restores `RECYCLED`. A token or start failure performs a
best-effort Passport freeze and then restores `RECYCLED`.

## 9. Teclaw Exclusion

Filtering is defensive at both the entry and Core boundaries:

- scheduled candidate construction excludes Teclaw before activity checks;
- `external_input` marks a Teclaw row processed and writes
  `check_result=unsupported`, `action_taken=skipped`, `source=external_input`;
- `RecycleBotService` rejects Teclaw for internal and public callers;
- `ActivateBotService` rejects Teclaw for page, internal, and public callers.

Scheduled filtering emits an aggregate count and does not create one audit row
per excluded Teclaw Bot.

## 10. Audit and Notification

An Owner-initiated public recycle writes no dormant notification.

On a real `ACTIVE -> RECYCLED` transition, a narrow audit service writes:

```text
run_id        = OpenAPI request_id
bot_id        = target bot_id
owner_id      = resolved owner_id
check_result  = manual
action_taken  = recycled
source        = openapi
days_inactive = null
dry_run       = 0
```

An idempotent `RECYCLED -> RECYCLED` call writes no business audit row; the
OpenAPI access log still records the request. Audit failure is logged but does
not turn an already completed recycle into an HTTP failure.

Activation retains its structured logs and OpenAPI request trace; it does not
write to the dormant check audit table.

## 11. HTTP and File Ownership

The existing 1,500-line `openapi_v1/bots/router.py` does not grow. Public
dormant routes move to:

```text
adapters/http/openapi_v1/dormant/
  __init__.py
  router.py
  schemas.py
```

The dormant sub-resource router mounts before the broad bots router and uses
`PublicAPIRoute`. Authorization and admission retain their central exact route
inventories.

## 12. Compatibility

- `POST /api/bots/{bot_id}/activate` keeps its path and `ApiResponse` shape.
- The page adapter translates `BotLifecycleResult` into its existing Chinese
  status message.
- `POST /api/internal/dormant/recycle-one` keeps Bearer authentication,
  `dry_run`, `reason`, notification, and `manual_ops` audit behavior.
- Scheduled scan and external input keep their current notification and dry-run
  semantics except for the newly enforced Teclaw exclusion.
- Existing OpenAPI activate callers may omit `owner_id` as before.
- The public activate contract changes its state-changing success from HTTP 200
  to HTTP 202; this is done before treating the endpoint as formally published.

## 13. Acceptance

- Personal ARCA and BaaS Bots complete recycle and reactivation flows.
- Two owners' `default` Bots are always resolved by the exact owner pair.
- Owner omission and explicit `owner_id` produce the same target.
- `ADMIN`, `MEMBER`, and unrelated callers receive masked 404 responses.
- Application grants cannot be redirected to a different owner.
- Desktop, service, and Teclaw Bots receive 409 from both public mutations.
- Legacy page and internal endpoint response contracts remain stable.
- Internal `dry_run`, notifications, and audits remain stable.
- OpenAPI authorization/admission inventories and generated Gateway schema
  contain the new and relocated routes.
- OCB Corp Passport and DeviceService bindings remain compatible.

## 14. Known Limitations and Follow-up

A separate reliability change must design and implement:

- durable activation/recycle operation ownership;
- a `RECYCLING` or equivalent transitional state;
- process-restart recovery and stale-operation takeover;
- Passport freeze compensation;
- shared lifecycle mutual exclusion with restart;
- provider release confirmation that distinguishes absent resources from
  transient upstream failures.

Until then, activation remains an in-process daemon task and recycle retains
the existing partial-execution windows. These are accepted limitations, not
completed capabilities.

## 15. Delivery

The implementation is delivered in two PRs:

1. Avernet `dev`: Core services, adapters, authorization, Teclaw filtering,
   tests, documentation, and generated OpenAPI schema.
2. OCB `dev`: merged Avernet gitlink, synchronized Gateway schema, and Corp DI
   compatibility validation.

No tcauthmng change and no runtime feature flag are required.
