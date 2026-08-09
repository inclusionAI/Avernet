# Tasks: Public API — Publish Lifecycle for Service Bots

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> Sequencing: bases on `dev` at `5e8bc36` (the access expansion, PR #904).
> Groups A–B are the Phase-1 core evolution from `design.md` and land **before**
> the public surface (Groups C–D) is wired on top of them.
>
> Paths below are relative to `src/backend/src/agentclaw/community/` unless the
> path starts with `src/` or `tests/`.

---

## Group A — The evolved core (`design.md` Phase 1)

### Task 1: `ReleaseFacts` — the typed ext  `[ ]`

- **Goal:** one Pydantic model that owns the publish record's `ext` shape;
  byte-compatible round-trip with every stored key.
- **Files:** `core/service_bot/release/__init__.py`,
  `core/service_bot/release/facts.py` (new),
  `tests/community/core/service_bot/release/test_release_facts.py` (new)
- **Done when:**
  - [ ] `StageBindings`, `BuildArtifact` (with `.present`), `FailureInfo`,
        `engine_overrides_by_stage` and opaque `passthrough` exist as designed
        (design §3.1); the legacy id-stash keys (`publish`, `restart`,
        `scale`) are parsed into clearly-deprecated fields for fallback reads.
  - [ ] `from_ext(None)`, `from_ext({})` and every legacy shape observed in
        the census (design §1.1) parse without loss.
  - [ ] The round-trip property test passes: `to_ext(from_ext(x))` preserves
        every key the model does not own, verbatim, including unknown keys.
- **Depends on:** —

### Task 2: `ReleaseStore` — the only door to ext  `[ ]`

- **Goal:** absorb `PublishExtState`'s persistence semantics behind a
  `ReleaseFacts`-typed surface.
- **Files:** `core/service_bot/release/store.py` (new),
  `core/service_bot/services/publish_flow/ext_state.py`,
  `tests/community/core/service_bot/release/test_release_store.py` (new)
- **Done when:**
  - [ ] `load` / `mutate` / `advance` / `advance_with_facts` carry over the
        latest-read-back, deep-copy-snapshot and CAS semantics verbatim
        (design §3.2).
  - [ ] `PublishExtState` delegates to the store (thin shell; dies in
        Phase 2) — its callers and tests are untouched in this task.
  - [ ] The store's tests pin the semantics independently of any mixin.
- **Depends on:** Task 1

### Task 3: The status machine as data  `[ ]`

- **Goal:** `RELEASE_MACHINE` declared once; `process()` and the failure
  writes consult it.
- **Files:** `core/service_bot/release/machine.py` (new),
  `core/service_bot/services/publish_flow_service.py`,
  the failure-write sites in `publish_flow/` (`build_stage.py`,
  `progress_sync_mixin.py`, `publish_flow_service.py`),
  `tests/community/core/service_bot/release/test_release_machine.py` (new)
- **Done when:**
  - [ ] The machine carries today's transitions exactly (design §3.3),
        including each TASK transition's `on_failure` target equal to the
        `source_status` value that site writes today.
  - [ ] `process()` looks up the USER transition instead of its two
        hand-written branches; messages and wire payloads are unchanged.
  - [ ] Failure writes take their rollback target from the machine; no site
        hand-writes `ext["source_status"]` a literal anymore.
  - [ ] The agreement test passes: exactly two USER transitions; every
        non-terminal status covered.
  - [ ] `test_publish_flow_service.py`, `test_publish_crash_windows.py`,
        `test_publish_tasks.py` pass **unmodified**.
- **Depends on:** Task 2

### Task 4: The ledger becomes the single home of workflow ids  `[ ]`

- **Goal:** stop the ext id-stash writes; convert every reader to
  ledger-first with legacy-ext fallback. Kills the "publish_id in four
  places" problem at the root.
- **Files:** `core/service_bot/release/operations.py` (new);
  writers: `publish_flow/publish_ext_mixin.py`, `publish_flow/release_stage.py`,
  `publish_flow/restart_mixin.py`, `publish_flow/scale_mixin.py`;
  readers: `publish_flow/progress_sync_mixin.py`, `publish_flow/retry_ops_mixin.py`,
  `publish_flow/rollback_ops_mixin.py`, `publish_flow/upgrade_resolution_mixin.py`,
  `services/baas_service.py`, `services/arca_image_pin.py`,
  `services/bot_publish_service.py`;
  `tests/community/core/service_bot/release/test_release_operations.py` (new)
- **Done when:**
  - [ ] `latest_release_workflow` / `latest_restart_workflow` /
        `latest_scale_workflow` / `is_restart_in_flight` answer from
        `PublishOperationRepository`, falling back to the deprecated
        `ReleaseFacts` fields only when the ledger has no row (pre-#197 data).
  - [ ] No site writes `ext.publish.*`, `ext.restart.*` or `ext.scale.*`
        anymore; `ext.binding` stays (typed, not relocated — design §3.4).
  - [ ] `restart_mixin`'s id-classification (`:525-527`) reads the ledger
        row's `operation_kind` instead of comparing two ext homes.
  - [ ] Binding reads at `baas_service.py:3425`, `arca_image_pin.py:134`,
        `bot_publish_service.py:641` go through `StageBindings`.
  - [ ] Existing tests asserting the removed ext writes are updated to assert
        the ledger row instead — each such edit listed individually in the PR.
  - [ ] Crash-window and e2e publish-boundary suites pass **unmodified**.
- **Depends on:** Tasks 1, 2

### Task 5: The no-raw-ext architecture gate  `[ ]`

- **Files:** `tests/community/architecture/test_release_ext_access.py` (new)
- **Done when:**
  - [ ] Outside `release/facts.py` and `release/store.py`, no module under
        `core/service_bot` touches `ext[` / `ext.get(` / `ext.setdefault(` on
        a publish record.
  - [ ] Grandfathered exceptions (approval / rollback / draft-restore / eval
        passthrough sites) are enumerated in the test, each with its
        Phase-2/3 pointer, so the list can only shrink.
- **Depends on:** Tasks 3, 4

---

## Group B — Policy for the public surface

### Task 6: Extract the role bar into the collaborator domain  `[ ]`

- **Goal:** one level-parameterised, fail-closed "does this caller hold at
  least level L on this bot?" — publishing needs a different bar than the
  operator surfaces, and role policy belongs to `bot_collaborator`.
- **Files:** `core/bot_collaborator/access.py` (new),
  `core/engine_runtime/gate.py`, both modules' `README.md`
- **Done when:**
  - [ ] `resolve_permission_level(...)` and `require_bot_role(..., min_level)`
        exist; the latter raises `BotNotFoundError` and logs caller (`%r`) and
        owner at the refusal; a lookup failure resolves to `NONE` (fail
        closed).
  - [ ] `gate.py` keeps `OPERATOR_LEVEL`, `require_bot_operator`,
        `resolve_operator_level` as delegations; `__all__` unchanged; no
        import site moves.
  - [ ] `## Context Boundary` sections updated in both modules.
  - [ ] `tests/community/core/engine_runtime/` and
        `…/openapi_v1/engine_runtime/test_operator_access.py` pass
        **unmodified**; `tests/community/architecture/` passes.
- **Depends on:** —

### Task 7: `ReleaseLifecycleService` + errors + protocol + DI  `[ ]`

- **Goal:** the public surface's core: target resolution + role bars, the two
  precondition-guarded machine advances, the two reads.
- **Files:** `core/service_bot/release/lifecycle.py` (new),
  `core/service_bot/release/errors.py` (new),
  `api/release_lifecycle_service.py` (new),
  `di/modules/service_bot_module.py`,
  `tests/community/architecture/test_service_api_conformance.py`,
  `tests/community/core/service_bot/release/test_lifecycle_service.py` (new)
- **Done when:**
  - [ ] `PUBLISH_LEVEL = ADMIN`, `PUBLISH_READ_LEVEL = MEMBER`, named once with
        the spec-Decision-3 rationale.
  - [ ] `_resolve_target` runs owner-scoped `get_bot` → `require_bot_role` →
        service-type check in that order; async façade via `asyncio.to_thread`.
  - [ ] `start_verify_release`: newest record by `bot_pk` must be `DRAFT`,
        else `ReleaseNotStartableError` with **no side effect**; the advance is
        `ReleaseStore.advance(DRAFT→BUILDING)` + `enqueue_verify_flow` on a
        win, CAS-then-enqueue in `process()`'s order; a lost CAS raises the
        same error.
  - [ ] `promote_release`: record must be `VALIDATING`, else
        `ReleaseNotPromotableError`; advance is
        `ReleaseStore.advance(VALIDATING→ONLINE_PUB)` + `enqueue_online_release`;
        lost CAS → same error.
  - [ ] The machine-agreement test extends to pin that this service drives
        exactly the machine's USER transitions.
  - [ ] `_load_release` asserts `record.source_bot_pk == facts.bot_pk` →
        masked `PublishNotFoundError` otherwise.
  - [ ] `list_releases` pages in the service (`total`, newest first);
        docstring records the deliberate non-paginated repository read.
  - [ ] Both writes re-read and return the post-advance record.
  - [ ] `ReleaseLifecycleServiceProtocol` has real signatures, is registered
        in the conformance gate, and is bound in `service_bot_module`.
- **Depends on:** Tasks 3, 6 (and 2 for the store)

---

## Group C — Public surface

### Task 8: Schemas, projections, router, mounting  `[ ]`

- **Files:** `adapters/http/openapi_v1/publish/{__init__,router,schemas}.py`
  (new), `adapters/http/openapi_v1/__init__.py`
- **Done when:**
  - [ ] `ReleaseState` maps `PublishStatus` 1:1 per plan §2, exhaustive by
        construction; `Release` publishes exactly the eight plan-§2 fields;
        the projection never reads `ext`; `failed` carries the fixed message.
  - [ ] Four routes at the plan-§1 paths, prefix `/bots/publish`; handlers
        take `PrincipalDep`, `UserIdDep`, `OwnerIdDep` (imported from
        `engine_runtime/params.py`), `PageParamsDep`, `request: Request`,
        `@envelope_errors`; writes declare `status_code=202` and have no
        request body.
  - [ ] Mounted in `_SUBGROUPS` above the bots router with
        `USER_SCOPED_ERROR_RESPONSES`.
- **Depends on:** Task 7

### Task 9: Error mapping  `[ ]`

- **Files:** `adapters/http/openapi_v1/responses.py`
- **Done when:**
  - [ ] Every row of the plan-§7 table present, leaves before
        `BotPublishServiceError`; **both** `BotNotFoundError` classes mapped
        with byte-identical 404 messages; distinct 409 subcodes in
        `ERROR_SUBCODES`; no message interpolates `str(exc)`.
- **Depends on:** Task 7

---

## Group D — Tests, docs, gates

### Task 10: Public-surface test suites  `[ ]`

- **Files:** `tests/community/adapters/http/openapi_v1/publish/` (new package
  per plan §9: endpoints, state-machine, access, projection, tenant-isolation)
- **Done when:**
  - [ ] All four handlers: success + every mapped error; writes answer 202 and
        nothing else.
  - [ ] Start and promote attempted from each of the ten `PublishStatus`
        values — exactly one succeeds per write, the rest are the fixed 409
        with no side effect; both CAS-loser paths refused identically.
  - [ ] Role matrix (owner / admin / member / stranger × writes and reads);
        refusals byte-identical to a missing bot.
  - [ ] State map exhaustive; withheld fields absent; no `ext` key reachable.
  - [ ] Cross-tenant and cross-bot lookups masked 404 against the real Track A
        guard.
- **Depends on:** Tasks 8, 9

### Task 11: Amend the surface-wide contract tests  `[ ]`

- **Files:** `tests/…/openapi_v1/test_explicit_user_id.py`,
  `…/test_path_convention.py`, `…/test_openapi_error_schema.py`
- **Done when:**
  - [ ] user-id counts 56/4 → 60/4; `publish` in the routed reserved list and
        absent from the unrouted one; the new group documents exactly the
        user-scoped response set.
- **Depends on:** Tasks 8, 12

### Task 12: Handoff documentation  `[ ]`

- **Files:** `src/backend/docs/openapi-v1/README.md`, `README.zh-CN.md`
- **Done when:**
  - [ ] Track B board row; endpoint section with the four-row table, state
        map, precondition rule, role bars, and the known limitation
        (re-publish of a live bot needs the internal upgrade).
  - [ ] `publish` added to `<!-- reserved-component-names -->` (routed list);
        the #909 deferred line struck; dated changelog entry; Chinese mirror
        carries all of it.
  - [ ] The internal evolution is recorded too: a short section pointing at
        `design.md`, the new `release/` package, and the phase plan — so the
        next person extending the pipeline builds on the new core, not the
        mixins.
- **Depends on:** Task 8

### Task 13: Full gates and PR  `[ ]`

- **Done when:**
  - [ ] `tests/community/architecture/` green — run **after** every new
        cross-module import is declared in the touched READMEs.
  - [ ] Internal suites green, unmodified except the individually-listed
        id-stash assertions from Task 4:
        `test_router_publish_coverage.py`, `test_publish_flow_service.py`,
        `test_publish_crash_windows.py`, `test_publish_tasks.py`,
        `tests/community/e2e/publish_boundary/`.
  - [ ] Engine-runtime operator and stage suites green, unmodified.
  - [ ] Full `tests/community` green; backend SAST/lint green.
  - [ ] PR (existing draft #918's branch) updated, titled
        `feat(backend): publish service-bot releases on the openapi v1 surface`,
        with `Problem` / `Solution` / `Validation` sections, linking #909,
        this spec directory and `design.md`. State explicitly what could not
        be run locally and why.
- **Depends on:** all
