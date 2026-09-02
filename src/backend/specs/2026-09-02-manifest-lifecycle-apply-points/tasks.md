# Tasks: Lifecycle Apply Points (W8)

Spec: `spec.md` · Plan: `plan.md` · Issue #1476.

Six groups. A is the shared vocabulary and the §2.12 pin; B–E are the four apply
points and the alias view, independent of each other once A lands; F is docs
and the regression sweep. Nothing adds a table or a column.

Conventions every task assumes:

- Every lifecycle apply goes through `start_apply`; nothing here runs the
  orchestrator, restarts a bot, republishes, or rebuilds a payload.
- A lifecycle apply that cannot start, or that ends `PARTIAL` / `FAILED`, never
  fails the lifecycle operation it rode on (§2.7).
- `ManifestApplyInProgressError` means "wait" or "not started", never "error".
- No test that asserts today's behaviour on a bot **without** a manifest is
  edited.

---

## Group A — Vocabulary and the ordering pin

## [ ] Task 1: Trigger constants
- **Files:** `core/bot_config_manifest/apply/triggers.py` (new),
  `core/bot_config_manifest/apply/outcomes.py`, `core/bot_config_manifest/__init__.py`
- **Done when:**
  - [ ] `EXPLICIT = "explicit"`, `PUT = "put"`, `START = "start"`,
        `REPUBLISH = "republish"` exist in one module; the creation triggers
        stay in `creation.py` and are referenced, not duplicated.
  - [ ] `ApplyReport.trigger`'s docstring names the full vocabulary.
  - [ ] A test asserts every trigger fits `String(32)`.
- **Depends on:** —

## [ ] Task 2: The §2.12 ordering pin
- **Files:** `tests/community/core/bot_config_manifest/test_iteration1_ordering.py` (new)
- **Done when:**
  - [ ] Asserts `steps_for({PRE_CONTAINER})` is exactly `(script,)` and that no
        `ON_CONTAINER` step is `script`.
  - [ ] Its docstring states the rule ("a manifest's `script` must not depend on
        anything the manifest declares"), why (first boot: script before
        everything), and that #1508 deletes this test.
- **Depends on:** —

---

## Group B — `PUT` takes effect

## [ ] Task 3: `declares_script` on the write result
- **Files:** `core/bot_config_manifest/bot_config_manifest_service_protocol.py`,
  `core/bot_config_manifest/services/config_manifest_service.py`
- **Done when:**
  - [ ] `ManifestWriteResult` carries `declares_script: bool`, computed from
        the validated parse with `declared_entries(parsed, ManifestSection.SCRIPT) is not None`.
  - [ ] Existing callers of `put` are unaffected (a defaulted field).
- **Depends on:** —

## [ ] Task 4: `PUT` starts the apply and says so
- **Files:** `adapters/http/openapi_v1/bots/config_manifest.py`,
  `adapters/http/openapi_v1/bots/config_manifest_support.py`,
  `adapters/http/openapi_v1/bots/schemas.py`
- **Done when:**
  - [ ] `ConfigManifest.apply: ConfigManifestApplyStarted | None` with
        `{apply_id, result: RUNNING | NOT_STARTED, reason}`; `GET`/`DELETE` leave
        it `None`.
  - [ ] After `manifest_service.put`, the route calls `start_apply(trigger=PUT,
        phases=ALL_PHASES, bot=bot, owner_id=…, actor_id=…, audit_actor=…)`
        with the same principal/audit split the explicit apply route uses.
  - [ ] `ManifestApplyInProgressError` → `NOT_STARTED` / `apply_in_progress`;
        any other exception → logged, `NOT_STARTED` / `not_started`. The
        response is `200` in all three cases and the document is stored.
  - [ ] When `declares_script`, `warnings` gains the delivery note (the
        materialiser's `DELIVERY_NOTE`, imported, not restated).
  - [ ] The docstring no longer says "Storing a manifest applies nothing yet";
        it states §2.6 and the `script` rule.
  - [ ] Endpoint tests: `apply.result == RUNNING` and the id is readable via
        `GET …/applies/{id}`; lock held → `NOT_STARTED` with the document
        stored; `script` declared → the warning; `DELETE` unchanged.
- **Depends on:** Task 1, Task 3

---

## Group C — The container comes up

## [ ] Task 5: `ManifestLifecycleListener`
- **Files:** `core/bot_config_manifest/lifecycle_listener.py` (new),
  `core/bot_config_manifest/__init__.py`
- **Done when:**
  - [ ] A `LifecycleBase` that subscribes `handle(DeviceActivatedEvent)` in
        `startup()` with the idempotent double-subscribe guard, and exposes
        `on_bot_activated(binding_id: int) -> None`.
  - [ ] One private path for both: resolve the bot by binding (`None` → skip);
        no stored manifest → skip **before** any other read; current binding
        differs → skip; a live creation job (`find_create_job` status not
        terminal) → skip; otherwise `start_apply(trigger=START, phases=ALL_PHASES,
        bot=bot, owner_id=actor_id=bot["owner_id"])`.
  - [ ] `ManifestApplyInProgressError` → info log; any exception → warning log;
        **nothing propagates**.
  - [ ] Unit tests for every branch above, plus: a manifest-less bot causes
        exactly one manifest-repository read; the callback path reaches the
        same code as the event path.
- **Depends on:** Task 1

## [ ] Task 6: Wire the listener and the teclaw callback
- **Files:** `di/modules/bot_management_module.py`,
  `core/bot_management/services/teclaw_publish_task_handler.py`
- **Done when:**
  - [ ] The listener is bound as a singleton so `discover_lifecycle_participants`
        runs its `startup()`; collaborators are lazy providers.
  - [ ] `TeclawPublishTaskHandler.__init__(..., on_activated: Callable[[int], None] | None = None)`;
        `_persist_terminal` calls it inside try/except after a successful
        transition to `ACTIVE` and before `_deliver_outbound_rule`; the
        crash-resume branch does not call it.
  - [ ] `TeclawPublishTaskLifecycle` passes it through; the DI provider wires
        `injector.get(ManifestLifecycleListener).on_bot_activated`.
  - [ ] Handler tests: the callback fires once on `PENDING → ACTIVE`, not on
        `FAILED`, not on crash-resume; a raising callback does not change the
        outcome.
- **Depends on:** Task 5

---

## Group D — Publish / republish

## [ ] Task 7: `manifest_apply_before_build`
- **Files:** `core/service_bot/services/publish_flow/manifest_apply_mixin.py` (new),
  `core/service_bot/services/publish_flow_service.py`,
  `di/modules/service_bot_module.py`
- **Done when:**
  - [ ] `PublishFlowService` mixes it in and takes `manifest_service_provider`
        and `apply_service_provider` (lazy), wired in `service_bot_module`.
  - [ ] `manifest_apply_before_build(record, operator) -> bool` implements the
        marker table in the plan (`ext["manifest_apply"]["build"]` ∈ apply id,
        `"none"`, `"not_started"`), writes it through `_mutate_and_update_ext`,
        targets `record.source_bot_id` / `record.owner_id`, uses `trigger=REPUBLISH`
        and `actor_id=operator`.
  - [ ] Returns `True` while waiting (`RUNNING`, or `InProgress` with no marker
        written), `False` otherwise. Never raises.
  - [ ] Tests: no manifest → `"none"`, no apply, `False`; manifest → apply
        started, marker = id, `True`; `RUNNING` → `True`; terminal (each of the
        three) → `False`; `InProgress` → `True`, no marker; start failure →
        `"not_started"`, `False`; the same tick twice starts one apply.
- **Depends on:** Task 1

## [ ] Task 8: The `BUILDING` wait branch
- **Files:** `core/service_bot/services/publish_flow/tasks.py`
- **Done when:**
  - [ ] In `PublishVerifyFlowHandler._run`, before `execute_build_phase`:
        `if await self._flow.manifest_apply_before_build(record, operator): return Reschedule(POLL_DELAY)`.
  - [ ] The handler's docstring states the wait and that a non-succeeding apply
        does not fail the publish.
  - [ ] A handler test with a flow stub: waiting → `Reschedule`, build not
        called; not waiting → build called exactly as before.
- **Depends on:** Task 7

---

## Group E — teclaw creation, and the alias view

## [ ] Task 9: Lift W13's teclaw refusal
- **Files:** `core/bot_config_manifest/creation.py`,
  `adapters/http/openapi_v1/bots/create_with_manifest.py`,
  `di/modules/bot_management_module.py`,
  `tests/community/core/bot_config_manifest/creation/test_creation_preflight.py`,
  `tests/community/endpoints/test_openapi_create_with_manifest.py`,
  other tests constructing the seam with `is_teclaw=`
- **Done when:**
  - [ ] `_TECLAW_REFUSAL`, the `engine` violation and the `is_teclaw` parameter
        are gone from `preflight_creation_manifest`, the seam and its provider.
  - [ ] The module and route docstrings state the first-boot semantics on both
        families and that the first-artifact guarantee is #1508's.
  - [ ] Tests: `mcp: []` on teclaw preflights clean; `script` on teclaw is
        refused with `unsupported_script` by the validator; endpoint `202` on
        teclaw; every job test passes unedited.
- **Depends on:** —

## [ ] Task 10: The splice helper
- **Files:** `core/bot_config_manifest/schema/splice.py` (new),
  `tests/community/core/bot_config_manifest/test_script_splice.py` (new)
- **Done when:**
  - [ ] `splice_script_section(document: str, body: str | None) -> str` per
        plan K-6: replace / append / remove the top-level `script` section;
        every other byte unchanged.
  - [ ] Literal block rendering chooses `|` / `|-` / `|+` by trailing newlines
        and adds an indentation indicator when needed; the result is parsed
        back and compared to `body`; on mismatch a JSON-quoted scalar is used
        and checked again; an unparseable document raises `ManifestValidationError`.
  - [ ] Round-trip tests: quotes, `$(id)`, `{token}`, leading spaces, no trailing
        newline, two trailing newlines, tabs, a CRLF body, an empty body; the
        rest of a document with comments and a `sources:` block is
        byte-identical after replace, append and remove.
- **Depends on:** —

## [ ] Task 11: `write_through_script` and `script_body`
- **Files:** `core/bot_config_manifest/services/config_manifest_service.py`,
  `core/bot_config_manifest/bot_config_manifest_service_protocol.py`,
  `di/modules/bot_management_module.py`,
  `tests/community/core/bot_config_manifest/test_write_through_script.py` (new)
- **Done when:**
  - [ ] The service takes a lazy `script_service_provider`.
  - [ ] `write_through_script(...) -> ManifestWriteResult | None`: `None` when
        no manifest; otherwise splice → `put` → row write with
        `placeholders.resolve(body, engine_type, env, tenant)` (or `delete` for
        `None`). A `put` refusal propagates and the row is untouched.
  - [ ] `script_body(entity_id, bot_id) -> str | None`: the declared body, or
        `None` when the manifest is absent or silent.
  - [ ] The conformance `_PAIRS` entry still passes (protocol and concrete
        signatures match).
  - [ ] Tests: replace / append / remove; the row equals the substituted body;
        the next apply plans `unchanged` (drive the script materialiser's
        `plan` against the fake script service); refusal leaves both untouched.
- **Depends on:** Task 10

## [ ] Task 12: The three startup-script routes
- **Files:** `adapters/http/openapi_v1/bots/router.py`,
  `adapters/http/openapi_v1/bots/startup_script_support.py`,
  `tests/community/endpoints/test_openapi_startup_script.py`,
  `tests/community/adapters/http/openapi_v1/test_bots_endpoints.py`
- **Done when:**
  - [ ] `PUT`: after the support check, `manifest_service.write_through_script(...)`;
        a result short-circuits the legacy write and the response is shaped
        from the manifest's body; `None` → the legacy path, unchanged.
  - [ ] `DELETE`: `write_through_script(body=None)`; `None` → legacy.
  - [ ] `GET`: `script_body(...)` when not `None`, else the row.
  - [ ] The withdraw-if-deleted guard runs on both arms of `PUT`.
  - [ ] Docstrings state the alias rule.
  - [ ] Endpoint tests: on a manifest bot, `PUT` updates `GET …/config-manifest`
        and the row; `DELETE` removes the section and the row; `GET` returns the
        declared body; on a bot without a manifest, the existing cases pass
        **unedited**.
- **Depends on:** Task 11

---

## Group F — Documentation and the sweep

## [ ] Task 13: Docs
- **Files:** `core/bot_config_manifest/README.md`, `core/service_bot/README.md`,
  `docs/bot-config-manifest/user-manual.zh-CN.md`,
  `docs/bot-config-manifest/work-items.zh-CN.md`, `docs/bot-config-manifest/work-items.md`
- **Done when:**
  - [ ] README: a "Lifecycle apply points (W8)" section naming the four points,
        the listener, the republish wait, the alias view, the trigger
        vocabulary, and the two deferrals; Context Boundary rows for the new
        provides/consumes (listener, splice, the service_bot dependency).
  - [ ] User manual §4.6 (the `PUT` response's `apply`), §5.5 (write-through
        semantics incl. substitution), §7 (per-point table: republish "before
        build, waits"; restart / first boot "when the container comes up";
        scale-out unchanged).
  - [ ] Work-items W8 (both languages): a progress block in W9's style — what
        landed per criterion, the first-artifact deferral to #1508 with the
        code reason, the health-surface deferral.
- **Depends on:** Tasks 4, 6, 8, 9, 12

## [ ] Task 14: Regression sweep
- **Files:** —
- **Done when:**
  - [ ] `uv run pytest tests/community/core/bot_config_manifest tests/community/endpoints/test_openapi_config_manifest*.py tests/community/endpoints/test_openapi_create_with_manifest.py tests/community/endpoints/test_openapi_startup_script.py tests/community/core/service_bot tests/community/core/bot_management tests/community/adapters/http/openapi_v1 tests/community/architecture` passes.
  - [ ] `test_no_script_is_byte_identical_to_the_bare_chain` is unmodified
        (`git diff --stat` on its file is empty).
  - [ ] Lint passes (`ruff`), and the oversized-module gate passes for
        `router.py` after the write-through arms (split into
        `startup_script_support.py` if it crosses the cap).
- **Depends on:** Task 13
