# Tasks: Per-Bot Startup Script

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: Store a bot's startup script
- **Goal:** Persist one script per bot with the audit fields the public API needs.
- **Files:** `src/backend/.../core/bot_startup_script/sql/2026_08_10_bot_startup_script.sql`,
  `src/backend/.../core/bot_startup_script/repository/models.py`,
  `src/backend/.../core/bot_startup_script/services/startup_script_service.py`,
  `src/backend/.../api/bot_startup_script_service.py`
- **Done when:**
  - [ ] DDL matches `plan.md`; table created on a clean SQLite and MySQL boot.
  - [ ] `get` on a bot that never set one returns an empty record, not an error.
  - [ ] `put` stores body, `script_sha256`, `size_bytes`, and the modifier.
  - [ ] A body over the size limit raises a typed error naming the limit.
  - [ ] `delete` is idempotent — deleting an absent script succeeds.
  - [ ] Protocol declares real signatures (not `*args/**kwargs`), and the concrete
        service does **not** inherit it (`core → api` import is forbidden).
  - [ ] The `(Protocol, ConcreteService)` pair is registered in `_PAIRS`
        (`test_service_api_conformance.py:76`) — the repo's link between the two,
        and what makes them navigable from one file.
- **Depends on:** —

## Task 2: Public API — read, replace, clear
- **Goal:** Expose the three script operations on the bots group.
- **Files:** `src/backend/.../adapters/http/openapi_v1/bots/router.py`,
  `.../openapi_v1/bots/schemas.py`, `.../openapi_v1/responses.py`,
  `src/backend/.../di/modules/` (registration)
- **Done when:**
  - [ ] `GET` / `PUT` / `DELETE /openapi/v1/bots/{bot_id}/startup-script` served.
  - [ ] Every response uses `Envelope[T]`; errors use the standard shape.
  - [ ] Ownership guard runs before any read or write; a non-operator gets 403.
  - [ ] Oversize body → 413 whose message names the limit.
  - [ ] `entity_id` never appears as a parameter (group contract).
  - [ ] `test_bots_startup_script.py` covers empty-read, write-then-read, delete, 403, 413.
- **Depends on:** Task 1

## Task 3: Declare startup-script capability on providers
- **Goal:** Make "can this container run a script" a declared property, not a
  `provider_type` string check.
- **Files:** `src/baas/.../api/paas/_protocols.py`,
  `src/baas/.../api/device_manage/_deploy_config.py`,
  `src/baas/.../core/service/paas/_{arca,k8s,standalone,poolab,teclaw,local}_paas_service.py`,
  `src/baas/tests/contract/spi/`
- **Done when:**
  - [ ] `supports_startup_script()` / `run_startup_script()` on the `PaasService` Protocol.
  - [ ] Arca, K8s, Docker/standalone, Poolab declare `True`; TeClaw and Local declare `False`.
  - [ ] `DeployConfig` carries `startup_script`, `startup_script_sha256`,
        `startup_script_timeout_seconds`, `startup_script_secret_envs`.
  - [ ] `check_protocols/api/paas/check_paas_service.py` gains a mypy-checked binding
        **per provider** — today it binds Arca only, so the new methods would go
        unchecked on the other five implementations.
  - [ ] A conformance test fails if any `PaasService` implementation omits the declaration.
- **Depends on:** —

## Task 4: Record and report runs
- **Goal:** Persist one row per container start and accept the result callback.
- **Files:** `src/baas/sqls/2026_08_10_device_startup_run.sql`,
  `src/baas/.../core/service/device_startup_script/_repository.py`,
  `src/baas/.../adapters/web/routers/bot_service/startup_script_router.py`
- **Done when:**
  - [ ] `POST /api/v1/devices/startup-script-callback` persists status, exit code,
        output, truncation flag, and `finished_at`.
  - [ ] Repeat callbacks for the same `run_id` are idempotent (unique key, no error).
  - [ ] The callback never reads or writes publish/batch/bot state.
  - [ ] A callback for an unknown device returns 404.
  - [ ] `GET /api/v1/bots/{tenant}/{bot_uuid}/startup-script/runs` returns the latest
        run per device, one row per instance.
- **Depends on:** Task 3

## Task 5: Make the dispatcher safe for caller-authored content
- **Goal:** Reuse the wrapper mechanism without letting a script body break out of it.
- **Files:** `src/baas/.../core/service/paas/_start_hook_dispatcher.py`
- **Done when:**
  - [ ] Script body transfers base64-encoded; it is never interpolated into shell syntax.
  - [ ] A body containing `HOOK_SCRIPT_EOF`, `$(id)`, and unbalanced quotes round-trips
        byte-exact.
  - [ ] The wrapper enforces the timeout itself and reports `TIMEOUT` distinctly from a
        non-zero exit.
  - [ ] Output is truncated at the documented cap with the truncation flag set.
  - [ ] Resolved secret values are masked from stdout/stderr before they are sent.
  - [ ] The platform hook's existing behavior is unchanged (its tests still pass).
- **Depends on:** Task 3

## Task 6: Run the stage on every container start
- **Goal:** One seam, called wherever a device becomes reachable — including the
  restart paths that skip hooks today.
- **Files:** `src/baas/.../core/service/device_startup_script/_stage.py`,
  `src/baas/.../core/service/device_manage/_device_service.py`,
  `src/baas/.../core/service/publish_manage/` (ACTIVE transition)
- **Done when:**
  - [ ] Stage dispatches after the device reaches `ACTIVE`, never before.
  - [ ] Wired at all three sites: no-hook fast path, publish-callback ACTIVE, and
        `_native_restart_device`.
  - [ ] K8s, Docker, and Poolab run the script on restart (they never did before).
  - [ ] Arca's destroy+create restart dispatches **exactly once** per start.
  - [ ] A non-zero exit, a timeout, or a dispatch failure leaves the device `ACTIVE`.
  - [ ] No stored script, unsupported provider, or kill switch off ⇒ no dispatch and
        no run row.
  - [ ] `startup_script_stage_enabled` in `baas_system_config` gates the whole path.
- **Depends on:** Tasks 4, 5

## Task 7: Carry the script from Avernet to BaaS
- **Goal:** Send the current script on create and on restart, so "takes effect on
  next start" is exact.
- **Files:** `src/backend/.../core/service_bot/services/baas_service.py`,
  `src/backend/.../api/baas_service.py`,
  `src/baas/.../adapters/web/routers/bot_service/management_router.py`
- **Done when:**
  - [ ] `startup_script` rides in the create payload's deploy config, separate from
        `after_create_cmd_hook`, whose value is byte-identical to today.
  - [ ] Restart sends the latest script, so an edit + restart runs the new one.
  - [ ] Editing the script alone does not touch a running container.
  - [ ] Both service-bot and desktop-bot create paths compile against the new field.
- **Depends on:** Tasks 3, 6

## Task 8: Public API — read run results
- **Goal:** Let a caller see what the last run did, per instance.
- **Files:** `src/backend/.../adapters/http/openapi_v1/bots/router.py`,
  `.../bots/schemas.py`, `src/backend/.../core/bot_startup_script/services/`
- **Done when:**
  - [ ] `GET /openapi/v1/bots/{bot_id}/startup-script/runs` returns one entry per instance.
  - [ ] Each entry carries status, exit code, output, truncation, and timestamps.
  - [ ] A scaled bot whose instances disagree reports both outcomes, not a summary.
  - [ ] `GET .../startup-script` reports `supported` / `unsupported_reason` from the
        bot's **container provider**; bot type is not an input.
  - [ ] A personal bot and a service bot on the same provider get the same answer —
        both reach the platform startup hook through `_build_create_bot_payload`.
  - [ ] A TeClaw-backed bot reads as unsupported, with the provider named as the reason.
  - [ ] A run whose recorded hash differs from the stored script is still returned
        (stale-instance visibility).
- **Depends on:** Tasks 2, 4, 7

## Task 9: Publish the public contract
- **Goal:** The gateway serves and validates the new routes.
- **Files:** `src/gateway/configs/schemas/bots.openapi.json`
- **Done when:**
  - [ ] Schema regenerated with `src/backend/scripts/dump_openapi.py` (sorted keys).
  - [ ] All four operations appear with security metadata and the envelope shape.
  - [ ] `src/gateway/tests/fixtures/bots.openapi.json` is **not** regenerated.
  - [ ] Gateway startup and forwarding tests pass against the new artifact.
- **Depends on:** Tasks 2, 8

## Task 10: Document the contract
- **Goal:** State the promises a caller cannot infer from the schema.
- **Files:** `src/backend/docs/openapi-v1/README.md`, `README.zh-CN.md`
- **Done when:**
  - [ ] Documents that the script runs on **every** start and must be idempotent, and
        that the platform does not dedupe.
  - [ ] States the size limit, the timeout, the output cap, and the permitted interpreter.
  - [ ] States that a failing script degrades the bot rather than blocking it.
  - [ ] States which providers can run it and what an unsupported bot returns.
- **Depends on:** Task 8

## Task 11: Tests & Verification
- **Goal:** Ensure the feature meets the spec's acceptance criteria.
- **Files:** `src/backend/tests/...`, `src/baas/tests/...`
- **Done when:**
  - [ ] Every acceptance criterion in `spec.md` maps to a passing test.
  - [ ] Module CI gates pass for backend, baas, and gateway (`OCB_PRE_PUSH_RUN_CI=1`).
  - [ ] A bot with no script shows byte-identical start behavior to before the change.
- **Depends on:** Tasks 9, 10

---

## Groups

- **Group A — Backend storage & public surface:** Tasks 1, 2
  - Theme: A bot can hold a startup script and owners can manage it, before anything runs it.
- **Group B — Provider capability contract:** Task 3
  - Theme: Whether a container can run a script becomes declared, so "unsupported" is answerable.
- **Group C — BaaS execution stage:** Tasks 4, 5, 6
  - Theme: The script actually runs — safely, on every start, without taking the agent down.
- **Group D — Cross-service wiring:** Tasks 7, 8
  - Theme: The script reaches the container and its results come back to the caller.
- **Group E — Contract publication:** Tasks 9, 10
  - Theme: The gateway serves it and the promises are written down.
- **Group F — Verification:** Task 11
  - Theme: Final spec acceptance check.
