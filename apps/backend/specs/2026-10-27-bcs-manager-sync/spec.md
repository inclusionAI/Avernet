# Backend → BCS bot manager best-effort sync specification

- Status: implemented; local regression passed, remote CI/live validation pending
- Feature baseline: `be5b69b21`
- Merge target: `dev`; release work is already merged, so no release/dev split remains
- Design: `/home/admin/workspace/docs/avernet/superpowers/specs/2026-10-26-backend-bcs-manager-sync-design.md`
- Operations: `apps/backend/docs/bot-manager-sync-operations.md`

## Problem

Backend owns Bot collaborators while BCS consumes a manager set. The projection must
follow committed Backend changes without adding BCS latency or failure to the business
transaction. The product accepts best-effort delivery and does not require distributed
ordering, a durable outbox, or eventual consistency.

## Contract

### Source of truth

A fresh projection contains every explicit `admin` and `member` collaborator that is
still eligible under the existing Team Space policy, excluding the current Backend
Owner. IDs are deduplicated and sorted; more than 1,000 is an error. Passport and
device credential updates remain admin-only.

A fact-read, stable-identity, eligibility, or projection failure sends nothing. It
must never clear BCS by substituting an empty roster.

### Business notification

`BotManagerSyncProtocol.request_sync` and `request_space_sync` are post-commit,
nonblocking notification surfaces. They perform bounded memory admission only and
return `QUEUED`, `COALESCED`, `DISABLED`, or `REJECTED`. They never confirm remote
state and never change a collaborator, work-order, Space, Bot-space, or Owner-handoff
result.

Owner handoff uses the same ordinary refresh path as every other roster-affecting
change. It does not invoke BCS owner transfer, establish owner alignment, or create a
persistent owner gate.

### Runtime

- Keep the dedicated manager-sync background loop.
- Run 4 workers.
- Bound Bot entries plus pending/expanding Space hints to 1,024 total, counting running entries.
- Page Space fan-out and repair at 100 keys.
- Coalesce only within one process. A notification arriving during a round marks the
  key dirty and causes a later fresh-read round.
- Do not use Redis leases, in-flight markers, namespace generations, owner holds, or
  CAS recovery.

Cross-replica, out-of-order, concurrent, and late writes are allowed. Process failure
may lose admitted work. There is no eventual-consistency guarantee. Stale managers,
including revoked permissions, may persist until a later successful sync or manual
resync. Existing editor locks reduce risk but do not serialize every trigger.

### Retry and outcome

One round has a 15-second budget and starts at most 3 sends. Before every send attempt,
read current facts and rebuild the full snapshot. A running synchronous SQL read
cannot be forcibly cancelled; a result arriving after the deadline is not sent.
Retry after approximately 1 second
and 2 seconds plus jitter, clipped by the remaining budget.

- `DeliveryFailure.NOT_SENT`: retryable.
- `DeliveryFailure.UNKNOWN`: retryable, including timeouts and HTTP 5xx; the prior
  request may still complete late.
- `DeliveryFailure.REJECTED`: permanent for the round.
- Shutdown cancellation ends the local task and does not create a marker.
- Exhaustion returns `SyncOutcome.FAILED`; later notifications remain admissible.

A confirmed envelope whose `total` differs from the sent roster is drift. Empty is
sent only when a successful authoritative read projects an actual empty set.

### Delivery identity

The HTTP plugin sends directly to `bcs_base_url`:

```http
PUT /api/v1/collaboration/bots/{bot_id}/managers
X-Avernet-Principal: <JWT>
```

There is no gateway hop and no Bearer header. The token is minted per send from the
existing shared gateway-principal signing key with HS256, `iss=backend`, `aud=bcs`,
`kid=bare`, a 60-second lifetime, and exactly one `app_name=backend` principal. Its
tenant comes from the `SyncKey`/snapshot scope. BCS must trust issuer `backend` and
allow `app=backend`, the same tenant, and action `managers:sync` in `trusted_apps`.

### Repair

`ManagerSyncRepairService` has only:

```python
request_bot(key: SyncKey) -> SubmissionResult
request_page(*, avernet_tenant: str, env: str, after_pk: int, limit: int) -> RepairPage
```

The page limit is at most 100 and `RepairPage.rejected_keys` preserves every refused
submission. Repair re-reads current facts through the normal worker. It has no marker
read/clear, CAS, drain proof, or owner-alignment operation.

### Configuration and persistence

The only keys are `enabled`, `env`, and `bcs_base_url`. Missing configuration is
disabled. Enabled configuration requires `env` and an HTTP(S) BCS origin. Unknown
keys fail validation. `ManagerSyncStack` contains only `bcs` and `reason`; it has no
coordinator. Singlebox explicitly enables the same real direct-BCS path through the
base `BotManagerSyncModule` provider; there is no `SingleboxBotManagerSyncModule`.
The recording test module is installed for TEST/CORP_TEST, not singlebox.

Do not add database schema, migrations, `Bot.ext` state, `ac_task_queue` writes,
`coordination_mode`, `coordination_generation`, `target_id`, memory/Redis modes, or
per-tenant bearer credentials.

## Acceptance criteria

1. Committed collaborator and indirect permission changes notify; rollback/failure
   paths do not.
2. A hung or failed BCS request does not delay or rewrite the business result.
3. Projection tests cover admin/member inclusion, Owner exclusion, Team Space
   eligibility, stable Bot identity, empty roster, and 1,000/1,001 boundaries.
4. Retry tests prove a fresh fact read before every send; `NOT_SENT` and `UNKNOWN`
   retry, `REJECTED` does not, and exhaustion is `FAILED`.
5. Dispatcher tests prove local dirty/coalescing, later work after failure, 4 workers,
   1,024 total admitted units including running/hints, and 100-key paging.
6. Shutdown tests prove cancellation ends local work and no manager-sync thread
   survives teardown.
7. Transport tests verify direct `X-Avernet-Principal` claims, 60-second TTL, tenant
   scope, no Bearer header, envelope validation, and timeout/5xx classification.
8. Repair tests cover `request_bot`, bounded `request_page`, and exact rejected keys,
   with no clear methods.
9. Repository/integration tests prove real projection boundaries and that read or
   projection failure never emits an empty clear.
10. Architecture/config tests prove the coordinator Protocol and implementations are
    removed and the three-key configuration is the only accepted surface.

### Live acceptance matrix

The final live suite contains four normal flows:

1. admin/member reach BCS and removal clears them;
2. update and leave;
3. work-order editor approval;
4. Team Space removal and rejoin.

Fault scenarios are explicit opt-in only: BCS 403 without business rollback, transient
disconnect followed by retry, and reported-count drift. The simplified contract has no
Owner, drain, marker-clear, CAS, or restart-barrier drill.

## Validation status

On 2026-10-28 the integrated local selection passed **1,178 tests**. It includes
all bot-collaborator, work-order and Space tests; Owner handoff/Bot-space regressions;
manager-sync service/dispatcher/lifecycle/resource/failure tests; gateway-principal,
HTTP plugin, consumer conformance, repository, DI/profile/config, and all architecture
tests. Command and output: `/tmp/manager-sync-simple-final-tests.log`.

Changed-file critical/unused-import lint and the whole-PR 1,000-line audit passed.
Final review then added focused regressions for lifecycle readiness-timeout cleanup,
complete principal-mint failure classification, and permanent-4xx classification;
those focused results are recorded in the final review report. The new live fault proxy
also has three isolated HTTP unit tests in the 1,178-test selection.
The earlier work-order sync tests were split without dropping assertions to keep the
original test file below 1,000 lines. No coverage threshold was lowered.

After final-review corrections, a coherent focused rerun passed **185 tests**, with
**95% statement coverage** across manager-sync core, configuration/composition, and
the production HTTP plugin (844 statements, 38 missed). This scoped measurement is
not the whole-Backend or changed-line CI denominator. Log:
`/tmp/manager-sync-simple-coverage.log`. Independent final review found no remaining
blocking findings.

Remote Backend CI and unified singlebox checks will be recorded after the requested
push. External live acceptance has not been run: no isolated deployment/credentials
were supplied for this execution. Local tests do not prove actual deployment rollout.
An unscoped local SAST invocation traversed `.venv` dependencies; use the repository's
commit-range-scoped invocation for the PR rather than treating dependency diagnostics
as source regressions.

## Compatibility and risk

This is a deliberate weakening of the baseline feature's ordering and recovery
claims. Redis coordination, persistent holds, CAS clearing, and unknown-write blocking
are removed rather than migrated. No data migration is required because those
mechanisms did not use Backend SQL persistence.

Disabling stops future notifications but does not remove managers already present in
BCS. Rollback is therefore “stop sending,” not “restore BCS.” Manual resync projects
current Backend facts when repair is needed.

The older review and validation records describe an invalidated design and are
historical only. The results above apply to the simplified contract; they do not
establish eventual consistency or replace the unrun external live acceptance.
