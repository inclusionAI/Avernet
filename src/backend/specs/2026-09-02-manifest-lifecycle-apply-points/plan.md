# Plan: Lifecycle Apply Points (W8)

Spec: `spec.md` in this directory. Work item W8, issue #1476.

## What already exists, and what that leaves

Everything that *applies* is built. W8 adds callers, one wait, one alias view,
and removes one refusal.

| Already there | Where | What W8 gets from it |
| --- | --- | --- |
| `start_apply(..., trigger=, phases=, bot=…)` — lock, re-validate, `RUNNING` row, enqueue | `services/config_manifest_apply_service.py` | The one entry point every lifecycle point calls; nothing here runs the orchestrator itself |
| `get_apply(entity_id, bot_id, apply_id)` / `last_apply` | same | The republish wait reads the record it started |
| `ManifestApplyInProgressError` raised **before** an id exists | same | Every lifecycle caller treats it as "wait" or "not started", never as failure |
| `_last_resolutions` → `SourceSession.baselines` | same, `apply/source_session.py` | D2's `strict` / `non_strict` is enforced for free once a lifecycle point goes through `start_apply` |
| `DeviceActivatedEvent` + `EventBus` + `SkillSymlinkListener` precedent | `core/events`, `core/skill_center/services/skill_symlink_listener.py` | The restart/first-boot hook and the exact pattern for it (resolve bot by binding, skip stale binding, never raise) |
| `PublishVerifyFlowHandler` with `Reschedule`, and `PublishFlowService._mutate_and_update_ext` | `publish_flow/tasks.py`, `publish_flow_service.py` | A durable place to wait before build, and a durable marker |
| `TeclawPublishTaskHandler._persist_terminal` | `bot_management/services/teclaw_publish_task_handler.py` | The one activation that publishes no event; gets a callback |
| `BotCreationManifestSeam.preflight` + `preflight_creation_manifest(is_teclaw=…)` | `bot_config_manifest/creation.py` | The refusal to delete |
| `BotStartupScriptService.put/delete/get_body`, `placeholders.resolve` | `core/bot_startup_script`, `bot_config_manifest/schema/placeholders.py` | The write-through's row write, with the same substitution the materialiser uses |
| `find_create_job(task_queue, tenant=, entity_id=, bot_id=)` | `bot_config_manifest/create_job.py` | "Is a W13 creation live for this bot?" — indexed while live |

## Architecture

```text
PUT /openapi/v1/bots/{bot_id}/config-manifest
  ├─ manifest_service.put(...)                          ← unchanged
  └─ apply_service.start_apply(trigger="put", ALL_PHASES)
        ├─ ApplyAccepted            → response.apply = {apply_id, RUNNING}
        ├─ ManifestApplyInProgress  → response.apply = {NOT_STARTED, apply_in_progress}
        └─ anything else (logged)   → response.apply = {NOT_STARTED, not_started}

DeviceService.report_device_alive ──publish──► DeviceActivatedEvent
TeclawPublishTaskHandler._persist_terminal ──callback──┐
                                                       ▼
                      ManifestLifecycleListener.on_bot_activated(binding_id)
                        ├─ bot = bots.get_by_binding_id(binding_id)   None → skip
                        ├─ manifests.get(entity, bot) is None          → skip
                        ├─ resolver.resolve_for_bot(...).binding_id != binding_id → skip
                        ├─ find_create_job(...) live                   → skip (W13 owns it)
                        └─ start_apply(trigger="start", ALL_PHASES)    in-progress → skip, log

PublishVerifyFlowHandler._run   (status == BUILDING)
  ├─ flow.manifest_apply_before_build(record, operator)
  │     ├─ ext.manifest_apply.build absent, bot has manifest
  │     │     → start_apply(trigger="republish") ; ext.manifest_apply.build = apply_id ; WAIT
  │     ├─ ext.manifest_apply.build = apply_id, get_apply RUNNING        → WAIT
  │     ├─ terminal (any status), or "none" / "not_started" marker       → PROCEED
  │     └─ InProgress → WAIT ; other start failure → marker "not_started" ; PROCEED
  ├─ WAIT     → Reschedule(POLL)
  └─ PROCEED  → execute_build_phase(...)                            ← unchanged

POST /openapi/v1/bots/with-manifest        ← teclaw refusal removed; job unchanged

PUT/DELETE/GET /openapi/v1/bots/{bot_id}/startup-script
  ├─ manifest_service.write_through_script(...) → ManifestWriteResult | None
  │     None  → legacy path, byte-for-byte                      ← unchanged
  │     result → response from the manifest's script
  └─ (GET) manifest declares script → its body ; else the row
```

## Key decisions

### K-1 The listener is a manifest-package `LifecycleBase`, discovered like the others

`core/bot_config_manifest/lifecycle_listener.py` holds `ManifestLifecycleListener`.
It subscribes `handle(DeviceActivatedEvent)` in `startup()` with the same
idempotent double-subscribe guard the skill listener uses, and exposes
`on_bot_activated(binding_id: int)` for the teclaw callback. Both paths run one
private `_apply_for_binding`.

Collaborators, all providers so the listener can be built while the injector
is still walking bindings: bot repository, manifest service, apply service,
device-context resolver, task-queue provider (for `find_create_job`), and the
tenant (read at call time — the event fires on a tenant-bound thread).

The `start` apply is started with `owner_id = actor_id = bot["owner_id"]`. The
owner is the only principal a lifecycle has; it is what the skill listener and
the W13 job already use.

It **never raises**: a failure to start is logged at warning. `report_device_alive`
is a callback path; the bus isolates handler failures, but the callback from the
teclaw handler is direct and must not turn a persisted activation into a
`Retry`.

### K-2 The republish wait lives in the flow, the outcome in the handler

`PublishFlowService` gets a mixin `publish_flow/manifest_apply_mixin.py` with one
method, `manifest_apply_before_build(record, operator) -> bool` ("still
waiting?"). The handler stays a step machine: one extra branch before
`execute_build_phase` returning `Reschedule(POLL_DELAY)`.

The marker is `ext["manifest_apply"]["build"]`, one of: an `apply_id`, `"none"`
(no manifest), `"not_started"` (could not start, reason logged). Written through
`_mutate_and_update_ext`, so it rides the same optimistic lock as every other
ext change. Re-entrancy: the marker is read first on every tick.

The apply targets the **source bot** (`record.source_bot_id`, owner
`record.owner_id`) at its draft stage, which is the only stage the manifest's
ports address. `actor_id` is the operator that drove `process`.

The mixin reaches the manifest layer through two lazy providers injected on
`PublishFlowService` (`manifest_service_provider`, `apply_service_provider`),
resolved from the injector by `service_bot_module`. Lazy because the apply graph
is large and `service_bot` must not import `bot_config_manifest` at module
scope; the boundary README lists the dependency.

Bounds: the apply lock TTL (`APPLY_LOCK_TTL_SECONDS`) and the apply task's own
deadline bound the wait; a report stranded `RUNNING` reads terminal once its
lock is stale (W4), so the wait cannot outlive it. The verify-flow task's
deadline stays what it is.

### K-3 teclaw activation: an optional callback, wired by DI

`TeclawPublishTaskHandler.__init__` gains `on_activated: Callable[[int], None] | None`.
`_persist_terminal` calls it (inside a try/except that logs) **after** a
successful transition to `ACTIVE` and before delivering the outbound rule, so a
delivery failure that retries the task does not re-fire it: the crash-resume
branch (`binding.status == ACTIVE` on entry) does not call it. `TeclawPublishTaskLifecycle`
passes it through; `bot_management_module` wires it to
`injector.get(ManifestLifecycleListener).on_bot_activated`.

### K-4 Lifting W13's refusal is a deletion

`preflight_creation_manifest` drops the `is_teclaw` violation; the parameter
goes with it (the seam's `is_teclaw` stays — it is not otherwise used by the
seam, so it is removed there too, and from the DI provider). `_TECLAW_REFUSAL`
is deleted. The route docstring's "ARCA-only" paragraph is replaced with the
first-boot statement. The job and the poll are untouched: `_CONTAINER_READY_STATUSES`
is the bot's status, which the teclaw terminal transition writes.

Tests: the two preflight tests asserting the refusal flip to assert acceptance
(`mcp: []` on teclaw preflights clean; `script` on teclaw is still refused by
the validator with `unsupported_script`). The endpoint scenario `teclaw_is_refused`
becomes `teclaw_is_accepted`.

### K-5 The `PUT` response grows an `apply` field

`ConfigManifest` gets `apply: ConfigManifestApplyStarted | None`, where the
model is `{apply_id: str | None, result: "RUNNING" | "NOT_STARTED", reason: str}`.
`GET` and `DELETE` leave it `None`. The `script` warning is appended in the
route from the parsed document (`result.record` has the document; whether
`script` is declared is `declared_entries(parsed, ManifestSection.SCRIPT) is not None`
— exposed through the service's `validate` result rather than re-parsed in the
adapter: `ManifestWriteResult` gains `declares_script: bool`).

### K-6 The write-through is one service method with a splice helper

`BotConfigManifestService.write_through_script(*, entity_id, bot_id, body: str | None,
modifier, active_engine, bot_type) -> ManifestWriteResult | None`:

1. `get` → `None` when there is no manifest: the caller falls back.
2. `splice_script_section(document, body)` (pure, `schema/splice.py`):
   `body is None` removes the top-level `script` section; otherwise replaces
   it, or appends one when absent. A section starts at a line matching
   `^script\s*:` and ends before the next line that starts at column 0 (a new
   top-level key or a column-0 comment) or at EOF. The body is rendered as a
   YAML literal block (`|`, `|-`, `|+` by trailing-newline count; an
   indentation indicator when the first line starts with a space). The helper
   then **parses its own output** and compares `parsed["script"]["body"]` to
   the body; on mismatch it renders a JSON-quoted scalar instead and checks
   again. A document it cannot splice (no parse) raises `ManifestValidationError`.
3. `put(...)` the spliced document — the same validation and all-or-nothing.
4. Write the row: `script_service.put(... script=placeholders.resolve(body, engine_type, env, tenant))`,
   or `script_service.delete` for `None`. The service gets a lazy
   `script_service_provider`, as the creation seam already has.

The router's `PUT` calls it after the existing support check and before the
legacy `put`; a returned result short-circuits the legacy write. `GET` reads
`manifest_service.script_body(entity_id, bot_id) -> str | None` (declared body
or `None`) and falls back to the row. The withdraw-if-deleted guard stays on
both arms.

### K-7 Trigger vocabulary and record reads

Triggers are constants in `apply/triggers.py`: `EXPLICIT`, `PUT`, `START`,
`REPUBLISH`, plus the two creation triggers re-exported from `creation.py` (which
keeps owning them, to avoid moving imports). The apply record's `String(32)`
column fits all of them; no migration.

`create_job._record_with_trigger` and the poll's `_creation_state` already
tolerate a foreign newest trigger (a later `explicit` apply); D-4 keeps the
listener from producing one *during* a creation.

### K-8 What is deliberately not touched

- `BotService.restart_bot`, `BaasService.upgrade_bot`, `_build_create_bot_payload`,
  `TeclawProvisionService.provision`, `execute_restart`: no seam added. The
  activation event and the build phase are the two hooks.
- The orchestrator, materialisers, order table, `ApplyPhase`.
- `SkillSymlinkListener`, `CronAutoSetupListener`.

## Files

### New

| File | What |
| --- | --- |
| `core/bot_config_manifest/lifecycle_listener.py` | `ManifestLifecycleListener` (event + teclaw callback → `start` apply) |
| `core/bot_config_manifest/apply/triggers.py` | Trigger constants |
| `core/bot_config_manifest/schema/splice.py` | `splice_script_section`, `render_script_section` |
| `core/service_bot/services/publish_flow/manifest_apply_mixin.py` | `manifest_apply_before_build` |
| `tests/community/core/bot_config_manifest/test_lifecycle_listener.py` | Listener cases |
| `tests/community/core/bot_config_manifest/test_script_splice.py` | Splice round-trips |
| `tests/community/core/bot_config_manifest/test_write_through_script.py` | Service write-through |
| `tests/community/core/bot_config_manifest/test_iteration1_ordering.py` | §2.12 pin |
| `tests/community/core/service_bot/services/publish_flow/test_manifest_apply_before_build.py` | The wait |

### Changed

| File | Change |
| --- | --- |
| `adapters/http/openapi_v1/bots/config_manifest.py` | `PUT` starts the apply; `apply` field; script warning; docstrings |
| `adapters/http/openapi_v1/bots/config_manifest_support.py`, `schemas.py` | `ConfigManifestApplyStarted`; `manifest_payload(apply=…)` |
| `adapters/http/openapi_v1/bots/router.py`, `startup_script_support.py` | Write-through arms on the three startup-script routes |
| `adapters/http/openapi_v1/bots/create_with_manifest.py` | Docstring: teclaw accepted; first-boot statement |
| `core/bot_config_manifest/creation.py` | Delete the teclaw refusal and `is_teclaw` |
| `core/bot_config_manifest/services/config_manifest_service.py`, `bot_config_manifest_service_protocol.py`, `api/bot_config_manifest_service.py` | `write_through_script`, `script_body`, `declares_script` on the write result |
| `core/bot_config_manifest/apply/outcomes.py` | Trigger docstring |
| `core/service_bot/services/publish_flow_service.py` | Mix in; two lazy providers |
| `core/service_bot/services/publish_flow/tasks.py` | The `BUILDING` wait branch |
| `core/bot_management/services/teclaw_publish_task_handler.py` | `on_activated` callback; lifecycle passes it |
| `di/modules/bot_management_module.py` | Bind the listener; wire the teclaw callback; drop `is_teclaw` from the seam |
| `di/modules/service_bot_module.py` | Providers for the flow's manifest/apply access |
| `core/bot_config_manifest/README.md`, `core/service_bot/README.md` (boundary) | Lifecycle section; dependency rows |
| `docs/bot-config-manifest/user-manual.zh-CN.md` §4.6, §5.5, §7 | `PUT` response, write-through, the per-point table |
| `docs/bot-config-manifest/work-items.zh-CN.md`, `work-items.md` W8 | Progress block: what landed, the two deferrals |
| Existing tests: `creation/test_creation_preflight.py`, `endpoints/test_openapi_create_with_manifest.py`, `endpoints/test_openapi_config_manifest.py`, `adapters/.../test_bots_endpoints.py` (startup-script cases on a manifest bot) | Flip the refusal; add the `apply` field and write-through cases |

## Risks

1. **The activation listener fires on a hot path.** `report_device_alive` runs
   on the poller thread and on the device callback request. `start_apply` is a
   lock row, a read, a validate and an enqueue — the same order of cost as the
   skill listener's DB reads — and the manifest lookup short-circuits for bots
   without one. Kept cheap by construction and pinned by a test that a
   manifest-less bot causes exactly one repository read.
2. **A `start` apply racing a `PUT` apply.** Both take the lock; the loser is
   skipped or reported `NOT_STARTED`. The document is re-read at execution
   (W13's level-triggered choice), so whichever runs converges to the latest.
3. **The republish wait adds a tick to every publish of a manifest bot.** One
   `POLL_DELAY_SECONDS` at minimum. Acceptable; bots without a manifest pay a
   single read.
4. **The teclaw callback is a new coupling from `bot_management` to the manifest
   layer.** Kept to a `Callable[[int], None]`, wired in DI, optional and
   defaulting to `None` so every existing construction and test stays valid.
5. **The splice meets a document it cannot parse back.** It raises rather than
   stores; the row is untouched. The round-trip check is what makes the textual
   approach safe.
6. **`is_teclaw` removal from the seam changes a DI provider signature.** Tests
   constructing the seam pass `is_teclaw=` today; they are updated in the same
   change.

## Testing strategy

- **Listener** — manifest-less bot: one read, no apply; stale binding: no
  apply; unresolved binding (scale-out / publish stage): no apply; live W13 job:
  no apply; terminal W13 job: apply with trigger `start`; `InProgress`: logged,
  no raise; start_apply raising: logged, no raise; teclaw callback path reaches
  the same code.
- **Republish wait** — no manifest: marker `none`, no wait; manifest: apply
  started with `republish`, marker holds the id, second tick with `RUNNING`
  waits, third with terminal proceeds; `PARTIAL` proceeds; `InProgress` waits
  without writing a marker; start failure writes `not_started` and proceeds;
  re-entrancy: the same tick twice starts one apply.
- **`PUT`** — endpoint: `200` with `apply.result == RUNNING` and an id that
  `GET …/applies/{id}` answers; lock held: `200` with `NOT_STARTED` /
  `apply_in_progress` and the document stored; `script` declared: the warning;
  `DELETE` unchanged.
- **teclaw creation** — preflight accepts `mcp: []` on teclaw; `script` on
  teclaw still `unsupported_script`; endpoint `202` on teclaw; the job test
  suite unchanged.
- **Write-through** — splice round-trips (quotes, `$(id)`, `{token}`, leading
  spaces, no trailing newline, two trailing newlines, a body that forces the
  quoted fallback); replace / append / remove leave the rest of the document
  byte-identical; the service writes the substituted row; `GET` returns the
  declared body; a bot without a manifest: the three existing endpoint tests
  pass unedited; a validation failure changes nothing.
- **§2.12** — `steps_for({PRE_CONTAINER}) == (script,)`; `ON_CONTAINER` has no
  script; the W13 ordering test stays.
- **Regression** — every existing manifest, apply, creation, startup-script,
  publish-flow and start-command test passes; `test_no_script_is_byte_identical_to_the_bare_chain`
  is not edited; architecture gates (`test_module_boundaries`,
  `test_service_api_conformance`, `test_http_adapter_layer_is_http_only`,
  `test_authorization_inventory`) pass.
