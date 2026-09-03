# MCP Device-Sync and Passport Regressions in the Skill-Set Control Plane

## Summary

The Installation-as-single-source-of-truth refactor (#1396, merged
`9c558e9af`, 2026-08-24) replaced the per-operation MCP sync methods on
`SkillSetService` with a single desired-state projection driven by
`BotRuntimeProjector`. The projection is correct as a *description* of the
Bot's target state, but it lost four behaviours the per-operation methods
had, and gained one it should not have:

1. **The Passport now asserts `identityMode: "owner"` for every MCP on every
   skill-set mutation**, silently discarding per-MCP caller-identity
   configuration. This is a privilege-boundary change, not a cosmetic one.
2. **MCP configuration is never removed from the device.** Removing an MCP
   from a Set drops it from the engine allow-list but leaves its full config
   — endpoint, `api_key`, custom headers — registered on the container
   permanently.
3. **Every mutation re-pushes the full MCP configuration for the entire
   projected set**, where the old code pushed exactly the one MCP that
   changed. For a Bot with N MCPs, adding one costs N catalogue lookups and
   `1 + 2(N-1)` device HTTP calls instead of 1.
4. **Every mutation runs both halves of the projection** — skills *and*
   MCP/CLI — and the compensation path runs the whole thing a second time. An
   MCP add resyncs all symlinks; a skill add re-declares the MCP allow-list.
5. `SkillSetService.refresh_mcp_scope` has no callers and is dead.

All five live in one call path and are fixed together. Problems 1–3 are
behaviour regressions against `REL20260820`; 4 is a cost regression; 5 is
cleanup.

## Background: the two downstreams

Two independent systems consume a Bot's MCP state, and conflating them is the
root of problems 1 and 2.

**The device** (the Bot's container) is reached through `DeviceSync`:

- `sync_single_mcp` → `POST /api/mcp` (or `PUT /api/mcp/{code}` on 409) —
  writes one MCP's *full configuration*: endpoint, `api_key`, custom headers,
  transport protocol.
- `sync_all_mcp_servers` → `POST /api/mcp/filter-servers` with
  `{"server_codes": [...]}` — declares which MCPs the engine may use. Codes
  only, no configuration.
- `sync_remove_mcp` → `DELETE /api/mcp/{code}` — removes one MCP's
  configuration.

**The authorization service** (AgentPass / tcauthmng) is reached through
`PassportPlugin.update_passport`. It holds the Bot's permission manifest,
including each MCP's `identityMode` — whether the MCP executes under the
*bot owner's* credential or the *invoking caller's*.

`sync_all_mcp_servers` is complete for the device and says nothing to the
Passport. `update_passport` is complete for the Passport and says nothing to
the device. Neither substitutes for the other.

---

## Problem 1 — Skill-set mutations reset every MCP's identity mode to `owner`

**Severity: security-relevant.** This is the highest-priority item.

`identityMode` decides whose credential an MCP call runs under. `caller`
means the invoking user's — that is what
`AgentPassportFacade.issueCallerExecutionCredential` exists to issue. `owner`
means the Bot owner's. Flipping `caller` → `owner` means an MCP that was
scoped to run as the caller now runs with the Bot owner's identity, for every
caller of that Bot.

### What the projector sends

`core/skill_center/services/bot_runtime_projector.py:374`:

```python
self._passport.update_passport(
    bot_id=bot_id,
    user_id=owner_id,
    engine_type=engine,
    resource_scope={
        "mcp_codes": filter_passport_mcp_codes(
            projection.mcp_server_codes
        ),
        "cli_items": effective_cli_items,
    },
)
```

`resource_scope` carries `mcp_codes` and `cli_items`. It does **not** carry
`mcp_items` — the only field that transports `identity_mode`.

### Why the omission is not neutral

`plugin_api/passport.py:58` documents the contract:

```python
class PassportResourceScope(TypedDict, total=False):
    """Complete resourceManifest scope for overwrite-style updatePassport calls.

    AgentPass/tcauthmng treats each resource list in updatePassport as a full
    replacement. Callers that update MCP or CLI scope must pass both lists here
    so one resource type does not accidentally clear the other.
    """

    mcp_codes: list[str]
    mcp_items: list[McpScopeItem]
    cli_items: list[CliItem]
```

`unpack_resource_scope` (`plugin_api/passport.py:106`) substitutes bare items
when `mcp_items` is absent:

```python
if "mcp_items" in resource_scope:
    return resource_scope["mcp_items"], cli_items
try:
    mcp_codes = resource_scope["mcp_codes"]
except KeyError as e:
    raise ValueError(
        "resource_scope must include mcp_codes and cli_items"
    ) from e
return [{"mcp_code": code} for code in mcp_codes], cli_items
```

Those substituted items have no `identity_mode`. The corp plugin then
supplies one — `ProdPassportPlugin._build_mcp_models`:

```python
identity_mode = item.get("identity_mode")
if identity_mode is None:
    identity_mode = "owner"
if identity_mode not in {"owner", "caller"}:
    raise PassportError("invalid MCP identity mode")
mcps.append(McpModel(
    mcp_code,
    mcp_name=item.get("mcp_name"),
    mcp_desc=item.get("mcp_desc"),
    identity_mode=identity_mode,
))
```

`McpModel.serialization` omits `identityMode` only when it is `None` — and
after the default above it never is:

```python
data = {"mcpCode": self.mcp_code}
...
if self.identity_mode is not None:
    data["identityMode"] = self.identity_mode
```

So `identityMode: "owner"` is **explicitly written into the wire payload**,
and `updatePassport` overwrites the list. The plugin states the semantics at
the call site:

```python
# updatePassport is type-scoped and overwrite-style per list:
# omitted list keeps existing scope; provided empty list clears that scope.
```

The design intent is visible in the contrast: `apply_first_agent_passport`
and `apply_agent_passport` build `McpModel(code)` with `identity_mode=None`,
so `identityMode` is omitted at *creation*. The `"owner"` default lives only
in `_build_mcp_models`, used only by the two *update* paths. On update,
identity is required to be explicit. The projector is the one caller that
isn't.

### The collision is direct

`update_mcp_identity_to_agent_principal` — the method that *sets* identity —
uses the same facade and the same field:

```python
request = UpdatePassportRequestDTO(
    bot_id=bot_id,
    owner_workno=user_id,
    mcps=mcps,
)
response = self._get_facade().updatePassport(request)
```

There is no separate identity store. The projector's write clobbers whatever
the caller-identity flow (`core/caller_identity/service.py:150`) set.

### What REL20260820 did instead

Every mutation path routed through `refresh_mcp_scope` → `_update_passport`,
which always sent all three lists, with real identity modes read from
`BotMcpCallConfigModel`:

```python
identity_modes = self.caller_identity_repository.list_draft_call_types(
    int(bot["id"]), str(engine_type)
)
mcp_items = passport_mcp_items_from_entries(synced_mcps, identity_modes=identity_modes)
...
resource_scope={
    "mcp_codes": synced_server_codes,
    "mcp_items": mcp_items,
    "cli_items": cli_items,
},
```

The existing test `tests/community/core/mcp/services/test_sync_service.py:554`
pins that shape:

```python
assert passport_update.update_passport.call_args.kwargs["resource_scope"] == {
    "mcp_codes": [],
    "mcp_items": [],
    "cli_items": [],
}
```

### Constraint on any fix

There is no "leave MCP scope alone" option. `_build_mcp_models` returns
`None` only when `mcp_items is None`, and `unpack_resource_scope` returns
`(None, None)` only when `resource_scope is None` — which the projector
cannot pass, because it must update CLI scope. Passing `resource_scope` at
all guarantees an MCP list goes over the wire. The projector must send
*correct* items; it cannot opt out.

### Acceptance criteria

- Every `update_passport` call from the projector includes `mcp_items` with
  an explicit `identity_mode` per MCP, sourced from the persisted call-config
  table.
- An MCP configured `caller` retains `caller` across every skill-set mutation
  that does not concern it — add/remove skill, add/remove MCP, activate,
  deactivate, Default-Set exclude/unexclude.
- An MCP with no call-config row resolves to `owner` (unchanged default).

---

## Problem 2 — MCP configuration is never removed from the device

Removing an MCP from a Set, or excluding one from the Default Set, drops the
code from the `filter-servers` allow-list but leaves its device registration
intact — including the stored `api_key` and custom headers. Registrations
accumulate for the life of the container.

### Current state

`remove_mcp_detail` exists and is fully implemented
(`core/mcp/services/sync_service.py:374`), but has **zero production
callers**. Only the definition, the protocol declarations, and tests
reference it. The projector never removes:

```python
async def _apply_non_skill_projection(...) -> None:
    if not await service.sync_mcp_desired_state(
        server_codes=set(projection.mcp_server_codes)
    ):
        raise SkillSetRuntimeReconcileError()
    ...
```

`sync_mcp_desired_state` pushes details and declares the allow-list. Nothing
in the path calls `sync_remove_mcp`.

Caller-parity against REL20260820:

| method | REL callers | dev callers |
| --- | --- | --- |
| `remove_mcp_detail` | 2 | **0** |

### What REL20260820 did

`remove_mcp_from_skill_set` removed from the device at both sites — the
Default-Set exclusion branch and the ordinary-Set branch — each guarded by a
"still in use?" check and rolled back on failure:

```python
should_remove = True
try:
    for ss in self.list_skill_sets(user_id=user_id, bolt_id=self.bot_id):
        if str(ss.get('id')) == skill_set_id:
            continue
        mcps_in_set = self.get_set_mcp_servers(...)
        if server_code in {m.get('server_code') for m in mcps_in_set if m.get('server_code')}:
            should_remove = False
            break
    default_server_codes = {
        c.get("server_code")
        for c in get_default_mcp_servers(self.engine_type, effective_template_type, ext_info=effective_ext_info)
    }
    if should_remove and server_code in default_server_codes:
        should_remove = False
except Exception as e:
    should_remove = False
    logger.warning(f"[remove_mcp_from_skill_set] Check usage failed: {e}, skip device removal")

if should_remove:
    remove_result = await self._mcp_sync_service.remove_mcp_detail(
        server_code=server_code,
        bot_id=self.bot_id,
        user_id=self.entity_id,
    )
    if not remove_result.get("success"):
        ...  # roll the DB row back, fail the operation
```

Three guards worth preserving: skip removal if another Set still holds the
MCP; skip if it is a platform/template default; and fail *closed* (skip
removal) if the usage check itself errors.

### Acceptance criteria

- Removing an MCP from an ordinary Set, or excluding one from the Default
  Set, removes its configuration from the device when no other active claim
  holds it.
- An MCP still claimed by another active Set, or by the engine/template
  default policy, is not removed.
- A failed usage check does not remove.
- A failed device removal fails the operation and leaves desired state
  unchanged.

---

## Problem 3 — Every mutation re-pushes configuration for every MCP

Adding one MCP re-pushes the full configuration of every MCP the Bot has.

### Current state

`core/skill_center/services/skill_set_service.py:518`:

```python
async def sync_mcp_desired_state(self, *, server_codes: set[str]) -> bool:
    try:
        entries: list[dict[str, Any]] = []
        for server_code in sorted(server_codes):
            detail = self.mcp_center.get_mcp_detail(server_code)
            if not detail:
                return False
            entries.append(detail)
        delivery = await self._mcp_sync_service.sync_mcp_details_for_bot(
            user_id=self.user_id or self.entity_id or "",
            mcp_entries=entries,
            ...
        )
```

`server_codes` is `projection.mcp_server_codes` — not a delta.
`RuntimeProjectionResolver.resolve` (`core/skill_center/runtime_resolver.py:80`)
builds it as the union of everything:

```python
mcp_codes = set(state.installed_mcp_server_codes)
mcp_codes.update(state.system_default_mcp_server_codes)
for asset in assets:
    for dependency in asset.mcp_dependencies:
        ...
```

and `installed_mcp_server_codes` comes from `list_installed_mcps(bot_id,
owner_id)` — scoped to the whole Bot, not the Set being edited.

`sync_mcp_details_for_bot` then fans out one `sync_single_mcp` per entry. For
an MCP already registered on the device, `push_single_mcp` costs two round
trips, not one:

```python
try:
    _create_mcp(transport, config)          # POST /api/mcp → 409
except Exception as e:
    if is_already_exists_error(e):
        _update_mcp(transport, config)      # PUT /api/mcp/{code}
```

Cost of adding one MCP to a Bot with N active MCPs:

| | catalogue lookups | device MCP writes |
| --- | --- | --- |
| REL20260820 | 1 | 1 × `POST` (201) |
| dev | N | 1 × `POST` + (N−1) × (`POST` 409 + `PUT` 200) |

### Two consequences beyond cost

**It fails closed on unrelated MCPs.** The loop bails on the first code with
no catalogue detail:

```python
detail = self.mcp_center.get_mcp_detail(server_code)
if not detail:
    return False
```

That `False` raises `SkillSetRuntimeReconcileError`, which
`MutationProjectionFlow` compensates — rolling back the add. One delisted MCP
anywhere in the Bot's set blocks *every* MCP add. On REL only the code being
added had to resolve.

**It overwrites device-side configuration.** Each redundant push re-runs
`build_mcp_sync_payload` and PUTs the result, so any device state not
reproducible from the DB is rewritten on every unrelated mutation.

### Where the push belongs

The single changed `server_code` is known at the control-plane mutation site,
`skill_set_management_service.py:437`, which dispatches on Set kind:

```python
if target["is_default"]:
    ...  # only an existing exclusion can be removed
    return await self._mutate(..., action="default_set_unexclude_mcp", ...)
return await self._mutate(
    ...,
    action="skill_set_add_mcp",
    runtime_required=bool(target.get("is_active")),
    ...
)
```

REL pushed there, unconditionally of Set active state. dev's
`runtime_required=bool(target.get("is_active"))` gate means an MCP added to
an *inactive* Set gets no device I/O at all — `MutationProjectionFlow.apply`
returns before projecting:

```python
if not runtime_required:
    result = mutation()
    return {**result.item, "changed": result.changed, **result.details}
```

so on dev the activation projection is currently the only thing that ever
configures those MCPs. Any fix that moves the push to the mutation site must
push **unconditionally**, restoring REL's invariant that a Set's MCPs are
installed on the device by the time the Set is activated — otherwise
activation would whitelist MCPs that were never configured.

`config_flow.py:158` is the existing precedent for the shape: write the DB
row, push the single MCP, roll the row back on failure.

Since `2befbc2e2` the ordinary-Set branch also resolves the catalogue entry
before opening the mutation (`_mcp_catalog_entry`,
`skill_set_management_service.py:719`), so an unknown `server_code` is now a
404 at the boundary rather than a crash. That narrows the *add's own*
exposure but does not touch this problem: the entry is projected down to
`{name, description, icon}` for the membership row, so it is not a device
payload, and the projection still fails closed on catalogue-missing codes
belonging to *other* MCPs.

### Acceptance criteria

- Adding one MCP produces exactly one device configuration write for that
  MCP, independent of how many MCPs the Bot already has.
- An MCP added to an inactive Set is configured on the device at add time, so
  activating that Set needs only the allow-list declaration.
- A Bot holding an MCP with no catalogue detail can still add, remove, and
  activate other MCPs.
- `sync_all_mcp_servers` continues to receive the complete projected set.

---

## Problem 4 — Both projection halves run on every mutation, twice on failure

`BotRuntimeProjector.project` always runs both halves:

```python
await self._apply_skill_projection(...)
await self._apply_non_skill_projection(...)
```

So an MCP mutation pays a full symlink resync it does not need, and a skill
mutation pays an allow-list declaration and (today) the full MCP fan-out.

`MutationProjectionFlow._project_or_compensate` calls the same full
`project()` on both the forward path and the compensating path:

```python
await self._runtime.project(bot_id=..., owner_id=..., retired_mappings=...)
except Exception as exc:
    self._repository.restore_desired_state(...)
    try:
        await self._runtime.project(bot_id=..., owner_id=..., retired_mappings=...)
```

A failed MCP add therefore costs two full projections. Combined with problem
3, that is `2N` detail pushes plus two symlink syncs for one failed
single-MCP operation.

The projector already has partial separation — `project`,
`project_mcp_and_cli`, `project_for_cleanup` — but no skills-only
counterpart, and `_mutate` has no way to declare which facet a mutation
touches.

### Constraints on any fix

**The compensation must apply the same scope in reverse.** A caller-supplied
callback cannot express that — the flow cannot derive a counter-projection
from an opaque callable — so what changed must be *declared*, not injected as
behaviour.

**"How many device calls" is a provider decision, not a caller decision.**
The engines do not agree on the shape of a delivery. arca/baas write per-MCP
config, declare `filter-servers`, and sync symlinks as separate calls, so
skipping a half is a real saving. Singlebox is the same minus `filter-servers`
(`SingleboxDeviceSyncService.sync_all_mcp_servers` is a documented no-op —
the engine needs `mcporter`). Teclaw composes and delivers one whole
artifact that already contains skills, MCP and CLI, so a mutation touching
both halves needs **one** delivery, not two — and splitting it into two
would make teclaw recompose and redeliver the same artifact twice.

A projector that branches on which halves to run therefore encodes the wrong
thing in the wrong place. The scope of a change belongs to the caller; the
number of device calls it costs belongs to the provider implementation,
reached through the existing `DeviceSyncDispatcher` registry
(`plugins/community/device_sync_dispatcher.py:41`, keyed on `ctx.provider`)
rather than a conditional in shared code.

### Acceptance criteria

- A mutation declares the scope it touched; nothing infers it.
- On arca/baas, an MCP-only mutation performs no symlink sync, and a
  skill-only mutation performs no MCP allow-list declaration and no Passport
  update.
- On a whole-artifact provider, a mutation touching both halves costs one
  delivery, not two.
- Selecting that behaviour goes through the provider registry; no shared
  code branches on provider or engine type to decide it.
- The compensating projection covers exactly the scope the forward
  projection did.

---

## Problem 5 — `SkillSetService.refresh_mcp_scope` is dead

`core/skill_center/services/skill_set_service.py:1914` has no callers. On REL
it was the mutation-path scope refresh; dev's projector inlines both of its
halves (`sync_all_mcp_servers` for the device, `update_passport` for the
Passport).

**Two different methods share this name, and only one is dead.** The
`SkillSetService` method is a thin wrapper that delegates to
`MCPSyncService.refresh_mcp_scope`; deleting the wrapper does not orphan the
callee, because the callee has its own live caller on a different path:

```python
# core/devices/services/device_service.py:1518
# inside DeviceService._sync_mcps_when_device_active
scope_result = await self._mcp_sync.refresh_mcp_scope(
    user_id=record.entity_id,
    entity_id=record.entity_id,
    bot_id=bot_id,
    entity_type=record.entity_type,
    engine_type=engine_type,
)
```

That is the device-ACTIVE reconcile — the path that re-declares scope and
re-pushes every MCP detail after a container restart, and the reason
restart/reprovision is out of scope for this work. So the asymmetry is not a
half-measure: the wrapper is unreachable, the callee is load-bearing.

### Acceptance criteria

- `SkillSetService.refresh_mcp_scope` is removed.
- `MCPSyncService.refresh_mcp_scope` still has exactly one production
  caller, `DeviceService._sync_mcps_when_device_active`, and neither is
  modified.

---

## Explicitly out of scope

**Device restart / reprovision.** Covered independently by
`DeviceService._sync_mcps_when_device_active`, present on both branches; it
declares scope then pushes all details. The projector's fan-out is
duplicative with it, not load-bearing for it.

> **Correction — this is false on `dev`.** `_sync_mcps_when_device_active`
> has **zero production callers** here; only three tests reference it, and
> they mock it out. The mocking is deliberate:
> `test_pending_activation_has_one_mcp_writer_owned_by_device_event` asserts
> it must *not* run — *"The Device callback publishes one event; it must not
> also sync MCP."*
>
> Ownership moved to the runtime-ready event path. First activation publishes
> `DeviceActivatedEvent`; a successful BaaS restart publishes the narrower
> `RuntimeProjectionRequestedEvent` so unrelated activation consumers are not
> replayed. `SkillSymlinkListener` handles both and calls
> `project(scope=ProjectionScope.everything())`. This is the general,
> layout-neutral full-projection path for a newly active or restarted
> container. AICoding's confirmed-template-update flow retains its narrower
> authorization/Skill compensation; the other historical full-detail push
> (`device_service.py:1529`) sits inside the dead method.
>
> So the projector's whole-set push on that path is load-bearing, not
> duplicative — which is why `claim_all_mcp` survives with exactly one
> listener. Every direct mutation path names its own delta.

**Skill-carried MCP dependencies.** `RuntimeProjectionResolver` folds each
skill's `mcp_dependencies` into `mcp_server_codes`, so those codes reach the
allow-list. REL had no such mechanism at all. Narrowing the detail push must
not leave them whitelisted-but-unconfigured — a state neither branch has
today. The plan settles which way this resolves; the spec records only that
allow-list membership and device configuration must not diverge.

> **Resolved in Group 8, no longer out of scope.** Skill mutations reconciled
> for exactly this reason: they held only a `skill_id` and could not name the
> dependencies. They now carry them on the mutation result, read under the
> row lock, and declare them as claimed/released — so the codes are delivered,
> not merely whitelisted. A dependency-free Skill mutation declares no MCP
> scope and skips that half entirely.

**Activation no longer deactivating peers.** Not a regression.
`SkillSetActivator._activate_skill_set_unlocked` on REL was already
incremental: *"激活单个能力集（增量激活）… 不会清除其他已激活的能力集"*.

**`SkillSetSwitcher._cleanup_all_non_reserved_items`.** Reachable on REL only
from `switch_to_skill_set`, which takes no production traffic. Dropped
deliberately.

**Default-CLI merge.** dev omits REL's `get_default_cli_items` merge in the
passport payload, by documented intent
(`bot_runtime_projector.py:265`): CLI removal is persisted by the
authorization service, so merging static engine defaults would undo it.

---

## Success criteria

1. A `caller`-mode MCP survives every skill-set mutation with its identity
   mode intact.
2. Removing or excluding an MCP removes its configuration — `api_key`
   included — from the device, unless another active claim holds it.
3. Adding one MCP costs one device configuration write.
4. On a per-call provider (arca/baas), an MCP-only mutation touches no
   symlinks and a skill-only mutation touches neither the device MCP
   allow-list nor the Passport; on a whole-artifact provider, a mutation
   touching both halves costs one delivery. Which of these happens is
   decided by the provider implementation behind `DeviceSyncDispatcher`, not
   by a conditional in shared code.
5. No production path calls `SkillSetService.refresh_mcp_scope`;
   `MCPSyncService.refresh_mcp_scope` keeps its device-active caller.
6. The existing `test_sync_service.py` resource-scope contract tests still
   pass unchanged.

---

## Resolution

Five of the six hold. What was actually shipped, against each:

1. **Holds.** The projector sends `mcp_items` with an explicit
   `identity_mode`. Group 7 closed the last gap: two non-projector callers
   built the code-only shape, and `remove_cli_from_default_skill_set` is
   itself a skill-set mutation, so this criterion did not hold until
   `unpack_resource_scope` began refusing that shape outright.
2. **Holds.** `sync_mcp_delivery` removes released configuration, and the
   projector subtracts the projected set first, so a code another claim still
   supplies is never deleted.
3. **Holds.** A declared claim is intersected with the projected set, so
   adding one MCP is one entry to `sync_mcp_details_for_bot`, which resolves
   the device once for the batch.
4. **Half holds.** An MCP-only mutation touches no symlinks and a skill-only
   mutation touches neither the device MCP calls nor the Passport, and the
   branch is on the caller's *declared scope* — never on provider or engine
   type, which is what the constraint was protecting. The whole-artifact
   half does **not** hold: teclaw still recomposes per half, because the
   `apply_runtime_projection` it needs is corp-side work outside this
   repository. The seam for it is now `SkillSetService.sync_mcp_projection`.
5. **Holds.** `SkillSetService.refresh_mcp_scope` is deleted;
   `MCPSyncService.refresh_mcp_scope` keeps its device-active caller.
6. **Holds.** Unchanged and passing.
