# Tasks — Installation as the Single Source of Truth

Groups are ordered so the suite stays green after each group. Every deletion
lands in the same group as the migration of its last caller.

## Group 1 — Renames (zero behavior change)

- [x] 1.1 `SkillSetControlPlaneService` → `SkillSetManagementService`:
      `core/skill_center/services/skill_set_control_plane.py` →
      `skill_set_management_service.py`; `api/skill_set_control_plane.py` →
      `api/skill_set_management_service.py` (protocol renamed); DI bindings,
      routers' `Injected(...)` types, tests. Error class names unchanged.
- [x] 1.2 `SkillSetControlPlaneRepository` → `CapabilityDesiredStateRepository`:
      implementation file, `core/repository/protocols/skill_set_control_plane.py`
      → `capability_desired_state.py`, `skill_set_control_plane_types.py` →
      `capability_desired_state_types.py`; all importers.
- [x] 1.2b Method/type renames per the plan's method-rename table (zero
      behavior): `SkillSetDesiredState` → `CapabilityDesiredState`,
      `SkillSetMutation` → `DesiredStateMutation`;
      `repair_bot_skillset_installations` → `flush_installations`;
      `set_active` → `set_skill_set_active`; `activate_mcp_direct` /
      `deactivate_mcp_direct` → `install_mcp` / `uninstall_mcp`; service
      `sync` → `legacy_activate`, `resources` → `list_resources`,
      `mcp_permissions` → `list_mcp_permissions`. Update protocols, DI,
      adapters, tests.
- [x] 1.3 `BotRuntimeProjectionReconciler` → `BotRuntimeProjector`:
      `bot_runtime_projection_reconciler.py` → `bot_runtime_projector.py`;
      methods `reconcile` → `project`, `reconcile_non_skill_projection` →
      `project_mcp_and_cli`, `reconcile_cleanup` → `project_for_cleanup`; both
      protocols (`api/` + `runtime_projection_contract.py`); delete the
      `SkillSetRuntimeReconciler` alias; DI, callers, tests
      (`tests/community/contracts/test_bot_runtime_projection_reconciler.py`
      → `test_bot_runtime_projector.py`).
- [x] 1.4 Full suite green before any behavior change — validated via the
      rename blast-radius set locally (1047 passed) and the CI Backend unit
      tests job on the pushed head (the sandbox is too slow for repeated
      full-suite runs; CI is the full-suite gate per push).

## Group 2 — One flush: MCPs + new exclusion semantics, retire the materializer

- [x] 2.1 Reshape the bridge type into `InstallationFlushPlan`
      (`member_skill_ids`, `skills_to_install`, `skills_to_uninstall`,
      `mcps_to_install`, `mcps_to_uninstall`) per plan component 1.
- [x] 2.2 `_resolve_bridge` → `_resolve_flush_plan`: collect MCP members per
      Set; **excluded Default-Set members (skills and MCPs) become inactive
      claims** and lose their Installation rows (spec Key domain rules). R3
      keeps a capability in one Set; on malformed two-Set data the plan errs
      safe (keeps a row an active Set accounts for).
- [x] 2.3 Extend `flush_installations`: fast path checks skill and MCP
      deltas; write path applies both (SAVEPOINT-per-row inserts, delete
      `*_to_uninstall ∩ installed`).
- [x] 2.4 Repository tests: MCP rows follow Set activation; excluded member
      rows removed (skill + MCP); malformed two-Set data errs safe (a row an
      active Set accounts for is kept); direct rows untouched; idempotent.
      Update the 2026-08-23 pinning test that asserted excluded rows are
      "left alone" (superseded — cite the spec).
- [x] 2.5 Swap the two materializer call sites to `flush_installations`
      (`bot_runtime_projector._resolve_plan`, Service-Bot
      `build_stage.py`); delete the materializer +
      `ensure_active_skillset_installations` (impl, protocol, DI, tests);
      update `core/service_bot/README.md`.

## Group 3 — The reader

- [x] 3.1 Add `api/bot_capability_state_reader.py`
      (`BotCapabilityStateReaderProtocol`: `flush`, `active_skill_assets`,
      `active_mcp_server_codes`) and
      `core/skill_center/services/bot_capability_state_reader.py`; DI
      bindings.
- [x] 3.2 New `tests/community/core/skill_center/
      test_bot_capability_state_reader.py`: flush-then-read for skills and
      MCPs, missing-Bot error, `bot` passed vs looked up.
- [x] 3.3 Repurpose `SkillRepository.list_bot_active_assets` → pure
      Installation join renamed `list_bot_installed_assets` (drop merge and
      the `engine` read parameter; dedup by id); update
      `core/repository/protocols/skills_pool.py`.
- [x] 3.4 Migrate merge-readers to `reader.active_skill_assets`: the
      projector (snapshot + plan; drop the Group-2 direct repair call), the
      name-conflict guard in the state service,
      `skills_pool/mapping_convergence.py`, `recovery_service.py`,
      `reconcile_service.py`, `active_aicoding_bridge_repair.py`, Service-Bot
      `build_stage.py` (flush via reader); wiring updates.
- [x] 3.5 Guard: no caller of `list_bot_installed_assets` outside the
      reader; no reference to `list_bot_active_assets` remains.
- [x] 3.6 Projector test pinning `bot_default_engine_types` Default-Set
      precedence; run the projector contract + skills_pool tests.

## Group 4 — Symlink/compose projection converges

- [x] 4.1 Inject the reader into `SkillSetService` (factory); replace the
      `get_active_skills` merge body with delegation (keys preserved: `id`,
      `name`, `git_path`, `skill_uuid`, `sc_version_number`).
- [x] 4.2 Update `test_skill_set_service_symlink_mappings.py`; add the
      exclusion case (excluded member no longer symlinked).
- [x] 4.3 Config-compose collector test pinning the skill dict contract.

## Group 5 — MCP union in one place

- [x] 5.1 Reimplement `collect_bot_active_mcps` per plan component 8
      (default policy ∪ installed; metadata from membership rows; stops
      iterating active ordinary Sets). `collect_bot_mcps` unchanged.
- [x] 5.2 Union tests: ordinary-set MCP appears via Installation; excluded
      default MCP absent; direct-installed MCP appears with minimal
      metadata.
- [x] 5.3 Projector contract test: `installed ∪ effective_default` inputs
      consistent.

## Group 6 — CapabilityOwnershipPolicy

- [x] 6.1 Add `core/skill_center/policies/capability_ownership.py` (R1–R3
      docstring; `is_set_managed` — **no exclusion carve-out** — over the
      private `_set_belongs_to_bot`, moved from
      `local_skill_state_service.skill_set_reaches_bot`;
      `require_can_join_set` raising with R2-before-R3 precedence).
- [x] 6.2 Collapse `_reject_skill_set_member` /
      `_require_no_normal_skill_set_membership` / `_set_governs` into thin
      wrappers over `is_set_managed` (each asset kind keeps its legacy error
      type); add the behavior-change test: an excluded Default-Set member is
      refused direct activate/deactivate.
- [x] 6.3 Route `add_skill` / `add_mcp` conflict checks through
      `require_can_join_set`; R3 now covers ANY Set — add the test: a
      Default-Set member (excluded or not) is refused when added to an
      ordinary Set; R2-before-R3 precedence pinned by a test.
- [x] 6.4 Policy unit tests
      (`tests/community/core/skill_center/test_capability_ownership.py`).

## Group 7 — DirectActivationService (one direct write path, skills ≡ MCPs)

- [x] 7.1 Extract per-table command modules
      `core/repository/implementations/skill_center/tables/
      {skill_installations,mcp_installations,default_exclusions}.py`
      (session-in functions); the UoW and the flush use them — each table's
      SQL now has one owner.
- [x] 7.2 Add UoW commands `install_skill` / `uninstall_skill` (mirror of
      the renamed `install_mcp` / `uninstall_mcp`: snapshot, R1 facts read
      under the transaction, write, `DesiredStateMutation` return).
- [x] 7.3 Extract the shared mutate-reconcile-compensate helper from
      `SkillSetManagementService._mutate/_reconcile` (module-private, used by
      both command services).
- [x] 7.4 Create `core/skill_center/services/direct_activation_service.py`
      (`DirectActivationService.activate_skill` / `deactivate_skill` /
      `activate_mcp` / `deactivate_mcp` per plan component 6) +
      `api/direct_activation_service.py`; MCP direct commands move here from
      the Set service; OpenAPI MCP router re-injects; `list_installed_mcps`
      reads move to the reader.
- [x] 7.5 Delete `LocalSkillStateService` (+ `api/local_skill_state_service.py`)
      and `SkillInstallationRepository` (+ protocol) once their last callers
      are migrated; port their tests to the new service (including MCP↔skill
      parity cases for direct activate/deactivate).
- [ ] 7.6 Guard: `ac_bot_skill_installation` / `ac_bot_mcp_installation` are
      written only from `tables/` modules, reached only via the UoW.

## Group 8 — Default-Set exclusion commands (restored opt-out)

- [ ] 8.1 UoW commands `exclude_default_skill` / `unexclude_default_skill` /
      `exclude_default_mcp` / `unexclude_default_mcp`: exclusion row +
      Installation delta in one transaction; `changed=False` when already in
      the requested state.
- [ ] 8.2 `SkillSetManagementService`: `remove_skill`/`remove_mcp` on the
      Default Set performs the exclusion (instead of raising);
      `add_skill`/`add_mcp` on the Default Set removes an existing exclusion,
      else `SYSTEM_DEFAULT_IMMUTABLE`. Runtime reconciled like every
      mutation.
- [ ] 8.3 Tests: exclude → inactive everywhere (listing, reader, runtime
      snapshot); un-exclude → active again; MCP cases mirror skill cases;
      adapter tests for the restored wire semantics on both HTTP surfaces.

## Group 9 — Retire the legacy activation writers

- [ ] 9.1 UoW `deactivate_all_sets` + `SkillSetManagementService.deactivate_all`;
      route `/api/skills/deactivate-all` through it (response shape kept);
      update `tests/community/_flows/skill_center/api_lifecycle.py`.
- [ ] 9.2 Reimplement deprecated `/api/skills/skillset/current` from
      `list_sets` (first ordinary active Set, else `None`).
- [ ] 9.3 Delete the dead data-init activation step mechanically
      (`_activate_and_sync_skill_sets` call site + method + activator ctor
      param + DI arg in `bot_management_module.py`) — no migration, dead
      feature.
- [ ] 9.4 Delete `SkillSetActivator`, `SkillSetSwitcher`, factories,
      `_DeviceSyncMixin`, result dataclasses (if unreferenced),
      `api/skill_set_{activator,switcher}_factory.py`, DI providers, and
      `CURRENT_SET_FILE` plumbing only they used; migrate or delete their
      tests.
- [ ] 9.5 Guard: no reference to Activator/Switcher anywhere.

## Group 10 — SkillQueryService (one query seam, one fewer layer)

- [ ] 10.1 Merge `LocalSkillQueryService` + `BotSkillAssetService` reads into
      `core/skill_center/services/skill_query_service.py` +
      `api/skill_query_service.py` (listing, detail, content, parameters
      incl. `replace_parameters` delegation, legacy reference resolution);
      detail and listing use `reader.flush`.
- [ ] 10.2 Re-point callers: OpenAPI skills router (reads →
      `SkillQueryService`; activate/deactivate → `DirectActivationService`),
      internal legacy activate/deactivate routes, `deprecated/skills.py`,
      `service_publication_facade.py`.
- [ ] 10.3 Delete `BotSkillAssetService` + `api/bot_skill_asset_service.py`
      + `LocalSkillQueryService` + `api/local_skill_query_service.py`; port
      tests; add the case: bridged skill shows `active=true` in detail
      before any listing ran.

## Group 11 — Dead code sweep and docs

- [ ] 11.1 Delete `core/skill_center/installation_compatibility.py`.
- [ ] 11.2 Delete `_ensure_default_set` / `_ensure_default_set_membership`
      from `local_skill_upload_service.py` (+ tests).
- [ ] 11.3 Verify zero callers by search, then delete legacy
      `SkillSetService` methods: `add_skills_to_set`,
      `remove_skill_from_set`, `add_mcp_to_skill_set`,
      `remove_mcp_from_skill_set` (keep any still reached; record in PR).
- [ ] 11.4 Update `core/skill_center/README.md` context boundary + narrative
      (one writer, one flush, one reader, one rule book; new names).
- [ ] 11.5 Update `adapters/http/skill_center/CLAUDE.md` key-file index.

## Group 12 — Full validation

- [ ] 12.1 `uv run pytest tests/community`.
- [ ] 12.2 SAST/lint gate (`scripts/ci/python_sast_local.sh`); report
      anything not runnable in the environment.
- [ ] 12.3 Adversarial re-read of the final diff: no reader bypasses the
      reader; no writer bypasses the UoW except the documented dead admin
      endpoint (spec C.7); no ownership decision outside the policy; every
      MCP operation's test has a skill twin.
