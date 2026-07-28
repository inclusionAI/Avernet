# Plan: Online Bot Supersede Cleanup

## Approach
Collapse the fragmented "reuse-vs-recreate" logic into one shape used by every
online deploy seam, and add a single provider-agnostic cleanup step so a
superseded bot is never left behind.

Two mechanisms, applied uniformly:

1. **Proactive decision** — before issuing, resolve the *candidate* online bot
   for this record (this record's own online binding first, then `last_pub_id`'s)
   and read its **BaaS status**. If a candidate exists and is not `RELEASED`
   (or otherwise terminal), **try `upgrade` (reuse the `bot_uuid`)**; otherwise
   **`first_release`** (nothing live to reuse or orphan).
2. **Reactive fallback** — when the `upgrade` returns a gone-bot signal, branch
   on the **error code**:
   - `BOT_NOT_FOUND` → the record is already gone → `first_release`, nothing to
     clean up.
   - `DEVICE_NOT_FOUND` → the record lingers (container gone) → **destroy the old
     bot first**, then `first_release`.

The provider difference resolves itself: on ARCA/DOCKER/K8S the `upgrade`
rebuilds in place and succeeds (no new bot, no orphan); on TeClaw/LOCAL/POOLAB a
gone container makes the `upgrade` fail with `DEVICE_NOT_FOUND`, and the fallback
retires the lingering record before recreating. No `if provider == …` branching
is needed anywhere.

This lets us **narrow `_ONLINE_UPGRADE_BLOCKING_BAAS_STATUSES` back to
`{RELEASED}`** (partially reverting `dcce9a6`): `FAILED`/`STOPPED`/`STOPPING`
should *attempt* the reuse and let the fallback clean up, rather than skip
straight to a new bot with no cleanup. `dcce9a6`'s broadening was a workaround
for the missing cleanup; once cleanup exists it's both unnecessary and harmful
(it blocks the ARCA in-place recovery).

## Affected Components
- **Deploy atom / error classification** (`operation_runner.py`) — carry the
  specific gone-bot error code out of the atom.
- **Reuse decision** (`upgrade_resolution_mixin.py`) — unified candidate
  resolution + status-based decision; narrowed blocking set.
- **Online release dispatch** (`publish_flow_service.py`) — resolve the upgrade
  target from this record's own binding first, then `last_pub_id`.
- **Upgrade fallback** (`release_stage.py`) — retire-by-code before falling back
  to first release.
- **Restart recreate** (`restart_mixin.py`) — retire-by-code before recreating.
- **Cleanup primitive** (`bot_build_service.py`) — one best-effort, idempotent
  "retire superseded bot" method both seams call.

## Data Model Changes
None. No schema, DDL, or migration. `baas_bot`/`baas_device`/`ac_publish_operation`
are unchanged; the fix only changes which BaaS calls are made and when.

## API / Interface Changes
- `TargetBotGoneError` gains an `error_code: str` attribute (was raised with no
  payload). `acquire_deploy_workflow` raises `TargetBotGoneError(error_code)`
  using the classified result code. **Internal only** — not a wire/API change.
- New `BotBuildService.retire_superseded_bot(bot_uuid: str, error_code: str) ->
  None` — best-effort, idempotent. Destroys the lingering bot **only** for
  `DEVICE_NOT_FOUND` (record present, container gone); no-op for `BOT_NOT_FOUND`
  (already gone). Never raises into the caller (logs + swallows), never touches a
  live `ACTIVE` bot.
- `_should_upgrade_online` semantics change: decision now derives from the
  resolved candidate's live BaaS status rather than the previous *record's*
  status plus a broad blocking set. Same signature.

## Key Files & Functions

### 1. Carry the gone-bot code (`operation_runner.py`)
- `TargetBotGoneError.__init__(self, error_code: str = "BOT_NOT_FOUND")` storing
  `self.error_code`.
- In `acquire_deploy_workflow`, when `result.error_code in BOT_GONE_ERROR_CODES`,
  `raise TargetBotGoneError(result.get("error_code"))`. `BOT_GONE_ERROR_CODES`
  stays `{BOT_NOT_FOUND, DEVICE_NOT_FOUND}`.

### 2. Unified reuse decision (`upgrade_resolution_mixin.py`)
- Narrow `_ONLINE_UPGRADE_BLOCKING_BAAS_STATUSES` → `{"RELEASED"}` (optionally
  also `"DESTROYING"` — a self-terminating record we should not try to upgrade;
  see Open Questions).
- New `_resolve_online_reuse_target(publish_record) -> tuple[str|None, int|None]`
  returning `(bot_uuid, binding_id)`:
  1. this record's own `ext.binding.online` → binding → `device_id`; if present,
     return it (covers the **retry of a failed first release** — the failed
     attempt's bot becomes the reuse candidate).
  2. else `last_pub_id` > 0 → previous record's `ext.binding.online` → binding →
     `device_id` (covers **re-publish**).
  3. else `(None, None)`.
- Rework `_should_upgrade_online(publish_record) -> bool`:
  - resolve the candidate via `_resolve_online_reuse_target`; `None` → `False`.
  - read status via `self._baas_service.get_bot(bot_uuid).get("status")`
    (best-effort; treat lookup failure / missing as "not reusable" → `False`).
  - `True` iff status is present and not in the (narrowed) blocking set.

### 3. Online release dispatch (`publish_flow_service.py`)
- `_execute_online_release` keeps the `if self._should_upgrade_online(...)`
  branch, but `_execute_upgrade_release` must resolve its `bot_uuid` /
  `existing_binding_id` from `_resolve_online_reuse_target(publish_record)` (own
  binding first, then `last_pub_id`) instead of only `last_pub_id`. This is what
  makes the **retry** seam attempt the reuse.
- `_execute_first_release` is unchanged — it remains the `fallback` target for
  `upgrade_release`.

### 4. Upgrade fallback retires the old bot (`release_stage.py`)
- In `upgrade_release`'s `except TargetBotGoneError as e:` block, before calling
  `fallback(...)`, call
  `self._build_service.retire_superseded_bot(bot_uuid, e.error_code)`.
- Ordering is **retire-then-first_release** (crash-safe, see Risks): the old bot
  is destroyed before the replacement is created. The old bot is already
  gone/failed in this branch, so there is no live-service gap.

### 5. Restart recreate retires the old bot (`restart_mixin.py`)
- In `execute_restart`'s `except TargetBotGoneError as e:` block, before
  `_recreate_restart_target(...)`, call
  `self._build_service.retire_superseded_bot(bot_uuid, e.error_code)`.
- `_recreate_restart_target` itself is otherwise unchanged (it already mints a
  new bot + binding as a crash-safe FIRST_RELEASE op).

### 6. Cleanup primitive (`bot_build_service.py`)
- `retire_superseded_bot(self, bot_uuid, error_code)`:
  - `if error_code != "DEVICE_NOT_FOUND": return` (BOT_NOT_FOUND → nothing to
    clean; already gone).
  - `try: self._baas_service.destroy_bot(bot_uuid)` — idempotent on the BaaS side
    (destroy tolerates already-gone); `except Exception: log.warning(...)` and
    swallow. Never raises into the deploy path.

### 7. Tests
- Unit, per seam, table-driven on `(provider, prior_baas_status, upgrade_result)`:
  - retry, ARCA, prior `FAILED` → upgrade succeeds, **same bot_uuid**, no destroy,
    no new bot.
  - retry, TeClaw, prior `FAILED`, upgrade → `DEVICE_NOT_FOUND` → `destroy(old)`
    called once, then first_release → exactly one live bot.
  - re-publish, prior `STOPPED` → attempts upgrade (not straight first_release);
    TeClaw path destroys old + recreates; ARCA reuses.
  - restart, target `DEVICE_NOT_FOUND` → destroy old + recreate; target
    `BOT_NOT_FOUND` → recreate, **no** destroy.
  - `retire_superseded_bot`: `BOT_NOT_FOUND` → no destroy; `DEVICE_NOT_FOUND` →
    destroy; destroy raising → swallowed, deploy still proceeds.
  - `TargetBotGoneError` carries the code end-to-end (atom → fallback).
- Crash-safety: redelivery of the fallback after `destroy(old)` but before
  first_release re-derives the (now-gone) old bot → `BOT_NOT_FOUND` branch →
  no double-destroy, no orphan, single live bot (idempotency test).
- Regression: update the existing `dcce9a6` tests that asserted "prior `FAILED`/
  `STOPPED` → first_release" to the new "attempt upgrade, clean up on fallback"
  outcome. The **observable** end state (single live online bot, re-publish
  after offline works) is preserved.

## Dependencies
- Reuses existing `baas_service.get_bot` (status read) and
  `baas_service.destroy_bot` (cleanup) — no new BaaS endpoints.
- No new packages, no config, no feature flag (behavior is strictly safer).

## Risks & Mitigations
- **Destroying a bot that is actually live.** `retire_superseded_bot` fires only
  on `DEVICE_NOT_FOUND` (the container is provably gone) and never on a
  successful upgrade or an `ACTIVE` status. Mitigation: gate strictly on the
  error code; unit test that `ACTIVE`/success never calls destroy.
- **Crash between destroy-old and create-new.** Mitigated by ordering
  (retire-before-recreate) + idempotency: on redelivery the fallback re-derives
  the old `bot_uuid` from `ext.binding.online` (not yet overwritten) → upgrade →
  now `BOT_NOT_FOUND` → no destroy, first_release adopts the in-doubt new bot by
  query. No orphan, no duplicate.
- **Narrowing the blocking set changes re-publish-after-offline (`dcce9a6`).**
  The offline→re-publish case (prior `STOPPED`) now *tries* upgrade first;
  TeClaw's gone container yields `DEVICE_NOT_FOUND` → destroy + recreate — same
  net result as today (one live bot) but without the orphan. Covered by keeping/
  adapting `dcce9a6`'s regression tests.
- **`DESTROYING` candidate.** Upgrading a `DESTROYING` bot is rejected by BaaS
  (not a gone-code). Mitigation: include `DESTROYING` in the blocking set so it
  routes to first_release; it self-terminates to `RELEASED`, so no retire needed.

## Alternatives Considered
- **Uniform "always destroy old + create new" (no reuse).** Simpler, but churns a
  new `bot_uuid` even on ARCA where an in-place rebuild would recover the bot and
  keep identity/state. Rejected in favor of try-upgrade-then-clean-up, which is
  only marginally more code and strictly better.
- **Per-seam patches.** Rejected — the decision is identical across seams; three
  copies would drift again (this bug *is* that drift).
- **Ledger-recorded retire step for crash-safety.** Considered, but the
  fallback's natural re-derivation + destroy idempotency already give
  crash-safety without extra bookkeeping. Kept out to minimize surface.

## Rollout
- Single change set to `REL20260728`; no migration, no flag. Safe to ship
  directly — the new behavior only ever *removes* bots that BaaS reports as gone.
- Prevents *new* orphans. The existing production backlog (already-orphaned bots)
  is cleaned by a separate one-off reconciliation (out of scope; see spec).

## Test Strategy
- Module unit tests (above) run under the standard service-bot suite.
- No E2E change required; existing publish-boundary E2E should stay green (they
  assert single-bot end states, which this preserves). Run them as a guard.
- Manual/staging sanity: force an online first-release failure on an ARCA bot,
  retry, confirm the same `bot_uuid` is recovered and no second `baas_bot` row
  appears; repeat on a TeClaw bot and confirm the old record is destroyed and a
  single new bot exists.
