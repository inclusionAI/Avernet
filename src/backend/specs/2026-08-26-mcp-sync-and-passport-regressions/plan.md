# Plan — MCP Device-Sync and Passport Regressions

Paths are relative to `src/backend/src/agentclaw/community/` unless noted.
Tests live under `src/backend/tests/`.

## The organising idea

Every problem in the spec is a consequence of one shape: the projection
declares a **complete desired state** and then treats "declare" and "deliver"
as the same act. Declaring is correctly total — `filter-servers` and the
Passport manifest are overwrite-style, they *must* carry the whole set.
Delivering is not: pushing an MCP's `api_key` to the device, or deleting it,
is a per-MCP act that should happen only when that MCP's claim actually
changed.

So the fix separates them, and it separates *who knows what*:

- **Declaration stays total.** `sync_all_mcp_servers` and `update_passport`
  keep receiving the full projected set. Nothing about that is wrong today,
  except that the Passport payload is incomplete (problem 1).
- **Delivery is scoped, and the mutation says to what.** `add_mcp` knows it
  claimed `{server_code}`; `remove_mcp` knows it released one. That fact
  travels with the mutation instead of being re-derived downstream.
- **The projector performs the delivery, the provider decides its shape.**
  The projector stays the only component doing device I/O, so compensation
  keeps working and the control-plane service needs no device dependency.
  How many device calls a delivery costs is answered by the `DeviceSync`
  implementation behind `DeviceSyncDispatcher`, not by a branch in shared
  code.

### Why declared, not derived

An earlier draft had the projector snapshot the MCP set before the mutation
and diff it afterwards (`claimed = post − pre`). That is strictly more
machinery for information the caller already holds, plus a second copy of the
set-union logic that could drift from `_resolve_plan`. Dropped.

What the diff did give for free was REL's removal guard: an MCP must not be
deleted from the device just because *this* Set dropped it, if something else
still supplies it. That is recovered with one filter against the
post-mutation projected set, which `_resolve_plan` already computes:

```python
claimed  = declared_claimed  & post_codes   # push only what really ended up claimed
released = declared_released - post_codes   # remove only what nothing else supplies
```

**The filter is a guard, not a source.** `claimed` never grows: it is
whatever the mutation declared, minus anything that did not survive into the
projected set. `add_mcp` declares exactly one code, so `claimed` is exactly
one code and delivery is exactly one device write — the intersection cannot
turn a single-MCP mutation into a batch. `activate` and the reconcile path
are the only n-ary claimants, which is why the parameter is a set at all.

REL's guard had two halves, and only one still applies here. The
cross-Set half is now moot: R3 keeps a capability in at most one Set
(`policies/capability_ownership.py:9`, `RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET`),
so no other Set can be holding the code this Set just released. The half
that still matters is non-membership supply — the engine/template default
policy and skills' `mcp_dependencies` both put codes into `post_codes`
without any Set claiming them. Dropping a Set's claim on such a code must
not delete it from the device, and `- post_codes` is what prevents that.

### Why the provider decides the call shape

The engines disagree about what a delivery is:

| provider | skills | MCP config | allow-list |
| --- | --- | --- | --- |
| arca / baas | `sync_symlinks` | per-MCP `POST`/`PUT`/`DELETE` | `filter-servers` |
| singlebox | `sync_symlinks` | per-MCP | no-op (engine lacks `mcporter`) |
| teclaw | one composed artifact carrying all three | | |

For arca/baas, skipping a half a mutation did not touch is a real saving. For
teclaw it is meaningless — worse, splitting a both-halves mutation into two
calls makes it recompose and redeliver the same artifact twice. A
`ProjectionFacet` enum branched on inside the projector would encode the
arca/baas answer as if it were universal.

`DeviceSyncDispatcher` is already the per-provider registry
(`plugins/community/device_sync_dispatcher.py:41`, keyed on `ctx.provider`),
and `DeviceSync` is already the per-provider behaviour seam. So the scope
becomes a *payload field* delivered through that seam, and each impl decides
what it costs.

---

## Problem 1 — Passport identity mode

### Old (REL20260820)

Mutation paths went through `refresh_mcp_scope` → `_update_passport`, which
read the persisted call-config table and sent all three lists:

```python
identity_modes = self.caller_identity_repository.list_draft_call_types(
    int(bot["id"]), str(engine_type)
)
mcp_items = passport_mcp_items_from_entries(synced_mcps, identity_modes=identity_modes)
...
self.passport_update.update_passport(
    bot_id=bot_id,
    user_id=user_id,
    resource_scope={
        "mcp_codes": synced_server_codes,
        "mcp_items": mcp_items,
        "cli_items": cli_items,
    },
    bot_name=bot_name, bot_desc=bot_desc, engine_type=engine_type,
)
```

### Current (dev)

`bot_runtime_projector.py:374` sends two of the three:

```python
resource_scope={
    "mcp_codes": filter_passport_mcp_codes(
        projection.mcp_server_codes
    ),
    "cli_items": effective_cli_items,
},
```

### Breakage

`unpack_resource_scope` substitutes items with no `identity_mode`;
`ProdPassportPlugin._build_mcp_models` defaults that to `"owner"`;
`McpModel.serialization` then emits `identityMode` because it is no longer
`None`; `updatePassport` overwrites the list. Net: every skill-set mutation
asserts `owner` for every MCP, clobbering whatever
`update_mcp_identity_to_agent_principal` set — same facade, same field, no
separate store.

### Fix

Inject the caller-identity repository into the projector and send real modes.
`ProdPassportPlugin._build_mcp_models` omits `mcpName`/`mcpDesc` when `None`,
so no MCP-Center round trip is needed — which keeps problem 3's win intact.

`core/skill_center/services/bot_runtime_projector.py`:

```python
    @inject
    def __init__(
        self,
        ...
        passport: PassportPlugin,
        caller_identity_repo: CallerIdentityRepositoryProtocol,   # NEW
    ) -> None:
        ...
        self._caller_identity_repo = caller_identity_repo
```

```python
    def _passport_mcp_items(
        self, *, bot: dict, engine: str, codes: list[str]
    ) -> list[McpScopeItem]:
        """Identity-bearing MCP scope, so an overwrite-style updatePassport
        cannot silently downgrade a ``caller`` MCP to ``owner``."""
        modes: Mapping[str, object] = {}
        bot_pk = bot.get("id")
        if bot_pk is not None:
            modes = self._caller_identity_repo.list_draft_call_types(
                int(bot_pk), engine
            )
        items: list[McpScopeItem] = []
        for code in codes:
            raw = modes.get(code, "owner")
            mode = str(getattr(raw, "value", raw)).strip().lower()
            items.append({"mcp_code": code, "identity_mode": mode})
        return items
```

and at the call site:

```python
        passport_codes = filter_passport_mcp_codes(projection.mcp_server_codes)
        self._passport.update_passport(
            bot_id=bot_id,
            user_id=owner_id,
            engine_type=engine,
            resource_scope={
                "mcp_codes": passport_codes,
                "mcp_items": self._passport_mcp_items(
                    bot=bot, engine=engine, codes=passport_codes
                ),
                "cli_items": effective_cli_items,
            },
        )
```

`_apply_non_skill_projection` does not currently receive `bot`; `_resolve_plan`
already returns it, so thread it through (`project`, `project_mcp_and_cli`,
`project_for_cleanup` all have it in scope).

A mode outside `{"owner", "caller"}` must not be silently coerced —
`_build_mcp_models` already raises `PassportError` on one, and
`McpCallType.parse` normalises the stored value, so passing the parsed value
through is sufficient.

---

## Problem 2 — device removal, and Problem 3 — fan-out

These share one implementation.

### Old (REL20260820)

Add pushed exactly one MCP:

```python
push_result = await self._mcp_sync_service.sync_mcp_detail(
    user_id=user_id,
    mcp_data=mcp_data,
    bot_id=self.bot_id,
    entity_id=self.entity_id,
)
```

Remove deleted exactly one, behind three guards (another Set still holds it /
it is a platform default / the check itself failed):

```python
if should_remove:
    remove_result = await self._mcp_sync_service.remove_mcp_detail(
        server_code=server_code, bot_id=self.bot_id, user_id=self.entity_id,
    )
```

### Current (dev)

`skill_set_service.py:531` builds details for the **entire** projected set and
fans them all out; nothing removes:

```python
entries: list[dict[str, Any]] = []
for server_code in sorted(server_codes):
    detail = self.mcp_center.get_mcp_detail(server_code)
    if not detail:
        return False
    entries.append(detail)
delivery = await self._mcp_sync_service.sync_mcp_details_for_bot(
    user_id=..., mcp_entries=entries, bot_id=..., entity_id=..., engine_type=...,
)
```

### Breakage

- N catalogue lookups and `1 + 2(N−1)` device writes per single-MCP change
  (`push_single_mcp` costs `POST` 409 + `PUT` for an already-registered MCP).
- `if not detail: return False` fails the whole projection — and therefore
  compensates the mutation — when *any* unrelated MCP is missing from the
  catalogue.
- Unrelated MCPs get their device configuration rewritten from the DB on
  every mutation.
- `remove_mcp_detail` has zero callers: `api_key` and headers persist on the
  container after removal.

### Fix

**The mutation declares what it touched.** A small value object travels with
the mutation from the control-plane command to the projector:

```python
@dataclass(frozen=True)
class ProjectionScope:
    """What one mutation changed, as the mutation itself knows it.

    Declared, never inferred: ``add_mcp`` holds the code it claimed and
    ``remove_mcp`` the one it released, so re-deriving them downstream would
    be a second source of truth for a fact the caller already has.
    """
    skills: bool = False
    mcp: bool = False
    claimed_mcp: frozenset[str] = frozenset()
    released_mcp: frozenset[str] = frozenset()
    reconcile: bool = False

    @classmethod
    def everything(cls) -> "ProjectionScope":
        """Reconcile: no mutation to declare, so every projected code is
        treated as newly claimed (device-activated restart, skill upload)."""
        return cls(skills=True, mcp=True, reconcile=True)
```

Sizes, so the cost is explicit: `add_mcp` and `remove_mcp` declare exactly
one code — one device write, which is the whole point of problems 2 and 3.
`activate` / `deactivate` declare the Set's members. Only `reconcile`
declares the full projected set, and only on paths that have no mutation to
ask.

`ProjectionScope.everything()` is the default, so any caller not updated
keeps today's behaviour.

**`SkillSetService.sync_mcp_desired_state` becomes declaration-only.** The
detail loop and `sync_mcp_details_for_bot` call are deleted; `get_mcp_detail`
is no longer called here at all, which also removes the fail-closed hazard.

```python
    async def sync_mcp_desired_state(self, *, server_codes: set[str]) -> bool:
        """Declare the complete MCP allow-list to the Bot runtime.

        Declaration is total on purpose: ``sync_all_mcp_servers`` is the
        device-level reconciliation command and clears stale entries, so it
        runs even for an empty set.  Per-MCP *configuration* delivery is not
        total — the mutation declares that scope, see ``sync_mcp_delivery``.
        """
        try:
            ctx = await asyncio.to_thread(
                self._resolver.resolve_for_bot,
                self.bot_id,
                self.entity_id or self.user_id or "",
            )
            return bool(
                await asyncio.to_thread(
                    self._device_sync_dispatcher.dispatch(ctx).sync_all_mcp_servers,
                    [{"server_code": code} for code in sorted(server_codes)],
                )
            )
        except Exception:
            logger.warning(
                "[sync_mcp_desired_state] MCP allow-list declaration failed for bot_id=%s",
                self.bot_id, exc_info=True,
            )
            return False
```

Note the argument shape: `filter_servers` reads `server_code`/`serverCode`
off each dict (`mcp_device_transport.py:76`), so bare strings cannot be
passed — wrap them.

**Delivery takes the declared codes**, fetching details only for what it
pushes:

```python
    async def sync_mcp_delivery(
        self, *, claimed: frozenset[str], released: frozenset[str]
    ) -> bool:
        """Deliver configuration for newly claimed MCPs and withdraw it for
        released ones.

        Both sets are declared by the mutation and already guarded against
        the projected set by the caller, so they are as small as the change
        was: one code for an MCP add or remove, the Set's members for an
        activation.  ``sync_mcp_details_for_bot`` resolves the device once
        for the batch; at one entry that is one device write, not a fan-out.
        """
        try:
            entries: list[dict[str, Any]] = []
            for server_code in sorted(claimed):
                detail = self.mcp_center.get_mcp_detail(server_code)
                if not detail:
                    logger.error(
                        "[sync_mcp_delivery] no catalogue detail for %s", server_code
                    )
                    return False
                entries.append(detail)
            if entries:
                delivery = await self._mcp_sync_service.sync_mcp_details_for_bot(
                    user_id=self.user_id or self.entity_id or "",
                    mcp_entries=entries,
                    bot_id=self.bot_id,
                    entity_id=self.entity_id,
                    engine_type=self.engine_type,
                )
                if not delivery.get("success"):
                    return False
            for server_code in sorted(released):
                removal = await self._mcp_sync_service.remove_mcp_detail(
                    server_code=server_code,
                    bot_id=self.bot_id,
                    user_id=self.entity_id or self.user_id or "",
                )
                if not removal.get("success"):
                    return False
            return True
        except Exception:
            logger.warning(
                "[sync_mcp_delivery] MCP delivery failed for bot_id=%s",
                self.bot_id, exc_info=True,
            )
            return False
```

`if not detail: return False` still fails, but now only for a code we are
actually installing — the same contract REL had.

**The projector guards the declared scope against the projected set.** The
mutation supplies the codes; the projected set only removes ones it must not
act on:

```python
        codes = set(projection.mcp_server_codes)
        if scope.reconcile:
            # No mutation declared anything (device restart, skill upload):
            # treat the whole projected set as newly claimed.
            claimed, released = frozenset(codes), frozenset()
        else:
            # Guard, not source. ``claimed`` cannot grow beyond what the
            # mutation declared, so ``add_mcp``'s single code stays a single
            # code and costs one device write.  ``- codes`` keeps a release
            # from deleting a code the default policy or a Skill dependency
            # still supplies without any Set claiming it.
            claimed = scope.claimed_mcp & codes
            released = scope.released_mcp - codes
        if not await service.sync_mcp_delivery(claimed=claimed, released=released):
            raise SkillSetRuntimeReconcileError()
        if not await service.sync_mcp_desired_state(server_codes=codes):
            raise SkillSetRuntimeReconcileError()
```

Note `scope.reconcile` rather than an identity comparison against
`everything()` — a dataclass equality check would also match a mutation that
happened to declare both halves.

Order matters and matches the old invariant: configuration lands before the
allow-list references it, and withdrawal happens before the allow-list stops
covering it.

**Compensation inverts the scope.** `MutationProjectionFlow` already swaps
its skill-mapping arguments on the compensating call; the MCP scope inverts
the same way:

```python
    def _inverted(scope: ProjectionScope) -> ProjectionScope:
        return replace(
            scope,
            claimed_mcp=scope.released_mcp,
            released_mcp=scope.claimed_mcp,
        )
```

No pre-snapshot is needed for this: the compensating projection re-resolves
the plan against the restored desired state, so `codes` is already the
pre-mutation set, and the filter above does the rest.

**Where each command's scope comes from.** `add_mcp` and `remove_mcp` hold
the single `server_code` outright.

`activate`/`deactivate` need the Set's MCP codes, and
`set_skill_set_active` already computes them under the same row lock it takes
for the mutation (`capability_desired_state.py:493`):

```python
mcp_codes = {str(member.server_code) for member in mcp_members}
```

So they ride back on `DesiredStateMutation.details` — the dict field that
already exists for exactly this
(`capability_desired_state_types.py:34`) — rather than the service issuing a
second, unlocked query that could disagree with what the mutation actually
installed. The scope is therefore finalised *after* `mutation()` returns, not
before it, for these two commands.

`add_skill`/`remove_skill` carry the skill's `mcp_dependencies` when it has
any.

This also settles the skill-carried dependency question: a dependency the
skill brings is declared like any other claim, and one it takes away is
declared released. Allow-list and configuration cannot diverge.

---

## Problem 4 — scoped projection, decided per provider

### Current

```python
    async def project(self, *, bot_id, owner_id, retired_mappings=()) -> None:
        service, bot, engine, projection, effective_cli_items = self._resolve_plan(...)
        await self._apply_skill_projection(...)
        await self._apply_non_skill_projection(...)
```

and both the forward and compensating paths in
`_mutation_flow.py:103` / `:119` call that same full `project`.

### Breakage

An MCP mutation resyncs all symlinks; a skill mutation declares the MCP
allow-list and updates the Passport. A failed mutation does all of it twice.

### Rejected fix

A `ProjectionFacet` enum (`SKILLS` / `MCP_AND_CLI` / `ALL`) branched on inside
`project`. It encodes the arca/baas call shape as universal: for a
whole-artifact provider, `ALL` should be one compose-and-deliver, and two
facet calls would recompose and redeliver the same artifact twice. It is also
the `if provider ==` in shared code that the registry exists to avoid.

### Fix

`ProjectionScope` (above) already carries `skills` and `mcp` booleans
alongside the MCP code sets. Rather than the projector branching on them, the
whole scope goes through the `DeviceSync` seam, and each provider impl
decides what it costs.

Widen the `DeviceSync` protocol (`core/devices/services/device_sync.py`) with
one intent-shaped method:

```python
    def apply_runtime_projection(
        self,
        intent: RuntimeProjectionIntent,
    ) -> dict[str, Any]:
        """Apply one scoped runtime projection.

        The intent carries everything a delivery could need — symlinks, the
        MCP configuration delta, the full allow-list — plus which parts this
        mutation actually touched. How many device calls that costs is the
        implementation's decision: a per-call engine skips the untouched
        halves, a whole-artifact engine composes once and delivers once
        regardless of scope.
        """
        ...
```

Implementations:

- `BaasDeviceSyncService` — today's behaviour, gated on the scope: symlinks
  only when `intent.scope.skills`, MCP config + `filter-servers` only when
  `intent.scope.mcp`.
- `SingleboxDeviceSyncService` — delegates, keeping its `sync_all_mcp_servers`
  no-op (`singlebox_device_sync.py:47`).
- teclaw (corp) — composes the artifact once from the whole intent and
  delivers it once, whatever the scope says. **This impl lives outside this
  repository and must land alongside.** Until it does, teclaw keeps the
  default.
- A default implementation on the protocol reproduces the per-call sequence,
  so a provider that does not override is unchanged.

Selection needs no new registry: `DeviceSyncDispatcher.dispatch(ctx)` already
returns the per-provider `DeviceSync`, keyed on `ctx.provider`.

The projector then stops orchestrating device calls and hands over the
intent, keeping only what is genuinely shared — resolving the plan, building
the Passport payload, raising `SkillSetRuntimeReconcileError`. Passport is
not part of the intent: AgentPass is not the device, and every provider
updates it the same way.

`project_mcp_and_cli` becomes `project(scope=ProjectionScope(mcp=True))` with
the named method kept as a thin alias, so `skill_center_module.py:918` and
`SkillSymlinkListener` need no edit.

### Scope declaration per command

The service has seven commands routing through `_mutate`
(`skill_set_management_service.py:286`–`:584`); each declares one scope.
`add_mcp` and `remove_mcp` cover the Default-Set exclusion branches too, so
the mapping is per command, not per repository call:

| command | scope |
| --- | --- |
| `add_mcp` (incl. `default_set_unexclude_mcp`) | `mcp=True, claimed={server_code}` |
| `remove_mcp` (incl. `default_set_exclude_mcp`) | `mcp=True, released={server_code}` |
| `add_skill` | `skills=True`, plus `mcp=True, claimed=<deps>` when the skill has `mcp_dependencies` |
| `remove_skill` | `skills=True`, plus `mcp=True, released=<deps>` when the skill has `mcp_dependencies` |
| `activate` | `skills=True, mcp=True, claimed=<set's codes>` |
| `deactivate` | `skills=True, mcp=True, released=<set's codes>` |
| `legacy_activate` | `skills=True, mcp=True, claimed=<set's codes>` |

Non-flow entry points — `SkillSymlinkListener` (`skill_center_module.py:914`)
and `LocalSkillUploadService._sync_runtime` (`:533`) — pass
`ProjectionScope.everything()`, reproducing today's full push. That is
correct for a freshly activated device and preserves the reconcile behaviour
the spec declared out of scope.

---

## Problem 5 — dead code

Delete `SkillSetService.refresh_mcp_scope`
(`core/skill_center/services/skill_set_service.py:1914-1950`).

Leave `MCPSyncService.refresh_mcp_scope` alone — a different method with a
live caller, `DeviceService._sync_mcps_when_device_active`
(`device_service.py:1518`), the device-ACTIVE reconcile path.

---

## Logging

Every change here is device- or authorization-facing and most of it is
invisible in the response body, so the log is the only record that the right
thing happened. Three of the five problems went unnoticed precisely because
nothing said what was pushed.

**Never log a credential.** `sync_mcp_delivery` handles entries straight from
MCP Center and `build_mcp_sync_payload` — they carry `api_key` and custom
headers. Log `server_code` and counts, never the entry, never the payload.
The repo's existing convention is explicit about this
(`plugins/.../passport.py`: *"Passport 日志只记录 token 存在性，禁止输出值或前缀"*)
and it applies with equal force to MCP configuration.

What each new or changed path logs:

| site | level | content |
| --- | --- | --- |
| `_passport_mcp_items` | INFO | `bot_id`, total count, how many resolved `caller` — the fact problem 1 silently destroyed. Codes only, no payload. |
| `_apply_non_skill_projection` scope guard | INFO | declared vs effective `claimed`/`released` **when the guard trimmed something**, with the codes it dropped and why (not in projected set / still supplied). Silent when nothing was trimmed. |
| `sync_mcp_delivery` push | INFO | `bot_id`, the `server_code`s pushed, count. |
| `sync_mcp_delivery` removal | **WARNING** | `bot_id`, `server_code`. Deleting device configuration is destructive and irreversible from our side — it deserves to be findable without DEBUG. |
| `sync_mcp_desired_state` | INFO | `bot_id`, declared code count (already partly present). |
| `apply_runtime_projection` impls | INFO | provider, scope, and how many device calls it made — the number that proves the teclaw single-delivery claim. |

Failure paths keep `exc_info=True` and name the `bot_id` and `server_code`
that failed, so a partial delivery is diagnosable from one line.

## Documentation

The existing code in this area carries dense "why" docstrings, and the
regressions this plan fixes were all invisible-by-omission. New code matches
that bar:

- `ProjectionScope` — why declared rather than derived, and that the
  projector's intersection is a guard that can only shrink it.
- `sync_mcp_desired_state` vs `sync_mcp_delivery` — the declare/deliver split
  stated at both sites, since the whole regression came from conflating them.
- The `- codes` removal guard — what it protects (non-membership supply:
  defaults and Skill dependencies) and what it no longer needs to
  (cross-Set, unreachable under R3).
- `apply_runtime_projection` — that call count is the implementation's
  decision, with the per-call and whole-artifact cases named.
- The `mcp_items` addition — a comment at the call site naming the
  overwrite-style contract and the `"owner"` default that made omission
  destructive, so nobody "simplifies" it back out.

## Files touched

| file | change |
| --- | --- |
| `core/skill_center/services/bot_runtime_projector.py` | identity-bearing `mcp_items`; accept `ProjectionScope`; filter declared codes against the projected set; hand an intent to `DeviceSync`; thread `bot` into `_apply_non_skill_projection` |
| `core/skill_center/services/skill_set_service.py` | `sync_mcp_desired_state` → declaration-only; new `sync_mcp_delivery`; delete `refresh_mcp_scope` |
| `core/skill_center/services/_mutation_flow.py` | `ProjectionScope`; forward and invert it across the compensating projection |
| `core/skill_center/services/skill_set_management_service.py` | per-command scope declaration |
| `core/repository/implementations/skill_center/capability_desired_state.py` | return the Set's MCP codes on the activate/deactivate mutation result |
| `core/devices/services/device_sync.py` | `apply_runtime_projection` + `RuntimeProjectionIntent`, with a per-call default |
| `core/devices/services/baas_device_sync.py`, `singlebox_device_sync.py` | scope-aware delivery |
| `core/skill_center/runtime_projection_contract.py`, `api/bot_runtime_projector.py` | protocol: `scope` parameter |
| `di/modules/skill_center_module.py` | inject `CallerIdentityRepositoryProtocol` into the projector |

## Tests

New:

- `mcp_items` carries `caller` for an MCP whose call-config says so, and
  `owner` for one with no row.
- Adding one MCP to a Bot with three pushes exactly one detail and declares
  four allow-list codes.
- Removing an MCP calls `remove_mcp_detail` once; an MCP still claimed by
  another active Set is not removed; a platform-default MCP is not removed.
- A Bot holding a catalogue-missing MCP can still add an unrelated one.
- Compensation inverts the scope: a projection failure after a successful add
  removes what it pushed.
- `ProjectionScope.everything()` pushes every projected code (device-activated
  reconcile unchanged).
- On the baas impl, an MCP-only scope performs no `sync_symlinks`; a
  skills-only scope performs no `sync_all_mcp_servers` and no per-MCP write.
- A fake whole-artifact `DeviceSync` receives one `apply_runtime_projection`
  call for a both-halves scope.

Must keep passing unchanged: `tests/community/core/mcp/services/test_sync_service.py`
resource-scope contract tests (`:158`, `:204`, `:319`, `:350`, `:393`,
`:431`, `:554`) — they cover `MCPSyncService.refresh_mcp_scope`, which this
plan does not touch.

## Sequencing

1. Problem 1 alone — smallest diff, highest severity, independently shippable.
2. `ProjectionScope` threaded end to end, defaulting to `everything()` — no
   behaviour change, isolates the plumbing.
3. Commands declare real scopes; delivery honours `claimed`/`released`:
   problems 2 and 3 land together.
4. `apply_runtime_projection` on `DeviceSync`, with the per-call default
   preserving behaviour, then baas/singlebox overrides.
5. Delete dead code (problem 5).

Group 4 needs the corp teclaw implementation to land alongside to realise its
benefit; the default keeps teclaw correct but not yet cheaper.

## Risks

- **Identity source.** `list_draft_call_types` reads `BotMcpCallConfigModel`
  on `(bot_pk, engine_type, env)`. "draft" is naming, not a staging state —
  it is the same source `_update_passport` uses. If a Bot's `engine_type` at
  projection time differs from the one the config rows were written under,
  modes resolve empty and fall back to `owner`. The projector uses
  `bot["active_engine"]`, the same value `_update_passport` resolves, so this
  matches existing behaviour rather than introducing drift.
- **A wrong declaration is now a wrong device call.** With the scope declared
  rather than derived, a command that declares the wrong codes pushes or
  deletes the wrong configuration. The `& codes` / `- codes` filter bounds
  the damage — nothing outside the projected set is ever pushed, and nothing
  inside it is ever deleted — so the worst case is a missed push, not a
  deletion of something still in use.
- **Widening `DeviceSync` is a cross-repo change.** The protocol gains a
  method every impl must satisfy; the corp teclaw impl is not visible from
  here. The per-call default on the protocol is what keeps that from being a
  breaking change.
- **Removal is now automatic.** Any declared-released code that has left the
  projected set gets `DELETE /api/mcp/{code}`. Step 2 landing the plumbing
  with `everything()` still in force exists to de-risk exactly this.
