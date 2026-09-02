# Plan: Lifecycle Apply Points (W8)

Spec: `spec.md` in this directory. Work item W8, issue #1476.

> **Revision 2 (2026-09-02).** Restart and republish are deferred by owner
> decision (spec D-1). This plan covers `PUT` taking effect, teclaw creation,
> and the legacy `/startup-script` alias view. Revision 1's activation listener
> and publish-build wait are recorded in the spec's *Follow-ups*, not built.

## What already exists, and what that leaves

Everything that *applies* is built. W8 adds one caller, one alias view, and
removes one refusal.

| Already there | Where | What W8 gets from it |
| --- | --- | --- |
| `start_apply(..., trigger=, phases=, bot=…)` — lock, re-validate, `RUNNING` row, enqueue | `services/config_manifest_apply_service.py` | The one entry point `PUT` calls; nothing here runs the orchestrator itself |
| `ManifestApplyInProgressError` raised **before** an id exists | same | `PUT` treats it as "not started", never as failure |
| `_last_resolutions` → `SourceSession.baselines` | same, `apply/source_session.py` | D2's `strict` / `non_strict` is enforced for free at `PUT` |
| `BotCreationManifestSeam.preflight` + `preflight_creation_manifest(is_teclaw=…)` | `bot_config_manifest/creation.py` | The refusal to delete |
| `BotStartupScriptService.put/delete/get_body`, `placeholders.resolve` | `core/bot_startup_script`, `bot_config_manifest/schema/placeholders.py` | The write-through's row write, with the same substitution the materialiser uses |
| `declared_entries(parsed, construct)` | `apply/orchestrator.py` | "Does the document declare `script`?" for the warning and the alias `GET` |
| `ScriptMaterialiser.DELIVERY_NOTE` | `apply/materialisers/script.py` | The one wording for "delivered now, executes at next provisioning" |

## Architecture

```text
PUT /openapi/v1/bots/{bot_id}/config-manifest
  ├─ manifest_service.put(...)                          ← unchanged
  ├─ warnings += DELIVERY_NOTE            if declares_script
  ├─ warnings += NOT_ACTIVE_NOTE          if bot.status != ACTIVE
  └─ apply_service.start_apply(trigger="put", ALL_PHASES)
        ├─ ApplyAccepted            → response.apply = {apply_id, RUNNING}
        ├─ ManifestApplyInProgress  → response.apply = {NOT_STARTED, apply_in_progress}
        └─ anything else (logged)   → response.apply = {NOT_STARTED, not_started}

POST /openapi/v1/bots/with-manifest        ← teclaw refusal removed; job unchanged

PUT/DELETE/GET /openapi/v1/bots/{bot_id}/startup-script
  ├─ manifest_service.write_through_script(...) → ManifestWriteResult | None
  │     None  → legacy path, byte-for-byte                      ← unchanged
  │     result → response from the manifest's script
  └─ (GET) manifest declares script → its body ; else the row
```

## Key decisions

### K-1 The `PUT` response grows an `apply` field

`ConfigManifest` gets `apply: ConfigManifestApplyStarted | None`, where the
model is `{apply_id: str | None, result: "RUNNING" | "NOT_STARTED", reason: str}`.
`GET` and `DELETE` leave it `None`.

Two warnings are appended in the route. The `script` note is the materialiser's
`DELIVERY_NOTE`, imported. The not-ACTIVE note is a constant next to it in the
support module and names the exact call to make later. Whether the document
declares `script` comes from the service: `ManifestWriteResult` gains
`declares_script: bool`, computed from the validated parse, so the adapter never
re-parses.

The apply is started with the same principal/audit split the explicit route
uses (`actor_id` is the principal, `audit_actor(caller, actor_id)` the label).
Both refusals `start_apply` can raise happen before an id exists; the route
maps `ManifestApplyInProgressError` to `apply_in_progress` and logs anything
else as `not_started`. The `200` and the stored document do not depend on
either.

### K-2 Lifting W13's refusal is a deletion

`preflight_creation_manifest` drops the `is_teclaw` violation and the
parameter; `_TECLAW_REFUSAL` goes; the seam and its DI provider drop `is_teclaw`
(the seam used it for nothing else). The route docstring's "ARCA-only" paragraph
becomes the first-boot statement. The job and the poll are untouched:
`_CONTAINER_READY_STATUSES` is the bot's status, which the teclaw terminal
transition writes.

Tests: the two preflight tests asserting the refusal flip to assert acceptance
(`mcp: []` on teclaw preflights clean; `script` on teclaw is still refused by
the validator with `unsupported_script`). The endpoint scenario
`teclaw_is_refused` becomes `teclaw_is_accepted`.

### K-3 The write-through is one service method with a splice helper

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
   again. A document it cannot splice raises `ManifestValidationError`.
3. `put(...)` the spliced document — the same validation and all-or-nothing.
4. Write the row: `script_service.put(... script=placeholders.resolve(body, engine_type, env, tenant))`,
   or `script_service.delete` for `None`. The service gets a lazy
   `script_service_provider`, as the creation seam already has.

The router's `PUT` calls it after the existing support check and before the
legacy `put`; a returned result short-circuits the legacy write. `GET` reads
`manifest_service.script_body(entity_id, bot_id) -> str | None` (declared body
or `None`) and falls back to the row. The withdraw-if-deleted guard stays on
both arms.

### K-4 Trigger vocabulary

Triggers are constants in `apply/triggers.py`: `EXPLICIT`, `PUT`, plus the two
creation triggers re-exported from `creation.py` (which keeps owning them). The
apply record's `String(32)` column fits; no migration.

### K-5 What is deliberately not touched

- `BotService.restart_bot`, `BaasService.upgrade_bot`, `_build_create_bot_payload`,
  `TeclawProvisionService.provision`, `PublishFlowService`, `DeviceService`,
  the teclaw publish poll: no seam added (spec D-1).
- The orchestrator, materialisers, order table, `ApplyPhase`.
- The creation job and the poll.

## Files

### New

| File | What |
| --- | --- |
| `core/bot_config_manifest/apply/triggers.py` | Trigger constants |
| `core/bot_config_manifest/schema/splice.py` | `splice_script_section`, `render_script_section` |
| `tests/community/core/bot_config_manifest/test_script_splice.py` | Splice round-trips |
| `tests/community/core/bot_config_manifest/test_write_through_script.py` | Service write-through |
| `tests/community/core/bot_config_manifest/test_iteration1_ordering.py` | §2.12 pin |

### Changed

| File | Change |
| --- | --- |
| `adapters/http/openapi_v1/bots/config_manifest.py` | `PUT` starts the apply; `apply` field; the two warnings; docstrings |
| `adapters/http/openapi_v1/bots/config_manifest_support.py`, `schemas.py` | `ConfigManifestApplyStarted`; `manifest_payload(apply=…)`; the not-ACTIVE note |
| `adapters/http/openapi_v1/bots/router.py`, `startup_script_support.py` | Write-through arms on the three startup-script routes |
| `adapters/http/openapi_v1/bots/create_with_manifest.py` | Docstring: teclaw accepted; first-boot statement |
| `core/bot_config_manifest/creation.py` | Delete the teclaw refusal and `is_teclaw` |
| `core/bot_config_manifest/services/config_manifest_service.py`, `bot_config_manifest_service_protocol.py`, `api/bot_config_manifest_service.py` | `write_through_script`, `script_body`, `declares_script` on the write result; lazy script-service provider |
| `core/bot_config_manifest/apply/outcomes.py` | Trigger docstring |
| `di/modules/bot_management_module.py` | Provider for the manifest service's script-service access; drop `is_teclaw` from the seam |
| `core/bot_config_manifest/README.md` | Lifecycle section; Context Boundary rows |
| `docs/bot-config-manifest/user-manual.zh-CN.md` §4.6, §5.5, §7 | `PUT` response, write-through, the per-point table with restart/republish marked deferred |
| `docs/bot-config-manifest/work-items.zh-CN.md`, `work-items.md` W8 | Progress block: what landed, the three deferrals (first artifact, restart/republish, health surface) |
| Existing tests: `creation/test_creation_preflight.py`, `endpoints/test_openapi_create_with_manifest.py`, `endpoints/test_openapi_config_manifest.py`, `adapters/.../test_bots_endpoints.py`, tests constructing the seam with `is_teclaw=` | Flip the refusal; add the `apply` field and write-through cases |

## Risks

1. **A `put` apply racing an explicit apply.** Both take the lock; the loser is
   reported `NOT_STARTED`. The document is re-read at execution (W13's
   level-triggered choice), so whichever runs converges to the latest.
2. **The splice meets a document it cannot parse back.** It raises rather than
   stores; the row is untouched. The round-trip check is what makes the textual
   approach safe.
3. **`is_teclaw` removal from the seam changes a DI provider signature.** Tests
   constructing the seam pass `is_teclaw=` today; they are updated in the same
   change.
4. **`router.py` size.** The write-through arms add lines to a module already
   near the oversized-module cap; the logic goes into
   `startup_script_support.py`, the routes only branch.

## Testing strategy

- **`PUT`** — endpoint: `200` with `apply.result == RUNNING` and an id that
  `GET …/applies/{id}` answers; lock held: `200` with `NOT_STARTED` /
  `apply_in_progress` and the document stored; `script` declared: the
  delivery warning; bot `PENDING`: the not-ACTIVE warning; `DELETE` unchanged.
- **teclaw creation** — preflight accepts `mcp: []` on teclaw; `script` on
  teclaw still `unsupported_script`; endpoint `202` on teclaw; the job test
  suite unchanged.
- **Write-through** — splice round-trips (quotes, `$(id)`, `{token}`, leading
  spaces, no trailing newline, two trailing newlines, tabs, CRLF, empty, a body
  that forces the quoted fallback); replace / append / remove leave the rest of
  the document byte-identical; the service writes the substituted row; the
  script materialiser's `plan` then reports `unchanged`; `GET` returns the
  declared body; a bot without a manifest: the existing endpoint tests pass
  unedited; a validation failure changes nothing.
- **§2.12** — `steps_for({PRE_CONTAINER}) == (script,)`; `ON_CONTAINER` has no
  script; the W13 ordering test stays.
- **Regression** — every existing manifest, apply, creation, startup-script and
  start-command test passes; `test_no_script_is_byte_identical_to_the_bare_chain`
  is not edited; architecture gates (`test_module_boundaries`,
  `test_service_api_conformance`, `test_http_adapter_layer_is_http_only`,
  `test_authorization_inventory`, `test_no_oversized_modules`) pass.

## Revision history

| | What changed, and why |
| --- | --- |
| **rev 1** | Four apply points: `PUT`, a `DeviceActivatedEvent` listener (plus a teclaw callback) for "the container came up", a durable wait before the publish build, and teclaw creation. |
| **rev 2** | Restart and republish deferred by the owner: nothing previously applied is lost on them, and a re-apply would only re-resolve a moving ref and correct drift. The listener and the build wait are dropped; the `PENDING`-then-`PUT` gap becomes a warning on the response (spec D-2). |
