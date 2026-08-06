# Plan: Teclaw Restart Is Not Supported

## Approach
Close the destructive path at its source with a single guard at the top of
`BotService.restart_bot`, before any state is read or written.

Three decisions shape the change:

**1. Guard on the bot's engine, not the binding's `device_provider`.**
`self.is_teclaw_bot(bot.get("active_engine"))` is the canonical teclaw
definition, delegating to `TeclawProvisionService.is_teclaw`. Keying on it
rather than the binding's provider column has three benefits: it works for a
teclaw bot with no binding (a `FAILED` bot whose binding was lost), it needs no
device-service round trip before the guard can fire, and it adds no new read of
the `device_provider == "teclaw"` axis — a value that conflates lifecycle
ownership with container flavor and is a known cleanup target. The two axes
agree in practice: a bot's container type follows its engine.

**2. Reuse `BotOperationNotAllowedError`.** Its docstring already states the
exact semantics needed — *"The operation is not supported for this bot and never
will be. Distinct from a transient failure: retrying cannot help, so delivery
surfaces should report it as a client error rather than a server fault."* It is
already the idiom for this shape of refusal (desktop bots use it in the OpenAPI
v1 router), and every restart surface already handles it. No new exception type,
no router changes.

**3. Place the guard beside the existing desktop guard.** Both answer the same
question — "is this bot's lifecycle managed by something other than the generic
device path?" — and both must fire before the lifecycle-state checks, the
restart lock, and any repository write. Sitting them together keeps the
precondition block readable and makes the inertness obvious by position.

The existing caller-side workarounds in `BotPublishService` and
`CreateBotForOthersService` are deliberately **left in place**. Neither is purely
a bug workaround: each expresses correct local behavior (skip a restart that is
unnecessary; report a domain-specific 400). With the service-level guard they
become defense in depth. Removing them would churn working code on a release
branch for no behavioral gain.

## Affected Components
| Component | Change |
| --- | --- |
| `BotService.restart_bot` | New teclaw precondition guard (the entire fix) |
| `bot_management` HTTP router | None — already maps the error on all three routes |
| `openapi_v1` bots router | None — mapped via `responses.py` |
| Web client (`useBot.ts`) | None — already surfaces a rejected restart correctly |
| `BotPublishService` / `CreateBotForOthersService` | None — existing guards retained |
| Tests | New coverage across service, surfaces, and internal callers |

## Data Model Changes
None. The guard performs no writes.

## API / Interface Changes
No signature changes. One behavioral change, surfaced through existing error
mappings:

| Surface | Before (teclaw) | After (teclaw) |
| --- | --- | --- |
| `POST /api/bots/{bot_id}/restart` | 200, container destroyed, bot → `FAILED` | 400, `"teclaw ..."`, nothing changed |
| `POST /api/bots/{bot_id}/restart-for-others` | as above | 400 |
| `POST /api/bots/restart-scheduler` | as above | 400 |
| `POST /v1/bots/{bot_id}/restart` | as above | 409 (`responses.py:177`) |

The OpenAPI v1 surface maps `BotOperationNotAllowedError` to **409**, not 400
(`responses.py:177`: `(409, "Operation not supported for this bot")`). Both are
client errors, satisfying the acceptance criterion; the asymmetry is pre-existing
and is not changed here.

Non-teclaw restart behavior is untouched on every surface.

## Key Files & Functions

### 1. The guard (`core/bot_management/services/bot_service.py`)
`restart_bot` begins at `:3833`. It loads the bot, then rejects desktop bots at
`:3870-3877`. Insert the teclaw guard immediately after that block — before the
`bot_status` read at `:3879`, the restart lock at `:3996`, and `stop_bot` at
`:4051`:

```python
if self.is_teclaw_bot(bot.get("active_engine")):
    raise BotOperationNotAllowedError("teclaw 类型的 Bot 不支持重启")
```

Message wording matches the existing refusal in `CreateBotForOthersService` so
users see one consistent string.

`is_teclaw_bot` is at `:3689`; `BotOperationNotAllowedError` at `:149`. Both are
already imported/defined in this module — no new imports.

### 2. Surfaces (no production changes)
Verified handlers on the current branch:
- `adapters/http/bot_management/router.py:414` (`restart_bot_for_others`) → 400
- `adapters/http/bot_management/router.py:501` (`restart_scheduler`) → 400
- `adapters/http/bot_management/router.py:2685` (`restart_bot`) → 400
- `adapters/http/openapi_v1/responses.py:177` → 409 envelope

### 3. Internal callers (no production changes)
- `bot_publish_service.py:1250` — already branches on `is_teclaw_bot` and skips
  the restart; the guard is never reached from here.
- `create_bot_for_others_service.py:307` — reachable for a teclaw bot when the
  bot is not `ACTIVE` and no restart-wait applies. It will now receive
  `BotOperationNotAllowedError` instead of silently destroying the container.
  Confirm this surfaces as a client error rather than an unhandled 500.

### 4. Tests
- `tests/community/core/bot_management/services/` — service-level guard behavior
  and inertness (new file).
- `tests/community/api/bot_management/test_router.py` — surface mappings.
- `tests/community/core/bot_management/services/test_create_bot_for_others_service.py`
  — internal caller behavior.

## Dependencies
None. No new packages, no DI changes, no migrations.

## Risks & Mitigations
| Risk | Mitigation |
| --- | --- |
| A legitimate teclaw restart use case exists that we are now blocking | Confirmed with the teclaw owner that restart is not a supported teclaw operation. The prior behavior was destructive, so nothing usable is lost. |
| `is_teclaw_bot` and the binding's `device_provider` disagree for some bot, so a teclaw-provider binding slips past an engine-keyed guard | The engine is the creation-time determinant of container type, and teclaw bindings are only ever minted for teclaw-engine bots (`TeclawProvisionService.provision`). A test asserts the guard fires for a teclaw bot regardless of binding state, including no binding at all. |
| Guard placed too late and a partial mutation still occurs | Placement is above every read and write in the method; a test asserts no repository, device-service, or task-queue call is made. |
| Callers relying on restart succeeding for teclaw now see an exception | Both internal callers audited: one never reaches it, the other is covered by a task. |
| Users lose their only "fix my stuck bot" affordance | Real, and accepted: the affordance did not work — it destroyed the bot. Recovery path tracked in #869. |

## Alternatives Considered
**Restart teclaw in place via `update_teclaw_bot`.** Designed in full before
being dropped. It would reuse the existing teclaw publish-poll machinery
(`TeclawPublishTaskHandler` and `transition_teclaw_publish_terminal` work
unchanged for a restart publish, provided `device_props["publish_id"]` is
overwritten). Rejected because the teclaw owner confirmed there is no restart
semantics for a teclaw container: `update_teclaw_bot` re-delivers configuration
to an existing container, which is a different operation and does not recover a
wedged one. Building it would have produced a restart that does not restart.
Revisitable via #869 question 3.

**Register `teclaw` as a `DeviceService` provider** so the generic
`stop_bot` + `start_bot` path works. Rejected on two grounds: it entrenches
`teclaw` on the lifecycle-provider axis we want to remove, and mechanically
"working" would be semantically wrong — destroying a container and provisioning
an empty one is a reset, not a restart, and would silently discard the user's
workspace files.

**Return a silent success (`success: True`, no-op).** Rejected. The client
optimistically writes `PENDING` and begins polling on a successful response
(`useBot.ts:746-751`), so a user with a stuck bot would be told it was
restarting when nothing happened. Returning an error routes through
`handleApiError` (`useBot.ts:740-744`), which surfaces the message and returns
before the optimistic write — correct UX with no frontend change.

**Guard at each delivery surface** instead of the service. Rejected: that is
what the two existing caller-side workarounds already do, and it is why the bug
survived — the surfaces nobody remembered still reach the destructive path.

## Rollout
Single PR onto `REL20260806`. No feature flag, no migration, no config. The
change is inert for every non-teclaw bot and strictly removes a destructive
operation for teclaw bots, so it can ship and be reverted freely.

Bots already broken by the prior behavior have been recovered manually; no
backfill is required.

## Test Strategy
**Service-level (primary).** The guard fires for a teclaw bot across `ACTIVE`,
`FAILED`, and `PENDING`, with a live binding, a stale binding, and no binding.
The strongest assertion is inertness: with mocked collaborators, assert the
device service, binding repository, bot repository, restart-lock repository and
task queue receive **no** calls — specifically no `release_device`, no
`destroy_bot`, no status write, no lock acquisition.

**Regression pin.** A test named for the original defect asserting that a teclaw
restart never reaches `stop_bot` — the single behavior whose reintroduction
would re-destroy containers.

**Non-regression.** Existing restart tests
(`test_bot_service_restart_idempotency.py`, `test_bot_service_restart_baas_envs.py`,
`test_bot_service_aix_extra_envs_restart.py`, `test_bot_service_stop_start.py`)
must pass unchanged, proving BaaS and arca paths are untouched.

**Surfaces.** Each of the four restart endpoints returns its client error for a
teclaw bot, asserted against the mapped status codes above.

**Internal caller.** `CreateBotForOthersService` reports a client error for a
teclaw bot reaching its restart call.

Full module gates before push per `AGENTS.md` (`OCB_PRE_PUSH_RUN_CI=1`), with
the merge target set to `REL20260806`.
