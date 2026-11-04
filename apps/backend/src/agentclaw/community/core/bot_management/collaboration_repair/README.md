# Collaboration repair

One-shot repair of one Bot's BCS collaboration metadata (profile,
ownership, managers, caller config) from committed Backend facts. This
package owns the read-only facts contract's Service API declaration and
the avatar projection shared with the daily registration/edit flows. It
is not an Owner-handoff path, not a scheduler, and not a durable
recovery system.

## Context Boundary

```yaml
purpose: Read-only repair facts for one Bot's BCS collaboration metadata (profile, ownership, managers, caller config) plus the one-shot repair Service API contract.
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
and all of their tests. The Service API is additive: nothing consumes it
until the repair service (a later task of this plan) implements the
Protocol against the same kernel vocabulary.

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
- Scope is exactly the four components declared by the kernel's
  `Component` type: `ownership`, `profile`, `managers`,
  `caller_config`. Nothing outside that set is created or extended here.
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
| `bot_id`, `entity_id` | immutable BCS address `<bot_id>:<entity_id>` |
| `bot_name` | profile `name` (missing saved value projects to `bot_id`, the same projection the daily BCN metadata path sends) |
| `bot_desc` | profile `summary` (missing saved value projects to `""`) |
| `ext` | avatar projection via the shared `avatar_from_record` helper |
| `owner_id` | current committed Owner (`owner_user_id`) |
| `call_type` | caller `enabled` for an established config |
| `caller_config_revision` | caller applicability + `source_revision` |

No other table is read, no mutator is called, the session closes before
any value is returned, and the revision is never bumped.

## Caller applicability rule

A saved `caller_config_revision > 0` marks an **established** caller
config (the writer in
`core/repository/implementations/identity/caller_identity.py` bumps the
column on every MCP/CLI caller-config mutation; `core/caller_identity/service.py`
then sends the bumped value as `source_revision` through the
`BcsCallerConfigSyncProtocol` declared in
`core/caller_identity/protocols.py`). A
revision of `0` means no caller config has ever been established, so
`RepairFacts.caller` is `None` — the only `None` meaning in this
contract.

An established config is always reported, enabled or disabled:
`CallerSnapshot.enabled` follows the saved aggregate call type
(`call_type == "caller"`), mirroring the existing domain rule
`enabled = bot_call_type is McpCallType.CALLER`, while
`source_revision` carries the saved revision unchanged. `enabled=False`
marks a valid **disabled** config that still syncs; it is never used as
the "not applicable" marker. An unknown saved `call_type` value raises a
facts error rather than parsing into a default payload.

## Failure semantics

- Facts failures propagate. A missing/deleted/out-of-scope Bot, an
  unresolvable stable identity (missing, blank or padded `bot_id` /
  `entity_id`), an unreadable saved avatar projection (illegal avatar
  field type or corrupt `ext` JSON), an unknown saved `call_type`, or
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
- Per-item outcomes are explicit. `RepairReport` carries one item per
  fixed component; `report_status` derives `succeeded` /
  `partial_failed` / `failed` from the items alone, and an empty report
  is rejected as a programming error because the service always emits
  the four items. An item's `error_code` is `None` when the item has no
  error of its own, so a code-less skipped/dependency item is valid.

## Request orchestration (`CollaborationRepairService`)

The service runs the plan's fixed order against one request-scoped
budget (`timeout_seconds` bound the whole repair; `clock` is injected
by the composition root): initial facts read → owner precondition →
`exists` probe → fresh ownership read + one `replace_ownership` → one
`update_profile` → fresh managers read + one `replace_managers` → `<=1`
`sync_caller` for an established config (disabled included). All three
reader calls run in `asyncio.to_thread`, so no network I/O observes an
open session; each send receives its current remaining budget, and the
outer `await` shares that same budget as a backstop.

Interpretation and stop rules (the service docstring is the authority):

- `OwnershipChange.owner_user_id is None` is the missing-Bot no-op —
  the item fails `collaboration_bot_not_found`, never success, and the
  rest of the request stops. A confirmation naming another Bot is
  `unknown`/`collaboration_response_malformed`; an owner that differs
  from the written snapshot is `failed`/`owner_drift`.
- `ManagerSetChange` has no missing marker (a missing Bot surfaces as a
  classified `BcsManagersError`); a returned change IS the
  confirmation. Roster re-verification is the background sync's drift
  diagnosis, not this contract.
- Any ownership anomaly stops profile/managers/caller
  (`skipped_dependency`). A profile failure continues except the
  vanished-target not-found code or an exhausted deadline; a managers
  failure does not stop the caller (an independent facet). Owner drift
  between the ownership write and the managers re-read never writes the
  stale roster: managers is `skipped_dependency`/`owner_drift` and an
  applicable caller follows as a plain `skipped_dependency`. An absent
  caller config stays `not_applicable` in every stopped tail.
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
and the one-shot repair needs that provider scope for its existence probe
and caller sync. A deployment without it is an `unavailable` deployment by
design: `CollaborationRepairFactory.open` refuses with
`RepairPreconditionError(collaboration_sync_unavailable)` → HTTP **503**
before a plugin is constructed or any write attempted.

The live acceptance stories
(`tests/community/acceptance/collaboration_repair/`) therefore drive the real
owner-scoped route and prove, from the live stores: the refusal and its
logged `provider-scope-unavailable` reason, zero BCS row creation for a
missing target, no partial writes to a drifted existing target, no enqueued
background work, the masked owner-scoped 404 for a non-owner, and — through
the same shared stack — the end-to-end avatar chain (Backend owner edit →
BCS profile write → V1 Group/Session list+get readback, including the
explicit `""` clear). The success-path report (four items, ownership /
profile / managers / `caller_config`), the metadata adapter and the
per-target `404 collaboration_bot_not_found` mapping are pinned by the
unit, contract-conformance and route suites; green repair execution requires
a deployment whose transport has provider scope (corp pre/prod-class
overlays), which is the deployment boundary recorded in the plan's
validation document.
