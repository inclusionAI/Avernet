# Tasks: Per-Bot Startup Script

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## [x] Task 1: Store a bot's startup script
- **Goal:** Persist one script per bot with the audit fields the public API needs.
- **Files:** `src/backend/.../core/bot_startup_script/sql/2026_08_10_bot_startup_script.sql`,
  `.../core/bot_startup_script/repository/models.py` (ORM model),
  `.../core/repository/protocols/bot/startup_script.py` (contract),
  `.../core/repository/implementations/bot/startup_script.py` (ORM body),
  `.../core/bot_startup_script/services/startup_script_service.py`,
  `src/backend/.../api/bot_startup_script_service.py`
- **Done when:**
  - [x] DDL matches `plan.md`; table created on a clean SQLite and MySQL boot.
  - [x] The repository contract lives under `core/repository/protocols/bot/` with
        `@abstractmethod` on every member, and the implementation under
        `core/repository/implementations/bot/` declares it as a base — so omitting
        a member fails at construction, per `core/repository/README.md:8`.
  - [x] `get` on a bot that never set one returns an empty record, not an error.
  - [x] `put` stores body, `size_bytes` and the modifier; over-limit raises a typed
        error naming the limit.
  - [x] `delete` is idempotent.
  - [x] `get_body` returns `""` for an unset bot — the payload path must never
        branch on `None`.
  - [x] Protocol declares real signatures; the concrete service does **not**
        inherit it; the pair is registered in `_PAIRS`
        (`test_service_api_conformance.py:76`).
- **Depends on:** —

## [x] Task 2: Compose the script into the start sequence
- **Goal:** Append the script to `_get_start_cmd`'s output so it runs after the
  platform's boot steps and before the callback, without being able to affect them.
- **Files:** `src/backend/.../core/service_bot/services/baas_service.py`
- **Done when:**
  - [x] No stored script ⇒ the returned string is **byte-identical** to today.
  - [x] The body is base64-encoded in Python and never interpolated into shell
        syntax; a body with quotes, `$(id)`, `HOOK_SCRIPT_EOF` and `{token}`
        round-trips byte-exact and is not placeholder-substituted by BaaS.
  - [x] The platform's exit status is captured to `__OCB_RC` before the script runs
        and re-asserted with `exit $__OCB_RC`, so a failing boot still reports
        non-zero **with** a script present.
  - [x] The script is skipped when the platform chain failed.
  - [x] The script runs under `timeout`, and its output goes to a dedicated log.
  - [x] `desktop_bot_service.py:1081` still compiles and is unaffected (the new
        parameter defaults to unset).
- **Depends on:** Task 1

## [x] Task 3: Carry the script into the create payload
- **Goal:** The stored script reaches `_get_start_cmd` on every create and restart.
- **Files:** `src/backend/.../core/devices/services/baas_device_service.py`,
  `src/backend/.../core/service_bot/services/baas_service.py`
- **Done when:**
  - [x] `_build_create_bot_payload` resolves the script centrally and forwards it
        to `_get_start_cmd`, so create, service-bot release **and** `upgrade_bot`
        (restart) all deliver it. Resolving in `_allocate_via_baas` instead was
        the first attempt and missed the restart path — the only path that can
        deliver a script, since one is written after the bot exists.
  - [x] A bot created before its owner writes a script picks it up on the next
        restart, and the API docs say the first write needs a restart.
  - [x] Personal and service bots behave identically; only `stage` differs, as today.
  - [x] Editing the script alone does not touch a running container.
- **Depends on:** Task 2

## [x] Task 4: Public API — read, replace, clear
- **Goal:** Expose the three script operations on the bots group.
- **Files:** `src/backend/.../adapters/http/openapi_v1/bots/router.py`,
  `.../bots/schemas.py`, `.../openapi_v1/responses.py`, `.../openapi_v1/admission.py`,
  `src/backend/.../di/modules/` (registration)
- **Done when:**
  - [x] `GET` / `PUT` / `DELETE /openapi/v1/bots/{bot_id}/startup-script` served.
  - [x] Every response uses `Envelope[T]`; errors use the standard shape.
  - [x] Each route declares `dependencies=_GRANT_CHECKED` and is registered in
        `ADMISSION` as `GRANT_CHECKED_OWN_BOT`.
  - [x] Ownership guard runs before any read or write; a non-operator gets 403.
  - [x] Oversize body → 413 whose message names the limit.
  - [x] `PUT` accepts only `{"script": ...}`; `updated_by` comes from the request
        principal and `updated_at` from `gmt_modified` — neither is client-supplied,
        and `extra="forbid"` fails a body that tries to set them (422) rather than
        dropping the value behind a 200.
  - [x] `supported` / `unsupported_reason` reported per bot. Support is a
        property of the **engine**, asked of `TeclawProvisionService.is_teclaw`
        (the single definition) rather than compared as a string here, and it
        never consults the bot's live container — so the answer is stable before
        the first start and during a lookup failure. Reworked at review: the
        earlier version keyed on the resolved `device_provider` and needed a
        third "inconclusive" state (503) purely to cover that lookup failing.
  - [x] Storage is scoped by `avernet_tenant` — column, guard registration and
        uniqueness key — because `ac_bots` is itself tenant-scoped, so a
        `bot_id` is unique only within a tenant. Without it two tenants
        colliding on `(entity_id, bot_id)` would share one row and each could
        overwrite the other's script.
  - [x] `PUT` on an unsupported bot is **refused** with 409; nothing is stored.
        The reason is served by `GET`, not the refusal — this surface's error
        messages are fixed by contract and never `str(exc)`.
  - [x] `GET` on an unsupported bot still answers — empty script,
        `supported: false`, reason naming the cause — rather than erroring.
  - [x] No `entity_id` parameter or response field anywhere (group contract);
        it exists only as a storage key resolved server-side.
- **Depends on:** Task 1

## ~~Task 5: Public API — read the last container start~~ — DESCOPED
**Built, then removed at review.** The whole `last-start` surface is out of this
change: the endpoint, `StartInstanceResult`, `BotStartupScriptRunReaderProtocol`,
the reader service, its DI provider, its `ADMISSION` entry and its tests.

The reason is a real limitation, not a preference: resolving *which* start to
report from the bot record only works for a personal bot or a **draft** service
bot. A published service bot does not carry the publish this reads, so the
endpoint would answer emptily for a whole class of bot while looking like it
worked — the same silent-wrong-answer failure the rest of this feature is careful
to avoid.

Getting create / update / delete right first, and treating the run result as
follow-up work with its own design, is the correct order. The docs now state
plainly that there is no API to read a run's result and that the container log is
the only place to see it.

- **Depends on:** —

## [x] Task 6: Publish the contract and document the limits
- **Goal:** The gateway serves the routes, and the promises a caller cannot infer
  from the schema are written down.
- **Files:** `src/gateway/configs/schemas/bots.openapi.json`,
  `src/backend/docs/openapi-v1/README.md`, `README.zh-CN.md`
- **Done when:**
  - [x] Schema regenerated with `dump_openapi.py`; all three operations present with
        security metadata; `src/gateway/tests/fixtures/bots.openapi.json` **not**
        regenerated.
  - [x] Docs state: runs on every start the platform composes and must be
        idempotent; the size limit and the timeout; that a failure degrades rather
        than blocks; that secrets must not be placed in the body; and that there is
        no API to read a run's result — the container log is the only place.
  - [x] Docs state the two limits plainly — the script does not re-run on providers
        whose restart is in-place, and teclaw bots cannot run it at all.
- **Depends on:** Task 4

## [x] Task 7: Tests & Verification
- **Goal:** Ensure the feature meets the spec's acceptance criteria.
- **Files:** `src/backend/tests/...`
- **Done when:**
  - [x] Every acceptance criterion in `spec.md` maps to a passing test.
  - [x] Backend and gateway module CI gates pass (`OCB_PRE_PUSH_RUN_CI=1`).
  - [x] All three endpoints carry happy **and** error cases in
        `tests/community/endpoints/test_openapi_startup_script.py`, so the flow
        coverage gate is satisfied by tests rather than by a
        `coverage_baseline.txt` entry — that file is byte-identical to `dev`.
        The "blocked on the #651 principal minter" note does not apply here: a
        case mints its own gateway principal, and a **user** principal suffices
        because `require_granted_bot` is a no-op for a human caller.
  - [x] A bot with no script produces a byte-identical start sequence — asserted,
        not assumed.
- **Depends on:** Task 6

---

## Groups

- **Group A — Storage and composition:** Tasks 1, 2, 3
  - Theme: The script is stored and reaches the container, safely, on every start
    the platform composes.
- **Group B — Public surface:** Task 4 (Task 5 descoped at review)
  - Theme: Owners can manage the script.
- **Group C — Contract publication:** Task 6
  - Theme: The gateway serves it and the limits are written down.
- **Group D — Verification:** Task 7
  - Theme: Final spec acceptance check.
