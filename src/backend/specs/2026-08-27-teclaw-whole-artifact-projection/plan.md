# Plan — One Whole-Artifact Delivery per Projection for Teclaw Bots

Implements `spec.md` in this directory.

All paths are relative to `src/backend/src/agentclaw/community/` unless
prefixed with `tests/`, which is relative to `src/backend/`.

## Shape of the change

Each engine implements its own runtime contract behind a protocol; the
projector resolves one from a registry and delegates. `BotRuntimeProjector`
stops knowing what a teclaw is.

**Today**, `project` runs two scope-gated halves, and the Passport update is
an unnamed `try/except` block at the *tail of the MCP half* rather than a
step of its own:

```
project(...)
  ├─ (service, bot, engine, projection, cli_items, identity_modes) = _resolve_plan(...)
  │        └─ if engine == "teclaw" and any center://  → refuse        (_build_plan:332)
  │
  ├─ if scope.skills or retired_mappings:  _apply_skill_projection(...)
  │        ├─ if engine == "teclaw" and any center corpus → refuse           (:417)
  │        ├─ if engine == "teclaw": sync_runtime(...); return               (:426)
  │        └─ else: Pool publish+verify, or legacy sync_runtime
  │
  └─ if scope.mcp:                         _apply_non_skill_projection(...)
           ├─ service.sync_mcp_projection(claimed, released, declared)   ← engine-specific
           └─ try: self._passport.update_passport(...)                   ← inline, :549-574
```

**After**, the two statements inside the MCP half are separated because they
have different owners — `sync_mcp_projection` is a runtime write that only
per-domain engines need, `update_passport` is the platform authorization
record that every engine needs identically:

```
project(...)
  ├─ plan = _resolve_plan(...)                 ← flushes Installation
  │     └─ runtime.validate_plan(...)          ← engine refuses what it can't carry
  │
  ├─ runtime = registry.for_engine(plan.engine)
  ├─ await runtime.apply(plan, scope, retired_mappings)
  │
  └─ if scope.mcp: _apply_passport_projection(...)
```

`_apply_passport_projection` is **new** — it does not exist today. It is the
`:549-574` block above, given a name so the projector can call it directly
once `sync_mcp_projection` has moved out from in front of it (`tasks.md` 3.5).
Its trigger is unchanged: `scope.mcp` before, `scope.mcp` after.

Two implementations of `EngineRuntimeProjection`:

| | `validate_plan` | `apply` |
| --- | --- | --- |
| `WholeArtifactRuntimeProjection` (teclaw) | refuse `center://` assets and `center` retirements | no-op if the scope declares nothing; else **one** `sync_runtime` |
| `PerDomainRuntimeProjection` (everything else) | no-op — Center is supported | today's two scope-gated halves, verbatim |

### Why this over an `if`

The first two drafts of this plan used a predicate
(`runtime_delivers_whole_artifact(engine)`). Rejected on review, correctly:

- A predicate answers *one* engine question. There are **four**
  `engine == "teclaw"` tests in the projector today — two about delivery
  (`_apply_skill_projection:417,426`) and two about the Center-corpus contract
  at plan-resolution time (`snapshot_skill_mappings:118`, `_build_plan:332`).
  A predicate removes two and leaves two, so the module still tests engine
  identity.
- It puts the knowledge in the caller. "How is this engine's runtime written"
  is the engine's fact, not the projector's.
- Adding an engine means editing a shared frozenset and re-reading every
  branch that consults it. With a registry it is a new class plus one entry.

Rule 19 ("Abstract After Two Examples, Not Before") is satisfied rather than
strained: there are two real implementations today, they differ in the whole
of their behaviour rather than a flag, and the boundary is a genuine
replacement seam — *"a single example can justify abstraction when it defines
a genuine boundary."*

The shape deliberately mirrors `DeviceSyncDispatcher` as reworked in #1592:
a protocol, one implementation per variant in its own module, and a routing
layer whose only job is picking one. That module's own words — *"Adding a
provider means adding a module and a routing entry — no edit to the other
providers' construction"* — are the goal here too.

### Keyed on `engine`, not device `provider`

The registry key is `bot["active_engine"]`, already in hand from
`_resolve_plan`. Keying on `DeviceContext.provider` was considered and
rejected on two concrete grounds:

1. **Cost.** `DeviceContextResolver.resolve_for_bot` is a binding query →
   `ConnInfoBuilder.build(...)` → a second bot query, and the builder performs
   a **blocking ws-info HTTP** call — which is why `sync_service.py:247,312`
   wrap it in `asyncio.to_thread` with a comment saying exactly that.
   `SkillSetService.sync_runtime` already calls `resolve_for_bot` internally,
   so keying on provider would resolve the device context twice per
   projection — on the very path this change exists to make cheaper.
2. **Failure surface.** `resolve_for_bot` raises `DeviceNotBoundError` for a
   Bot with no active binding. The projector resolves no device today; an
   unbound Bot degrades inside `sync_runtime` to `False`, which becomes
   `SkillSetRuntimeReconcileError` and is compensated by
   `MutationProjectionFlow`. Provider-keying would leak a different exception
   type out of `project()` and change that contract.

The two agree for teclaw regardless: `device_context_resolver:211` derives
`engine_type = "teclaw" if provider == "teclaw" else active_engine`.

### Why the Passport stays outside the protocol

`update_passport` writes the platform authorization record, not a runtime. It
is identical for every engine and gated on `scope.mcp` identically in both
implementations. Keeping it in the projector means the identity-coloured
`resource_scope` — the fix from
`specs/2026-08-26-mcp-sync-and-passport-regressions` problem 1, which stopped
skill-set mutations silently demoting every Caller MCP to Owner — lives in
exactly one place and cannot drift between two implementations.

**Does a teclaw Bot need it at all?** Yes, and the artifact does not subsume
it. A teclaw container is issued an AgentPass token that is written onto it as
an *egress rule*:

```python
# core/bot_management/services/teclaw_publish_task_handler.py:261,283
agent_pass_token = self._passport_plugin.query_token(bot_id, owner_id) or ""
updated = self._baas.update_teclaw_outbound_rule_by_bot_uuid(
    bot_uuid, agent_pass_token=agent_pass_token,
)
```

`TeclawProvisionService`'s module docstring describes the same wiring — the
publish-poll task *"pushes the bot's passport-token outbound rule to the
started container"*, deferred only because the PaaS device does not exist
until BaaS executes the create publish.

The two carry different things. The artifact is the container's
*configuration*: MCP endpoints with `api_key` and headers inlined by
`McporterComposer`. The passport is the platform's *authorization*: which MCPs
the Bot may reach and under whose `identityMode`, enforced at the gateway that
token opens. Skipping `update_passport` for teclaw would leave a container
holding a valid token against a stale manifest — MCPs configured in its
artifact that the authorization record does not list, or listed under the
wrong identity. The token itself is unaffected; it is minted once at bot
creation by `apply_first_agent_passport`.

Nothing in this repository gates the passport on engine. Note, though, that
the community implementation (`SelfIssuedPassportPlugin`) makes
`update_passport` a deliberate no-op — it validates the scope shape and
returns, a community deployment being its own authority — so the call is only
load-bearing in the corp deployment, whose `PassportPlugin` brokers through
tcauthmng/AgentPass and is not in this repository.

### A Core protocol, not a Plugin one

`plugin_api/` is for contracts where core calls out to a swappable external
dependency, selected per deployment profile (Rule 3). This selection is
between two **core domain behaviours**: community, corp and local all want
teclaw to behave identically. So the protocol lives in `core/skill_center/`
with no `Plugin` base, no `@plugin_impl`, no impl-registry entry, and no
plugin conformance test — the machinery `DeviceSync` itself also avoids
("the Core `DeviceSync` deliberately does NOT inherit `Plugin`").

## Changes

### 1. `core/skill_center/runtime_projection_contract.py`

**1a. `ResolvedCapabilityPlan`** — a frozen dataclass replacing the 6-tuple
`_resolve_plan` returns to three call sites:

```python
@dataclass(frozen=True, slots=True)
class ResolvedCapabilityPlan:
    bot_id: str
    owner_id: str
    service: object          # SkillSetService; untyped to avoid a cycle
    bot: dict
    engine: str
    projection: RuntimeProjection
    effective_cli_items: list[dict]
    identity_modes: Mapping[str, object]
```

`bot_id` / `owner_id` join the tuple's six so an implementation can act from
the plan alone. A prerequisite for crossing the seam, and an improvement on
its own — a positional 6-tuple unpacked in three places is already a hazard.

**1b. `EngineRuntimeProjection`** — the protocol:

```python
@runtime_checkable
class EngineRuntimeProjection(Protocol):
    """How one engine's runtime consumes a capability projection."""

    def validate_plan(
        self, *, skill_assets, retired_mappings=()
    ) -> None:
        """Refuse desired state this runtime has no contract for.

        Called during plan resolution, before anything is written, so a
        refusal costs no partial application.
        """

    async def apply(
        self, *, plan, scope, retired_mappings
    ) -> None:
        """Write the plan to the runtime.

        Raises ``SkillSetRuntimeReconcileError`` if the runtime did not
        converge. How many runtime calls this takes is the implementation's
        decision, not the caller's.
        """
```

**1c.** `ProjectionScope` keeps every field, default, `everything()` and
`inverted()`. Its docstring gains one paragraph: the guarantees it documents
("a single-MCP add stays a single device write", `claim_all_mcp`'s
empty-container premise, delivery-before-declaration ordering) are
`PerDomainRuntimeProjection`'s, and an implementation is free to read the
scope differently — pointing at `EngineRuntimeProjection`.

### 2. `core/skill_center/services/runtime_projections/` (new package)

**2a. `whole_artifact.py` — `WholeArtifactRuntimeProjection`.** No injected
collaborators; works from the plan.

- `validate_plan`: raise `SkillSetRuntimeReconcileError` if any asset is
  `center://` or any retirement has `corpus == "center"`. This is the union of
  today's checks at `:118`, `:332` and `:417`, which were already three
  spellings of one rule.
- `apply`: return early (with today's skip-log) when the scope declares
  nothing; re-assert `validate_plan` from the plan's own assets as
  defence-in-depth; then one `plan.service.sync_runtime(desired_skills=...)`,
  raising if falsy.

**2b. `per_domain.py` — `PerDomainRuntimeProjection`.** Injected with
`SkillsPoolRuntimeProtocol` and `SkillsPoolLayoutRepositoryProtocol`.

- `validate_plan`: `pass`. Per-domain engines support Center; the docstring
  says so rather than leaving an empty body unexplained.
- `apply`: today's `_apply_skill_projection` and `_apply_non_skill_projection`
  (minus the Passport tail), moved with their comments intact, plus
  `_apply_pool_mappings`. The scope gating, the claimed/released guard, the
  skip-logging and the deliver-before-declare ordering all move **verbatim** —
  this is a relocation, not a rewrite.

  The relocation step carries teclaw's arms across too, and drops them only
  once `WholeArtifactRuntimeProjection` is registered to receive teclaw.
  Deleting them at move time would route teclaw onto the Pool/legacy path in
  the interval — a behaviour change smuggled into the group that promises
  none. See `tasks.md` 3.2 / 4.5.

**2c. `registry.py` — `EngineRuntimeProjectionRegistry`.** A dict plus a
default:

```python
def for_engine(self, engine: str) -> EngineRuntimeProjection:
    return self._by_engine.get(engine, self._default)
```

Per-domain is the default rather than a fourth enumerated key, so a new
engine that behaves like `openclaw` needs no registration at all — only an
engine whose runtime genuinely differs does. Logs at INFO which
implementation a projection resolved to, once per projection.

### 3. `core/skill_center/services/bot_runtime_projector.py`

- `__init__` gains `registry: EngineRuntimeProjectionRegistry`; `pool_runtime`
  and `pool_layouts` leave, since only `PerDomainRuntimeProjection` uses them.
- `snapshot_skill_mappings` and `_build_plan` call
  `registry.for_engine(engine).validate_plan(...)` in place of their
  `engine == "teclaw"` checks.
- `_resolve_plan` / `_resolve_cleanup_plan` / `_build_plan` return
  `ResolvedCapabilityPlan`.
- `project` becomes: resolve plan → resolve runtime → `apply` → Passport if
  `scope.mcp`.
- `_apply_skill_projection`, `_apply_non_skill_projection` and
  `_apply_pool_mappings` are **deleted** here, having moved to `per_domain.py`.
- `_apply_passport_projection` is extracted from the tail of the old
  `_apply_non_skill_projection` and stays on the projector.
- `project_mcp_and_cli` collapses into the same four lines as `project`: its
  "MCP/CLI only" behaviour is already exactly what `apply` does when
  `scope.skills` is false, so no second protocol method is needed.
- `project_for_cleanup` keeps its own Center refusal and its explicit
  `service.sync_runtime(...)` — that is a deliberate legacy-synchronizer path,
  not the Pool path, and the docstring says so. It then delegates the MCP half
  through `apply` and calls the Passport. Its comment records that it has no
  production caller.

**Expected end state: `grep -c teclaw bot_runtime_projector.py` → 0.**

### 4. `di/modules/skill_center_module.py`

One `@provider` building the registry from the two implementations —
`PerDomainRuntimeProjection` needs `pool_runtime` / `pool_layouts`, both
already bound. `binder.bind(BotRuntimeProjector, to=BotRuntimeProjector)`
stays; the injector resolves the new constructor key from the provider.

### 5. `core/skill_center/README.md`

`BotRuntimeProjector`'s new dependency is intra-module, so `consumes` is
unchanged. Whether `EngineRuntimeProjection` belongs in `provides` is settled
by running the Rule-22 architecture test, not by guessing.

## Tests

### New — `tests/community/core/skill_center/test_skill_set_management_service.py`

The projector's fakes already live here. `_RuntimeFactoryService` (`:474`)
records `desired_skills` / `mcp_codes` as last-write-wins scalars, which
cannot distinguish one call from four; it already records `deliveries` as a
list, with a comment on why. Follow that idiom: add `runtime_syncs` appended
in `sync_runtime` (`:484`), and `mcp_projections` appended at the top of
`sync_mcp_projection` (`:498`) before it delegates, preserving the
deliver-before-declare composition at `:512-514`. Leave `desired_skills`,
`mcp_codes`, `deliveries`, `collect_calls` and the unrelated stub at `:2865`
untouched.

Every projector test constructs `BotRuntimeProjector(...)` directly, so all of
them need the new `registry=` argument. Add one `_registry()` helper beside
the fakes that builds a real registry over the two real implementations — a
fake registry would test the wiring instead of the behaviour.

1. `test_teclaw_projects_the_whole_artifact_once_per_scope_shape` —
   parametrised over `ProjectionScope(skills=True)`,
   `(mcp=True, claimed_mcp={"x"})`, `(mcp=True, released_mcp={"x"})`,
   `(skills=True, mcp=True, claimed_mcp={"x"})` and `everything()`. Each:
   `len(runtime_syncs) == 1`, `mcp_projections == []`. (criteria 1, 2, 3)
2. `test_teclaw_mcp_only_scope_still_delivers_the_skill_bearing_artifact` —
   `skills=False, mcp=True`: one delivery, carrying the projection's
   `desired_skills`, not empty. (criterion 2)
3. `test_teclaw_still_updates_the_passport_with_identity_coloured_items` —
   one `update_passport` with `mcp_codes`, `mcp_items` carrying
   `identity_mode`, `cli_items`; and an `mcp=False` scope makes no Passport
   call. Guards the extraction. (criterion 4)
4. `test_teclaw_empty_scope_delivers_nothing` — both lists empty, no Passport
   call. (criterion 8)
5. `test_teclaw_failed_delivery_raises_reconcile_error` — `sync_runtime`
   returns `False`: `SkillSetRuntimeReconcileError`, no Passport call.
   (criterion 7)
6. `test_per_domain_engine_keeps_the_scope_split` — an **openclaw** Bot with
   `(mcp=True, claimed_mcp={"x"})`: `runtime_syncs == []`,
   `len(mcp_projections) == 1`, `claimed` still guarded to the projected set.
   The regression guard for criterion 5. (criterion 5)
7. `test_registry_defaults_unknown_engines_to_the_per_domain_projection` — a
   Bot with an engine absent from the registry resolves to
   `PerDomainRuntimeProjection`. Pins the default rule, which is what keeps
   `claude_code` / `aicoding` / `hermes` working without registration.
8. `test_projector_contains_no_engine_identity_test` — assert
   `"teclaw" not in Path(bot_runtime_projector.__file__).read_text()`.
   Blunt, but it is criterion 6 stated exactly, and it fails loudly the first
   time someone reaches for a shortcut. Include the reason in the assertion
   message.

### Existing — must keep passing, modulo the `registry=` argument

- `test_teclaw_v4_rejects_center_without_any_center_runtime_request:2493` —
  the Center refusal must still fire before any runtime call, now from
  `validate_plan`. The direct test of the consolidation.
- `test_teclaw_v4_repo_projection_uses_artifact_runtime_not_pool_mapping:2519`
- `test_non_skill_projection_never_writes_skill_mappings:2552` — openclaw on
  `project_mcp_and_cli`; direct cover that its collapse into the common shape
  changed nothing.
- `tests/community/contracts/test_bot_runtime_projector.py` — Service API
  conformance. Public signatures do not move; if it fails, something leaked.
- `tests/community/core/devices/test_teclaw_device_sync.py` — untouched.

Beyond adding `registry=` to construction, **no existing assertion may
change**. If one does, the move was not a move.

## Validation

```bash
cd src/backend
.venv/bin/python -m pytest \
  tests/community/core/skill_center/ \
  tests/community/contracts/test_bot_runtime_projector.py \
  tests/community/core/devices/test_teclaw_device_sync.py \
  tests/community/core/mcp/ \
  tests/community/architecture/ \
  tests/community/di/ -q
```

`tests/community/di/` is in the list because this change adds a DI provider —
a wiring break would otherwise surface only at runtime.

Then `scripts/ci/python_sast_local.sh` from the repo root.

**Environment.** `uv sync --frozen` cannot run here: `uv.lock` pins
`mirrors.aliyun.com`, which this sandbox answers `403` to. The venv was
populated with `UV_DEFAULT_INDEX=https://pypi.org/simple uv sync` and
`uv.lock` restored from backup — **`uv.lock` must not appear in the diff.**
Run pytest as `.venv/bin/python -m pytest`, never `uv run pytest`.

Baseline before any edit: 123 passed across
`test_skill_set_management_service.py` (77), and `test_teclaw_device_sync.py`
+ `test_bot_runtime_projector.py` + `test_direct_activation_service.py` (46).

## Risks

**This is now a restructure, not a branch.** The per-domain path moves file,
so "per-domain is untouched" stops being provable by reading `git diff` and
becomes provable only by the tests. That is the cost of the design and it is
accepted; the mitigation is that the move is mechanical, no existing
assertion may change, and Group 2 lands the relocation with *no* behaviour
change before Group 3 changes teclaw.

**The Passport extraction.** Highest-severity single step: that block is the
security-relevant fix from the 2026-08-26 spec. Move the body with no edits;
guarded by test 3 and by `test_non_skill_projection_never_writes_skill_mappings`
passing unedited.

**Losing the `get_mcp_detail` catalogue check on teclaw.** An unknown code
currently fails at `sync_mcp_delivery:569-581`. Afterwards the composer's own
collector raises `McpDetailUnavailableError` on the same condition
(`config_compose/services/collector.py:62`), `_compose_and_deliver` converts
it to `{"success": False}`, and a falsy `sync_runtime` still raises. Fail-closed
one layer down; test 5 pins it.

**A teclaw Bot whose MCP config reaches the container only via
`sync_single_mcp`.** Not the case: it ignores `mcp_data` and delivers the
composed artifact, into which `McporterComposer` inlines each MCP's endpoint,
`api_key` and headers — what `sync_service.py:571` means by *"凭据已内联进产物"*.
Covered by `test_mcp_methods_redeliver_the_whole_artifact_and_return_bool`.

**Registry misregistration.** An engine wrongly mapped to whole-artifact would
break it badly. Mitigated by per-domain being the *default* — a mistake
requires an explicit wrong entry, not an omission — and by test 7.
