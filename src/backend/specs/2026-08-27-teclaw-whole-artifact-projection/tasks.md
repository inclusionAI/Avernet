# Tasks — One Whole-Artifact Delivery per Projection for Teclaw Bots

Five groups. The ordering carries the safety argument for a change that
relocates the per-domain path: **Group 3 moves code with no behaviour change,
and Group 4 changes teclaw only afterwards.** Reviewing them as separate
commits is what makes "per-domain is untouched" checkable, now that it is no
longer visible in a diff.

Paths are relative to `src/backend/src/agentclaw/community/` unless prefixed
with `tests/`, which is relative to `src/backend/`.

**Environment.** Use `.venv/bin/python -m pytest` from `src/backend`. Never
`uv run` or `uv sync --frozen` — `uv.lock` pins `mirrors.aliyun.com`, which
this sandbox answers `403` to. `uv.lock` must not appear in any commit; check
`git status` before each one.

**Commit per group.** Groups 1-4 each end in a commit on
`REL20260828_teclaw_whole_artifact_projection`; Group 5 validates and pushes.
That is what lets a reviewer read the relocation and the behaviour change
separately.

---

## Group 1 — The plan becomes a value (no behaviour change)

- [x] 1.1 In `core/skill_center/runtime_projection_contract.py`, add
      `ResolvedCapabilityPlan` — a `@dataclass(frozen=True, slots=True)` with
      `bot_id`, `owner_id`, `service`, `bot`, `engine`, `projection`,
      `effective_cli_items`, `identity_modes`. Type `service` loosely to avoid
      importing `SkillSetService` (cycle); say so in a comment. Export it.

- [x] 1.2 In `bot_runtime_projector.py`, make `_build_plan`, `_resolve_plan`
      and `_resolve_cleanup_plan` return `ResolvedCapabilityPlan`, and update
      the three unpack sites (`project`, `project_mcp_and_cli`,
      `project_for_cleanup`) to attribute access. `bot_id` / `owner_id` are
      already arguments to plan resolution — put them on the plan rather than
      threading them separately.

- [x] 1.3 Green check: `.venv/bin/python -m pytest
      tests/community/core/skill_center/ tests/community/contracts/ -q`.
      Group 1 is a pure refactor — **every test must pass unedited**. If one
      needs a change, the tuple was carrying meaning the dataclass lost.

- [ ] 1.4 Commit: `refactor(backend): give the capability plan a name`.

---

## Group 2 — The protocol and registry (no behaviour change)

- [x] 2.1 In `runtime_projection_contract.py`, add the `EngineRuntimeProjection`
      Protocol (`@runtime_checkable`) with `validate_plan(*, skill_assets,
      retired_mappings=())` and `async apply(*, plan, scope,
      retired_mappings)`. Docstrings state the contract: `validate_plan` runs
      during plan resolution before anything is written; `apply` raises
      `SkillSetRuntimeReconcileError` if the runtime did not converge, and
      owns how many runtime calls that takes. Export it.

- [x] 2.2 Extend the `ProjectionScope` docstring: the guarantees it documents
      are `PerDomainRuntimeProjection`'s, and an implementation may read the
      scope differently — pointing at `EngineRuntimeProjection`. Change no
      field, default, `everything()` or `inverted()`.

- [x] 2.3 Create `core/skill_center/services/runtime_projections/__init__.py`
      and `registry.py` with `EngineRuntimeProjectionRegistry`: a
      `{engine: EngineRuntimeProjection}` map plus a **default**.
      `for_engine(engine)` returns the map entry or the default. Per-domain is
      the default so a new ordinary engine needs no registration — only one
      whose runtime genuinely differs does. Log at INFO, once per resolution,
      which implementation was chosen.

- [x] 2.4 Green check: nothing consumes these yet, so
      `tests/community/core/skill_center/ tests/community/architecture/ -q`
      must be unchanged. The architecture run is the Rule-22 gate — if it
      objects to the new package, fix `core/skill_center/README.md`'s Context
      Boundary now rather than at the end.

- [ ] 2.5 Commit: `refactor(backend): add the engine runtime projection seam`.

---

## Group 3 — Move the per-domain path behind the seam (no behaviour change)

This is the relocation. Nothing here may change what any engine does.

- [x] 3.1 Create `runtime_projections/per_domain.py` with
      `PerDomainRuntimeProjection`, constructed with
      `SkillsPoolRuntimeProtocol` and `SkillsPoolLayoutRepositoryProtocol`.

- [x] 3.2 **Move** `_apply_skill_projection` and `_apply_pool_mappings` into
      it, comments intact — **including the two teclaw arms** at `:417-423`
      and `:426-432`. They look out of place in a class called
      "per-domain", and they are: they get deleted in 4.5, once
      `WholeArtifactRuntimeProjection` exists to receive teclaw. Deleting them
      here instead would silently route teclaw onto the Pool/legacy path for
      the length of this group, which is exactly the behaviour change this
      group promises not to make. The Pool-vs-legacy decision, the `retired`
      handling and the `SkillMappingSourceLayout` choice move **verbatim**.

- [x] 3.3 **Move** `_apply_non_skill_projection` into it, minus its `try/except`
      Passport tail (`:549-574`). The `claim_all_mcp` branch, the
      claimed/released guard against the projected set, the guard-log and the
      `sync_mcp_projection` call move **verbatim**.

- [x] 3.4 Give it `apply`, composing the two moved halves with today's scope
      gating and today's two skip-logs, in today's order. Give it
      `validate_plan` carrying today's `engine == "teclaw"` Center checks from
      `snapshot_skill_mappings:118` and `_build_plan:332` — again transitional,
      deleted in 4.5 — so that wiring 4.5's call sites in this group would be
      behaviour-preserving. Its docstring says both halves of that: what the
      method is for, and that the engine test inside it is scaffolding.

- [x] 3.5 **Extract** `_apply_passport_projection(*, plan, bot_id, owner_id)`
      and keep it on `BotRuntimeProjector`. This method does **not** exist
      today: the Passport update is currently an unnamed `try/except` block at
      the tail of `_apply_non_skill_projection` (`:549-574`), reached only by
      falling through `sync_mcp_projection`. 3.3 moves that call out to
      `per_domain.py`, so the tail needs a name to be invoked on its own.
      **Move the body with no edits**: it is the identity-coloured
      `resource_scope` fix from
      `specs/2026-08-26-mcp-sync-and-passport-regressions` problem 1, and a
      drifted copy silently reasserts `identityMode: "owner"`. Highest-risk
      step in the change. Its trigger stays `scope.mcp`, unchanged.

- [x] 3.6 In `bot_runtime_projector.py`: add `registry:
      EngineRuntimeProjectionRegistry` to `__init__`, drop `pool_runtime` and
      `pool_layouts` (now only `PerDomainRuntimeProjection` uses them), and
      rewrite `project` as: resolve plan → `registry.for_engine(plan.engine)`
      → `await runtime.apply(...)` → `_apply_passport_projection(...)` under
      `if scope.mcp`. Delete the three moved methods.

- [x] 3.7 `project_mcp_and_cli`: collapse to the same four lines. Its
      "MCP/CLI only" behaviour is exactly what `apply` does when
      `scope.skills` is false, so it needs no second protocol method. Keep its
      docstring's explanation of *why* the caller wants that.

- [x] 3.8 `project_for_cleanup`: keep its own Center refusal and its explicit
      `service.sync_runtime(...)` — a deliberate legacy-synchronizer path, not
      the Pool path — then delegate the MCP half through `apply` and call the
      Passport. Add a comment recording that it has no production caller.

- [x] 3.9 `di/modules/skill_center_module.py`: one `@provider` building
      `EngineRuntimeProjectionRegistry` over both implementations.
      `PerDomainRuntimeProjection` needs `pool_runtime` / `pool_layouts`, both
      already bound. Leave `binder.bind(BotRuntimeProjector, ...)` alone.

- [x] 3.10 Tests: add a `_registry()` helper beside the fakes that builds a
      **real** registry over both real implementations — a fake registry would
      test the wiring instead of the behaviour — and thread `registry=` into
      every direct `BotRuntimeProjector(...)` construction.

- [x] 3.11 Green check: `.venv/bin/python -m pytest
      tests/community/core/skill_center/ tests/community/contracts/
      tests/community/core/mcp/ tests/community/di/
      tests/community/architecture/ -q`. **Beyond adding `registry=`, no
      existing assertion may change.** If one does, the move was not a move —
      fix the code, not the test.

- [ ] 3.12 Commit: `refactor(backend): move per-domain projection behind the
      engine seam`. State in the body that behaviour is unchanged for every
      engine, teclaw included.

---

## Group 4 — Teclaw delivers once

- [x] 4.1 Add the regression guard **first**:
      `test_per_domain_engine_keeps_the_scope_split` — an openclaw Bot with
      `ProjectionScope(mcp=True, claimed_mcp=frozenset({"x"}))`:
      `runtime_syncs == []`, `len(mcp_projections) == 1`, and `claimed` still
      guarded down to the projected set. It must pass before 4.3 and after.
      (criterion 5)

- [x] 4.2 Extend `_RuntimeFactoryService` (`:474`) so call counts are
      observable, following its existing `deliveries` list idiom rather than
      integer counters: `runtime_syncs` appended in `sync_runtime` (`:484`),
      and `mcp_projections` appended at the top of `sync_mcp_projection`
      (`:498`) **before** it delegates, preserving the deliver-before-declare
      composition at `:512-514`. Leave `desired_skills`, `mcp_codes`,
      `deliveries`, `collect_calls` and the unrelated stub at `:2865` alone.
      Re-run: still green, unedited.

- [x] 4.3 Create `runtime_projections/whole_artifact.py` with
      `WholeArtifactRuntimeProjection` — no injected collaborators.
      `validate_plan` refuses `center://` assets and `center` retirements (the
      union of today's `:118`, `:332` and `:417` checks, which were three
      spellings of one rule). `apply`: skip-log and return when the scope
      declares nothing (`scope.skills or scope.mcp or retired_mappings`);
      re-assert `validate_plan` from the plan's own assets as defence in
      depth; then one `plan.service.sync_runtime(...)`, raising if falsy. Its
      docstring states the invariant: the plan is resolved, so one delivery
      carries both halves.

- [x] 4.4 Register `{"teclaw": WholeArtifactRuntimeProjection()}` in the DI
      provider from 3.9. From here teclaw routes to the new implementation, so
      the transitional teclaw code in `per_domain.py` is dead.

- [x] 4.5 Delete the now-dead teclaw arms and Center checks from
      `per_domain.py` (the ones 3.2/3.4 carried over), and point
      `snapshot_skill_mappings` and `_build_plan` at
      `registry.for_engine(engine).validate_plan(...)`. Both already have
      `engine` and `skill_assets` in hand. Do 4.4 before this: reversed, teclaw
      would take the Pool/legacy path in between.

- [x] 4.6 Verify the end state — `teclaw` appears in exactly two places under
      `core/skill_center/`: `whole_artifact.py` (where it is the subject) and
      the registry entry (where it is the key). Neither
      `bot_runtime_projector.py` nor `per_domain.py` may contain it.
      (criterion 6)

- [x] 4.7 Behaviour tests:
      - `test_teclaw_projects_the_whole_artifact_once_per_scope_shape` —
        parametrised over `skills=True`; `mcp=True, claimed_mcp={"x"}`;
        `mcp=True, released_mcp={"x"}`; both halves; and `everything()`. Each:
        `len(runtime_syncs) == 1`, `mcp_projections == []`. (criteria 1, 2, 3)
      - `test_teclaw_mcp_only_scope_still_delivers_the_skill_bearing_artifact`
        — `skills=False, mcp=True`: one delivery carrying the projection's
        `desired_skills`, not empty. (criterion 2)
      - `test_teclaw_still_updates_the_passport_with_identity_coloured_items`
        — one `update_passport` with `mcp_codes`, `mcp_items` carrying
        `identity_mode`, `cli_items`; `mcp=False` makes no Passport call.
        Guards 3.5. (criterion 4)
      - `test_teclaw_empty_scope_delivers_nothing` — both lists empty, no
        Passport call. (criterion 8)
      - `test_teclaw_failed_delivery_raises_reconcile_error` — `sync_runtime`
        returns `False`: `SkillSetRuntimeReconcileError`, no Passport call.
        (criterion 7)
      - `test_registry_defaults_unknown_engines_to_the_per_domain_projection`
        — an engine absent from the registry resolves to per-domain. Pins what
        keeps `claude_code` / `aicoding` / `hermes` working unregistered.
      - `test_projector_and_per_domain_contain_no_engine_identity_test` —
        assert `"teclaw"` appears in neither `bot_runtime_projector.py` nor
        `per_domain.py`. Blunt, but it is criterion 6 stated exactly; put the
        reason in the assertion message.

- [x] 4.8 Confirm unedited (beyond 3.10's `registry=`):
      `test_teclaw_v4_rejects_center_without_any_center_runtime_request:2493`
      — the direct test that the Center refusal survived consolidation into
      `validate_plan`;
      `test_teclaw_v4_repo_projection_uses_artifact_runtime_not_pool_mapping:2519`;
      `test_non_skill_projection_never_writes_skill_mappings:2552`.

- [ ] 4.9 Commit: `feat(backend): deliver one whole artifact per teclaw
      projection`.

---

## Group 5 — Prove nothing else moved

- [x] 5.1 Full relevant suite:
      ```
      .venv/bin/python -m pytest \
        tests/community/core/skill_center/ \
        tests/community/contracts/test_bot_runtime_projector.py \
        tests/community/core/devices/test_teclaw_device_sync.py \
        tests/community/core/mcp/ \
        tests/community/di/ \
        tests/community/architecture/ -q
      ```

- [x] 5.2 Broader sweep. The local `tests/community` run is dominated by slow
      e2e/singlebox paths (5% after 10 minutes in this sandbox), so this was
      satisfied by CI's **Backend unit tests** job on head `35168e6`, which
      runs the same suite in the project's own environment: **success**
      (17:00:18 → 17:14:54). **Singlebox coverage** also green. All 8 required
      checks pass. No failure to classify.

- [x] 5.3 Lint/SAST: `scripts/ci/python_sast_local.sh` from the repo root.

- [x] 5.4 `git status` — `uv.lock` unmodified, no `.venv` artefact staged.
      Then read the whole diff adversarially: every line either defines the
      seam, moves code across it unchanged, implements an engine, or tests it.

- [x] 5.5 Push and update PR #1616's body to the registry design — the
      Solution and Compatibility sections currently describe the early-branch
      version. Then mark it ready for review.

---

## Open questions

None blocking. Decisions confirmed with the author this session:

1. **Registry keyed on `engine`, not device `provider`.** `resolve_for_bot`
   costs a binding query plus a blocking ws-info HTTP that `sync_runtime`
   then repeats, and it raises `DeviceNotBoundError` for an unbound Bot,
   changing `project()`'s failure surface. `engine` is free and is already
   the module's vocabulary.
2. **Protocol covers `apply` + `validate_plan`.** Delivery alone would leave
   the two plan-time Center checks as engine strings; both together take the
   projector to zero.
3. **The Passport stays outside the protocol** — engine-agnostic, and keeping
   it unsplit protects the `identityMode` fix.
4. **One PR**, sequenced so Groups 1-3 are behaviour-neutral and Group 4 is
   the only behaviour change.

Two adjacent defects are deliberately **not** fixed here, written up in
`spec.md` → *Out of scope, reported*: Skill-declared MCP dependencies never
reaching a teclaw artifact, and the `strict_policy_context` divergence between
the projector and the composer. Both survive this change unchanged.
