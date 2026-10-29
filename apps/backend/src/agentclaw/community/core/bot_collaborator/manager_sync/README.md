# `agentclaw.community.core.bot_collaborator.manager_sync`

Best-effort projection of Backend Bot collaborators onto the BCS manager set.
Business code submits a post-commit notification; this package owns the pure roster
projection, bounded local dispatcher, one retry-bounded sync round, dedicated-loop
lifecycle, diagnostics, and maintenance resubmission.

## Direction of travel

```text
business service
  └─ BotManagerSyncProtocol.request_*       (memory-only notification)
       └─ BotManagerSyncDispatcher           (local queue/dirty coalescing)
            ├─ ManagerSyncReadRepository     (fresh authoritative facts)
            ├─ build_snapshot                (admin + member projection)
            └─ BcsManagersPlugin             (full replacement in BCS)
```

Core remains transport-agnostic. HTTP and principal minting belong to the BCS plugin
and composition root. The repository is read-only; this feature writes no database
sync state and uses no task queue.

## Projection

`build_snapshot` includes explicit `admin` and `member` collaborators, excludes the
current Backend Owner before applying the 1,000-ID bound in both reader and
projector, applies the existing Team Space eligibility rule, deduplicates
and sorts IDs. An explicit Owner row does not consume manager capacity. The stable BCS identity uses the Bot's
`entity_id`, not its Owner.

Passport and device credentials remain admin-only; their filtered admin list is not
the BCS roster.

A fact-read, identity, eligibility, or projection failure sends nothing. It never
becomes an empty BCS replacement.

## Runtime semantics

- Notification is post-commit, nonblocking, and memory-only.
- The existing dedicated loop runs 4 workers.
- Capacity is 1,024 Bot entries plus pending Space hints, with running entries counted.
- Space expansion and repair page at 100 keys.
- One process coalesces duplicate keys and marks a running key dirty so it is read
  again after the current round.
- Every send attempt re-reads current facts.
- A round starts at most 3 sends within 15 seconds, with about 1s/2s plus jitter
  between retries.
- `NOT_SENT` and `UNKNOWN` delivery failures are retryable; `REJECTED` is permanent
  for the round. Exhaustion is `FAILED`.
- Shutdown cancellation ends local work. It does not create a marker or prove that a
  remote request did not finish.

There is no distributed coordinator. Cross-replica, out-of-order, concurrent, and
late writes are accepted. No eventual convergence is promised. Stale managers,
including revoked permissions, can remain until another successful notification or a
manual repair request.

Owner handoff is an ordinary refresh: this package does not transfer BCS ownership,
prove owners match, or retain an owner hold.

## Repair

`ManagerSyncRepairService` exposes only `request_bot` and bounded `request_page`.
Pages are at most 100 keys and preserve exact rejected keys. Repair submits fresh work
through the same notifier; it has no marker, clear, CAS, or roster payload API.

## Context Boundary

```yaml
purpose: Project current Backend Bot collaborator facts to BCS managers through a bounded, genuinely best-effort local runtime.
provides:
  - build_snapshot
  - BotManagerSyncService
  - BotManagerSyncDispatcher
  - DisabledBotManagerSync
  - BotManagerSyncLifecycle
  - DisabledBotManagerSyncLifecycle
  - ManagerSyncRepairService
  - DisabledManagerSyncRepair
  - SyncRunPolicy
  - SyncDiagnostics
  - notify_bot_manager_sync
  - notify_space_manager_sync
consumes:
  - BotManagerSyncProtocol
  - ManagerSyncReadRepository
  - BcsManagersPlugin
internal_dependencies:
  - agentclaw.community.api.bot_manager_sync
  - agentclaw.community.kernel.bot_manager_sync
  - agentclaw.community.kernel.lifecycle
  - agentclaw.community.plugin_api.bcs_managers
  - agentclaw.community.core.repository.protocols.bot.manager_sync
  - agentclaw.community.log
  - agentclaw.community.utils.avernet_tenant
```

### Change impact

Changes to projection rules affect BCS authorization for every Bot collaborator.
Changes to retry or dispatcher semantics affect the duration and likelihood of stale
manager permissions but must never change the committed Backend business result. The
BCS plugin consumer contract is covered under `tests/community/contracts/`; runtime,
failure-isolation, resource-bound, repair, trigger, and projection tests live under
`tests/community/core/bot_collaborator/manager_sync/`.

## Notification diagnostics

Business callers share the two notification helpers after their writes succeed.
The helpers isolate only notifier exceptions, not database failures. Admission
records finite queued/coalesced outcomes with an observability-only `SyncReason`;
counters and their snapshots are thread-safe. Shutdown rejection is classified
as closed even when the submitted environment also mismatches.
