# Tasks — MCP Device-Sync and Passport Regressions

Groups are ordered so the suite stays green after each one, and so the
highest-severity fix (Group 1) is independently shippable. Group 3 is the
only group that changes device delivery behaviour; Group 2 exists to land its
plumbing separately and de-risk it.

Paths are relative to `src/backend/src/agentclaw/community/` unless noted.

## Group 1 — Passport identity mode (problem 1)

- [ ] 1.1 Inject `CallerIdentityRepositoryProtocol`
      (`core/repository/protocols/identity.py:58`) into `BotRuntimeProjector.__init__`
      (`core/skill_center/services/bot_runtime_projector.py:57`); bind it in
      `di/modules/skill_center_module.py`.
- [ ] 1.2 Add `BotRuntimeProjector._passport_mcp_items(*, bot, engine, codes)`:
      read `list_draft_call_types(int(bot["id"]), engine)`, emit
      `{"mcp_code": code, "identity_mode": mode}` per code, defaulting to
      `"owner"` when the Bot has no row for that code. `McpCallType` is a
      `StrEnum` (`core/caller_identity/models.py:16`), so normalise with
      `str(...)` rather than assuming a `.value` attribute exists.
- [ ] 1.3 Thread `bot` into `_apply_non_skill_projection` — `_resolve_plan`
      already returns it, and all three entry points (`project`,
      `project_mcp_and_cli`, `project_for_cleanup`) hold it.
- [ ] 1.4 Add `"mcp_items"` to the `resource_scope` at
      `bot_runtime_projector.py:369`, built from the same
      `filter_passport_mcp_codes(...)` list already passed as `mcp_codes`.
- [ ] 1.5 Tests: a `caller`-configured MCP round-trips as `caller`; an MCP
      with no call-config row resolves to `owner`; a Bot whose call-config
      lookup returns empty still sends one item per projected code.
- [ ] 1.6 Verify `tests/community/core/mcp/services/test_sync_service.py`
      resource-scope assertions (`:158`, `:204`, `:319`, `:350`, `:393`,
      `:431`, `:554`) still pass untouched — they cover
      `MCPSyncService.refresh_mcp_scope`, which this group does not modify.

## Group 2 — Delta plumbing, delivery still total (no behaviour change)

Every step here is behaviour-preserving: the delta is computed and threaded,
but delivery still pushes the full set. The suite must be green at the end of
this group *without* any test expectation changing.

- [ ] 2.1 Extract the MCP-set union out of
      `BotRuntimeProjector._resolve_plan` into one helper so the snapshot and
      the projection cannot drift (plan risk 2). Both callers use it.
- [ ] 2.2 Add `BotRuntimeProjector.snapshot_mcp_codes(*, bot_id, owner_id)
      -> frozenset[str]` using that helper; declare it on
      `core/skill_center/runtime_projection_contract.py` and
      `api/bot_runtime_projector.py`.
- [ ] 2.3 Add `previous_mcp_codes: frozenset[str] = frozenset()` to `project`,
      `project_mcp_and_cli`, `project_for_cleanup` and
      `_apply_non_skill_projection`; thread it through. Nothing reads it yet.
- [ ] 2.4 `MutationProjectionFlow.apply`: take the pre-mutation snapshot
      beside the existing `snapshot_skill_mappings` call and pass it to
      `_project_or_compensate`; the compensating projection passes the
      post-mutation set as its baseline so the delta inverts, mirroring
      `retired_logical_skill_mappings`.
- [ ] 2.5 Confirm the non-flow entry points — `SkillSymlinkListener`
      (`di/modules/skill_center_module.py:914`) and
      `LocalSkillUploadService._sync_runtime` (`:533`) — reach the default
      `frozenset()`, which will mean "everything is newly claimed" in Group 3.

## Group 3 — Delta delivery: removal and fan-out (problems 2 and 3)

- [ ] 3.1 Add `SkillSetService.sync_mcp_delivery(*, claimed, released)`:
      fetch catalogue details for `claimed` only, push via
      `sync_mcp_details_for_bot`, then `remove_mcp_detail` each `released`
      code. Skip the push entirely when `claimed` is empty.
- [ ] 3.2 Reduce `SkillSetService.sync_mcp_desired_state` to declaration
      only — delete the detail loop and the `sync_mcp_details_for_bot` call.
      `sync_all_mcp_servers` takes dicts and reads `server_code`/`serverCode`
      off each (`core/devices/services/mcp_device_transport.py:76`), so pass
      `[{"server_code": c} for c in sorted(server_codes)]`, not bare strings.
- [ ] 3.3 `_apply_non_skill_projection`: call `sync_mcp_delivery` with
      `claimed = codes - previous_mcp_codes` and
      `released = previous_mcp_codes - codes` **before**
      `sync_mcp_desired_state`, so configuration lands before the allow-list
      cites it and withdrawal precedes the allow-list dropping it.
- [ ] 3.4 Tests — fan-out: adding one MCP to a Bot with three others pushes
      exactly one detail and declares four allow-list codes; a Bot holding a
      catalogue-missing MCP can still add an unrelated one (the old
      `if not detail: return False` no longer sees unrelated codes).
- [ ] 3.5 Tests — removal: removing an MCP calls `remove_mcp_detail` once; an
      MCP still claimed by another active Set is **not** removed; a
      platform/template-default MCP is **not** removed. These two guards are
      structural (such codes never leave the post set) — assert them so a
      future `_resolve_plan` change cannot silently start deleting device
      config.
- [ ] 3.6 Test — compensation inverts the delta: a projection failure after a
      successful add removes what it pushed.
- [ ] 3.7 Test — `project` with `previous_mcp_codes=frozenset()` pushes every
      projected code, so the device-activated reconcile path is unchanged.

## Group 4 — Projection facets (problem 4)

- [ ] 4.1 Add `ProjectionFacet` (`SKILLS` / `MCP_AND_CLI` / `ALL`) in
      `core/skill_center/services/_mutation_flow.py`; default `ALL`
      everywhere so unenumerated callers keep today's behaviour.
- [ ] 4.2 `BotRuntimeProjector.project` dispatches on the facet; keep
      `project_mcp_and_cli` as a thin alias for `project(facet=MCP_AND_CLI)`
      so `skill_center_module.py:918` and `SkillSymlinkListener` need no edit.
- [ ] 4.3 `MutationProjectionFlow.apply` / `_mutate` accept and forward the
      facet, on both the forward and the compensating projection.
- [ ] 4.4 Declare the facet on each of the seven commands per the plan's
      table. `add_skill` / `remove_skill` resolve to `ALL` when the skill
      carries `mcp_dependencies`, `SKILLS` otherwise.
- [ ] 4.5 Tests: an MCP-only mutation performs no `sync_symlinks`; a
      skill-only mutation with no `mcp_dependencies` performs no
      `sync_all_mcp_servers` and no `update_passport`; a skill *with*
      dependencies still projects both.

## Group 5 — Dead code (problem 5)

- [ ] 5.1 Delete `SkillSetService.refresh_mcp_scope`
      (`core/skill_center/services/skill_set_service.py:1914-1951`) and any
      now-unused imports.
- [ ] 5.2 Confirm `MCPSyncService.refresh_mcp_scope` and its caller
      `DeviceService._sync_mcps_when_device_active`
      (`core/devices/services/device_service.py:1494`) are untouched.

## Verification

- [ ] V1 Backend test suite green.
- [ ] V2 Lint / typecheck per the repo's contributor commands.
- [ ] V3 Re-read the spec's six success criteria against the diff.
