# Plan — Installation as the Single Source of Truth

Paths below are relative to `src/backend/src/agentclaw/community/` unless
noted. Tests live under `src/backend/tests/`.

## Architecture at a glance

![Target architecture](architecture.svg)

Reading order: writers (top) consult the ownership rules and commit through
transactional repositories; the lazy flush keeps Installation agreeing with
SkillSet configuration; the reader is the only door to "what is active", and
every consumer walks through it.

Principles applied:

- **One flush.** `repair_bot_skillset_installations` is the only algorithm
  that maps SkillSet desired state onto Installation (now skills *and* MCPs).
  The insert-only materializer is deleted.
- **One reader.** `BotInstallationReader` is the only component that answers
  "what is active for this Bot", always as flush-then-read. Nothing else
  merges membership in memory.
- **One rule book.** `CapabilityOwnershipPolicy` decides who controls a
  capability's activation state; enforcement sites fetch facts and apply its
  decision.
- **One MCP union.** `effective active MCPs = default policy ∪ installed`,
  computed once inside `collect_bot_active_mcps`.
- **No new layers.** Two small new components (reader, policy); the control
  plane, repositories, resolver, and reconciler keep their current roles.

## Components and contracts

### 1. Domain model (types)

`core/repository/skill_set_control_plane_types.py` — the bridge gains the MCP
half; everything else is unchanged:

```python
@dataclass(frozen=True)
class BotSkillSetBridge:
    """What a Bot's Sets imply for Installation, split by desired state.

    ``members``/``activate``/``deactivate`` — skills (unchanged semantics:
    activate = members of active/Default Sets minus Default exclusions;
    deactivate = members only inactive Sets claim; active claim wins).
    ``mcp_activate``/``mcp_deactivate`` — the identical split for the same
    Sets' MCP members (``ac_skill_set_mcp``), Default MCP exclusions applied.
    """
    members: frozenset[int]
    activate: frozenset[int]
    deactivate: frozenset[int]
    mcp_activate: frozenset[str] = frozenset()
    mcp_deactivate: frozenset[str] = frozenset()
```

Unchanged and load-bearing: `SkillSetDesiredState` / `SkillSetMutation`
(command snapshots), `RegisteredSkillAsset` (skills_pool),
`RuntimeDesiredState` / `RuntimeProjection` (runtime resolver inputs/outputs).

### 2. The flush — `SkillSetControlPlaneRepository.repair_bot_skillset_installations`

`core/repository/implementations/skill_center/bot_skillset_installations.py`

```python
def repair_bot_skillset_installations(
    self, *, bot_id: str, owner_id: str, env: str,
    engine_type: str | None = None,
    default_engine_types: tuple[str, ...] | None = None,
) -> BotSkillSetBridge:
    """Make Installation say what SkillSet membership implies — the lazy flush.

    Skills AND MCPs, one algorithm: resolve the bridge over every Set the Bot
    has (ordinary + the always-active Default); insert missing rows for
    ``activate``/``mcp_activate``; delete ``deactivate``/``mcp_deactivate``
    rows; never touch rows no Set explains (direct activations). Read-only
    fast path when Installation already agrees; convergent and idempotent.
    Returns the bridge so callers do not resolve twice.
    """
```

Changes: `_resolve_bridge` also walks `SkillSetMCPServer` members (Default
Sets minus `excluded_mcp_codes`); the write transaction applies the MCP delta
to `BotMCPInstallation` with the same SAVEPOINT-per-row race tolerance as
`_install_one`. `ensure_active_skillset_installations` and
`ActiveSkillSetInstallationMaterializer` are deleted (protocol, impl, DI,
tests) — the repair is the only flush.

### 3. The reader — `BotInstallationReader` (new)

Protocol in `api/bot_installation_reader.py`, implementation in
`core/skill_center/services/bot_installation_reader.py`:

```python
class BotInstallationReaderProtocol(Protocol):
    """The one read model for a Bot's active capabilities.

    Installation is the single source of truth; the tables are not
    backfilled, so every read first flushes SkillSet activation into
    Installation, then answers from Installation alone.
    """

    def flush(self, *, bot: Mapping[str, Any]) -> BotSkillSetBridge:
        """Reconcile Installation with SkillSet desired state for one Bot."""

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

Implementation notes: `BotInstallationReader(bot_repo, control_plane_repo,
skill_repo)`; engine scope derived from the Bot row via `bot_engine_scope`
helpers (`bot_engine_type`, `bot_default_engine_types` — layout engine
first, the already-ratified Default-Set precedence); when `bot` is omitted it
is loaded via `bot_repo.get_by_id_and_owner` and a missing Bot raises
`LocalSkillNotFoundError`.

The backing repository read: `SkillRepository.list_bot_active_assets` loses
its in-memory merge and becomes a pure Installation→`ac_skill` join renamed
`list_bot_installed_assets` (dedup by skill id, no `engine` parameter —
engine scoping happens in the flush). After migration, its only caller is the
reader.

Consumers migrated onto the reader (all previous `list_bot_active_assets`
callers): `BotRuntimeProjectionReconciler` (snapshot + plan; drops its
materializer call), `LocalSkillStateService` (name-conflict guard),
`skills_pool/mapping_convergence.py`, `recovery_service.py`,
`reconcile_service.py`, `active_aicoding_bridge_repair.py`, Service-Bot
`publish_flow/build_stage.py` (flush via reader). Plus, for flush entry-point
unification: `LocalSkillQueryService` (listing) and `BotSkillAssetService`
(detail) call `reader.flush` instead of reaching the repair themselves.

### 4. The rule book — `CapabilityOwnershipPolicy` (new)

`core/skill_center/policies/capability_ownership.py` — pure decisions over
caller-supplied facts; enforcement sites fetch facts in their own
(transactional) context and keep raising their existing wire-stable errors:

```python
"""The one authority for who controls a capability's activation state.

R1 — Set-managed, no direct control. A capability reached by a Set of the
     Bot's is activated/deactivated only through that Set's lifecycle;
     direct activate/deactivate is refused. Default-Set members are
     Set-managed unless excluded — exclusion hands them back to direct
     control. Identical for skills and MCPs.
R2 — Deactivate before joining. A capability holding a direct Installation
     row cannot be added to a Set (checked before R3, preserving today's
     error precedence on the wire).
R3 — One ordinary Set per capability. A capability held by one ordinary Set
     cannot be added to another.
"""

def skill_set_reaches_bot(skill_set, *, bot_id, owner_id,
                          engine_type, default_engine_types) -> bool:
    """Whether a Set governs this Bot's capabilities (moved verbatim from
    local_skill_state_service — ownerless platform Defaults by engine,
    owned Sets by (bolt_id, user_id, engine))."""

def governing_set(
    *, referencing_sets: Sequence[Mapping[str, Any]],
    bot_id: str, owner_id: str,
    engine_type: str | None, default_engine_types: tuple[str, ...],
    is_excluded_from_default: Callable[[Mapping[str, Any]], bool],
) -> Mapping[str, Any] | None:
    """R1: the Set that owns this capability's state, or None = directly
    controllable. ``referencing_sets`` are the Sets holding a membership row
    for the capability; the callable answers the Default-exclusion lookup."""

def membership_conflict(
    *, directly_installed: bool, held_by_other_ordinary_set: bool,
) -> str | None:
    """R2 + R3 for the add-to-Set commands, in today's precedence:
    'RESOURCE_DIRECT_ACTIVE' | 'RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET' | None.
    """
```

Enforcement sites (decision here, raise at the site, errors unchanged):

| Site | Rule | Today | After |
| --- | --- | --- | --- |
| `LocalSkillStateService._reject_skill_set_member` (Local direct) | R1 | own copy | `governing_set` |
| `LocalSkillStateService._require_no_normal_skill_set_membership` (Repo direct) | R1 | second copy | `governing_set` (the two guards collapse into one) |
| `SkillSetControlPlaneRepository.add_skill` | R2+R3 | inline queries | facts → `membership_conflict` |
| `McpSkillSetControlPlaneCommands.add_mcp` | R2+R3 | inline queries | facts → `membership_conflict` |
| `activate_mcp_direct` / `deactivate_mcp_direct` | R1 | ordinary Sets only — **gap** | `governing_set` incl. Default Sets minus MCP exclusions (spec D.10 correction) |

`skill_set_reaches_bot` and `_set_governs` move out of
`local_skill_state_service.py` into the policy; the service keeps thin
raise-wrappers.

### 5. Command side — `SkillSetControlPlaneService` (existing) + `deactivate_all`

Unchanged role: resolve the Bot, check ACL, run one repository mutation, then
one runtime reconcile with compensating restore (`_mutate`). One addition
replaces the legacy switcher's last live job:

```python
# SkillSetControlPlaneService
async def deactivate_all(self, *, bot_id: str, owner_id: str, user_id: str) -> dict:
    """Converge the Bot to Default-Set capabilities only (one command,
    one reconcile) — the canonical /api/skills/deactivate-all."""

# SkillSetControlPlaneRepositoryProtocol
def deactivate_all_sets(
    self, *, bot_id: str, owner_id: str, engine_type: str | None = None,
) -> SkillSetMutation:
    """One transaction: every ordinary Set -> is_active=0; delete the Bot's
    skill Installation rows (direct ones included — 'all skills') and the
    MCP Installation rows those Sets claimed (direct MCPs stay). Snapshot
    for compensation like every other mutation."""
```

The post-mutation reconcile flushes, which re-installs Default-Set members —
so the end state is exactly "Default capabilities only".

### 6. Legacy projections converge — `SkillSetService`

`get_active_skills` keeps its signature for its two callers (symlink
mappings, teclaw config-compose collector) but stops merging:

```python
def get_active_skills(self, user_id=None, bolt_id=None) -> list[dict]:
    """Delegates to BotInstallationReader — flush-then-read, no merge.
    Dict keys preserved for the two consumers: id, name, git_path,
    skill_uuid, sc_version_number."""
```

`get_symlink_mappings` is untouched as a *formatter* (assets in → symlink
mappings out); its no-argument path now sees installation-backed data, which
closes the exclusion drift for every device-sync caller (Bot provisioning,
DeviceActivated listener, propagation, internal sync endpoints) with zero
edits at those call sites.

`collect_bot_active_mcps` keeps its signature (it satisfies `BotMCPProvider`
for mcp sync, bot_profile, caller_identity, compose) and becomes the one MCP
union:

```python
def collect_bot_active_mcps(self, entity_id, bot_id, user_id,
                            entity_type="staff", engine_type=None) -> list[dict]:
    """Effective active MCPs = default policy ∪ installed.
    1. Default-Set projection, unchanged helpers: static engine/template
       defaults + Default-Set DB rows − exclusions (get_set_mcp_servers).
    2. reader.active_mcp_server_codes(...) — flushes, then installed codes.
    3. One entry per installed code not already present, metadata enriched
       from the Bot's ac_skill_set_mcp rows when a membership row exists,
       else {server_code, name: server_code, status: 'ONLINE'}.
    Stops iterating active ordinary Sets."""
```

`collect_bot_mcps` (all Sets, display-only) is unchanged.

### 7. Runtime projection — `BotRuntimeProjectionReconciler` (existing)

Inputs change, structure does not:

```python
skill_assets = reader.active_skill_assets(bot_id=..., owner_id=..., bot=bot)
#   was: materializer + pool_skills.list_bot_active_assets (merge)
installed_mcps = reader.active_mcp_server_codes(...)          # unchanged fact,
effective_mcps = service.collect_bot_active_mcps(...)         # now both come
#   from Installation + policy, so the resolver's union is merely a dedup
```

`RuntimeProjectionResolver` is untouched. Behavior note: assets now follow
`bot_default_engine_types` Default-Set precedence (spec F.13).

## Work items (→ task groups)

1. **Flush unification** — components 1–2: bridge MCP fields, repair MCP
   half, retire the materializer, swap its two call sites.
2. **Reader** — component 3: protocol + impl + DI; repurpose/rename the
   repository read; migrate all merge-readers; guard-grep that nothing
   bypasses the reader.
3. **Symlink/compose convergence** — component 6 (`get_active_skills`
   delegation) + exclusion-honored tests.
4. **MCP union** — component 6 (`collect_bot_active_mcps`) + reconciler
   consistency check.
5. **Ownership policy** — component 4: extract, collapse the duplicate skill
   guards, close the MCP Default-Set gap.
6. **Legacy writer retirement** — component 5: `deactivate_all` command; the
   deprecated `/skillset/current` read answers from `list_sets`; delete
   Activator/Switcher/factories/protocols/DI; delete the dead data-init
   activation step mechanically (call site, method, ctor param, DI arg — no
   migration, dead feature).
7. **Flush-consistent detail** — reader.flush in `BotSkillAssetService.get_skill`
   and `LocalSkillQueryService`.
8. **Dead code + docs** — spec E.11/E.12: verified deletions, README context
   boundary, adapter CLAUDE.md index.

## Risks and mitigations

- **Flush on more paths** (compose, provisioning, pool jobs): the repair's
  read-only fast path keeps steady state to a few indexed reads; writes fire
  only on real drift. Watch singlebox E2E timings.
- **Default-Set engine precedence** (layout engine first) can change which
  Default Set a routed-engine Bot projects — deliberate alignment with the
  listing/control plane; pinned by a reconciler test.
- **`get_active_skills` shape**: consumers read only the preserved keys; a
  focused test per consumer pins the contract.
- **`deactivate-all` semantics** change is confined to an endpoint with no
  frontend callers (verified); the flows test updates deliberately.
- **Error-precedence compatibility** in membership commands is encoded in
  `membership_conflict` and pinned by tests.
- **Deletion fallout**: every deletion lands in the same group as the
  migration of its last caller, keeping each group green in isolation.

## Validation

Per work item: the focused pytest files named in tasks.md. Before push:

```bash
cd src/backend
uv run pytest tests/community/core/skill_center tests/community/repository/skill_center \
  tests/community/services tests/community/contracts \
  tests/community/adapters/http/skill_center \
  tests/community/adapters/http/openapi_v1/test_skills_endpoints.py \
  tests/community/adapters/http/openapi_v1/test_skill_sets_endpoints.py \
  tests/community/adapters/http/openapi_v1/test_skills_contract.py
uv run pytest tests/community   # full community suite
```

plus the repo's pre-push SAST/lint gate (`scripts/ci/python_sast_local.sh`
via the installed hook). Anything not runnable in the environment is reported
explicitly in the PR's Validation section.
