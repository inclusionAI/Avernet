# Tasks — Installation as the Single Source of Truth

Groups are ordered so the suite stays green after each group. Every deletion
lands in the same group as the migration of its last caller.

## Group 1 — One flush: extend the repair to MCPs, retire the materializer

- [ ] 1.1 Add `mcp_activate` / `mcp_deactivate` (frozenset[str], default
      empty) to `BotSkillSetBridge` in
      `core/repository/skill_set_control_plane_types.py`.
- [ ] 1.2 In `bot_skillset_installations.py`, extend `_resolve_bridge` to
      collect MCP members per Set (Default Sets minus `excluded_mcp_codes`),
      with the same active-claim-wins rule as skills.
- [ ] 1.3 Extend `repair_bot_skillset_installations`: fast path checks skill
      *and* MCP deltas; write path inserts missing `BotMCPInstallation` rows
      (SAVEPOINT-per-row like `_install_one`) and deletes
      `mcp_deactivate ∩ installed`.
- [ ] 1.4 Repository tests: MCP rows follow set activation, Default-Set MCP
      exclusions honored, direct MCP rows untouched, repair idempotent
      (extend `tests/community/repository/skill_center/
      test_skill_set_control_plane_uow.py` or add a sibling).
- [ ] 1.5 Swap the two materializer call sites to the repair:
      `bot_runtime_projection_reconciler._resolve_plan` and
      `core/service_bot/services/publish_flow/build_stage.py` (temporary
      direct repo call; Group 2 moves them onto the reader).
- [ ] 1.6 Delete `active_skillset_installation_materializer.py`,
      `ensure_active_skillset_installations` (impl + protocol), their DI
      bindings (`skill_center_module.py`, `service_bot_module.py` wiring),
      and their tests. Update `core/service_bot/README.md` dependency line.
- [ ] 1.7 Run Group 1 test files + reconciler contract test.

## Group 2 — The reader

- [ ] 2.1 Add `api/bot_installation_reader.py`
      (`BotInstallationReaderProtocol`: `flush`, `active_skill_assets`,
      `active_mcp_server_codes` — docstrings per plan component 3).
- [ ] 2.2 Add `core/skill_center/services/bot_installation_reader.py`
      implementing the protocol over `bot_repo` + the control-plane repo +
      the skill repo; engine scoping via `bot_engine_scope` helpers.
- [ ] 2.3 Bind in `di/modules/skill_center_module.py` (impl + protocol).
- [ ] 2.4 New `tests/community/core/skill_center/
      test_bot_installation_reader.py`: flush-then-read for skills and MCPs,
      missing-Bot error, `bot` row passed vs looked up.
- [ ] 2.5 Repurpose `SkillRepository.list_bot_active_assets` → pure
      Installation join renamed `list_bot_installed_assets` (drop the merge
      and the `engine` read parameter; dedup by id); update
      `core/repository/protocols/skills_pool.py`.
- [ ] 2.6 Migrate merge-readers to `reader.active_skill_assets`:
      reconciler (`snapshot_skill_mappings`, `_build_plan` — drop the Group-1
      direct repair call), `local_skill_state_service`
      `_require_no_runtime_name_conflict`, `skills_pool/mapping_convergence.py`,
      `recovery_service.py`, `reconcile_service.py`,
      `active_aicoding_bridge_repair.py`, and Service-Bot `build_stage.py`
      (flush via reader). Update their DI/constructor wiring.
- [ ] 2.7 Guard: `grep` shows no caller of `list_bot_installed_assets`
      outside `bot_installation_reader.py`, and no remaining reference to
      `list_bot_active_assets`.
- [ ] 2.8 Add a reconciler test pinning Default-Set selection via
      `bot_default_engine_types` (layout engine first). Run reconciler
      contract, skills_pool, and local_skill_state tests.

## Group 3 — Symlink/compose projection converges

- [ ] 3.1 Inject the reader into `SkillSetService` via
      `SkillSetServiceFactory`; replace the `get_active_skills` merge body
      with delegation to `reader.active_skill_assets`, preserving the dict
      keys consumers read (`id`, `name`, `git_path`, `skill_uuid`,
      `sc_version_number`).
- [ ] 3.2 Update `tests/community/services/
      test_skill_set_service_symlink_mappings.py`: seed Installation (or stub
      the reader); add the Default-Set-exclusion case proving excluded
      members are no longer symlinked.
- [ ] 3.3 Add/adjust a config-compose collector test pinning the skill dict
      contract (`tests/community/core/config_compose/...`).

## Group 4 — MCP union in one place

- [ ] 4.1 Reimplement `SkillSetService.collect_bot_active_mcps` per plan
      component 6: Default-Set projection (unchanged helpers) ∪ installed
      codes from `reader.active_mcp_server_codes`, metadata enriched from
      the Bot's `SkillSetMCPServer` rows, minimal entry otherwise. Stop
      iterating active ordinary Sets. `collect_bot_mcps` unchanged.
- [ ] 4.2 Tests for the union (active ordinary set MCP appears via
      Installation; excluded default MCP absent; direct-installed MCP
      appears with minimal metadata).
- [ ] 4.3 Verify the reconciler's `installed ∪ effective_default` inputs are
      consistent post-change (contract test).

## Group 5 — CapabilityOwnershipPolicy

- [ ] 5.1 Add `core/skill_center/policies/capability_ownership.py` with the
      module docstring stating R1–R3 and the pure decision functions
      (`skill_set_reaches_bot` moved verbatim, `governing_set`,
      `membership_conflict`) per plan component 4.
- [ ] 5.2 Collapse the two duplicate skill guards in
      `local_skill_state_service.py` (`_reject_skill_set_member`,
      `_require_no_normal_skill_set_membership`, `_set_governs`) into thin
      raise-wrappers over `governing_set`; existing error types unchanged.
- [ ] 5.3 Route `add_skill` / `add_mcp` conflict decisions through
      `membership_conflict` (facts still read under the transaction; error
      precedence R2-before-R3 pinned by a test).
- [ ] 5.4 Close the MCP gap: `activate_mcp_direct` / `deactivate_mcp_direct`
      use `governing_set` including Default Sets minus MCP exclusions
      (spec D.10); add tests: direct deactivate of a Default-Set MCP is
      refused; an *excluded* Default-Set MCP remains directly controllable.
- [ ] 5.5 Policy unit tests
      (`tests/community/core/skill_center/test_capability_ownership.py`) +
      rerun local_skill_state and control-plane UoW tests.

## Group 6 — Retire the legacy activation writers

- [ ] 6.1 Add `deactivate_all` to `SkillSetControlPlaneService` and
      `deactivate_all_sets` to the repository (one txn: ordinary Sets
      inactive; delete the Bot's skill-Installation rows and set-claimed MCP
      rows; snapshot/restore; standard `_mutate` reconcile). Route
      `/api/skills/deactivate-all` through it, keeping the response shape.
      Repository + service + adapter tests; update
      `tests/community/_flows/skill_center/api_lifecycle.py`.
- [ ] 6.2 Reimplement deprecated `/api/skills/skillset/current` from
      `control_plane.list_sets` (first ordinary active Set, else `None`).
- [ ] 6.3 Delete the dead data-init activation step mechanically — the
      `_activate_and_sync_skill_sets` call site (line ~388), the method, the
      `skill_set_activator_factory` ctor param, and the DI argument in
      `bot_management_module.py`. No migration, no new tests: dead feature
      per the domain owner (spec Out of Scope).
- [ ] 6.4 Delete `SkillSetActivator`, `SkillSetSwitcher`, both factories,
      `_DeviceSyncMixin`, the result dataclasses (if unreferenced),
      `api/skill_set_activator_factory.py`,
      `api/skill_set_switcher_factory.py`, DI providers in
      `skill_center_module.py` / `skill_center_protocols.py`, and
      `CURRENT_SET_FILE` plumbing only they used. Migrate or delete their
      tests (`test_skill_set_auto_activate.py` and friends).
- [ ] 6.5 Guard: `grep` shows no reference to Activator/Switcher anywhere
      (src, tests, DI; docs updated in Group 8).

## Group 7 — Flush-consistent skill detail

- [ ] 7.1 `BotSkillAssetService.get_skill`: call `reader.flush(bot=bot)`
      before the Installation read (reader injected via DI).
- [ ] 7.2 `LocalSkillQueryService`: replace the direct
      `repair_bot_skillset_installations` call with `reader.flush`, keep
      using the returned bridge; drop its control-plane-repo dependency if
      now unused.
- [ ] 7.3 Update `test_bot_skill_asset_service.py`,
      `test_local_skill_query_service.py`; add a case: bridged skill shows
      `active=true` in detail before any listing ran.

## Group 8 — Dead code sweep and docs

- [ ] 8.1 Delete `core/skill_center/installation_compatibility.py`.
- [ ] 8.2 Delete `_ensure_default_set` / `_ensure_default_set_membership`
      from `local_skill_upload_service.py` (+ their tests).
- [ ] 8.3 For each of `add_skills_to_set`, `remove_skill_from_set`,
      `add_mcp_to_skill_set`, `remove_mcp_from_skill_set` on
      `SkillSetService`: verify zero callers by search, then delete (keep any
      that are still reached; record which in the PR).
- [ ] 8.4 Update `core/skill_center/README.md` context boundary + narrative
      (single flush-then-read model; drop deleted components; add the reader
      and the ownership policy).
- [ ] 8.5 Update `adapters/http/skill_center/CLAUDE.md` key-file index rows
      naming deleted classes.

## Group 9 — Full validation

- [ ] 9.1 `uv run pytest tests/community` (backend suite).
- [ ] 9.2 SAST/lint gate (`scripts/ci/python_sast_local.sh` via pre-push
      hook) — report anything not runnable in the environment.
- [ ] 9.3 Re-read the final diff adversarially; confirm no reader bypasses
      the reader component, no writer bypasses the control plane except the
      tolerated admin path (spec C.7), and no ownership decision lives
      outside the policy.
