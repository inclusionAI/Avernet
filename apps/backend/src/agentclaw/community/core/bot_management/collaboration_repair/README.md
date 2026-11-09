# Collaboration repair

One-shot repair of one Bot's BCS collaboration metadata (profile,
ownership, managers) from committed Backend facts. This
package owns the read-only facts contract's Service API declaration and
the avatar projection shared with the daily registration/edit flows. It
is not an Owner-handoff path, not a scheduler, and not a durable
recovery system.

## Context Boundary

```yaml
purpose: Read-only repair facts for one Bot's BCS collaboration metadata (profile, ownership, managers) plus the one-shot repair Service API contract.
provides:
  - CollaborationRepairServiceProtocol
  - CollaborationRepairService
  - avatar_from_record
  - outbound_avatar_from_record
consumes:
  - CollaborationRepairReadRepository
  - OwnershipSyncReadRepository
  - ManagerSyncReadRepository
  - BcsOwnershipPlugin
  - BcsManagersPlugin
  - BcsCollaborationMetadataPlugin
internal_dependencies:
  - agentclaw.community.kernel.collaboration_repair
  - agentclaw.community.kernel.bot_manager_sync
  - agentclaw.community.kernel.bot_ownership_sync
  - agentclaw.community.core.repository.protocols.bot
  - agentclaw.community.core.bot_collaborator.manager_sync
  - agentclaw.community.core.bot_management.ownership_sync
  - agentclaw.community.plugin_api
  - agentclaw.community.log
```

### Change impact

The kernel DTOs, error codes and `report_status` rule live in
`agentclaw.community.kernel.collaboration_repair`; renaming a field there
ripples through every layer of this feature — the Service API Protocol,
the read repository contract and implementation, the metadata Plugin API
and all of their tests. The additive Service API and its HTTP adapter use this same kernel
vocabulary.

## Composition root (`di/`)

`CollaborationRepairService` is built per request; this Core package never
imports the composition root. `di/modules/collaboration_repair_module.py`
binds `CollaborationRepairFactory`, and its
`open(profile_headers) -> AsyncContextManager[CollaborationRepairServiceProtocol]`
is the only entry point an HTTP adapter uses: the adapter submits the already
filtered `Cookie` / `Authorization` context and a command, and never selects
an implementation.

- **Unavailable is explicit and early.** With the feature off
  (`bot_manager_sync.enabled`), ownership delivery off
  (`ownership_enabled` — repair always writes ownership, so a managers-only
  deployment cannot run it), no principal signing key, or no BCN provider
  credentials/scope for the current environment, `open` raises
  `RepairPreconditionError(collaboration_sync_unavailable)` **before**
  constructing a plugin or attempting any write.
- **One request owns its plugins.** Each `open` builds fresh
  `HttpBcsOwnershipPlugin` / `HttpBcsManagersPlugin` instances plus the BCN
  metadata adapter, and closes all three in `finally`. The background
  `ManagerSyncStack` instances belong to the worker loop and are never
  borrowed, so a pooled client is born and released on the loop that runs the
  request.
- **One budget, no knobs.** The request budget is the `timeout` passed to
  `BcnService` (stored as `_timeout`, 30s today); a non-positive value is
  refused as `invalid-budget` rather than running a zero-length request. There
  is no repair-specific configuration, retry policy, queue, or persisted
  delivery state.
- **Profiles.** Production binds the HTTP/BCN source; the `test`/`corp_test`
  columns (`di/modules/infrastructure/test/`) bind the recording
  implementations, so a local deployment needs no private service.

## Capability boundary

- Existing-only. Repair targets Bots that already exist on both sides;
  it never registers a missing Bot, never initializes version-0
  ownership, and never substitutes an implicit registration for
  explicit repair.
- Scope is exactly the three components declared by the kernel's
  `Component` type: `ownership`, `profile`, `managers`. Nothing outside that set is created or extended here.
- One bounded send per item with an explicit `timeout_seconds` budget.
  There are no retries, no queue, and no compensation: a component that
  could not be confirmed is reported, not silently retried into success.
- The Service API takes a single explicit `RepairCommand`
  (`SyncKey` + `actor_user_id`). There is no register/ensure entry point
  and no batch API in this package.
- The repair reports per-component outcomes and never rewrites a failed
  or unknown item into a success. `unknown` means the remote outcome is
  unconfirmed — it is never equal to "untouched", and never presented as
  success.

## Facts read by the repository

`CollaborationRepairReadRepository.read_facts(key)` opens one short
tenant-scoped session and reads the saved `ac_bots` row selected by the
exact `avernet_tenant` + `env` + `bot_pk` + `is_delete = 0` scope. Fields
consumed from the row:

| Column | Use |
| --- | --- |
| `id`, `avernet_tenant`, `env`, `is_delete` | scope + liveness filter |
| `bot_id`, `entity_id`, `bot_type` | established address via `bcn_bot_reference`: service entity reference, retained conversion reference, or the legacy personal/default Owner reference |
| `bot_name` | profile `name` (missing saved value projects to `bot_id`, the same projection the daily BCN metadata path sends) |
| `bot_desc` | profile `summary` (missing saved value projects to `""`) |
| `ext` | avatar projection via `avatar_from_record` and retained `bcn_provider_bot_ref` for converted service Bots |
| `owner_id` | current committed Owner (`owner_user_id`) |

No other table is read, no mutator is called, the session closes before
any value is returned, and the revision is never bumped.

## Caller configuration is outside repair scope

The repair command neither reads/validates caller configuration nor sends it
or includes it in the report. Existing MCP/CLI identity-mode updates continue
to synchronize caller configuration through their normal business path.

## Failure semantics

- Initial facts failures propagate. A missing/deleted/out-of-scope Bot, an
  unresolvable stable identity (missing, blank or padded `bot_id` /
  `entity_id`), an unreadable saved avatar projection (illegal avatar
  field type or corrupt `ext` JSON), or
  an SQL failure raise `RepairFactsError` with the finite
  `RepairFactsErrorCode` vocabulary. No case produces a default or
  empty payload, and no case sends anything to BCS.
- Preconditions fail before any send with `RepairPreconditionError` and
  the finite `RepairPreconditionErrorCode` vocabulary: the addressed Bot
  is missing on the delivery side (`collaboration_bot_not_found`), the
  sync runtime is unavailable, the Bot's owner changed so the command no
  longer names a stable target, or the repair deadline is exhausted.
- Delivery is classified, never wrapped: one unconfirmed metadata send
  raises `MetadataDeliveryError(kind, code)` with the stable
  NOT_SENT / REJECTED / UNKNOWN classification. HTTP status mapping
  belongs to the adapter, not to Core or kernel.
- Empty/blank summaries preserve BCS's existing summary. A valid onboard
  acknowledgement is nevertheless a successful profile repair; success does
  not imply that the remote summary was cleared. The recording Plugin models
  the same preservation and successful outcome. Actual delivery failures and
  malformed acknowledgements retain their existing classifications.
- Per-item outcomes are explicit. `RepairReport` carries one item per
  fixed component; `report_status` derives `succeeded` /
  `partial_failed` / `failed` from the items alone, and an empty report
  is rejected as a programming error because the service always emits
  the three items. An item's `error_code` is `None` when the item has no
  error of its own, so a code-less skipped/dependency item is valid.

## Request orchestration (`CollaborationRepairService`)

The service runs the plan's fixed order against one request-scoped
budget (`timeout_seconds` bound the whole repair; `clock` is injected
by the composition root): initial facts read → owner precondition →
`exists` probe → fresh ownership read + one `replace_ownership` → one
`update_profile` → fresh managers read + one `replace_managers`. All three
reader calls run in `asyncio.to_thread`, so no network I/O observes an
open session; each read await and each send uses the current remaining
budget. A timed-out reader may finish on its worker thread later, but its
result is discarded and cannot initiate another write.

Interpretation and stop rules (implementing design §7.3–7.5):

- `OwnershipChange.owner_user_id is None` is the missing-Bot no-op —
  the item fails `collaboration_bot_not_found`, never success, and the
  rest of the request stops. A confirmation naming another Bot is
  `unknown`/`collaboration_response_malformed`; an owner that differs
  from the written snapshot is `failed`/`owner_drift`.
- `ManagerSetChange` has no missing marker (a missing Bot surfaces as a
  classified `BcsManagersError(REJECTED, BOT_NOT_FOUND_CODE)`); a returned change IS the
  confirmation. Roster re-verification is the background sync's drift
  diagnosis, not this contract.
- Any ownership anomaly stops profile/managers (`skipped_dependency`). A
  profile failure continues except for a vanished target or exhausted deadline.
  Classified managers facts failures produce `failed` with their existing
  `RepairFactsErrorCode` and preserve completed ownership/profile outcomes.
  Initial facts and ownership validation failures before any write remain
  error envelopes. Unclassified exceptions and cancellation still propagate.
  Owner drift between the ownership write and managers re-read never writes
  the stale roster: managers is `skipped_dependency`/`owner_drift`.
- Deadline: tripping before the first item is a precondition
  (`repair_deadline_exceeded`, no report). Mid-execution, the current
  item takes `failed`/`delivery_timeout` when its send never started
  (never `unknown`) and `unknown`/`delivery_timeout` — or the delivered
  timeout code — when the send lost the race with the budget; every
  later applicable item is `skipped_dependency`.
- The `exists` probe precedes every item, so its delivery failures map
  to start preconditions: `collaboration_sync_unavailable` for the
  unavailable family, `repair_deadline_exceeded` for `delivery_timeout`
  (the API layer's 504 family); an adapter's gate precondition passes
  through unchanged.

## Live-coverage posture

The public-local singlebox stack ships **no BCN provider credentials** (the
`bcn` block of `application-singlebox.yaml` deliberately leaves them empty),
and the one-shot repair needs that provider scope for its existence probe. A deployment without it is an `unavailable` deployment by
design: `CollaborationRepairFactory.open` refuses with
`RepairPreconditionError(collaboration_sync_unavailable)` → HTTP **503**
before a plugin is constructed or any write attempted.

The coverage runner explicitly provisions a temporary Provider through BCS
HTTP, allowlists its ID in disposable runtime configuration, and installs an
acceptance-only production plugin source. Ordinary startup and the shared daily
BCN configuration remain credentialless. Credentials stay in a private runtime
file, outside the report artifacts and source tree.

The live acceptance stories (`tests/community/acceptance/collaboration_repair/`)
call the real owner-scoped HTTP route and verify profile restoration, manager
convergence, ownership confirmation, the three-item success report, missing-target
404 with zero creation, invalid-avatar refusal, and non-owner masking. The same
stack exercises avatar edits and explicit clears with V1 Group/Session readback.
Readers, projectors and delivery plugins remain production implementations;
there are no test-only calls into Core. Credentialless 503, classified failures,
deadline handling and metadata delivery remain covered by unit/contract
suites. See the acceptance README for fixture provisioning and isolation.

## Compatibility update — 2026-11-05

Repair reports now contain exactly three components rather than four; the
HTTP schema and Plugin API no longer expose caller repair. Blank summaries
are successful on a valid BCS acknowledgement and retain BCS Keep semantics.
See the canonical design and `repair-scope-update-plan.md` for this deliberate
contract change. The existing `/openapi/v1` route and normal caller APIs remain.
