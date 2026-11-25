# bot_dormant

Personal managed-cloud Bot dormancy, recycling and reactivation. Teclaw,
desktop and service Bots are outside the managed dormant lifecycle.

## Context Boundary

```yaml
purpose: "Personal Bot dormant scanning, recycle safety and reactivation admission."
provides:
  - BotDormantRecycleOperationProtocol
  - BotDormantRecycleServiceProtocol
  - BotDormantActivateServiceProtocol
  - ActivateBotService
  - DormantBotService
  - RecycleOperations
  - RecycleLifecycle
  - ManagedRecycleRuntime
consumes:
  - PassportPlugin
  - DatabasePlugin
  - CachePlugin
  - HttpClient
  - BotRepository
  - BotRecycleRepositoryProtocol
  - EngineProvisioningStrategy
  - RecycleServices
  - TaskQueueService
  - BaasRecycleRuntime
internal_dependencies:
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.common_config
  - agentclaw.community.core.service_bot
  - agentclaw.community.core.task_queue
  - agentclaw.community.core.repository.protocols.bot
  - agentclaw.community.kernel.lifecycle
  - agentclaw.community.plugin_api
  - agentclaw.community.log
  - agentclaw.community.utils
```

### Change impact

Confirmed-fastdisk AICoding Bots use durable `bot.recycle` admission, read-only
operation queries and guarded activation, retaining the existing release success criterion. Non-coding
Bots and AICoding Bots without confirmed fastdisk use the original synchronous
recycle path and do not create a queue/journal state.
`recycle_task.py` is the single worker; `recycle_service.py` owns the facade and
pinned release runtime. Neutral journal/contracts live in the existing
`bot_management/engines/restart_contract.py`; no extra table or state machine is added.

Only the backup precondition dispatches an engine strategy. AICoding owns the
existing live-mount probe, helper, failure switch, receipt policy and backup budget;
default engines do none of those operations. Shared dormant code must not import
`engines.aicoding` or interpret its backup configuration. Scheduled scans retain
the existing synchronous `recycle()` path. Real internal ops and OpenAPI requests
share `submit()`, which selects synchronous recycle or durable async admission;
dry-run remains a preview without queued work.

Blocking backup failure or release failure changes only the operation to FAILED,
not the serving Bot state. A successful release call proceeds directly to completion;
there is no additional physical-status polling. In-flight operations veto
conflicting lifecycle operations for every engine. Completion/audit atomicity
belongs to the recycle repository, not HTTP adapters. All three Bot types use the
same activation claim/rollback while preserving existing provisioning.

### Internal manual recycle

- `POST /api/internal/dormant/recycle-one` keeps the existing JSON inputs:
  required `bot_id`/`owner_id`, `dry_run=true` by default, and optional `reason`.
  There is no caller-selected async parameter. The existing Bearer auth is unchanged.
- With `dry_run=false`, only confirmed-fastdisk AICoding Bots use the shared async
  worker. A new or duplicate unresolved operation returns `202` and `{ok: true, data: ...}` with
  `bot_id`, `owner_id`, current `status`, `changed`, `operation_id`, `phase`,
  `dry_run=false`, and `run_id` as an alias of `operation_id`. This is admission,
  not completion. Already RECYCLED without unresolved work returns `200`,
  `changed=false`, and null operation/run identifiers; it starts no new work.
  Preview retains its existing `200` result, notification preview and audit.
- `GET /api/internal/dormant/bots/{bot_id}/recycle-operations/{operation_id}`
  requires `owner_id` as a query parameter and the same internal authentication.
  Its `{ok: true, data: ...}` uses the shared projection: `bot_id`, `owner_id`,
  `operation_id`, `bot_status`, `phase`, `error_code`, `message`, `warning`.
  `200` means the read succeeded, including `FAILED`; new tasks finish as
  `SUCCEEDED` or `FAILED`. Legacy `NEEDS_CONFIRMATION` records remain readable
  but are not replayed or repaired. The GET never advances
  work. Only the latest handle is retained; missing, replaced or out-of-scope
  Bot/owner/environment/tenant handles return `404`.
- Manual success audit and recycle notification are inserted in the completion
  transaction, only after the release call succeeds. Existing content and daily
  suppression are preserved, including suppression by dry-run/cancelled rows.
  Duplicate submissions retain the first submission's source/reason; an
  OpenAPI-started operation does not acquire a manual notification on retry.
- Admission failures are synchronous: missing Bot `404`, incompatible/busy
  lifecycle `409`, unavailable durable acceptance `503`, unsupported Bot `400`.
  After `202`, use the operation's phase/error/warning instead of the HTTP code.
  Backup blocking follows the existing AICoding restart switch; activation
  interfaces, scheduled scans and authentication are not changed here.

See `apps/backend/docs/openapi-v1/README.md` and
`apps/backend/docs/plans/2026-11-07-openapi-recycle-async.md` from repository root for contract,
rollout ordering and limits; no new table is required.
