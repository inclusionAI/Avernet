# Tasks — MCP Device-Sync and Passport Regressions

Groups are ordered so the suite stays green after each one, and so the
highest-severity fix (Group 1) is independently shippable. Group 2 threads
the scope with no behaviour change; Group 3 is the one group that changes
device delivery.

Paths are relative to `src/backend/src/agentclaw/community/` unless noted.

## Group 1 — Passport identity mode (problem 1)

- [x] 1.1 Inject `CallerIdentityRepositoryProtocol`
      (`core/repository/protocols/identity.py:58`) into `BotRuntimeProjector.__init__`
      (`core/skill_center/services/bot_runtime_projector.py:58`).
      **No binding added to `di/modules/skill_center_module.py`**, contrary to
      this task as first written: `CallerIdentityModule` already binds the
      protocol in the same container (`di/container.py:134`), and
      `SkillCenterModule` is never assembled standalone, so re-binding it
      would only create a second binding for one key — which injector
      resolves by last-module-wins, silently. Also declared the new import in
      the module's Context Boundary (`core/skill_center/README.md`), without
      which the Rule-22 architecture test fails backend CI.
- [x] 1.2 Resolve MCP execution identity for the Passport scope. Shipped in
      two pieces rather than the single method this task described:
      `_resolve_mcp_identity_modes(*, bot, bot_id, engine)` reads
      `list_draft_call_types(int(bot["id"]), engine)` during **plan
      resolution**, and `_passport_mcp_items(*, identity_modes, bot_id,
      engine, codes)` colours the declared codes with it. The read had to move
      upstream: at the Passport call it would abort after the device
      allow-list was already written, and the compensating projection would
      hit the same failure and be unable to counter-project.
      Normalisation lives in `passport_scope._normalized_identity_mode`, shared
      with `passport_mcp_items_from_entries`, rather than a second copy here.
- [x] 1.3 Thread `bot` into `_apply_non_skill_projection` — `_resolve_plan`
      already returns it, and all three entry points (`project`,
      `project_mcp_and_cli`, `project_for_cleanup`) hold it.
- [x] 1.4 Add `"mcp_items"` to the `resource_scope` at
      `bot_runtime_projector.py:374`, built from the same
      `filter_passport_mcp_codes(...)` list already passed as `mcp_codes`.
- [x] 1.5 Tests: a `caller`-configured MCP round-trips as `caller`; an MCP
      with no call-config row resolves to `owner`; a Bot whose call-config
      lookup returns empty still sends one item per projected code.
- [x] 1.6 Verify `tests/community/core/mcp/services/test_sync_service.py`
      resource-scope assertions (`:158`, `:204`, `:319`, `:350`, `:393`,
      `:431`, `:554`) still pass untouched — they cover
      `MCPSyncService.refresh_mcp_scope`, which this group does not modify.

### Deferred out of Group 1 — two more callers with the same defect

Code review found the identity-less `resource_scope` is not unique to the
projector. Both are live today and neither is covered by any group below:

- `adapters/http/skill_center/skillsets.py:2019`
  (`remove_cli_from_default_skill_set`) — removing a CLI demotes every Caller
  MCP on the Bot. This is a **skill-set mutation**, so spec criterion 1 is
  not fully met until it is fixed.
- `core/bot_management/engines/aicoding/strategy.py:657` — a restart with
  `confirmed_template_update` does the same. Outside criterion 1's wording,
  same defect.

Patching them one at a time leaves the trap armed for the next caller. The
structural fix is `unpack_resource_scope` (`plugin_api/passport.py:106`):
stop it synthesising identity-less items and reject an MCP-bearing scope
that omits `mcp_items`, which makes all three call sites correct at once and
turns a future omission into a hard failure instead of a silent privilege
change. That touches a shared seam and breaks two tests pinning the current
shapes (`contracts/gateway/test_rule15_skillsets.py:206`,
`core/bot_management/services/test_restart_authorization_refresh.py:160`),
so it is a scope call for the author rather than something to fold in
silently. **Raised in the final report; not actioned.**

## Group 2 — Thread `ProjectionScope`, defaulting to everything (no behaviour change)

Every step is behaviour-preserving: the scope is declared and carried, but
every caller still passes `ProjectionScope.everything()`. The suite must be
green at the end of this group *without* any test expectation changing.

- [x] 2.1 Add `ProjectionScope` (frozen dataclass: `skills`, `mcp`,
      `claimed_mcp`, `released_mcp`, `reconcile`, plus `everything()`) in
      `core/skill_center/services/_mutation_flow.py`. `reconcile` is an
      explicit flag, not an equality check against `everything()` — a
      mutation that declares both halves must not be mistaken for a
      reconcile.
- [x] 2.2 Add `scope: ProjectionScope = ProjectionScope.everything()` to
      `project`, `project_mcp_and_cli`, `project_for_cleanup` and
      `_apply_non_skill_projection`; declare it on
      `core/skill_center/runtime_projection_contract.py` and
      `api/bot_runtime_projector.py`. Nothing reads it yet.
- [x] 2.3 `MutationProjectionFlow.apply` / `_mutate` accept and forward the
      scope on the forward projection, and the inverted scope
      (`claimed_mcp` ↔ `released_mcp`) on the compensating one, mirroring how
      `retired_logical_skill_mappings` already swaps its arguments at
      `_mutation_flow.py:119`.
- [ ] 2.4 Confirm the non-flow entry points — `SkillSymlinkListener`
      (`di/modules/skill_center_module.py:914`) and
      `LocalSkillUploadService._sync_runtime` (`:533`) — reach the
      `everything()` default.

## Group 3 — Scoped delivery: removal and fan-out (problems 2 and 3)

- [ ] 3.1 Return the Set's MCP codes on the activate/deactivate mutation:
      `set_skill_set_active` already computes `mcp_codes` under the row lock
      (`core/repository/implementations/skill_center/capability_desired_state.py:493`),
      so put them on `DesiredStateMutation.details` rather than re-querying
      unlocked in the service.
- [ ] 3.2 Declare the real scope on each of the seven commands per the plan's
      table (`skill_set_management_service.py:286`–`:584`). `add_mcp` /
      `remove_mcp` know their `server_code` directly; `activate` /
      `deactivate` read theirs from `details` after `mutation()` returns;
      `add_skill` / `remove_skill` add the skill's `mcp_dependencies` when it
      has any.
- [ ] 3.3 Add `SkillSetService.sync_mcp_delivery(*, claimed, released)`:
      fetch catalogue details for `claimed` only, push via
      `sync_mcp_details_for_bot`, then `remove_mcp_detail` each `released`
      code. Skip the push entirely when `claimed` is empty.
- [ ] 3.4 Reduce `SkillSetService.sync_mcp_desired_state` to declaration
      only — delete the detail loop and the `sync_mcp_details_for_bot` call.
      `sync_all_mcp_servers` takes dicts and reads `server_code`/`serverCode`
      off each (`core/devices/services/mcp_device_transport.py:76`), so pass
      `[{"server_code": c} for c in sorted(server_codes)]`, not bare strings.
- [ ] 3.5 `_apply_non_skill_projection`: guard the declared scope against the
      projected set — `claimed = scope.claimed_mcp & codes`,
      `released = scope.released_mcp - codes` — then call `sync_mcp_delivery`
      **before** `sync_mcp_desired_state`, so configuration lands before the
      allow-list cites it and withdrawal precedes the allow-list dropping it.
      A `reconcile` scope means `claimed = codes`, `released = ∅`. The
      intersection is a guard, never a source: it cannot enlarge what the
      mutation declared, so `add_mcp`'s one code stays one code.
- [ ] 3.6 Tests — fan-out: adding one MCP to a Bot with three others pushes
      exactly one detail (assert the `sync_single_mcp` call count is 1, not
      just that the right code appears) and declares four allow-list codes; a
      Bot holding a catalogue-missing MCP can still add an unrelated one.
- [ ] 3.7 Tests — removal guard: removing an MCP calls `remove_mcp_detail`
      exactly once; a platform/template-default MCP is **not** removed, nor
      is one a Skill still lists in `mcp_dependencies` — both stay in `codes`
      without Set membership, so `- codes` spares them. (The cross-Set case
      needs no test: R3 makes it unreachable,
      `policies/capability_ownership.py:9`.) Assert directly — a future
      `_resolve_plan` change must not silently start deleting device config.
- [ ] 3.8 Test — compensation inverts the scope: a projection failure after a
      successful add removes what it pushed.
- [ ] 3.9 Test — a `reconcile` scope pushes every projected code, so the
      device-activated reconcile path is unchanged.

## Group 4 — Delivery shape decided per provider (problem 4)

- [ ] 4.1 Define `RuntimeProjectionIntent` (symlinks, MCP claimed/released,
      full allow-list, the `ProjectionScope`) and add
      `DeviceSync.apply_runtime_projection(intent)` to
      `core/devices/services/device_sync.py`, with a **default implementation
      reproducing today's per-call sequence** so no impl breaks and teclaw
      stays correct until its own override lands.
- [ ] 4.2 `BaasDeviceSyncService.apply_runtime_projection`: symlinks only when
      `intent.scope.skills`; MCP config + `filter-servers` only when
      `intent.scope.mcp`.
- [ ] 4.3 `SingleboxDeviceSyncService`: delegate, preserving its
      `sync_all_mcp_servers` no-op (`singlebox_device_sync.py:47`).
- [ ] 4.4 `BotRuntimeProjector` hands the intent to
      `DeviceSyncDispatcher.dispatch(ctx)` instead of orchestrating the device
      calls itself. It keeps plan resolution, the Passport payload (AgentPass
      is not the device, and every provider updates it identically), and
      `SkillSetRuntimeReconcileError`. No branch on provider or engine type in
      shared code.
- [ ] 4.5 Keep `project_mcp_and_cli` as a thin alias for
      `project(scope=ProjectionScope(mcp=True))` so `skill_center_module.py:918`
      and `SkillSymlinkListener` need no edit.
- [ ] 4.6 Tests: on the baas impl an MCP-only scope performs no
      `sync_symlinks`, and a skills-only scope performs no
      `sync_all_mcp_servers` and no per-MCP write; a fake whole-artifact
      `DeviceSync` receives exactly one `apply_runtime_projection` call for a
      both-halves scope.
- [ ] 4.7 Flag for the corp side: teclaw needs its own
      `apply_runtime_projection` composing and delivering once, or it keeps
      the default (correct, not yet cheaper). Out of this repository.

## Group 5 — Dead code (problem 5)

- [ ] 5.1 Delete `SkillSetService.refresh_mcp_scope`
      (`core/skill_center/services/skill_set_service.py:1914-1950`) and any
      now-unused imports.
- [ ] 5.2 Confirm `MCPSyncService.refresh_mcp_scope` still has exactly one
      production caller, `DeviceService._sync_mcps_when_device_active`
      (`core/devices/services/device_service.py:1518`), and that neither is
      modified.

## Group 6 — Documentation and logging

Interleave with the groups above rather than deferring — a docstring written
after the fact records what the code does, not why it had to. Landing them
per group is the intent; this group is the checklist that nothing was missed.

- [ ] 6.1 Docstrings per the plan's Documentation section: `ProjectionScope`
      (declared not derived; the guard only shrinks), the
      `sync_mcp_desired_state` / `sync_mcp_delivery` declare-vs-deliver split
      at both sites, the `- codes` guard's real justification, and
      `apply_runtime_projection` (call count is the impl's decision).
- [ ] 6.2 Comment at the `mcp_items` call site naming the overwrite-style
      contract and the `"owner"` default that made the omission destructive,
      so it is not "simplified" back out.
- [ ] 6.3 Logging per the plan's table: identity-mode counts, guard trims
      (only when something was trimmed), pushes at INFO, **removals at
      WARNING**, per-provider device-call counts.
- [ ] 6.4 **Audit every new log line for secrets.** MCP entries from MCP
      Center and `build_mcp_sync_payload` carry `api_key` and custom headers;
      log `server_code` and counts only, never the entry or payload. Grep the
      diff for logged variables that could hold one before pushing.
- [ ] 6.5 Failure paths keep `exc_info=True` and name `bot_id` +
      `server_code`, so a partial delivery is diagnosable from one line.

## Verification

- [ ] V1 Backend test suite green.
- [ ] V2 Lint / typecheck per the repo's contributor commands.
- [ ] V3 Re-read the spec's six success criteria against the diff.
