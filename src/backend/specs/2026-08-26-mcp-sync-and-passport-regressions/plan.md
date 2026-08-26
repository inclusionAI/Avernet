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

So the fix is to split them:

- **Declaration stays total.** `sync_all_mcp_servers` and `update_passport`
  keep receiving the full projected set. Nothing about that is wrong today,
  except that the Passport payload is incomplete (problem 1).
- **Delivery becomes a delta.** The projector computes
  `claimed = post − pre` and `released = pre − post` over the projected MCP
  code set, pushes configuration for `claimed`, removes it for `released`,
  and touches nothing else.

This single change resolves problems 2 and 3 together, and it does so more
cleanly than porting REL's per-operation pushes back:

| REL mechanism | replaced by |
| --- | --- |
| push at `add_mcp_to_skill_set` | `claimed` delta |
| `should_remove` cross-Set usage scan | structural — an MCP another Set still claims never leaves the post set |
| `default_server_codes` skip | structural — platform defaults are in `system_default_mcp_server_codes`, always in the post set |
| `remove_mcp_detail` at two sites | `released` delta |

One point in favour of the rejected alternative, checked and found not to
apply: since `2befbc2e2` the add site already calls
`self._mcp_center.get_mcp_detail(server_code)` via `_mcp_catalog_entry`
(`skill_set_management_service.py:719`), so a mutation-site push looks like
it could reuse that fetch. It cannot — the helper narrows the result to
`{name, description, icon}` for the membership row, and a device push needs
the full entry. A mutation-site push would still add its own catalogue call.

It also removes the need for the "push unconditionally at add time"
constraint the spec flagged. Adding an MCP to an *inactive* Set installs no
claim, so the delta is empty and nothing is pushed — and activating that Set
later adds the claims, so the delta is exactly those codes and they get
pushed then. dev's `runtime_required=bool(target.get("is_active"))` gate stays
as-is, and unlike REL there is no orphan registration for an inactive Set.

Problem 4 (facets) is the same idea one level up: `project` currently
delivers both halves unconditionally; it should deliver only the facet the
mutation touched.

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

**`SkillSetService.sync_mcp_desired_state` becomes declaration-only.** The
detail loop and `sync_mcp_details_for_bot` call are deleted; `get_mcp_detail`
is no longer called here at all, which also removes the fail-closed hazard.

```python
    async def sync_mcp_desired_state(self, *, server_codes: set[str]) -> bool:
        """Declare the complete MCP allow-list to the Bot runtime.

        Declaration is total on purpose: ``sync_all_mcp_servers`` is the
        device-level reconciliation command and clears stale entries, so it
        runs even for an empty set.  Per-MCP *configuration* delivery is not
        total — it is driven by the claim delta in ``sync_mcp_delivery``.
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

**A new delivery method carries the delta.** It fetches details only for the
codes being pushed:

```python
    async def sync_mcp_delivery(
        self, *, claimed: set[str], released: set[str]
    ) -> bool:
        """Deliver configuration for newly claimed MCPs and withdraw it for
        released ones.  Unlike the allow-list declaration this is a delta:
        an MCP whose claim did not change is not re-pushed, so one mutation
        never rewrites another MCP's device-side configuration."""
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
actually installing — which is the same contract REL had.

**The projector computes the delta.** `_resolve_plan` already produces the
post-mutation projected set; the pre-mutation set is one DB read, taken
before the mutation by the same flow that already snapshots skill mappings.

`core/skill_center/runtime_projection_contract.py` and
`api/bot_runtime_projector.py` gain:

```python
    async def snapshot_mcp_codes(
        self, *, bot_id: str, owner_id: str
    ) -> frozenset[str]: ...
```

implemented as the MCP half of `_resolve_plan`'s projection, and
`_apply_non_skill_projection` gains a `previous_mcp_codes` parameter:

```python
        codes = set(projection.mcp_server_codes)
        if not await service.sync_mcp_delivery(
            claimed=codes - previous_mcp_codes,
            released=previous_mcp_codes - codes,
        ):
            raise SkillSetRuntimeReconcileError()
        if not await service.sync_mcp_desired_state(server_codes=codes):
            raise SkillSetRuntimeReconcileError()
```

Order matters and matches the old invariant: configuration lands before the
allow-list references it, and withdrawal happens before the allow-list stops
covering it.

`MutationProjectionFlow.apply` takes the pre-snapshot alongside the existing
mapping snapshot:

```python
        previous_mappings = await self._runtime.snapshot_skill_mappings(
            bot_id=bot_id, owner_id=owner_id,
        )
        previous_mcp_codes = await self._runtime.snapshot_mcp_codes(
            bot_id=bot_id, owner_id=owner_id,
        )
        result = mutation()
        await self._project_or_compensate(
            ..., previous_mcp_codes=previous_mcp_codes,
        )
```

and the compensating call passes the *current* set as the baseline so the
delta inverts, exactly as `retired_logical_skill_mappings` already inverts
for skills.

**Entry points with no mutation** — `SkillSymlinkListener` (device activated)
and `LocalSkillUploadService` — call `project`/`project_mcp_and_cli` without a
flow. They pass `previous_mcp_codes=frozenset()`, which makes every projected
code "claimed" and reproduces today's full push. That is correct for a
freshly activated device and preserves the reconcile behaviour the spec
declared out of scope.

This also settles the skill-carried `mcp_dependencies` question: a dependency
entering the projected set is `claimed` like any other code and gets its
configuration; one leaving is `released`. Allow-list and configuration cannot
diverge, and no separate push site is needed.

---

## Problem 4 — projection facets

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

### Fix

Declare the facet at the mutation, do not inject a callback — the
compensating projection has to apply the *same* facet in reverse, which an
opaque callable cannot express.

`core/skill_center/services/_mutation_flow.py`:

```python
class ProjectionFacet(Enum):
    SKILLS = "skills"
    MCP_AND_CLI = "mcp_and_cli"
    ALL = "all"
```

`project` takes it and dispatches:

```python
    async def project(
        self, *, bot_id, owner_id, retired_mappings=(),
        previous_mcp_codes: frozenset[str] = frozenset(),
        facet: ProjectionFacet = ProjectionFacet.ALL,
    ) -> None:
        service, bot, engine, projection, effective_cli_items = self._resolve_plan(...)
        if facet in (ProjectionFacet.SKILLS, ProjectionFacet.ALL):
            await self._apply_skill_projection(...)
        if facet in (ProjectionFacet.MCP_AND_CLI, ProjectionFacet.ALL):
            await self._apply_non_skill_projection(..., previous_mcp_codes=previous_mcp_codes)
```

`_mutate` passes it through, and each command declares its own:

The service has exactly seven commands routing through `_mutate`
(`skill_set_management_service.py:286`–`:584`); each declares one facet.
`add_mcp` and `remove_mcp` cover the Default-Set exclusion branches too, so
the mapping is per command, not per repository call:

| command | facet |
| --- | --- |
| `add_mcp` (incl. `default_set_unexclude_mcp` branch) | `MCP_AND_CLI` |
| `remove_mcp` (incl. `default_set_exclude_mcp` branch) | `MCP_AND_CLI` |
| `add_skill` | `SKILLS`, or `ALL` when the skill carries `mcp_dependencies` |
| `remove_skill` | `SKILLS`, or `ALL` when the skill carries `mcp_dependencies` |
| `activate` | `ALL` |
| `deactivate` | `ALL` |
| `legacy_activate` | `ALL` |

`project_mcp_and_cli` becomes `project(facet=MCP_AND_CLI)`; keep the named
method as a thin alias so `skill_center_module.py:918` and the listener stay
untouched.

The default is `ALL`, so any caller not enumerated above keeps today's
behaviour.

**Caveat to hold.** A skill carrying `mcp_dependencies` changes the MCP set,
so `add_skill`/`remove_skill` cannot be `SKILLS` when the skill has
dependencies. Resolve it in the command:

```python
facet = (
    ProjectionFacet.ALL
    if self._skill_has_mcp_dependencies(...)
    else ProjectionFacet.SKILLS
)
```

Cheaper and safer than the alternative, and it keeps the delta honest.

---

## Problem 5 — dead code

Delete `SkillSetService.refresh_mcp_scope`
(`core/skill_center/services/skill_set_service.py:1914-1950`). Leave
`MCPSyncService.refresh_mcp_scope` and its caller
`DeviceService._sync_mcps_when_device_active` alone — different method, still
live.

---

## Files touched

| file | change |
| --- | --- |
| `core/skill_center/services/bot_runtime_projector.py` | identity-bearing `mcp_items`; `previous_mcp_codes`; `snapshot_mcp_codes`; facet dispatch; thread `bot` into `_apply_non_skill_projection` |
| `core/skill_center/services/skill_set_service.py` | `sync_mcp_desired_state` → declaration-only; new `sync_mcp_delivery`; delete `refresh_mcp_scope` |
| `core/skill_center/services/_mutation_flow.py` | `ProjectionFacet`; MCP pre-snapshot; pass facet + baseline through forward and compensating projections |
| `core/skill_center/services/skill_set_management_service.py` | per-command facet declaration |
| `core/skill_center/runtime_projection_contract.py`, `api/bot_runtime_projector.py` | protocol: `snapshot_mcp_codes`, `facet`, `previous_mcp_codes` |
| `di/modules/skill_center_module.py` | inject `CallerIdentityRepositoryProtocol` into the projector |

## Tests

New:

- `mcp_items` carries `caller` for an MCP whose call-config says so, and
  `owner` for one with no row — asserted on the projector's
  `update_passport` kwargs.
- Adding one MCP to a Bot with three pushes exactly one detail and declares
  three allow-list codes.
- Removing an MCP calls `remove_mcp_detail` once; an MCP still claimed by
  another active Set is not removed; a platform-default MCP is not removed.
- A Bot holding a catalogue-missing MCP can still add an unrelated one.
- An MCP-only mutation performs no `sync_symlinks`; a skill-only mutation
  (no `mcp_dependencies`) performs no `sync_all_mcp_servers` and no
  `update_passport`.
- Compensation on projection failure inverts the MCP delta.
- `project` with `previous_mcp_codes=frozenset()` pushes every projected code
  (device-activated reconcile path unchanged).

Must keep passing unchanged: `tests/community/core/mcp/services/test_sync_service.py`
resource-scope contract tests (`:158`, `:204`, `:319`, `:350`, `:393`, `:431`,
`:554`) — they cover `MCPSyncService.refresh_mcp_scope`, which this plan does
not touch.

## Sequencing

1. Problem 1 alone — smallest diff, highest severity, independently shippable.
2. `snapshot_mcp_codes` + delta plumbing, delivery still total (no behaviour
   change) — isolates the plumbing from the behaviour flip.
3. Flip delivery to the delta: problems 2 and 3 land together.
4. Facets (problem 4).
5. Delete dead code (problem 5).

## Risks

- **Identity source.** `list_draft_call_types` reads
  `BotMcpCallConfigModel` on `(bot_pk, engine_type, env)`. "draft" is naming,
  not a staging state — it is the same source `_update_passport` uses. If a
  Bot's `engine_type` at projection time differs from the one the config rows
  were written under, modes resolve empty and fall back to `owner`. The
  projector uses `bot["active_engine"]`, the same value `_update_passport`
  resolves, so this matches existing behaviour rather than introducing drift.
- **Delta correctness depends on the pre-snapshot.** If `snapshot_mcp_codes`
  and `_resolve_plan` disagree about how the set is built, the delta is
  wrong in both directions. They must share one helper, not two copies of
  the union logic.
- **Removal is now automatic.** Any code that leaves the projected set gets
  `DELETE /api/mcp/{code}`. The platform-default and other-Set cases are
  structurally excluded, but a bug in `_resolve_plan` would now delete device
  configuration rather than merely mis-declare an allow-list. Group 2 landing
  the plumbing with delivery still total exists to de-risk exactly this.
