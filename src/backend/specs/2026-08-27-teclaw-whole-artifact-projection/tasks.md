# Tasks — One Whole-Artifact Delivery per Projection for Teclaw Bots

Three groups, ordered so the suite is green after each one.

Group 1 adds the vocabulary and changes nothing observable. Group 2 is the
behaviour change, written test-first so the regression guard for per-domain
engines exists before the branch that could break it. Group 3 is the sweep
that proves nothing else moved.

Paths are relative to `src/backend/src/agentclaw/community/` unless prefixed
with `tests/`, which is relative to `src/backend/`.

**Environment.** Use `.venv/bin/python -m pytest` from `src/backend`. Do not
run `uv run` or `uv sync --frozen` — `uv.lock` pins `mirrors.aliyun.com`,
which this sandbox answers `403` to. `uv.lock` must not appear in any commit;
check `git status` before each one.

---

## Group 1 — Name the property (no behaviour change)

- [ ] 1.1 In `core/skill_center/runtime_projection_contract.py`, add
      `_WHOLE_ARTIFACT_ENGINES = frozenset({"teclaw"})` and
      `runtime_delivers_whole_artifact(engine: str) -> bool` above
      `ProjectionScope`. Docstring names
      `core/devices/services/teclaw_device_sync.py` as the authority and says
      why the predicate exists (one call carries both halves, so a second
      restates the first). Add the function to `__all__`.

- [ ] 1.2 Extend the `ProjectionScope` class docstring with one paragraph:
      its guarantees — "a single-MCP add stays a single device write", and
      `claim_all_mcp`'s "a freshly active container holds no MCP
      configuration" premise — describe engines whose halves have separate
      runtime endpoints; on a whole-artifact engine the projector reads the
      scope through `runtime_delivers_whole_artifact`. Do **not** change any
      field, default, `everything()`, or `inverted()`.

- [ ] 1.3 Confirm no boundary change is needed: the new symbol is consumed
      only inside `core/skill_center`, so `core/skill_center/README.md`'s
      Context Boundary `provides`/`consumes` lists stay as they are. Verify by
      running `tests/community/architecture/ -q` — this is the Rule-22 gate,
      and it must pass before Group 2 starts.

- [ ] 1.4 Green check: `.venv/bin/python -m pytest
      tests/community/core/skill_center/ tests/community/architecture/ -q`.
      Expect no test to change behaviour — Group 1 adds an unused function.

---

## Group 2 — One delivery on a whole-artifact runtime

### 2a. Test scaffolding first

- [ ] 2.1 In `tests/community/core/skill_center/test_skill_set_management_service.py`,
      extend `_RuntimeFactoryService` (`:474`) so call **counts** are
      observable, following the existing `deliveries` list idiom rather than
      adding integer counters:
      - `self.runtime_syncs: list[list[dict]] = []`, appended in
        `sync_runtime` (`:484`) — keep the existing `self.desired_skills`
        assignment so no current assertion changes.
      - `self.mcp_projections: list[tuple[frozenset[str], frozenset[str], set[str]]] = []`,
        appended at the top of `sync_mcp_projection` (`:498`) **before** it
        delegates to `sync_mcp_delivery` / `sync_mcp_desired_state`, so the
        deliver-before-declare composition at `:512-514` — which existing
        tests assert on — is preserved verbatim.
      Leave the unrelated `sync_mcp_projection` stub at `:2865` alone.

- [ ] 2.2 Confirm the scaffolding is inert:
      `.venv/bin/python -m pytest
      tests/community/core/skill_center/test_skill_set_management_service.py -q`
      must still report **77 passed**.

### 2b. The regression guard, before the change that could trip it

- [ ] 2.3 Add `test_per_domain_engine_keeps_the_scope_split` beside the teclaw
      projector tests (after `:2519`). An **openclaw** Bot (`_RuntimeBots`)
      with `ProjectionScope(mcp=True, claimed_mcp=frozenset({"x"}))`:
      assert `runtime_syncs == []` and `len(mcp_projections) == 1`, and that
      the recorded `claimed` is still guarded down to the projected set. This
      is acceptance criterion 5 and the highest-value test in the change —
      it must pass **before** and after Group 2c.

### 2c. Implementation — the branch, then the cleanup behind it

Order matters: 2.6 extracts before 2.7 consumes, and 2.9 deletes the teclaw
arms only after 2.8 has given them a new home. Run 2.10 after each of
2.6-2.9 rather than only at the end.

- [ ] 2.4 `bot_runtime_projector.py`: import
      `runtime_delivers_whole_artifact` alongside the existing
      `ProjectionScope` import.

- [ ] 2.5 Extend the `BotRuntimeProjector` class docstring's "Resolving and
      applying are separated by the `ProjectionScope`" paragraph with the
      whole-artifact case, in the register of the surrounding prose. Doing the
      prose first makes the following edits read against a stated invariant.

- [ ] 2.6 **Extract** `_apply_passport_projection(*, identity_modes, engine,
      bot_id, owner_id, projection, effective_cli_items)` from the `try/except`
      tail of `_apply_non_skill_projection` (`:549-574`) and call it from
      there. Synchronous — nothing in the block awaits. **Move the body with
      no edits**: it is the identity-coloured `resource_scope` fix from
      `specs/2026-08-26-mcp-sync-and-passport-regressions` problem 1, and a
      drifted copy silently reasserts `identityMode: "owner"`.

- [ ] 2.7 Add `_apply_whole_artifact_projection(*, service, bot_id, owner_id,
      projection, retired_mappings, scope, identity_modes,
      effective_cli_items)`: the Center refusal (moved from
      `_apply_skill_projection:417-423`, comment included), then the
      nothing-declared no-op guard `scope.skills or scope.mcp or
      retired_mappings`, then one `service.sync_runtime(...)`, then
      `_apply_passport_projection(...)` under `if scope.mcp`. Its docstring
      states the invariant: the plan is resolved, so one delivery carries both
      halves.

- [ ] 2.8 In `project` (`:124`), **insert** immediately after `_resolve_plan`
      and above the existing comment block:
      ```python
      if runtime_delivers_whole_artifact(engine):
          await self._apply_whole_artifact_projection(...)
          return
      ```
      with a comment on why this is the right line — `_resolve_plan` is what
      flushed Installation, so desired state is final and the composer needs
      nothing else. **Do not touch the two scope-driven halves below it**:
      this task must show up in `git diff` as a pure insertion, with no
      modified line in the per-domain path.

- [ ] 2.9 `_apply_skill_projection`: delete both teclaw arms (`:417-423`,
      `:426-432`) and drop the now-unused `engine` parameter; update its one
      call site in `project`. The method is now purely per-domain (Pool vs
      legacy). Dropping the parameter is deliberate — it turns a
      reintroduced teclaw branch into a `NameError` rather than a silent
      regression.

- [ ] 2.10 `project_mcp_and_cli` (`:188`) and `project_for_cleanup` (`:218`):
      one comment each recording that they are per-domain-only paths, with the
      evidence (`project_for_cleanup` has no production caller;
      `project_mcp_and_cli`'s only caller is desktop-gated and teclaw is never
      Pool-capable). **No code change** in either.

- [ ] 2.11 After each of 2.6-2.9:
      `.venv/bin/python -m pytest
      tests/community/core/skill_center/test_skill_set_management_service.py -q`.
      2.6 and 2.10 must stay at the Group-1 count; 2.7-2.9 may only change
      teclaw results.

### 2d. Behaviour tests

- [ ] 2.12 `test_teclaw_projects_the_whole_artifact_once_per_scope_shape` —
      parametrised over `ProjectionScope(skills=True)`,
      `ProjectionScope(mcp=True, claimed_mcp=frozenset({"x"}))`,
      `ProjectionScope(mcp=True, released_mcp=frozenset({"x"}))`,
      `ProjectionScope(skills=True, mcp=True, claimed_mcp=frozenset({"x"}))`
      and `ProjectionScope.everything()`. For each:
      `len(runtime_syncs) == 1` and `mcp_projections == []`.
      (criteria 1, 2, 3)

- [ ] 2.13 `test_teclaw_mcp_only_scope_still_delivers_the_skill_bearing_artifact`
      — `skills=False, mcp=True`: exactly one delivery, and the
      `desired_skills` it carried equal the projection's assets rather than
      being empty. Pins the behaviour most likely to be optimised back out.
      (criterion 2)

- [ ] 2.14 `test_teclaw_still_updates_the_passport_with_identity_coloured_items`
      — one `update_passport` call whose `resource_scope` carries `mcp_codes`,
      `mcp_items` with resolved `identity_mode`, and `cli_items`; and an
      `mcp=False` scope makes no Passport call at all. Guards the 2.6
      extraction. (criterion 4)

- [ ] 2.15 `test_teclaw_empty_scope_delivers_nothing` — `ProjectionScope()`:
      `runtime_syncs == []`, `mcp_projections == []`, no Passport call.
      (criterion 8)

- [ ] 2.16 `test_teclaw_failed_delivery_raises_reconcile_error` — a fake whose
      `sync_runtime` returns `False`: expect `SkillSetRuntimeReconcileError`
      and no Passport call, so `MutationProjectionFlow` still compensates.
      (criterion 7)

- [ ] 2.17 Confirm these pass **unedited**:
      `test_teclaw_v4_rejects_center_without_any_center_runtime_request:2493`
      (the direct test that the Center refusal survived its move in 2.7/2.9),
      `test_teclaw_v4_repo_projection_uses_artifact_runtime_not_pool_mapping:2519`,
      and `test_non_skill_projection_never_writes_skill_mappings:2552` (direct
      cover that the 2.6 extraction changed nothing for openclaw). If any
      needs an edit, the implementation moved something it should not have —
      fix the implementation, not the test.

      No test is added for `project_mcp_and_cli` / `project_for_cleanup` on a
      teclaw Bot: neither is reachable, and pinning behaviour of an
      unreachable path would assert the wrong contract.

---

## Group 3 — Prove nothing else moved

- [ ] 3.1 Full relevant suite:
      ```
      .venv/bin/python -m pytest \
        tests/community/core/skill_center/ \
        tests/community/contracts/test_bot_runtime_projector.py \
        tests/community/core/devices/test_teclaw_device_sync.py \
        tests/community/core/mcp/ \
        tests/community/architecture/ -q
      ```
      `test_bot_runtime_projector.py` must pass with no edit — if it fails,
      `deliver_mcp_to_runtime` leaked into the Service API protocol and must
      be made private again.

- [ ] 3.2 Broader regression sweep: `.venv/bin/python -m pytest
      tests/community -q -x`. Record the pass count. Investigate any failure
      rather than assuming it is pre-existing — re-run it at
      `git stash`-clean HEAD to classify.

- [ ] 3.3 Lint/SAST gate: `scripts/ci/python_sast_local.sh` from the repo
      root (the gate `scripts/ci/pre_push.sh` runs in lint-only mode).

- [ ] 3.4 `git status` — confirm `uv.lock` is **not** modified and no
      `.venv` artefact is staged. Then review the full diff adversarially:
      every changed line either adds the predicate, routes a decision through
      it, or tests it.

- [ ] 3.5 Commit to `REL20260828` and push with `git push -u origin
      REL20260828`. Open a **draft** PR using
      `.github/pull_request_template.md`'s sections (Problem / Solution /
      Validation / Compatibility and risk / Spec), title
      `refactor(backend): deliver one whole artifact per teclaw projection`.
      Link this spec directory under **Spec**. Under **Validation**, state
      the pass counts and that `uv sync --frozen` could not run in the
      sandbox, with the reason.

---

## Open questions

None blocking. Two decisions taken unilaterally, both recorded in `plan.md`
with their rejected alternatives:

1. The teclaw arms are **moved out of** `_apply_skill_projection` into the new
   `_apply_whole_artifact_projection` rather than called into, so that past
   the branch no per-domain code knows teclaw exists. Dropping the freed
   `engine` parameter is part of that: it makes a reintroduced branch fail
   loudly.
2. Only `project` gets the branch. `project_for_cleanup` has no production
   caller, and `project_mcp_and_cli`'s single caller is desktop-gated while
   teclaw is never Pool-capable — so both are unreachable for teclaw, and its
   premise ("a cutover owns the Skill mappings") is incoherent for an engine
   that cannot deliver MCP without redelivering Skills. Both get a comment,
   not speculative code.

Two adjacent defects are deliberately **not** fixed here and are written up
under `spec.md` → *Out of scope, reported*: Skill-declared MCP dependencies
never reaching a teclaw artifact, and the `strict_policy_context` divergence
between the projector and the composer. Both survive this change unchanged.
