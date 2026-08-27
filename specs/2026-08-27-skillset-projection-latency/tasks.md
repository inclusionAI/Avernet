# Tasks: SkillSet Projection Latency

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

> **Scope of this change: P1 and P2 only.** P3, P4 and P5 from `spec.md` are
> deferred and tracked separately — the analysis and change sketches stay in
> `spec.md` / `plan.md` for those issues to work from:
>
> | Spec | Issue | What |
> |---|---|---|
> | P3 | inclusionAI/Avernet#1621 | `flush_installations` runs four times per mutation |
> | P4 | inclusionAI/Avernet#1622 | MCP allow-list and Passport rewritten when unchanged |
> | P5 | inclusionAI/Avernet#1623 | Skill mappings published when provably unchanged |

## Task 1 `[x]`: Thread the loaded binding record through ARCA connection resolution

- **Goal:** Stop re-reading a binding row that the caller already holds, so one
  device address resolution costs no extra `get_by_id`.
- **Files:**
  - `src/backend/src/agentclaw/community/core/devices/services/conn_info_builders/arca_builder.py`
  - `src/backend/src/agentclaw/community/core/devices/services/device_service.py`
  - `src/backend/src/agentclaw/community/core/devices/services/device_service_router.py`
  - `src/backend/src/agentclaw/community/core/devices/services/local_device_service.py`
  - `src/backend/src/agentclaw/community/core/devices/services/baas_device_service.py`
- **Done when:**
  - [x] `get_device_connection_v2` accepts keyword-only `record: DeviceBindingRecord | None = None`
        and uses it instead of `self.get_device(binding_id=...)` when supplied.
  - [x] `get_device_connection` accepts the same keyword on the base, the router,
        and both provider overrides; each defaults to today's read when it is `None`.
  - [x] `DeviceServiceRouter._get_provider_for_binding` accepts `record=` and routes
        from it without a database read when supplied.
  - [x] `ArcaConnInfoBuilder.build` passes `record=binding`.
  - [x] New test asserts one `resolve_for_bot` on an arca bot issues exactly one
        binding read (`get_active_by_bot_and_owner`) and zero `get_by_id` calls.
  - [x] `tests/community/core/devices/services/test_device_service_router.py` and
        `test_device_service.py` still pass with no signature-related failures.
- **Depends on:** —

## Task 2 `[x]`: Add `publish_and_verify_mappings` with a single device resolution

- **Goal:** One device address resolution serves center-ensure, publish, and
  verify for a projection, instead of one resolution per call.
- **Files:**
  - `src/backend/src/agentclaw/community/core/skills_pool/runtime.py`
  - `src/backend/src/agentclaw/community/core/skills_pool/models.py`
  - `src/backend/src/agentclaw/community/core/skills_pool/ports.py`
- **Done when:**
  - [x] `MappingPublishOutcome(published, verified, reported_inline)` exists in
        `models.py` as a frozen dataclass. (`reported_inline`, not
        `verified_inline`: a runtime can report inline *and* report failure.)
  - [x] `_invoke` accepts an optional pre-resolved `DeviceContext` and only
        resolves when none is given.
  - [x] `publish_and_verify_mappings` resolves once and passes that context to
        `_ensure_center_mappings`, the publish call, and the fallback verify.
  - [x] `SkillsPoolRuntimeProtocol` declares the new method; existing
        `publish_mappings` / `verify_mappings` signatures are unchanged —
        the context-carrying halves stay private, so no second implementation
        of the port has to grow a devices-layer parameter.
  - [x] New test asserts three device calls (center ensure + publish + verify)
        trigger exactly one `resolve_for_bot`.
  - [x] `tests/community/di/test_skills_pool_wiring.py` still resolves the
        protocol from the injector.
- **Depends on:** —

## Task 3 `[x]`: Report inline verification from the engine publish path

- **Goal:** A publish that verified its own result says so, so the backend can
  skip the second round trip.
- **Files:**
  - `src/engine/src/engine/community/plugins/skills_pool/layout_activation.py`
  - `src/engine/src/engine/community/core/skills/models.py`
  - `src/engine/src/engine/community/core/adapters/openclaw/skills.py`
  - `src/engine/src/engine/community/core/adapters/claude_code/skills.py`
- **Done when:**
  - [x] `MappingPublishResult` (`layout_activation.py:114`) carries
        `verified: bool | None = None` declared **after** `evidence`, which has
        no default on that frozen slots dataclass.
  - [x] `publish_pool_mappings` runs `verify_skill_mappings` inline on the success
        path with the same arguments and sets `verified` from its `valid`; the
        `published=False` early returns leave it `None`.
  - [x] `verified` survives all three hops — the plugin dataclass's `to_data()`,
        the adapter's dict→model rebuild, and the core model's `to_data()` — and
        every one of them **omits** the key when it is `None`, so absence stays
        distinguishable from `false`.
  - [x] The openclaw and claude_code adapters carry `verified` from the raw port
        response into `PoolMappingPublishResult`.
  - [x] Test: a clean publish reports `verified=true`; a publish whose target is
        then corrupted reports `verified=false`; a result built without the flag
        omits the key from the wire body.
  - [x] `src/engine/.../api/tests/test_skills_router.py` still passes.
- **Depends on:** —

## Task 4 `[x]`: Consume the inline verification signal and drop the second round trip

- **Goal:** The projector makes one device call to publish when the runtime
  verified inline, and two when it did not.
- **Files:**
  - `src/backend/src/agentclaw/community/core/skills_pool/runtime.py`
  - `src/backend/src/agentclaw/community/core/skill_center/services/bot_runtime_projector.py`
- **Done when:**
  - [x] `publish_and_verify_mappings` reads `data["verified"]` from the publish
        response; only literal `True` skips the fallback verify.
  - [x] A response with no `verified` key falls back to a separate `/verify` call.
  - [x] A response with `verified: false` is treated as not converged — no
        fallback call, `verified=False` returned.
  - [x] `_apply_pool_mappings` calls `publish_and_verify_mappings` and still
        raises `SkillSetRuntimeReconcileError` when the outcome is not verified.
  - [x] Tests cover all three signal states and assert the transport call count
        for each.
- **Depends on:** Task 2, Task 3

## Task 5: Update the existing device and pool-runtime test surface

- **Goal:** Bring the doubles and pinned assertions in the existing suite in line
  with the new signatures, without weakening what they assert.
- **Files:**
  - `src/backend/tests/community/core/skill_center/test_skill_set_management_service.py`
  - `src/backend/tests/community/core/devices/services/test_device_service_router.py`
  - `src/backend/tests/community/core/devices/services/test_device_service.py`
  - `src/backend/tests/community/core/devices/services/test_device_context_resolver.py`
- **Done when:**
  - [ ] The `_RuntimePool` double (`test_skill_set_management_service.py:648`)
        implements `publish_and_verify_mappings` and keeps recording publish and
        verify calls separately, so existing assertions on those lists still mean
        what they meant.
  - [ ] `_CenterRuntimePool` (`:665`) still asserts the probe path for Center
        projections.
  - [ ] Device-service tests still pass with the added `record=` keyword; no test
        is weakened to accommodate a signature change.
  - [ ] `tests/community/di/test_skills_pool_wiring.py` still resolves
        `SkillsPoolRuntimeProtocol` from the injector.
- **Depends on:** Task 4

## Task 6: Tests & Verification

- **Goal:** Confirm the P1 and P2 acceptance criteria hold.
- **Files:** the test files named in Tasks 1-5
- **Done when:**
  - [ ] Exactly one device address resolution per projection, whatever the number
        of device calls it makes.
  - [ ] Exactly one binding row read per device address resolution.
  - [ ] At most one device publish call when the runtime verifies inline; two when
        it does not.
  - [ ] A runtime with no inline-verification signal still gets a separate verify;
        `verified: false` is treated as not converged; an unverified publish still
        raises `SkillSetRuntimeReconcileError`.
  - [ ] Projection failure still compensates and counter-projects, unchanged.
  - [ ] No change to what desired state is persisted or what the runtime holds.
  - [ ] Backend and engine module gates pass: `scripts/ci/pre_push.sh`
        (or `OCB_PRE_PUSH_RUN_CI=1`).
  - [ ] Deferred criteria (flush counts, the two skips) are explicitly **not**
        claimed here — they belong to #1621, #1622 and #1623.
- **Depends on:** Task 5

---

## Groups

- **Group A — Device resolution (P1):** Tasks 1, 2
  - Theme: One device address resolution per projection, and one binding read per
    resolution. Pure plumbing, no behavior change, independently shippable.
- **Group B — Inline verification (P2):** Tasks 3, 4
  - Theme: The engine verifies its own publish and reports it; the backend drops
    the second round trip when it does, and falls back safely when it does not.
    Spans `src/engine` and `src/backend` — the two halves are only useful together.
- **Group C — Verification:** Tasks 5, 6
  - Theme: Existing test surface brought in line, then the P1/P2 acceptance check.
