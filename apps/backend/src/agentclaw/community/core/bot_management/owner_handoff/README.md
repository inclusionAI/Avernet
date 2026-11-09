# Owner handoff

## Context Boundary

```yaml
purpose: Coordinate durable responsibility transfer without changing Bot resource identity.
provides:
  - OwnerHandoffServiceProtocol
  - OwnerHandoffService
  - OwnerHandoffPolicy
  - OwnerHandoffTask
  - assert_bot_mutable
consumes:
  - BotOwnershipSyncProtocol
  - OwnerHandoffRepositoryProtocol
  - PassportPlugin
  - StaffDeptPlugin
  - RuntimePassportTokenUpdaterProtocol
  - BotRepository
  - TaskQueueService
  - BotManagerSyncProtocol
  - SpaceServiceProtocol
  - SpaceAccessServiceProtocol
internal_dependencies:
  - agentclaw.community.api.bot_ownership_sync
  - agentclaw.community.core.bot_management.ownership_sync.notifications
  - agentclaw.community.core.bot_collaborator.manager_sync.notifications
  - agentclaw.community.api.bot_manager_sync
  - agentclaw.community.core.base
  - agentclaw.community.core.bot_management.owner_handoff
  - agentclaw.community.core.execution_identity.protocols
  - agentclaw.community.core.repository.protocols
  - agentclaw.community.core.spaces
  - agentclaw.community.core.task_queue
  - agentclaw.community.plugin_api
  - agentclaw.community.log
  - agentclaw.community.kernel.bot_manager_sync
  - agentclaw.community.kernel.lifecycle
  - agentclaw.community.utils
```

### Change impact

Service API callers are the authenticated HTTP command adapter and TaskQueue
handler. The Passport Plugin API grows optional correlated reissue, capability
negotiation and generation-scoped retirement; corporate OCB/tcauthmng must be
upgraded together. Public local Passport explicitly reports unsupported, rather
than pretending mock tokens prove a real credential cutover.

## Contract v1

Only a current Owner or configured super administrator initiates a non-default
service Bot transfer. Require a directory identity record; directory data does
not certify active employment. Idempotency keys identify one actor/target request
per scoped Bot. Get/retry expose only non-secret journal facts to the actor,
previous/new Owner or an administrator. Status access grants no Bot resource
permission. Only queued, unclaimed operations can cancel.

QUEUED → REISSUING → WAITING_AUTHORIZATION → UPDATING_RUNTIME →
RETIRING_OLD_CREDENTIALS → COMMITTING_OWNER → SUCCEEDED.

Persist reissue intent before RPC; uncertain outcomes remain frozen for remote
operation-ID reconciliation, never automatic repeat issuance. PENDING AAC is
not success. Runtime injection must acknowledge at least one binding and all
existing updater targets must succeed. Retire previous generations while
preserving the selected authorized credential, then commit Owner/name and remove
the old Owner collaborator row atomically. Other collaborator rows, historical
actors, App Grants, whitelist, personal resources and resource locator columns
are unchanged. Do not invoke the usual Passport admin-sync path, which could
mint another credential generation.

Existing ACTIVE independent executors keep their identity and binding; only
credential references change. Without such a binding execution follows the new
Owner and no independent binding is created. Reissue is still needed in both
cases to replace Owner administration on the credential generation.

The Bot ext marker and stable collaboration lock make the Bot read-only at the
shared intercepted write gates. Dedicated editor-request and execution-identity
repositories recheck under the same Bot row lock. Outstanding collaborator
orders block initiation/commit. Opaque friend-request IDs cannot identify a Bot:
block pending friend approvals assigned to the old Owner and all processing
friend approvals conservatively. Existing history is never reassigned.

## Recovery and release

`user_config.owner_handoff` accepts boolean `enabled` and `recovery_enabled`, both
false by default. Enabling initiation requires recovery; recovery requires the
TaskQueue worker. Schema precedes both. Disabled defaults do not query this
new table. Disable initiation while keeping recovery enabled to drain operations.
An active operation cannot be rolled back by disabling configuration or reverting
SQL: remote credential revocation is irreversible; reconcile forward.

The worker lease and journal survive process restarts. Enqueue is a separate
transaction; a boot/periodic reconciler re-enqueues nonterminal journals,
including other tenants explicitly carried in task payloads. Only that recovery
inventory reads journals across tenants; execution and Bot reads remain scoped.
Transport/provider exception details, tokens and confirmation URLs are not stored
in the journal. Authorization may remain pending and require existing external
AAC consent tooling; the journal does not expose credentials to an administrator.

Production initiation remains disabled pending B8 real-device/AAC acceptance,
verification of provider revocation idempotency and ALC→AEC invalidation,
long-lived connection disposition, complete concurrent mutation acceptance,
Frontend and SecBaaS integration. Unit doubles cannot establish those facts.

## Space assignment during handoff

Legacy NULL `space_id` remains NULL. A Bot assigned to its current Owner's
PERSONAL Space moves to the new Owner's PERSONAL Space. The coordinator prepares
that local Space and its ADMIN membership through SpaceService before freezing
(creating it if absent, with no remote SC team request). A failed/cancelled
handoff may leave that ordinary personal Space initialized, but never moves the
Bot early. SC mapping remains governed by normal Space initialization/repair.
TEAM assignment stays unchanged and requires the target Owner's ACTIVE
membership; handoff does not grant membership or move a team Bot into a personal
Space. Missing/deleted/mismatched spaces fail closed.

Admission checks these relations before creating the journal or freezing the
Bot. Final commit locks and rechecks the source, target and ACTIVE membership,
then writes Owner/name/space and SUCCEEDED in the same transaction. A membership
removed during credential work blocks completion and keeps the recoverable
operation frozen; restore membership and retry. Existing manager-sync submission
after commit observes both the new Owner and Space. Bot/entity keys and resource
locators are unchanged. This changes no schema or frontend protocol, and does
not backfill already-SUCCEEDED handoffs; use the existing authorized Bot Space
assignment API for those. No new quota rule is introduced by responsibility
handoff; the ordinary explicit Space-move API retains its own quota checks.

MCP legacy-key compatibility: before the final owner switch, the finish
transaction moves uniquely attributable current-owner Bot MCP overrides to the
existing immutable entity coordinate, consuming the old aliases. Existing
canonical values win in full. This repairs legacy configuration keys without
changing Bot identity, permissions, user defaults or the handoff feature flag.
Normalization and owner/journal/freeze changes roll back together on failure.

## Post-commit BCS ownership notification

`OwnerHandoffService.step()` submits independent managers and Owner hints only
after `finish()` successfully commits `SUCCEEDED`. The two helpers isolate only
notification exceptions: persistence failures still propagate. Initiation,
authorization waiting, cancellation, failed commit, and reads/retries of an
already-completed operation do not create another completion notification.

The additive v1 `BotOwnershipSyncProtocol` is a memory-only Service API consumed
by this coordinator, not a BCS transaction participant. Its helper is owned by
[ownership_sync](../ownership_sync/README.md). The existing managers-only API
retains its semantics. `bot_manager_sync.ownership_enabled` controls delivery;
`owner_handoff.enabled` controls initiation and remains unchanged. Delivery can
be lost after commit, leaving old BCS Owner permissions active until corrected.
The handoff's durable journal is not an ownership-delivery outbox.

## Expired historical passport compatibility

An authorized service-Bot handoff first uses the existing valid-passport query.
Only if no credential ID is returned does QUEUED request `query_handoff_passport`.
This metadata-only Plugin API reads a non-deleted expired passport; it never
returns a usable token or makes an expired certificate valid. Missing history
still yields CURRENT_PASSPORT_REQUIRED before recording remote issuance intent.

The corp adapter negotiates protocol v1 plus `expired_passport_handoff: 1` before
calling `queryOwnerHandoffPassport`. A v1 server without this additive capability
continues to serve valid-certificate handoffs and is never sent the new RPC.
The old credential ID is still persisted and supplied as expectedCredentialId.
The server must reject a newer credential that appeared after the snapshot and
must preserve historical resource/admin configuration, replacing only the Owner
admin and execution identity according to the existing handoff rules.

Deploy tcauthmng first, then the corp adapter and community Backend together.
No schema or frontend changes are required. Existing QUEUED operations resume
through the normal recovery worker; no new request_key or manual Owner update is
needed. REISSUING and later phases keep their valid-certificate generation checks.
The fallback does not support deleted certificates or Bots with no recoverable
history, and it never revokes the shared Agent through revokeIdentityCredential.

## Direct ARCA runtime acknowledgement

Direct ARCA header updates acknowledge one sandbox with the exact boolean True;
BaaS/teclaw updates return a physical-device list. BotService preserves this
provider distinction when producing runtime device_count. An explicit ARCA True
counts as one acknowledged target without inventing a physical-device list.
False, empty lists, exceptions, truthy non-booleans and boolean results from
other providers do not establish an ARCA acknowledgement. The handoff runtime
fence remains unchanged: every returned binding must confirm at least one target.

A task already at UPDATING_RUNTIME/RUNTIME_ACK_REQUIRED can retry the same
operation after the Backend fix is deployed. It reuses the selected credential,
updates runtime targets again, then commits the Owner and releases its maintenance
lock through the normal finish transaction. No direct lock/journal edits or
credential reissue are needed for this receipt-format compatibility repair.
