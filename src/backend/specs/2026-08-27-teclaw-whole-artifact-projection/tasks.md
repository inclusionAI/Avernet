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

### 2c. Implementation

- [ ] 2.4 `bot_runtime_projector.py`: import
      `runtime_delivers_whole_artifact` alongside the existing
      `ProjectionScope` import.

- [ ] 2.5 In `project` (`:124`), after `_resolve_plan` returns, compute
      `whole_artifact = runtime_delivers_whole_artifact(engine)` and widen the
      Skill-half condition to
      `(whole_artifact and (scope.skills or scope.mcp or retired_mappings))
      or scope.skills or retired_mappings`. Extend the existing block comment
      — do not replace it — with why a whole-artifact engine ignores the split
      (both halves ride in one composed document; the scope can only pick how
      many identical copies get sent). Leave the `else` skip-log untouched.

- [ ] 2.6 Give `_apply_non_skill_projection` (`:503`) a keyword-only
      `deliver_mcp_to_runtime: bool` and wrap **only** the claimed/released
      guard plus the `sync_mcp_projection` call in it. The `else` branch logs
      that MCP delivery folded into the whole-artifact projection. Keep
      `codes = set(projection.mcp_server_codes)` above the branch, and leave
      the Passport block below it unmoved, un-re-indented and un-re-gated.

- [ ] 2.7 Pass `deliver_mcp_to_runtime=not whole_artifact` from all three
      entry points — `project` (`:124`), `project_mcp_and_cli` (`:188`),
      `project_for_cleanup` (`:218`) — each computing the predicate from the
      `engine` its plan resolution already returned. `project_for_cleanup`
      keeps its Center refusal (`:238`) and its unconditional `sync_runtime`
      (`:240`) exactly as they are.

- [ ] 2.8 Extend the `BotRuntimeProjector` class docstring's "Resolving and
      applying are separated by the `ProjectionScope`" paragraph with the
      whole-artifact case, in the register of the surrounding prose.

### 2d. Behaviour tests

- [ ] 2.9 `test_teclaw_projects_the_whole_artifact_once_per_scope_shape` —
      parametrised over `ProjectionScope(skills=True)`,
      `ProjectionScope(mcp=True, claimed_mcp=frozenset({"x"}))`,
      `ProjectionScope(mcp=True, released_mcp=frozenset({"x"}))`,
      `ProjectionScope(skills=True, mcp=True, claimed_mcp=frozenset({"x"}))`
      and `ProjectionScope.everything()`. For each:
      `len(runtime_syncs) == 1` and `mcp_projections == []`.
      (criteria 1, 2, 3)

- [ ] 2.10 `test_teclaw_mcp_only_scope_still_delivers_the_skill_bearing_artifact`
      — `skills=False, mcp=True`: exactly one delivery, and the
      `desired_skills` it carried equal the projection's assets rather than
      being empty. Pins the behaviour most likely to be optimised back out.
      (criterion 2)

- [ ] 2.11 `test_teclaw_still_updates_the_passport_with_identity_coloured_items`
      — one `update_passport` call whose `resource_scope` carries `mcp_codes`,
      `mcp_items` with resolved `identity_mode`, and `cli_items`; and an
      `mcp=False` scope makes no Passport call at all. (criterion 4)

- [ ] 2.12 `test_teclaw_empty_scope_delivers_nothing` — `ProjectionScope()`:
      `runtime_syncs == []`, `mcp_projections == []`, no Passport call.
      (criterion 8)

- [ ] 2.13 `test_teclaw_failed_delivery_raises_reconcile_error` — a fake whose
      `sync_runtime` returns `False`: expect `SkillSetRuntimeReconcileError`
      and no Passport call, so `MutationProjectionFlow` still compensates.
      (criterion 7)

- [ ] 2.14 `test_teclaw_cleanup_and_non_skill_entry_points_skip_mcp_runtime_delivery`
      — `project_for_cleanup` and `project_mcp_and_cli` on a teclaw Bot:
      `mcp_projections == []` with the Passport still updated. (criterion 6)

- [ ] 2.15 Confirm the two pre-existing teclaw tests pass **unedited**:
      `test_teclaw_v4_rejects_center_without_any_center_runtime_request:2493`
      and
      `test_teclaw_v4_repo_projection_uses_artifact_runtime_not_pool_mapping:2519`.
      If either needs an edit, the implementation moved something it should
      not have — fix the implementation, not the test.

- [ ] 2.16 Same for `test_non_skill_projection_never_writes_skill_mappings:2552`
      (an openclaw bot on `project_mcp_and_cli`): must pass unedited.

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

1. The single delivery rides the existing `_apply_skill_projection` teclaw
   branch rather than a new `_deliver_whole_artifact` method — that branch is
   already exactly one `sync_runtime` call and already carries the
   Center-corpus refusal, and a second copy of either could drift.
2. `project_mcp_and_cli` gets the same treatment as `project` even though it
   is unreachable for teclaw today (desktop-only caller; teclaw is never
   Pool-capable). Uniformity across entry points is acceptance criterion 6.

Two adjacent defects are deliberately **not** fixed here and are written up
under `spec.md` → *Out of scope, reported*: Skill-declared MCP dependencies
never reaching a teclaw artifact, and the `strict_policy_context` divergence
between the projector and the composer. Both survive this change unchanged.
