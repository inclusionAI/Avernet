# Plan: Online Bot Supersede Cleanup

## Approach
Collapse the fragmented "reuse-vs-recreate" logic into one shared, **provider-aware**
decision used by every online deploy seam, and add a single cleanup step so a
superseded bot is never left behind.

### Verified provider behavior (this drives the whole design)
The reuse/cleanup decision **cannot** be driven by the upgrade's error code,
because the two providers signal a gone container completely differently:

- **Non-teclaw (`baas` → ARCA/DOCKER/K8S):** the BaaS `update` **destroys and
  recreates the device in place** (tolerating an already-gone device). So an
  `upgrade` against a `FAILED`/`STOPPED` bot **rebuilds it under the same
  `bot_uuid`** and *recovers*. No new bot, no orphan.
- **TeClaw (+ LOCAL/POOLAB):** the BaaS `update` is `_native_update_device` —
  it just re-delivers config to the existing container via
  `plugin.update_bot(provider_device_id, …)`. If the container is gone (e.g. a
  prior **offline STOP** destroyed it — `stop_device` leaves the row `STOPPED`
  with a **stale** `provider_device_id`), `plugin.update_bot` raises, and
  `_native_update_device` **catches it, marks the device `FAILED`, and returns**.
  The failure surfaces **asynchronously as a `FAILED` publish** — **never** as a
  synchronous `BOT_NOT_FOUND`/`DEVICE_NOT_FOUND` to the backend. So a teclaw
  `upgrade` on a gone container does not "fall back"; it fails the publish and
  strands the record.

`resolve_container_provider(bot)` returns only `"teclaw"` or `"baas"` (from the
bot's engine), so the branch is a single `== TECLAW_DEVICE_PROVIDER` check.

This is also what `dcce9a6` was really doing: its `_should_upgrade_online`
`FAILED`/`STOPPED` skip (→ first-release) was the *actual* fix for teclaw
offline→re-publish; its `DEVICE_NOT_FOUND` handling is, for teclaw, effectively
dead. We keep that skip for teclaw and **add the missing cleanup**; we improve
the non-teclaw path to *reuse* instead of always recreating.

### The unified decision
Resolve the *candidate* online bot for this record (this record's own online
binding first, then `last_pub_id`'s), read its **BaaS status**, and pick:

| Candidate status | `baas`/ARCA (rebuild-in-place update) | teclaw (native in-place update) |
|---|---|---|
| `ACTIVE` | `UPGRADE` | `UPGRADE` (live container) |
| `FAILED` / `STOPPED` / `STOPPING` | `UPGRADE` (rebuilds in place) | `RETIRE_THEN_FIRST_RELEASE` |
| `RELEASED` / absent / `DESTROYING` | `FIRST_RELEASE` | `FIRST_RELEASE` |
| no candidate at all | `FIRST_RELEASE` | `FIRST_RELEASE` |

- **`RETIRE_THEN_FIRST_RELEASE`** = `destroy(old_bot_uuid)` (idempotent;
  failures propagate so we never create the replacement while the old bot may
  still be live), then create a fresh bot. This is the teclaw cleanup the
  pipeline is missing today.
- The **reactive `DEVICE_NOT_FOUND` fallback stays as a secondary net** for the
  rare non-teclaw race (status read said reusable, but the `upgrade` still hit a
  gone device): `BOT_NOT_FOUND` → first_release; `DEVICE_NOT_FOUND` → retire +
  first_release. It is no longer the primary mechanism.

## Affected Components
- **Reuse decision** (`upgrade_resolution_mixin.py`) — one provider-aware
  `_decide_online_deploy(...)` returning `UPGRADE | RETIRE_THEN_FIRST_RELEASE |
  FIRST_RELEASE`; candidate resolution from own-binding-then-`last_pub_id`.
- **Online release dispatch** (`publish_flow_service.py`) — consume the decision;
  resolve the upgrade target from this record's own binding first.
- **Deploy atom / error classification** (`operation_runner.py`) — carry the
  specific gone-bot error code out of the atom (for the secondary fallback).
- **Upgrade fallback** (`release_stage.py`) — retire-by-code before falling back.
- **Restart recreate** (`restart_mixin.py`) — apply the same decision + retire.
- **Cleanup primitive** (`bot_build_service.py`) — one idempotent "retire
  superseded bot" method whose failures propagate.

## Data Model Changes
None. No schema, DDL, or migration.

## API / Interface Changes
- `TargetBotGoneError` gains `error_code: str` (was raised with no payload);
  `acquire_deploy_workflow` raises `TargetBotGoneError(result["error_code"])`.
  Internal only.
- New `BotBuildService.retire_superseded_bot(bot_uuid: str) -> None` —
  idempotent `destroy_bot` (deterministic `request_id`); failures **propagate**
  (never report a failed lifecycle write as success — the durable deploy retries
  before creating the replacement); never called for an `ACTIVE`/reused bot.
- `_should_upgrade_online(bool)` is replaced by `_decide_online_deploy(...)`
  returning a 3-way decision (provider-aware). The old boolean and the static
  `_ONLINE_UPGRADE_BLOCKING_BAAS_STATUSES` set are removed — the status/provider
  matrix above subsumes them.

## Key Files & Functions

### 1. Provider-aware decision (`upgrade_resolution_mixin.py`)
- Remove `_ONLINE_UPGRADE_BLOCKING_BAAS_STATUSES` (superseded by the matrix).
- `_resolve_online_reuse_target(publish_record) -> tuple[str|None, int|None]`:
  own `ext.binding.online` → binding → `device_id`; else `last_pub_id`'s; else
  `(None, None)`.
- `_decide_online_deploy(publish_record, bot) -> OnlineDeployDecision`:
  1. candidate = `_resolve_online_reuse_target(...)`; `None` → `FIRST_RELEASE`.
  2. `status = self._baas_service.get_bot(bot_uuid).get("status")` (a genuine 404
     is already normalized to `RELEASED`; a raised error is transient/non-404 and
     **propagates** so the durable task retries — it is NOT treated as gone.
     Missing/empty status → `FIRST_RELEASE`).
  3. `RELEASED`/`DESTROYING` → `FIRST_RELEASE`.
  4. `ACTIVE` → `UPGRADE`.
  5. `FAILED`/`STOPPED`/`STOPPING`:
     - `resolve_container_provider(bot) == TECLAW_DEVICE_PROVIDER` →
       `RETIRE_THEN_FIRST_RELEASE`.
     - else → `UPGRADE`.
  6. `PENDING`/unknown → `UPGRADE` (optimistic; the deploy atom / progress poll
     settles a still-provisioning bot). See Open Questions.

### 2. Online release dispatch (`publish_flow_service.py`)
- `_execute_online_release` switches on `_decide_online_deploy(...)`:
  - `UPGRADE` → `_execute_upgrade_release(...)` (target = the resolved candidate,
    own-binding-first — this is what makes the **retry** seam reuse).
  - `RETIRE_THEN_FIRST_RELEASE` → `retire_superseded_bot(candidate)` then
    `_execute_first_release(...)`.
  - `FIRST_RELEASE` → `_execute_first_release(...)`.
- `_execute_first_release` unchanged (still the `fallback` for `upgrade_release`).

### 3. Carry the gone-bot code (`operation_runner.py`)
- `TargetBotGoneError.__init__(self, error_code="BOT_NOT_FOUND")` storing it.
- `acquire_deploy_workflow` raises `TargetBotGoneError(result.get("error_code"))`.
  `BOT_GONE_ERROR_CODES` unchanged.

### 4. Upgrade fallback (secondary net) (`release_stage.py`)
- In `upgrade_release`'s `except TargetBotGoneError as e:`, before `fallback(...)`,
  `self._build_service.retire_superseded_bot(bot_uuid)` **only when**
  `e.error_code == "DEVICE_NOT_FOUND"` (record lingers); skip for `BOT_NOT_FOUND`.

### 5. Restart (`restart_mixin.py`)
- `execute_restart` applies `_decide_online_deploy` on the restart target:
  `UPGRADE` reuses the in-place upgrade path; every non-`UPGRADE` decision
  recreates *directly* (`RETIRE_THEN_FIRST_RELEASE` retires first), opening+
  abandoning a fresh `RESTART` op before `_recreate_restart_target` so
  `sync_restart_progress` reads the recreate's workflow via `ext.restart` rather
  than a stale earlier `RESTART` op. A `FIRST_RELEASE`/`DESTROYING` target is not
  routed through `upgrade_async` (its UPDATE fails with a non-`BOT_NOT_FOUND`
  error the atom would not fall back on). The `except TargetBotGoneError` on the
  `UPGRADE` path still applies the code-gated `retire_superseded_bot`
  (`DEVICE_NOT_FOUND` only) before recreating.

### 6. Cleanup primitive (`bot_build_service.py`)
- `retire_superseded_bot(self, bot_uuid)`: `self._baas_service.destroy_bot(
  bot_uuid, request_id=md5("retire_"+bot_uuid))` (idempotent — the deterministic
  `request_id` makes a redelivery reuse the same destroy); failures **propagate**
  (never report a failed lifecycle write as success — the caller must not create
  the replacement while the old bot may still be live).

### 7. Tests
- Table-driven on `(provider ∈ {teclaw, baas}, prior_status, get_bot result)`:
  - `baas` + `FAILED` → `UPGRADE`, same `bot_uuid`, no destroy, no new bot
    (the real `BOT-9bce` recovery).
  - `teclaw` + `FAILED`/`STOPPED` → `destroy(old)` once, then first_release →
    exactly one live bot.
  - re-publish `baas` + prior `STOPPED` → upgrade (rebuild); `teclaw` + prior
    `STOPPED` → destroy + recreate (the offline→re-publish case, now orphan-free).
  - `ACTIVE` (either provider) → upgrade, never destroy.
  - `RELEASED`/absent → first_release, never destroy.
  - restart target `teclaw`+`STOPPED` → destroy + recreate; `baas`+`FAILED` →
    upgrade.
  - secondary fallback: `DEVICE_NOT_FOUND` → destroy; `BOT_NOT_FOUND` → no destroy.
  - `retire_superseded_bot` raising → **propagates** (deploy does not proceed to
    create a replacement; the durable task retries).
- Crash-safety: redelivery after `destroy(old)` but before first_release →
  candidate now `RELEASED` → `FIRST_RELEASE`, no double-destroy, single live bot.
- Regression: adapt `dcce9a6`'s tests — teclaw offline→re-publish still yields a
  single live bot (now with the old one destroyed, not orphaned); non-teclaw
  `FAILED`/`STOPPED` now *reuses* instead of recreating.

## Dependencies
- Reuses `baas_service.get_bot` (status), `baas_service.destroy_bot` (cleanup),
  `resolve_container_provider` (branch). No new BaaS endpoints, packages, config,
  or flags.

## Risks & Mitigations
- **Destroying a live bot.** `retire_superseded_bot` is only reached for
  `RETIRE_THEN_FIRST_RELEASE` (teclaw + `FAILED`/`STOPPED`/`STOPPING`) or a
  confirmed `DEVICE_NOT_FOUND` fallback — never for `ACTIVE`/success. Unit-tested.
- **Stale status read (TOCTOU).** `get_bot` status may lag. If it says `ACTIVE`
  but the container is actually gone: non-teclaw upgrade rebuilds anyway; teclaw
  upgrade fails the publish (same as today) — the retry re-enters and, on the
  next pass, the status reads `FAILED` → teclaw retire+recreate. Bounded, not an
  orphan.
- **A `baas` bot on a native-in-place BaaS provider (LOCAL/POOLAB).** The backend
  only distinguishes teclaw vs baas; a non-teclaw bot on a native provider would
  fail-publish on upgrade like teclaw. Not a prod path (prod `baas` = ARCA). The
  publish-failure + retry loop is the backstop; documented as a known edge.
- **Crash between destroy-old and create-new.** Idempotent: redelivery re-reads
  status → `RELEASED` → first_release adopts the in-doubt new bot by query. No
  orphan, no duplicate.

## Alternatives Considered
- **Reactive-only (branch on the upgrade error code).** Rejected — the original
  plan; it does not work for teclaw, which signals a gone container as a `FAILED`
  publish, not an error code. This whole revision exists because of that.
- **Uniform "always destroy + create".** Simpler but churns a new `bot_uuid` on
  non-teclaw where an in-place rebuild recovers the bot and keeps identity/state.
  Rejected in favor of provider-aware reuse.
- **Per-seam patches.** Rejected — the decision is identical across seams.

## Rollout
- Single change set to `REL20260728`; no migration, no flag. Behavior only ever
  removes bots BaaS reports as gone / rebuilds recoverable ones.
- Prevents *new* orphans; the existing production backlog is a separate one-off
  reconciliation (out of scope; see spec).

## Test Strategy
- Module unit tests (above) under the service-bot suite.
- Publish-boundary E2E as a guard (they assert single-bot end states).
- Staging sanity: (1) fail an online first-release on a **baas/ARCA** bot, retry,
  confirm the **same `bot_uuid`** recovers and no second `baas_bot` row appears;
  (2) offline then re-publish a **teclaw** bot, confirm the old `STOPPED` record
  is destroyed and exactly one new live bot exists.

## Open Questions
- `PENDING` candidate: treat as `UPGRADE` (optimistic) or wait/poll? Proposed
  `UPGRADE`; confirm.
- `DESTROYING` routes to `FIRST_RELEASE` (self-terminating, no retire) — confirm
  that's the desired handling vs. waiting for `RELEASED`.
- Empty-record `BOT_NOT_FOUND` sub-case (record present, zero devices): still
  out-of-scope cosmetic per spec; confirm.
