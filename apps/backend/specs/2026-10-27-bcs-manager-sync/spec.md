# Backend → BCS bot manager best-effort sync specification

- Status: implemented; local regression passed, remote CI/live validation pending
- Feature baseline: `be5b69b21`
- Merge target: `REL20261029` (PR #3208), including the BCS local trusted-app
  configuration. The temporary split PR #3254 is closed without merging.
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
Owner. IDs are deduplicated and sorted; more than 1,000 **non-Owner** managers is an
error. The repository applies the same exclusion before its bound, so 1,000
managers plus an explicit Owner row is valid. Passport and
device credential updates remain admin-only.

A fact-read, stable-identity, eligibility, or projection failure sends nothing. It
must never clear BCS by substituting an empty roster.

### Business notification

`BotManagerSyncProtocol.request_sync` and `request_space_sync` are post-commit,
nonblocking notification surfaces. They perform bounded memory admission only and
return `QUEUED`, `COALESCED`, `DISABLED`, or `REJECTED`. They never confirm remote
state and never change a collaborator, work-order, Space, Bot-space, or Owner-handoff
result. All business call sites use shared, transport-free post-commit
notification helpers; only notification failures are isolated, never persistence
failures. Normal admission emits `submit_admitted` with the finite trigger
`SyncReason` and queued/coalesced outcome; the reason does not affect scheduling.
Diagnostics increments and snapshots are protected by the same thread lock.
Closed admission takes precedence over environment-mismatch diagnostics.

Owner handoff uses the same ordinary refresh path as every other roster-affecting
change. It does not invoke BCS owner transfer, establish owner alignment, or create a
persistent owner gate.

### Runtime

- Keep the dedicated manager-sync background loop. Concurrent startup calls must
  create at most one worker loop; failed startup closes admission and cleans up.
  Each lifecycle instance is single-use: reactivation requires a new instance/
  process, because its dispatcher admission remains closed after stop/failure.
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

### Singlebox CI readback

The existing `manager_sync` / `bot_collaborator` acceptance targets include
`test_manager_sync_bcs_projection.py`. The story registers an isolated Bot/Humans
in real BCS, changes collaborators through Backend HTTP, and asserts the exact
manager set via an authorized BCS Owner GET, including removal to empty. Its
local-only fixture uses the existing backfill API and official `bcs-admin`
ownership initialization for a fresh, isolated stable target before any
collaborator writes; it does not prove the production registration lifecycle. It uses
the real dispatcher, reader, principal signer and HTTP plugin, not a test sync
call. When enabled, unavailable BCS or mismatched projection fails the story;
coverage alone is not delivery evidence. This local-stack contract check does
not replace external deployment acceptance.

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

The previous head `cd755b118` passed all applicable PR CI checks on 2026-10-29
(Unit Tests run 40690684626, Singlebox Coverage run 40690684627). That result
predates the review follow-up above; its new regression and CI results are
recorded separately in the PR validation. External live acceptance has not been run: no isolated deployment/credentials
were supplied for this execution. Local tests do not prove actual deployment rollout.
An unscoped local SAST invocation traversed `.venv` dependencies; use the repository's
commit-range-scoped invocation for the PR rather than treating dependency diagnostics
as source regressions.

### 2026-10-29 review follow-up

The integrated Backend selection passed **1,124 tests** (42 existing warnings),
including all affected business modules, reader, delivery/consumer, profile/config
and the complete architecture suite. Log: `/tmp/pr3208-review-regression-final.log`.
An earlier run exposed invalid Bot-space fixtures omitting the persisted primary
key; these now match the real repository result rather than relying on a swallowed
notification argument error. No production persistence contract was weakened.
The CORP_TEST finding was a review false positive: its implicit default already
selected recording delivery; selection is now explicit and regression-tested.
New singlebox readback and this head's remote CI results are recorded separately
in the PR; the previous head's green checks are not evidence for these changes.

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

## Release target (2026-10-29)

PR #3208 targets `REL20261029` and includes the BCS local trusted-app rule.
The temporary extraction to #3254 was cancelled at the user's request; no
split-only runtime config injection remains. Deployments still need the correct
shared signing key and tenant-scoped authorization before enabling delivery.

Before the short-lived split, `fc5fa08fb` passed all applicable remote checks
(Unit Tests 40808582323, Singlebox Coverage 40808582389). Current-head CI evidence
is recorded separately in the PR.

## Further bounded reduction

The sync service shares terminal diagnostic recording and returned outcomes;
the dispatcher no longer exposes an unused clock override; the read repository
lets domain errors propagate without redundant re-raise handlers. Retry reasons,
attempt counts, cancellation, SQL error mapping and resource bounds are unchanged.
These three production files shrink by 75 lines. The real singlebox story drops
an unused canonical Bot/token registration and keeps only its actual stable
projection target, reducing the test by 40 lines without removing assertions.
The dedicated-loop lifecycle, repair API and protocol classifications remain
intact; a threading redesign would add risk rather than safely simplify this PR.

Release `828c4b1b5` was merged with the DI container conflict resolved by keeping
both module sets and removing duplicate moved imports; DI/profile regression
passed 74 tests. Full current-head results are recorded in the PR.
