# `agentclaw.community.core.bot_collaborator.manager_sync`

Backend→BCS bot manager set sync — the projection layer plus the sync
orchestration. Fixed value types live in `kernel/bot_manager_sync.py`
(pure, stdlib-only); this package owns the projector that turns
read-only `SyncFacts` into the desired `ManagerSnapshot`, and the
service that delivers one snapshot round to BCS.

## Direction of travel

```text
api BotManagerSyncProtocol          core projector               BcsManagersPlugin
   request_sync ──▶ core sync service ──▶ build_snapshot ──▶ replace_managers ──▶ BCS
                         │
                         └─▶ ManagerSyncReadRepository (read-only facts) +
                             ManagerSyncCoordinator (lease + in-flight markers)
```

- **Service → Plugin only.** Business code calls the Service API Protocol
  (`api/bot_manager_sync.py`) and the outbound plugins
  (`plugin_api/bcs_managers.py`, `plugin_api/manager_sync_coordination.py`);
  this package never imports a plugin implementation and never speaks HTTP
  or Redis.
- **The repository is read-only.** The sync path persists nothing about
  collaborators; `ManagerSyncReadRepository` exists to source facts, not
  to mutate them.
- **No task persistence.** Sync is coordinated through leases and
  markers in the shared store (`ManagerSyncCoordinator`) — there is no
  durable queue table, no retry log, and no task record owned here.

## Projection rules

`build_snapshot` keeps exactly the `admin`/`member` collaborators that
are still space-eligible, excluding the current owner, dedupes and
sorts the ids, and caps the roster at 1,000 (`SnapshotTooLarge` beyond).
An unknown role or an empty `user_id` is a hard error — never silently
projected as a member — and a Bot missing its stable identity raises
`SnapshotUnavailable`. The `digest` is the SHA-256 over canonical JSON
of key, target (`bcs_bot_id`, stable across owner handoffs), owner, and
the full id list, so senders can dedupe identical pushes.

## The sync round (`BotManagerSyncService.sync_once`)

One bounded round per key: claim lease → read facts (in a thread) →
`build_snapshot` → begin attempt → `replace_managers` with the remaining
budget → verify the reported total → finish attempt → release lease.
The safety postures, each pinned by a test:

- **Never replay an uncertain write.** `begin` returns uncertain, or
  the PUT reports `DeliveryFailure.UNKNOWN`, or the round is cancelled
  after the PUT was invoked ⇒ no send, report `UNKNOWN`, keep the
  in-flight marker; the *next* `sync_once` on that key is BLOCKED at
  claim time until the marker is explicitly resolved. `finish` is never
  called for an uncertain attempt.
- **Only claim-BUSY is DEFERRED.** Any other infrastructure ambiguity
  (coordinator unavailable, lease renewal refused) is FAILED — never
  dressed up as BUSY or DEFERRED.
- **NOT_SENT is the only retryable failure.** The attempt is finished
  first (marker cleared), then the round may re-read and send again
  with a **new** attempt id, inside the remaining budget, at most
  `SyncRunPolicy(max_attempts=3)` sends per round, backing off
  ~1s/2s + jitter bounded by the remaining budget.
- **False success is DRIFT.** A confirmed `total` that disagrees with
  the pushed roster is DRIFT; total 0 against an empty roster is only
  `ACKNOWLEDGED_EMPTY` — it proves nothing about target existence
  (Tasks 10/12 verify that from registration evidence).
- **Deadline hygiene.** A threaded SQL read cannot be cancelled — a
  late result is dropped (no begin, no send) once the remaining budget
  is smaller than a minimal send window.
- **Lease hygiene.** The lease is renewed only when its remaining
  window is smaller than the remaining budget + 2s, strictly inside
  the deadline; failed renewal means no send. The default 15s budget /
  30s lease renews zero times. The outer `finally` releases exactly
  this holder's lease and never touches a marker.
- **Holder uniqueness.** The service mints one holder string
  (`hostname:pid:uuid4[:8]`) per instance; the lease value IS the
  holder, so two processes (or two instances) must never share one.

`hold_owner_check(key)` plants the independent owner-hold marker in a
thread — never any HTTP — for the dispatcher's high-priority
`OWNER_HANDOFF` reason: marker set → BLOCKED, infrastructure failure →
FAILED with a WARNING.

## Diagnostics

`SyncDiagnostics.record` accepts only its own finite enums
(`SyncEvent`, `SyncOutcome`) and integer counts — no payloads, no
credentials — and emits one aggregatable `manager_sync event=…
outcome=…` log line per event (infrastructure failures at WARNING).
The `(event, outcome)` counter on the instance is the hook for a later
metrics exporter; there is no new external dependency.

## Context Boundary

```yaml
purpose: "Project bot collaborator facts onto the BCS manager set and drive one bounded, marker-safe delivery round."
provides:
  - build_snapshot
  - "Projection rules: explicit admin/member roles, owner excluded, deduped+sorted, 1000-id cap, stable digest"
  - BotManagerSyncService.sync_once / hold_owner_check
  - BotManagerSyncDispatcher.request_sync / request_space_sync (bounded, memory-only admission)
  - BotManagerSyncLifecycle (the dedicated worker loop owner)
  - ManagerSyncRepairService (first backfill / targeted compensation / marker recovery)
  - SyncRunPolicy; SyncDiagnostics
consumes:
  - ManagerSyncReadRepository     # read-only facts (constructor-injected)
  - BcsManagersPlugin             # full-replace delivery (constructor-injected)
  - ManagerSyncCoordinator        # lease + markers (constructor-injected)
internal_dependencies:
  - agentclaw.community.kernel.bot_manager_sync   # the fixed, transport-free type dictionary
  - agentclaw.community.kernel.lifecycle           # LifecycleBase, for the dedicated-loop participant
  - agentclaw.community.plugin_api.bcs_managers
  - agentclaw.community.plugin_api.manager_sync_coordination
  - agentclaw.community.core.repository.protocols.bot.manager_sync  # read-only facts contract
  - agentclaw.community.log
  - agentclaw.community.utils.avernet_tenant      # worker-side per-entry tenant scoping
```

### Change impact

The DTO/enum members are a frozen type dictionary shared with the
plugin Protocols and later sync tasks; renaming anything here rewrites
every consumer. The outcome mapping of `sync_once` is the dispatcher's
contract (e.g. DISPATCH on FAILED, human/AI resolution on UNKNOWN /
BLOCKED via marker recovery) — changing an outcome's meaning is a
cross-module contract change. The consumer conformance suites for the
plugin Protocols live under `tests/community/contracts/`
(`test_bcs_managers.py`, `test_manager_sync_coordination.py`); failure
isolation and resource bounds are pinned in
`tests/community/core/bot_collaborator/manager_sync/`
(`test_failure_isolation.py`, `test_resource_bounds.py`), and the
multi-client atomicity evidence runs against real Redis in
`tests/community/integration/test_manager_sync_redis.py`.