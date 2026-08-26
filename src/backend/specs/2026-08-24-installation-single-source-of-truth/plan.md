# Plan — Installation as the Single Source of Truth

Paths below are relative to `src/backend/src/agentclaw/community/` unless
noted. Tests live under `src/backend/tests/`.

## Architecture at a glance

![Target architecture](architecture.svg)

Reading order: two symmetric command services (Set-scoped, capability-scoped)
consult one rule book and commit through one unit-of-work repository — the
only writer of activation state; the lazy flush keeps Installation agreeing
with SkillSet configuration; the reader is the only door to "what is active",
and every consumer walks through it.

Principles applied:

- **One writer.** All activation state (Set state, membership, exclusions,
  installations) is written by one UoW repository, invoked by two command
  services.
- **One flush.** `flush_installations` is the only algorithm mapping Set
  configuration onto Installation (skills and MCPs). The materializer dies.
- **One reader.** Flush-then-read, nothing else merges membership in memory.
- **One rule book.** `CapabilityOwnershipPolicy` decides R1–R3 for both
  enforcement sites.
- **Skills ≡ MCPs.** Every MCP operation mirrors its skill counterpart —
  same service, same UoW pattern, same policy, same flush treatment.
- **Flush ≠ runtime projection.** The flush is DB-side only (Set config →
  Installation; the reader's job, never touches a device). The runtime
  projection is DB→engine (Installation-backed state → symlinks / MCP /
  Passport via `BotRuntimeProjector`) and ends every command, synchronously,
  compensated on failure — that trigger stays with the two command services.
- **Fewer layers.** `BotSkillAssetService` (dispatch layer) and
  `LocalSkillStateService` (misnamed, parallel write path) dissolve into
  `DirectActivationService` + `SkillQueryService`.
- **Names say what things do.** Layer vocabulary is deliberate: *services*
  speak intent (`activate_skill`), the *repository* speaks facts
  (`install_skill` — it writes the Installation row), the *reader* answers
  (`active_skill_assets`), the *projector* pushes (`project`). One word per
  concept: **flush** (DB↔DB), **projection** (DB→engine), **install**
  (hold an Installation row).

## Renames — components

| Today | Target | Why |
| --- | --- | --- |
| `SkillSetControlPlaneService` | `SkillSetManagementService` | named by scope: everything done *to a SkillSet* |
| `LocalSkillStateService` | `DirectActivationService` | not "Local" (handles market/repo skills too); parallel to the Set service by scope |
| `SkillSetControlPlaneRepository` (+ protocol, types module) | `CapabilityDesiredStateRepository` | named by aggregate, since both services write through it |
| `BotInstallationReader` (prev. draft) | `BotCapabilityStateReader` | consistent `Capability*` axis |
| `BotSkillAssetService` + `LocalSkillQueryService` | `SkillQueryService` | one query component, one fewer layer |
| `BotRuntimeProjectionReconciler` (+ protocol) | `BotRuntimeProjector` | "reconcile" collided with the flush; named after its verb, pairing with `RuntimeProjectionResolver` |

## Renames — methods and types (existing code)

| Today | Target | Why |
| --- | --- | --- |
| `repair_bot_skillset_installations` | `flush_installations` | one word for one concept — the docs' term is "the lazy flush"; "repair" stays prose. Covers MCPs now, so the old name was wrong twice |
| `BotSkillSetBridge(members, activate, deactivate, …)` | `InstallationFlushPlan(member_skill_ids, skills_to_install, skills_to_uninstall, mcps_to_install, mcps_to_uninstall)` | says what it is (what the flush will do) instead of "bridge"; `activate` as a *field* collided with the service verb |
| `SkillSetDesiredState` / `SkillSetMutation` | `CapabilityDesiredState` / `DesiredStateMutation` | the snapshot spans the whole aggregate (sets, membership, both installations), not one SkillSet |
| `set_active` (repo, takes a bool) | `set_skill_set_active` | on a Capability-named repo, "set_active" no longer says *what* is being activated |
| `activate_mcp_direct` / `deactivate_mcp_direct` (repo) | `install_mcp` / `uninstall_mcp` | the repository speaks facts: these write/delete an Installation row; "direct" as a suffix explained the caller, not the effect |
| `sync` (service, legacy `/skillset/sync` wire) | `legacy_activate` | "sync" says nothing; this is the published legacy additive activate without the MCP permission gate |
| `resources` (service) | `list_resources` | verb prefix, consistent with `list_sets` / `list_skills` / `list_mcps` |
| `mcp_permissions` (service) | `list_mcp_permissions` | same |
| `reconcile` / `reconcile_non_skill_projection` / `reconcile_cleanup` (projector) | `project` / `project_mcp_and_cli` / `project_for_cleanup` | verb matches the component; "non_skill" named what it *excludes* — the new name names what it projects |
| `skill_set_reaches_bot` (module helper) | `_set_belongs_to_bot` (policy-private) | "reaches" was opaque; the question is whether the Set is one of the Bot's (its own, or the platform Default for its engine) — and nothing outside the policy needs it |

Error classes (`SkillSetControlPlane*Error`, `LocalSkill*Error`) keep their
names — accepted naming debt to bound the diff (spec F.15).

## Components and contracts

### 1. Domain model (types)

`core/repository/capability_desired_state_types.py` (renamed):

```python
@dataclass(frozen=True)
class InstallationFlushPlan:
    """What the flush resolved from Set configuration — and applied.

    ``skills_to_install`` = members of active/Default Sets;
    ``skills_to_uninstall`` = members only inactive claims account for
    (an excluded Default-Set member is an inactive claim — exclusion is
    the Default Set's per-Bot deactivation). R3 keeps a capability in at
    most one Set, so claims never truly compete; on historical malformed
    data the flush errs safe and keeps a row an active Set accounts for.
    ``mcps_to_install``/``mcps_to_uninstall`` are the identical split for
    the same Sets' MCP members. ``member_skill_ids`` is the reachability
    union the public listing needs.
    """
    member_skill_ids: frozenset[int]
    skills_to_install: frozenset[int]
    skills_to_uninstall: frozenset[int]
    mcps_to_install: frozenset[str] = frozenset()
    mcps_to_uninstall: frozenset[str] = frozenset()
```

`CapabilityDesiredState` (renamed from `SkillSetDesiredState`) and
`DesiredStateMutation` (renamed from `SkillSetMutation`) keep their shapes.
Unchanged and load-bearing: `RegisteredSkillAsset`,
`RuntimeDesiredState` / `RuntimeProjection`.

### 2. The one writer — `CapabilityDesiredStateRepository`

One unit of work per aggregate. Internally, **each table's SQL has one
owner**: per-table command modules under
`core/repository/implementations/skill_center/tables/` that take the session
as a parameter; the UoW composes them in one transaction. (Session-owning
one-repo-per-table is deliberately rejected: it would reintroduce the
eventual-atomicity bug this UoW exists to prevent — its module docstring
already says so.)

```python
# tables/skill_installations.py — the ONLY code that writes
# ac_bot_skill_installation (mcp_installations.py, default_exclusions.py,
# set_rows.py, set_members.py follow the same shape)
def install(session, *, bot_id, owner_id, env, skill_id) -> bool: ...
def uninstall(session, *, bot_id, owner_id, env, skill_id) -> bool: ...
def installed_ids(session, *, bot_id, owner_id, env) -> set[int]: ...
```

The UoW's command surface. The repository speaks in **facts** — install =
write the Installation row — while services speak in intent (activate):

```python
class CapabilityDesiredStateRepositoryProtocol(Protocol):
    # Sets & membership (existing, renames per table above):
    # create/get/update/delete_set, set_skill_set_active,
    # add_skill, remove_skill, add_mcp, remove_mcp, list_*,
    # snapshot_desired_state / restore_desired_state.

    # Direct capability rows — skill pair is NEW, mirroring the renamed MCP
    # pair, so both command services share one write path. R1 facts are read
    # under this transaction and decided by CapabilityOwnershipPolicy:
    def install_skill(self, *, bot_id, owner_id, skill_id, engine_type=None) -> DesiredStateMutation: ...
    def uninstall_skill(self, *, bot_id, owner_id, skill_id, engine_type=None) -> DesiredStateMutation: ...
    def install_mcp(self, *, bot_id, owner_id, server_code, engine_type=None) -> DesiredStateMutation: ...
    def uninstall_mcp(self, *, bot_id, owner_id, server_code, engine_type=None) -> DesiredStateMutation: ...

    # Default-Set exclusions — NEW commands restoring the dead opt-out
    # (spec E.11): exclusion row + Installation delta in one transaction.
    def exclude_default_skill(self, *, bot_id, owner_id, set_id, skill_id, ...) -> DesiredStateMutation: ...
    def unexclude_default_skill(self, *, bot_id, owner_id, set_id, skill_id, ...) -> DesiredStateMutation: ...
    def exclude_default_mcp(self, *, bot_id, owner_id, set_id, server_code, ...) -> DesiredStateMutation: ...
    def unexclude_default_mcp(self, *, bot_id, owner_id, set_id, server_code, ...) -> DesiredStateMutation: ...

    # deactivate-all — NEW (spec C.6): ordinary Sets -> inactive; delete the
    # Bot's skill Installation rows and Set-claimed MCP rows, one txn.
    def deactivate_all_sets(self, *, bot_id, owner_id, engine_type=None) -> DesiredStateMutation: ...

    # The lazy flush (renamed from repair_bot_skillset_installations;
    # extended to MCPs; excluded members are inactive claims):
    def flush_installations(self, *, bot_id, owner_id, env, ...) -> InstallationFlushPlan: ...
```

`SkillInstallationRepository` (separate session-owning writer of the same
table) is deleted; its callers move to the UoW commands or the reader.

### 3. The reader — `BotCapabilityStateReader` (new)

Protocol in `api/bot_capability_state_reader.py`, implementation in
`core/skill_center/services/bot_capability_state_reader.py`:

```python
class BotCapabilityStateReaderProtocol(Protocol):
    """The one read model for a Bot's active capabilities.

    Installation is the single source of truth; the tables are not
    backfilled, so every read first flushes SkillSet configuration into
    Installation, then answers from Installation alone.
    """

    def flush(self, *, bot: Mapping[str, Any]) -> InstallationFlushPlan:
        """Make Installation agree with SkillSet configuration for one Bot
        (delegates to the UoW's flush_installations)."""

    def active_skill_assets(
        self, *, bot_id: str, owner_id: str,
        bot: Mapping[str, Any] | None = None,
    ) -> tuple[RegisteredSkillAsset, ...]:
        """Flush, then read ac_bot_skill_installation joined to ac_skill."""

    def active_mcp_server_codes(
        self, *, bot_id: str, owner_id: str,
        bot: Mapping[str, Any] | None = None,
    ) -> frozenset[str]:
        """Flush, then read ac_bot_mcp_installation."""
```

Engine scope from the Bot row via `bot_engine_scope` (layout-engine-first
Default-Set precedence); `bot` omitted → loaded via
`bot_repo.get_by_id_and_owner`, missing Bot raises `LocalSkillNotFoundError`.
The flush is DB-side only — the reader never triggers a runtime projection.

Backing read: `SkillRepository.list_bot_active_assets` loses its merge and
becomes a pure Installation→`ac_skill` join, renamed
`list_bot_installed_assets`; after migration its only caller is the reader.

Migrated consumers (all previous merge-readers): the projector (snapshot +
plan), the direct-activation name-conflict guard,
`skills_pool/{mapping_convergence,recovery_service,reconcile_service,
active_aicoding_bridge_repair}.py`, Service-Bot `publish_flow/build_stage.py`,
plus `SkillQueryService` (listing/detail) via `reader.flush`.

### 4. The rule book — `CapabilityOwnershipPolicy` (new)

`core/skill_center/policies/capability_ownership.py`. R1 is a pure decision
(the two direct-activation sites keep their distinct legacy error types);
R2+R3 raise directly, because both membership sites raise the same
`SkillSetControlPlaneConflictError` — one raise site instead of two copies.

```python
"""The one authority for who controls a capability's activation state.

R1 — Set-managed, no direct control. A capability that is a member of ANY
     Set of the Bot's — the Default Set included, excluded or not — is
     activated/deactivated only through Set-level operations (activate/
     deactivate the Set; exclude/un-exclude for Default-Set members).
R2 — Deactivate before joining. A capability holding a direct Installation
     row cannot be added to a Set (checked before R3 — today's precedence).
R3 — One Set per capability: held by ANY Set (ordinary or Default, excluded
     or not) ⇒ cannot be added to another.
Identical for skills and MCPs.
"""

def is_set_managed(
    *, referencing_sets: Sequence[Mapping[str, Any]],
    bot_id: str, owner_id: str,
    engine_type: str | None, default_engine_types: tuple[str, ...],
) -> bool:
    """R1: is some Set of this Bot's managing the capability? True ⇒ direct
    activate/deactivate must be refused. ``referencing_sets`` are the Sets
    holding a membership row for the capability; sets that are not the
    Bot's (another Bot's, another engine's) are filtered out here via
    _set_belongs_to_bot."""

def require_can_join_set(
    *, is_directly_active: bool, is_in_another_set: bool,
) -> None:
    """R2 + R3 for the add-to-Set commands, in today's error precedence:
    raises SkillSetControlPlaneConflictError('RESOURCE_DIRECT_ACTIVE'),
    then ('RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET'); returns when joining
    is allowed. ``is_in_another_set`` covers ordinary AND Default Sets."""

def _set_belongs_to_bot(skill_set, *, bot_id, owner_id,
                        engine_type, default_engine_types) -> bool:
    """Is this Set one of the Bot's — its own (bolt_id, user_id, engine),
    or the ownerless platform Default for one of the Bot's engines?
    (Moved from local_skill_state_service's skill_set_reaches_bot.)"""
```

Enforcement sites:

| Site | Rule | Change |
| --- | --- | --- |
| `DirectActivationService` (skills) | R1 | two near-copy guards collapse into one `is_set_managed` decision; **excluded members now refused too**; each asset kind keeps its legacy error type |
| `DirectActivationService` (MCPs) | R1 | gains the Default-Set half it misses today (spec D.10) |
| UoW `add_skill` / `add_mcp` | R2+R3 | inline checks become facts → `require_can_join_set` |

### 5. Command service A — `SkillSetManagementService` (renamed)

Everything done *to a SkillSet*. Existing shape is kept — ACL via `_bot`,
one UoW mutation, then one synchronous **runtime projection** via
`BotRuntimeProjector` with compensating restore (this is the DB→engine sync,
not the flush); additions and method renames:

```python
class SkillSetManagementService:
    # existing: list_sets/create_set/get_set/update_set/delete_set,
    # list_skills/list_mcps, add_skill/remove_skill, add_mcp/remove_mcp,
    # activate/deactivate,
    # list_resources          (renamed from `resources`)
    # list_mcp_permissions    (renamed from `mcp_permissions`)
    # request_mcp_permissions

    async def legacy_activate(self, *, bot_id, owner_id, actor_id, set_id) -> dict:
        """Renamed from `sync` — the published legacy /skillset/sync wire:
        additive activate without the MCP permission gate."""

    async def deactivate_all(self, *, bot_id, owner_id, user_id) -> dict:
        """Converge the Bot to Default-Set capabilities only — the canonical
        /api/skills/deactivate-all."""

    # Default-Set opt-out, restored (spec E.11). Wire mapping keeps the
    # historical routes: remove_skill/remove_mcp on the Default Set now
    # performs the exclusion instead of raising; add_skill/add_mcp on the
    # Default Set removes an existing exclusion, else SYSTEM_DEFAULT_IMMUTABLE.
```

MCP *direct* activation moves out of this service into
`DirectActivationService` (spec F.12) — the OpenAPI MCP router's injection
changes, routes stay put.

### 6. Command service B — `DirectActivationService` (new; absorbs `LocalSkillStateService`)

Four explicit intent methods — no boolean flag arguments; the skill/MCP and
activate/deactivate twins read identically at every call site:

```python
class DirectActivationService:
    """Activate/deactivate ONE capability (skill or MCP) for a Bot, directly.
    Legal only when no Set governs it (Policy R1). Same authorization, same
    UoW write, same compensation as the Set service — one pattern, two scopes.
    """

    async def activate_skill(self, *, skill_id, bot_id, owner_id, actor_id) -> dict:
        """Resolve the asset (local rows carry their own Bot/owner; shared
        repo assets take the addressed Bot/owner), authorize (owner or MEMBER
        collaborator), enforce R1 (is_set_managed over facts read in the UoW
        transaction) and the runtime-name-conflict guard, then
        UoW install_skill + runtime projection."""

    async def deactivate_skill(self, *, skill_id, bot_id, owner_id, actor_id) -> dict:
        """Mirror of activate_skill; UoW uninstall_skill."""

    async def activate_mcp(self, *, server_code, bot_id, owner_id, actor_id) -> dict:
        """Identical semantics for MCPs (moved from the Set service);
        UoW install_mcp."""

    async def deactivate_mcp(self, *, server_code, bot_id, owner_id, actor_id) -> dict:
        """Mirror; UoW uninstall_mcp."""
```

The mutate-then-project-with-compensation orchestration is extracted from
today's `SkillSetControlPlaneService._mutate/_reconcile` into one internal
helper shared by both command services (a module-private class, not a new
public layer), replacing `LocalSkillStateService`'s hand-rolled rollback.

### 7. The query side — `SkillQueryService` (merges `LocalSkillQueryService` + `BotSkillAssetService` reads)

```python
class SkillQueryService:
    """Answer questions about a Bot's skills. No activation writes.

    list_bot_skills(...)      # paging; reader.flush first (active is a filter)
    get_skill(...)            # detail; active from Installation after flush
    get_content(...)          # SKILL.md via device/repo storage
    get_parameters(...) / replace_parameters(...)   # delegate to the existing
                              # parameter service (the one non-read, kept here
                              # so routers keep a single seam)
    resolve_legacy_skill_id(...)                    # legacy wire references
    """
```

`BotSkillAssetService` is deleted: its `set_active` dispatch goes to
`DirectActivationService`; the OpenAPI skills router, the legacy internal
activate/deactivate routes, `deprecated/skills.py`, and
`service_publication_facade.py` re-point to the two new seams.

### 8. Legacy `SkillSetService` (BFF facade) — delegations only

Method names here are deliberately **unchanged**: `get_active_skills` and
`collect_bot_active_mcps` are published seams (the latter is the
`BotMCPProvider` protocol) with many callers; only their bodies change.

`get_active_skills` delegates to the reader (dict keys preserved: `id`,
`name`, `git_path`, `skill_uuid`, `sc_version_number`) — symlink mappings and
teclaw compose converge with zero call-site edits. `collect_bot_active_mcps`
becomes the one MCP union:

```python
def collect_bot_active_mcps(self, entity_id, bot_id, user_id,
                            entity_type="staff", engine_type=None) -> list[dict]:
    """default policy ∪ installed.
    1. Default-Set projection (unchanged helpers): static engine/template
       defaults + Default-Set rows − exclusions.
    2. reader.active_mcp_server_codes(...) — flushes, then installed codes.
    3. Entries for installed codes not already present, metadata from the
       Bot's ac_skill_set_mcp rows when available, else minimal entry.
    Stops iterating active ordinary Sets."""
```

### 9. Runtime projection — `BotRuntimeProjector` (renamed)

`BotRuntimeProjectionReconciler` → `BotRuntimeProjector`, methods
`reconcile()` → `project()`, `reconcile_non_skill_projection()` →
`project_mcp_and_cli()` (named for what it projects, not what it skips),
`reconcile_cleanup()` → `project_for_cleanup()`; `snapshot_skill_mappings()`
stays. The legacy `SkillSetRuntimeReconciler` alias is deleted. Rationale:
"reconcile" collided with the flush — two reconciliations at different
boundaries — and the settled vocabulary is *flush* (DB↔DB) vs *runtime
projection* (DB→engine); the projector pairs with the pure
`RuntimeProjectionResolver`.

Inputs come from the reader (assets, installed MCP codes) and the converged
`collect_bot_active_mcps`; structure and `RuntimeProjectionResolver` are
untouched. Assets now follow `bot_default_engine_types` Default-Set
precedence (spec H.18).

### Why the command services trigger the runtime projection

The projection's *content* is indeed just what the reader answers — the
command services never compute it. What they own is the *trigger* and the
*failure handling*: the reader is passive and the engine never polls the
database, so after a command commits new desired state, nothing would move
the running Bot until some unrelated flow happened to project. The published
contract of every activation command is synchronous — success means the
runtime converged, failure means desired state was compensated — and that
contract can only be kept by the component that ran the command. The
dependency chain is:

```
command service ──1──▶ UoW write (desired state)
                ──2──▶ BotRuntimeProjector.project()
                              └──▶ reader (flush + read Installation)
                              └──▶ engine adapters (symlinks · MCP · Passport)
```

Two corollaries: right after a command, the flush inside step 2's read is a
no-op — the UoW already wrote Installation eagerly; the flush earns its keep
on reads that arrive over unflushed legacy data. And when the durable
task-queue design lands (out of scope; the `skill_activation_sync_task`
skeleton is its seam), it is exactly this synchronous trigger that moves onto
the queue — the projector and the reader are unchanged by that move.

## Migration scope — which surfaces land on the new components

This refactor migrates **every activation-state read and write** — HTTP and
background — onto the new components. What stays behind is non-activation
content/admin tooling, explicitly listed.

**On the new components after this refactor:**

| Surface | Lands on |
| --- | --- |
| OpenAPI `/bots/{id}/skill-sets/*` (all routes) | `SkillSetManagementService` (already canonical; renamed) |
| OpenAPI `/bots/{id}/skills/*` reads (listing, detail, content, parameters) | `SkillQueryService` |
| OpenAPI `/bots/{id}/skills/{id}/activate\|deactivate` | `DirectActivationService` |
| OpenAPI MCP direct activate/deactivate + installed listing | `DirectActivationService` / reader |
| Internal `/api/skillsets/*` CRUD, membership, MCPs | already canonical; Default-Set remove/add gains the restored exclusion semantics |
| Internal `/api/skills/skillset/{activate,deactivate,sync,active}` | already canonical (`sync` route → `legacy_activate`) |
| Internal `/api/skills/{id}/activate\|deactivate` (legacy wire) | `DirectActivationService` via the legacy-reference resolution in `SkillQueryService` |
| `/api/skills/deactivate-all`, deprecated `/skillset/current` | migrated in Group 9 |
| Background: runtime projector, skills_pool convergence/recovery/reconcile/bridge-repair, Service-Bot build, symlink listener / propagation / provisioning, teclaw config-compose, MCP sync | reader (directly, or via the `get_active_skills` / `collect_bot_active_mcps` delegations) |

**Deliberately not migrated (follow-up; they edit shared Set *content*
upstream of the flush, not per-Bot activation):** the admin content tooling
(`/api/skillsets/admin/init-and-sync`, `/admin/set-default-skills[-fast]`,
`/admin/fix-git-path`) and the Default-Set bootstrap
(`/default/ensure`, `/default/current`), which stay on the legacy
repositories; and the legacy `SkillSetService` BFF display reads
(`get_set_mcp_servers` merge, legacy listing helpers), which remain as a
shrinking facade whose activation facts already come from the reader.

**Dead, not migrated:** `/admin/set-skillset-active` (serves a dead feature,
deprecated soon; the flush tolerates it until deletion) and the data-init
activation step (deleted).

## Work items (→ task groups)

1. **Renames** — both tables above: files, classes, methods, types,
   protocols, DI, tests; zero behavior change.
2. **Flush** — `InstallationFlushPlan` MCP fields; excluded Default-Set
   members become inactive claims; retire the materializer.
3. **Reader** — protocol + impl + DI; repurpose the repository read; migrate
   all merge-readers; bypass-grep guard.
4. **Symlink/compose convergence** — `get_active_skills` delegation.
5. **MCP union** — `collect_bot_active_mcps`.
6. **Ownership policy** — extract R1–R3 (`is_set_managed`,
   `require_can_join_set`); collapse duplicate guards.
7. **DirectActivationService** — absorb `LocalSkillStateService`; move MCP
   direct commands; `install_skill`/`uninstall_skill` UoW commands;
   per-table installation modules; delete `SkillInstallationRepository`;
   shared mutate helper.
8. **Exclusion commands** — UoW exclusion commands + Default-Set
   remove/add wire mapping + parity tests.
9. **Legacy writer retirement** — `deactivate_all`; `/skillset/current`
   from `list_sets`; delete Activator/Switcher; delete the dead data-init
   step mechanically.
10. **SkillQueryService** — merge, dissolve `BotSkillAssetService`,
    flush-consistent detail.
11. **Dead code + docs** — verified deletions; README context boundary;
    adapter CLAUDE.md.
12. **Full validation.**

## Risks and mitigations

- **Excluded-member semantics change** (rows deleted by the flush; direct
  control refused): supersedes the 2026-08-23 decision at the domain owner's
  direction; the listing spec's pinning tests are updated deliberately, and
  the new exclusion commands land in the same change so the opt-out story is
  whole.
- **Flush on more paths**: the flush's read-only fast path keeps steady
  state cheap; watch singlebox E2E timings.
- **Rename fallout**: Group 1 is pure renames validated by the full suite
  before any behavior change lands.
- **Default-Set engine precedence** (layout-engine-first) pinned by a
  projector test.
- **Error-precedence compatibility** (R2 before R3) encoded in
  `require_can_join_set` and pinned by tests.
- **Deletion fallout**: every deletion lands with the migration of its last
  caller.

## Validation

Per work item: the focused pytest files named in tasks.md. Before push:

```bash
cd src/backend
uv run pytest tests/community/core/skill_center tests/community/repository/skill_center \
  tests/community/services tests/community/contracts \
  tests/community/adapters/http/skill_center \
  tests/community/adapters/http/openapi_v1/test_skills_endpoints.py \
  tests/community/adapters/http/openapi_v1/test_skill_sets_endpoints.py \
  tests/community/adapters/http/openapi_v1/test_mcp_endpoints.py \
  tests/community/adapters/http/openapi_v1/test_skills_contract.py
uv run pytest tests/community   # full community suite
```

plus the repo's pre-push SAST/lint gate (`scripts/ci/python_sast_local.sh`).
Anything not runnable in the environment is reported explicitly in the PR's
Validation section.
