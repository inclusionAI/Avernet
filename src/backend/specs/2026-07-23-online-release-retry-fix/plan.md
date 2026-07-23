# Plan: Online-Release Retry Regression Fix

## Approach

Retry stops asking "is the online release recorded?" entirely. Retry still only
applies to a `FAILED` record and still rolls it back to its pre-failure status
(`ext.source_status`); the change is the dispatch after that rollback: when the
pre-failure status is `ONLINE_PUB`, retry now unconditionally re-enqueues the
`online_release` task — instead of choosing restart-vs-rerun — because the
task's gate + release process already decide run-vs-skip and
first-release-vs-upgrade idempotently. The
predicate keeps only its gate consumer and is renamed
`is_current_online_deployment`. The shared *deploy atom* — open ledger op →
acquire workflow (with uniform `BOT_NOT_FOUND` classification) → validate ids —
is extracted so `first_release`, `upgrade_release`, and `execute_restart` share
one crash-safe shape, and restart's `BOT_NOT_FOUND` recreate leg becomes an
abandon-plus-fresh-first-release-op with a **new binding** (closing the
documented orphan window). Verify flow behavior is untouched.

## Affected Components

- `src/backend/src/agentclaw/community/core/service_bot/services/publish_flow/retry_ops_mixin.py`
  — retry dispatch; restart-vs-release decision and the `ext.retry` flag placement.
- `src/backend/src/agentclaw/community/core/service_bot/services/publish_flow_service.py`
  — the predicate (rename + docstring); its helpers stay as-is.
- `src/backend/src/agentclaw/community/core/service_bot/services/publish_flow/tasks.py`
  — `PublishOnlineReleaseHandler` gate call site + module docstring.
- `src/backend/src/agentclaw/community/core/service_bot/services/publish_flow/release_stage.py`
  — `first_release` / `upgrade_release` rebased onto the deploy atom.
- `src/backend/src/agentclaw/community/core/service_bot/services/publish_flow/restart_mixin.py`
  — `execute_restart` rebased onto the atom; new crash-safe recreate leg.
- `src/backend/src/agentclaw/community/core/service_bot/services/publish_flow/operation_runner.py`
  — hosts the new deploy-atom helper (uses only runner + ledger seams).
- `src/backend/tests/community/core/service_bot/` — updated + new tests
  (cross-publish-boundary module).

## Data Model Changes

None. No new tables/columns; no new `PublishOperationKind` (the restart-recreate
leg reuses `FIRST_RELEASE`, see below), so the kind partition
(`models.py:313-336`) is unchanged and the exhaustiveness test keeps passing.

## API / Interface Changes

No HTTP API changes. Internal interfaces:

- `PublishFlowService.is_online_release_recorded(publish_id)` →
  **renamed** `is_current_online_deployment(publish_id)`. Same signature and
  semantics as post-#341 (COMPLETED release op **and** not superseded on the
  bot timeline). Docstring rewritten to state: gate-only predicate; never to be
  consulted by restart or retry ("restart must always re-deploy via BaaS").
- `RetryOpsMixin._retry_uses_baas_restart(publish_id, rollback_status)` →
  **deleted**. Replaced by a module-level
  `_RESTART_RETRY_STATUSES = frozenset({VALIDATE_PUB, SUCCESS})` membership
  check in `retry()` (no `publish_id`-dependent logic left).
- New in `operation_runner.py`:
  - `class TargetBotGoneError(Exception)` — uniform "BaaS says BOT_NOT_FOUND"
    signal (replaces `release_stage._BotNotFoundError` and restart's inline
    check).
  - `async def acquire_deploy_workflow(runner, *, publish_id, kind, stage,
    operator, issue, bot_uuid=None, params=None) -> PublishOperationRecord` —
    the atom: `open_operation` → wrap `issue` so a
    `{success: False, error_code: "BOT_NOT_FOUND"}` result raises
    `TargetBotGoneError` → `acquire_workflow` → on `TargetBotGoneError`
    abandon the op and re-raise (caller runs its fallback) → validate
    `baas_publish_id` (and `bot_uuid` for creation kinds,
    `PublishOperationKind.creation_kinds()` at `models.py:262`) → return the
    op. **Deliberately excludes**: any skip-if-current-deployment check
    (spec: restart always hits BaaS) and the follow-up binding/ext writes
    (they differ per operation and stay with each caller).

## Key Files & Functions

### 1. Retry re-route (`retry_ops_mixin.py`)

- `retry()` step 5-6 (`retry_ops_mixin.py:99-125`): compute
  `use_restart = rollback_status in _RESTART_RETRY_STATUSES` **before** the
  rollback write; include `ext["retry"] = True` in that single rollback write
  **only when `use_restart`** (today it is set unconditionally at
  `retry_ops_mixin.py:103`). The ONLINE_PUB branch instead clears any stale
  flag (`PublishExtState.clear_retry_flag`) in the same write.
  - This also fixes a latent stranding bug in the current release-path retry:
    with `ext.retry=True` left set, the poll redirect
    (`progress_sync_mixin.py:350-354`) sends an ONLINE_PUB record to
    `sync_restart_progress`, which prefers the ledger's **latest RESTART op**
    (`progress_sync_mixin.py:551-557`) — a stale prior restart, or nothing —
    instead of the fresh release workflow. Routing the flag by branch makes
    the poll drive the release path via `ext.publish.online` as designed.
- Dispatch (`retry_ops_mixin.py:120-125`): `use_restart` → `_retry_via_restart`
  (unchanged); `rollback_status in (VALIDATING, ONLINE_PUB)` →
  `_retry_via_online_release` (unchanged body); else `_retry_via_verify_flow`
  (unchanged). Docstrings updated: VALIDATE_PUB stays restart because the
  verify release self-advances `BUILT→VALIDATE_PUB` (`release_stage.py:93-101`)
  and cannot idempotently re-enter (symmetry deferred — follow-up spec);
  SUCCESS stays restart because a live record's release **is** current and the
  gate would skip BaaS (the exact misuse the rename guards against).

### 2. Rename (`publish_flow_service.py`, `tasks.py`)

- `publish_flow_service.py:867` — rename to `is_current_online_deployment`;
  keep `_completed_online_release_op` (`:902`) and
  `_online_release_superseded` (`:929`) unchanged. Docstring: single consumer =
  the online-release gate; explicitly forbid use in retry/restart.
- `tasks.py:302` — call renamed method; fix stale docstrings at
  `tasks.py:17-19` and `tasks.py:273-280` (they still describe the pre-#341
  `ext.publish.online` marker as the guard; the guard is the ledger-driven
  liveness predicate).

### 3. Deploy atom + restart recreate fix (`operation_runner.py`, `release_stage.py`, `restart_mixin.py`)

- `release_stage.py`:
  - `first_release` (`:150-244`): open/acquire/validate
    (`:174-204`) collapses into `acquire_deploy_workflow`; binding +
    `record_release_ext` + `complete_operation` stay.
  - `upgrade_release` (`:246-356`): drop `_BotNotFoundError` and the inline
    check (`:293-298`); catch `TargetBotGoneError` (op already abandoned by
    the atom) → run `fallback` as today (`:300-314`).
- `restart_mixin.py` `execute_restart` (`:179-312`):
  - Happy path: open/acquire (`:224-280`) collapses into
    `acquire_deploy_workflow` with `kind=RESTART`; the ext.restart dual-write,
    handle refresh, and `complete_operation` stay.
  - `BOT_NOT_FOUND` leg (`:243-270`): the inline `release_async` recreate is
    replaced by `_recreate_restart_target(...)`, which runs **after** the atom
    abandoned the RESTART op ("BOT_NOT_FOUND -> recreate"):
    1. `acquire_deploy_workflow` with `kind=FIRST_RELEASE` for the same
       stage — a creation op, so a crash-resume rebuilds from the ledger
       exactly like a normal first release (the bounded Option-C orphan
       window replaces today's unbounded one; this is the guarantee match
       the spec requires).
    2. Mint a **new** binding via `create_release_binding`
       (`device_binding_mixin.py:24-47`), recorded into the op `result`
       (re-run skips it) — never reuse the binding that points at the gone
       bot.
    3. `_mutate_and_update_ext`: `ext.binding.<stage>` and
       `ext.publish.<stage>` → new ids, plus `ext.restart.<stage>` → new
       workflow id. The last write matters: after abandoning the RESTART op,
       `sync_restart_progress` finds no ledger workflow id and falls back to
       `ext.restart` (`progress_sync_mixin.py:556-557`), so restart-progress
       sync keeps working for the recreate. No status change (restart runs at
       SUCCESS / *_PUB; `record_release_ext`'s status CAS does not fit here,
       which is why the recreate leg does its own ext write).
    4. `refresh_publish_handle` + `complete_operation`.
  - Reusing `FIRST_RELEASE` (vs a new kind): the recreate genuinely deploys
    this record's version as a fresh bot — a version-setting create. For an
    online recreate, the record's latest release op then being this op makes
    `is_current_online_deployment` true, which is correct (its version *is*
    the live deployment). No kind-partition/DDL churn.
  - Note: `execute_restart` is stage-agnostic, so the recreate fix also covers
    verify-**stage** restarts. This is intentional and does not touch the
    verify release/retry flow (the out-of-scope item).

### 4. Tests

- Update: `test_publish_crash_windows.py:385-475` (rename call sites),
  `test_publish_tasks.py:49` (fake method rename),
  `test_bot_publish_service.py:2064` (comment), retry tests asserting the old
  restart-vs-rerun split, restart BOT_NOT_FOUND tests.
- New: `tests/community/core/service_bot/services/test_publish_cross_publish_flows.py`
  — reuses the `test_publish_crash_windows.py` harness pattern (real SQLite
  ledger `:60-67`, scripted `FakeBaas` `:91-108`, real `PublishFlowService`
  with mocked collaborators).

## Dependencies

None new.

## Risks & Mitigations

- **Risk:** An ONLINE_PUB retry of a still-live release no longer re-deploys
  (previously the restart branch re-deployed). If BaaS-side state were somehow
  bad while the ledger says "current", retry alone won't heal it.
  **Mitigation:** Deliberate, spec'd behavior (criterion: no redundant
  re-deploy). The explicit `/restart` endpoint remains the "force a re-deploy"
  tool and always hits BaaS.
- **Risk:** Removing the retry-flag from the ONLINE_PUB branch changes which
  sync path the poll takes for online retries.
  **Mitigation:** That is the fix (poll must follow `ext.publish.online`, the
  fresh release). Covered by a dedicated retry-then-poll test; restart
  branches (VALIDATE_PUB/SUCCESS) keep the flag and their redirect.
- **Risk:** Atom extraction silently alters a crash-window guarantee.
  **Mitigation:** The atom is a code motion of the shared open/acquire/validate
  sequence; the crash-window suite (`test_publish_crash_windows.py`) runs
  against the real seams (`issue`, `record_workflow`) and must stay green
  unmodified except for renames.
- **Risk:** Restart-recreate reusing `FIRST_RELEASE` could confuse the
  liveness predicate for records whose original release was an `UPGRADE`
  (both kinds now present; `_completed_online_release_op` at
  `publish_flow_service.py:914-927` already handles coexisting kinds by
  keeping the max `baas_publish_id`).
  **Mitigation:** Exactly the coexistence path #341 built; add an explicit
  test (restart-recreate after upgrade → predicate still true).
- **Risk:** Verify-stage restarts also get the new recreate leg (shared code).
  **Mitigation:** Behavior change is strictly an improvement (no orphan
  window); verify release/retry flow untouched; covered by a verify-stage
  restart BOT_NOT_FOUND test.

## Alternatives Considered

- **Keep a restart branch for "already-live" online retries** (status quo
  minus the predicate flaw): rejected — it re-deploys redundantly, keeps two
  code paths answering one question, and is the misuse pattern that caused the
  regression.
- **A dedicated `RESTART_RECREATE` op kind**: cleaner telemetry, but requires
  classifying it in `baas_publish_types` + the deploy partition and teaching
  `sync_restart_progress` a second kind; `FIRST_RELEASE` + the `ext.restart`
  dual-write achieves the same guarantees with no enum churn.
- **Putting the atom in `release_stage.py`**: rejected — restart lives in a
  mixin with different deps; `operation_runner.py` is the natural home (the
  atom uses only runner/ledger seams and stays facade-free).

## Rollout

No flag, no migration, no BaaS-side change. Ships as one PR to `REL20260723`
(branch `claude/fix-online-release-recorded-tnhdll`). Backwards-compatible with
in-flight records: an old FAILED record with `source_status=online_pub` simply
takes the release path on its next retry; existing ledger rows are read
unchanged.

## Test Strategy

- **Unit (updated):** predicate rename tests; retry dispatch tests — ONLINE_PUB
  always → `_retry_via_online_release` (recorded or not), VALIDATE_PUB/SUCCESS
  → restart; retry-flag written only on restart branches.
- **Unit (new):** restart BOT_NOT_FOUND recreate — abandons RESTART op, opens
  FIRST_RELEASE op, mints new binding, dual-writes `ext.restart`; crash between
  create and record converges without a second orphan (crash-window harness);
  restart of a live current deployment still calls BaaS (the point-2 guard).
- **Cross-publish-boundary (new module):** each scenario drives **multiple
  publish records** against one shared FakeBaas bot and asserts live-deployment
  correctness + no duplicate bots/bindings:
  1. Upgrade chain: v1 first-release → SUCCESS → v2 upgrade → SUCCESS; v1
     superseded, v2 is current.
  2. Rollback-then-re-promote (#5984 shape): v2 rolled back (ROLLBACK_DEPLOY
     lands) → v2 re-promoted → gate re-runs the release (predicate false) →
     v2 current again.
  3. Retry interleaved with a later publish: v1 ONLINE_PUB fails → v2
     publishes on the same bot → v1 retry re-runs its release path (never
     restarts over v2's deploy).
  4. Restart-recreate after an upgrade chain: bot gone → recreate → predicate
     true for the recreated record.
- **Suite:** full `tests/community/core/service_bot/` green; pre-push contract
  per `AGENTS.md` with merge target `REL20260723`
  (`AVERNET_PRE_PUSH_MERGE_TARGET`).
