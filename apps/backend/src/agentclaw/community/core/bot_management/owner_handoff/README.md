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
  - OwnerHandoffRepositoryProtocol
  - PassportPlugin
  - StaffDeptPlugin
  - RuntimePassportTokenUpdaterProtocol
  - BotRepository
  - TaskQueueService
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.core.bot_management.owner_handoff
  - agentclaw.community.core.execution_identity.protocols
  - agentclaw.community.core.repository.protocols
  - agentclaw.community.core.task_queue
  - agentclaw.community.plugin_api
  - agentclaw.community.log
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
