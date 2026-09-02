# Tasks: Lifecycle Apply Points (W8)

Spec: `spec.md` · Plan: `plan.md` · Issue #1476.

> **Revision 2.** Restart and republish deferred (spec D-1). Ten tasks in four
> groups. A is vocabulary and the §2.12 pin; B is `PUT`; C is teclaw creation
> and the alias view; D is docs and the sweep. Nothing adds a table or a column.

Conventions every task assumes:

- The `PUT` apply goes through `start_apply`; nothing here runs the
  orchestrator, restarts a bot, republishes, or rebuilds a payload.
- An apply that cannot start never fails the `PUT` it rode on (§2.6, §2.7).
- `ManifestApplyInProgressError` means "not started", never "error".
- No test that asserts today's behaviour on a bot **without** a manifest is
  edited.

---

## Group A — Vocabulary and the ordering pin

## [ ] Task 1: Trigger constants
- **Files:** `core/bot_config_manifest/apply/triggers.py` (new),
  `core/bot_config_manifest/apply/outcomes.py`, `core/bot_config_manifest/__init__.py`
- **Done when:**
  - [ ] `EXPLICIT = "explicit"` and `PUT = "put"` exist in one module; the
        creation triggers stay in `creation.py` and are referenced, not
        duplicated.
  - [ ] `ApplyReport.trigger`'s docstring names the vocabulary and says restart
        and republish are deferred.
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
  `adapters/http/openapi_v1/bots/schemas.py`,
  `tests/community/endpoints/test_openapi_config_manifest.py`
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
  - [ ] When `declares_script`, `warnings` gains the materialiser's
        `DELIVERY_NOTE` (imported, not restated).
  - [ ] When the bot's status is not `ACTIVE`, `warnings` gains the not-ACTIVE
        note naming `POST …/config-manifest/apply` as the call to make once it
        is. Apply itself is started for both phases regardless (§2.7).
  - [ ] The docstring no longer says "Storing a manifest applies nothing yet";
        it states §2.6, the `script` rule, and the not-ACTIVE behaviour.
  - [ ] Endpoint tests: `apply.result == RUNNING` and the id is readable via
        `GET …/applies/{id}`; lock held → `NOT_STARTED` with the document
        stored; `script` declared → the delivery warning; `PENDING` bot → the
        not-ACTIVE warning; `DELETE` unchanged.
- **Depends on:** Task 1, Task 3

---

## Group C — teclaw creation, and the alias view

## [ ] Task 5: Lift W13's teclaw refusal
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

## [ ] Task 6: The splice helper
- **Files:** `core/bot_config_manifest/schema/splice.py` (new),
  `tests/community/core/bot_config_manifest/test_script_splice.py` (new)
- **Done when:**
  - [ ] `splice_script_section(document: str, body: str | None) -> str` per
        plan K-3: replace / append / remove the top-level `script` section;
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

## [ ] Task 7: `write_through_script` and `script_body`
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
- **Depends on:** Task 6

## [ ] Task 8: The three startup-script routes
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
- **Depends on:** Task 7

---

## Group D — Documentation and the sweep

## [ ] Task 9: Docs
- **Files:** `core/bot_config_manifest/README.md`,
  `docs/bot-config-manifest/user-manual.zh-CN.md`,
  `docs/bot-config-manifest/work-items.zh-CN.md`, `docs/bot-config-manifest/work-items.md`
- **Done when:**
  - [ ] README: a "Lifecycle apply points (W8)" section naming `PUT` and
        creation as the apply points, the alias view, the trigger vocabulary,
        and the three deferrals with their reasons; Context Boundary rows for
        the new provides (splice, write-through).
  - [ ] User manual §4.6 (the `PUT` response's `apply` and the two warnings),
        §5.5 (write-through semantics incl. substitution), §7 (per-point table:
        restart / republish "no re-apply in this iteration; nothing previously
        applied is lost; a moved ref or drift converges at the next `PUT` or
        explicit apply").
  - [ ] Work-items W8 (both languages): a progress block in W9's style — what
        landed per criterion, the first-artifact deferral to #1508 with the
        code reason, the restart/republish deferral with the owner's reasoning,
        the health-surface deferral.
- **Depends on:** Tasks 4, 5, 8

## [ ] Task 10: Regression sweep
- **Files:** —
- **Done when:**
  - [ ] `uv run pytest tests/community/core/bot_config_manifest tests/community/endpoints/test_openapi_config_manifest*.py tests/community/endpoints/test_openapi_create_with_manifest.py tests/community/endpoints/test_openapi_startup_script.py tests/community/core/service_bot/services/test_baas_service_start_cmd.py tests/community/core/bot_management tests/community/adapters/http/openapi_v1 tests/community/architecture` passes.
  - [ ] `test_no_script_is_byte_identical_to_the_bare_chain` is unmodified
        (`git diff --stat` on its file is empty).
  - [ ] Lint passes (`ruff`), and the oversized-module gate passes for
        `router.py`.
- **Depends on:** Task 9
