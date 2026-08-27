# Tasks: SkillSet Projection Latency

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: Thread the loaded binding record through ARCA connection resolution

- **Goal:** Stop re-reading a binding row that the caller already holds, so one
  device address resolution costs no extra `get_by_id`.
- **Files:**
  - `src/backend/src/agentclaw/community/core/devices/services/conn_info_builders/arca_builder.py`
  - `src/backend/src/agentclaw/community/core/devices/services/device_service.py`
  - `src/backend/src/agentclaw/community/core/devices/services/device_service_router.py`
  - `src/backend/src/agentclaw/community/core/devices/services/local_device_service.py`
  - `src/backend/src/agentclaw/community/core/devices/services/baas_device_service.py`
- **Done when:**
  - [ ] `get_device_connection_v2` accepts keyword-only `record: DeviceBindingRecord | None = None`
        and uses it instead of `self.get_device(binding_id=...)` when supplied.
  - [ ] `get_device_connection` accepts the same keyword on the base, the router,
        and both provider overrides; each defaults to today's read when it is `None`.
  - [ ] `DeviceServiceRouter._get_provider_for_binding` accepts `record=` and routes
        from it without a database read when supplied.
  - [ ] `ArcaConnInfoBuilder.build` passes `record=binding`.
  - [ ] New test asserts one `resolve_for_bot` on an arca bot issues exactly one
        binding read (`get_active_by_bot_and_owner`) and zero `get_by_id` calls.
  - [ ] `tests/community/core/devices/services/test_device_service_router.py` and
        `test_device_service.py` still pass with no signature-related failures.
- **Depends on:** —

## Task 2: Add `publish_and_verify_mappings` with a single device resolution

- **Goal:** One device address resolution serves center-ensure, publish, and
  verify for a projection, instead of one resolution per call.
- **Files:**
  - `src/backend/src/agentclaw/community/core/skills_pool/runtime.py`
  - `src/backend/src/agentclaw/community/core/skills_pool/models.py`
  - `src/backend/src/agentclaw/community/core/skills_pool/ports.py`
- **Done when:**
  - [ ] `MappingPublishOutcome(published, verified, verified_inline)` exists in
        `models.py` as a frozen dataclass.
  - [ ] `_invoke` accepts an optional pre-resolved `DeviceContext` and only
        resolves when none is given.
  - [ ] `publish_and_verify_mappings` resolves once and passes that context to
        `_ensure_center_mappings`, the publish call, and the fallback verify.
  - [ ] `SkillsPoolRuntimeProtocol` declares the new method; existing
        `publish_mappings` / `verify_mappings` signatures are unchanged.
  - [ ] New test asserts three device calls (center ensure + publish + verify)
        trigger exactly one `resolve_for_bot`.
  - [ ] `tests/community/di/test_skills_pool_wiring.py` still resolves the
        protocol from the injector.
- **Depends on:** —

## Task 3: Report inline verification from the engine publish path

- **Goal:** A publish that verified its own result says so, so the backend can
  skip the second round trip.
- **Files:**
  - `src/engine/src/engine/community/plugins/skills_pool/layout_activation.py`
  - `src/engine/src/engine/community/core/skills/models.py`
  - `src/engine/src/engine/community/core/adapters/openclaw/skills.py`
  - `src/engine/src/engine/community/core/adapters/claude_code/skills.py`
- **Done when:**
  - [ ] `MappingPublishResult` (`layout_activation.py:114`) carries
        `verified: bool | None = None` declared **after** `evidence`, which has
        no default on that frozen slots dataclass.
  - [ ] `publish_pool_mappings` runs `verify_skill_mappings` inline on the success
        path with the same arguments and sets `verified` from its `valid`; the
        `published=False` early returns leave it `None`.
  - [ ] `verified` survives all three hops — the plugin dataclass's `to_data()`,
        the adapter's dict→model rebuild, and the core model's `to_data()` — and
        every one of them **omits** the key when it is `None`, so absence stays
        distinguishable from `false`.
  - [ ] The openclaw and claude_code adapters carry `verified` from the raw port
        response into `PoolMappingPublishResult`.
  - [ ] Test: a clean publish reports `verified=true`; a publish whose target is
        then corrupted reports `verified=false`; a result built without the flag
        omits the key from the wire body.
  - [ ] `src/engine/.../api/tests/test_skills_router.py` still passes.
- **Depends on:** —

## Task 4: Consume the inline verification signal and drop the second round trip

- **Goal:** The projector makes one device call to publish when the runtime
  verified inline, and two when it did not.
- **Files:**
  - `src/backend/src/agentclaw/community/core/skills_pool/runtime.py`
  - `src/backend/src/agentclaw/community/core/skill_center/services/bot_runtime_projector.py`
- **Done when:**
  - [ ] `publish_and_verify_mappings` reads `data["verified"]` from the publish
        response; only literal `True` skips the fallback verify.
  - [ ] A response with no `verified` key falls back to a separate `/verify` call.
  - [ ] A response with `verified: false` is treated as not converged — no
        fallback call, `verified=False` returned.
  - [ ] `_apply_pool_mappings` calls `publish_and_verify_mappings` and still
        raises `SkillSetRuntimeReconcileError` when the outcome is not verified.
  - [ ] Tests cover all three signal states and assert the transport call count
        for each.
- **Depends on:** Task 2, Task 3

## Task 5: Resolve post-mutation Skill assets once and pass them to the plan

- **Goal:** Cut `flush_installations` from four runs per mutation to two.
- **Files:**
  - `src/backend/src/agentclaw/community/core/skill_center/services/bot_runtime_projector.py`
  - `src/backend/src/agentclaw/community/core/skill_center/services/_mutation_flow.py`
  - `src/backend/src/agentclaw/community/core/skill_center/runtime_projection_contract.py`
  - `src/backend/src/agentclaw/community/api/bot_runtime_projector.py`
- **Done when:**
  - [ ] `snapshot_skill_assets` exists on `BotRuntimeProjector` and on both
        protocol declarations, keeping the teclaw `center://` guard.
  - [ ] `project` accepts `skill_assets: Sequence[RegisteredSkillAsset] | None = None`
        and `_build_plan` uses it instead of calling the reader when supplied.
  - [ ] `MutationProjectionFlow` resolves assets once after the mutation, derives
        `current_mappings` from them, and passes them into `project`.
  - [ ] `snapshot_skill_mappings` still exists and behaves as before for any
        caller that does not need the assets.
  - [ ] `skill_assets=None` still resolves internally, so
        `SkillSymlinkListener`'s direct `project(...)` is unaffected.
  - [ ] Test asserts the reader's flush runs exactly twice per mutation — once
        before, once after.
- **Depends on:** —

## Task 6: Share one installed-MCP read between the Default union and the resolver

- **Goal:** Remove the fourth flush and the duplicate `list_installed_mcps`.
- **Files:**
  - `src/backend/src/agentclaw/community/core/skill_center/services/bot_runtime_projector.py`
  - `src/backend/src/agentclaw/community/core/skill_center/services/skill_set_service.py`
- **Done when:**
  - [ ] `collect_bot_active_mcps` accepts keyword-only
        `installed_codes: frozenset[str] | None = None` and skips
        `_installed_mcp_codes` when supplied.
  - [ ] `_build_plan` reads installed MCPs once, after the Skill-asset flush, and
        uses that one value for both `collect_bot_active_mcps` and
        `RuntimeDesiredState.installed_mcp_server_codes`.
  - [ ] The entity-keyed fallback inside `_installed_mcp_codes` is untouched for
        callers that do not supply the codes.
  - [ ] `tests/.../services/test_collect_bot_active_mcps_union.py` covers both the
        supplied and the unsupplied path and still passes.
- **Depends on:** Task 5

## Task 7: Skip the MCP half when the projected set did not change

- **Goal:** Stop sending an identical allow-list and Passport manifest when a
  mutation's claims and releases both vanish against the projected set.
- **Files:**
  - `src/backend/src/agentclaw/community/core/skill_center/services/bot_runtime_projector.py`
- **Done when:**
  - [ ] Inside `_apply_non_skill_projection`, an empty `claimed` and empty
        `released` in the non-`claim_all_mcp` branch returns before
        `sync_mcp_projection` and before `update_passport`.
  - [ ] The skip logs bot id, engine, and the declared set size.
  - [ ] The guard is unreachable from the `claim_all_mcp` branch by construction.
  - [ ] Test: a deactivate whose MCPs are all still supplied by the Default policy
        performs zero device MCP calls and zero passport updates.
  - [ ] Test: `ProjectionScope(mcp=True, claim_all_mcp=True)` still declares the
        full set and pushes the passport.
- **Depends on:** —

## Task 8: Skip the Skill half when the mapping set is provably unchanged

- **Goal:** A mutation that moves no Skill sends no publish and no verify.
- **Files:**
  - `src/backend/src/agentclaw/community/core/skill_center/services/_mutation_flow.py`
- **Done when:**
  - [ ] `_project_or_compensate` narrows `scope` to `skills=False` when
        `set(current_mappings) == set(previous_mappings)` and there are no
        retirements.
  - [ ] The MCP half still runs; only the Skill flag is narrowed.
  - [ ] The skip logs the reason.
  - [ ] The compensating projection path is unaffected — a failed forward
        projection still restores desired state and counter-projects.
  - [ ] Test: deactivating an already-inactive Set issues no mapping publish.
  - [ ] Test: a mutation with retirements still publishes even when the current
        and previous sets compare equal.
  - [ ] Test: `SkillSymlinkListener`'s direct `project(ProjectionScope.everything())`
        still publishes — the skip lives in the flow, not the projector.
- **Depends on:** Task 5

## Task 9: Update the existing projection test surface

- **Goal:** Bring the doubles and pinned assertions in the existing suite in line
  with the new signatures, without weakening what they assert.
- **Files:**
  - `src/backend/tests/community/core/skill_center/test_skill_set_management_service.py`
  - `src/backend/tests/community/contracts/test_bot_runtime_projector.py`
  - `src/backend/tests/community/core/skill_center/test_bot_capability_state_reader.py`
- **Done when:**
  - [ ] The `_Runtime` double (`:259`) implements `snapshot_skill_assets` and
        accepts `skill_assets=` on `project`.
  - [ ] `_RecordingReconciler` (`contracts/test_bot_runtime_projector.py:20`)
        implements `snapshot_skill_assets`; both protocol conformance assertions
        still hold.
  - [ ] `test_existing_claude_code_skill_set_deactivate_uses_full_projection` (`:1609`)
        and `test_deactivate_retires_mappings_removed_from_the_runtime_projection` (`:821`)
        pass unchanged — confirming P5 does not narrow a scope where mappings moved.
  - [ ] `test_runtime_projection_flushes_installations_first` (`:1806`) is
        re-read against the two-flush shape and still asserts flush-before-read
        ordering rather than a stale count.
- **Depends on:** Task 4, Task 6, Task 7, Task 8

## Task 10: Tests & Verification

- **Goal:** Confirm every spec acceptance criterion holds.
- **Files:** the test files named in Tasks 1–9; `specs/2026-08-27-skillset-projection-latency/spec.md`
- **Done when:**
  - [ ] At most one device publish call when the runtime verifies inline; two when
        it does not.
  - [ ] Exactly one device address resolution per projection.
  - [ ] Exactly one binding row read per device address resolution.
  - [ ] At most two `flush_installations` per mutation; at most one
        `list_installed_mcps` after the mutation.
  - [ ] Empty claim and release ⇒ no allow-list declaration, no Passport update,
        skip logged.
  - [ ] Unchanged mapping set and no retirements ⇒ no publish, no verify, skip
        logged.
  - [ ] Projection failure still compensates and counter-projects.
  - [ ] A runtime with no inline-verification signal still gets a separate verify,
        and an unverified publish still raises `SkillSetRuntimeReconcileError`.
  - [ ] `claim_all_mcp` still declares the full MCP set.
  - [ ] Backend module gates pass: `scripts/ci/pre_push.sh` (or
        `OCB_PRE_PUSH_RUN_CI=1`) for `src/backend` and `src/engine`.
  - [ ] Wall-clock targets recorded as *expected*, to be confirmed against a
        prepub bot after deploy — not asserted in CI.
- **Depends on:** Task 9

---

## Groups

- **Group A — Device resolution:** Tasks 1, 2
  - Theme: One device address resolution per projection, and one binding read per
    resolution. Pure plumbing, no behavior change, independently shippable.
- **Group B — Inline verification:** Tasks 3, 4
  - Theme: The engine verifies its own publish and reports it; the backend drops
    the second round trip when it does, and falls back safely when it does not.
    Spans `src/engine` and `src/backend` — the two halves are only useful together.
- **Group C — Read once:** Tasks 5, 6
  - Theme: Resolve post-mutation Bot state once and thread it, taking flushes from
    four to two and installed-MCP reads from two to one.
- **Group D — Skip unchanged halves:** Tasks 7, 8
  - Theme: The two evidence-based skips. This is the only group that changes
    observable behavior (fewer device writes for no-op projections), so it is
    reviewed on its own.
- **Group E — Verification:** Tasks 9, 10
  - Theme: Existing test surface brought in line, then the full spec acceptance
    check.
