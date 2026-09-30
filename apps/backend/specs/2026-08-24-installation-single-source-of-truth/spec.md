# Installation Is the Single Source of Truth for a Bot's Active Capabilities

## Summary

One fact decides what a Bot actively runs: the Installation tables
(`ac_bot_skill_installation`, `ac_bot_mcp_installation`). SkillSet
configuration (sets, membership, Default-Set exclusions) must be reflected
into Installation — eagerly by the canonical commands, and lazily on read
everywhere else, because the tables are not backfilled. Today that rule is
only half-implemented: some readers still merge SkillSet membership in memory,
two of those merges disagree, two lazy-flush mechanisms overlap, legacy write
paths flip `is_active` without touching Installation, the
activation-ownership rules are scattered across four places, and the product
has lost its only way to opt out of a Default-Set member. This refactor
finishes the cutover: **every reader answers from Installation after one
shared lazy flush; Installation has exactly one writer; skills and MCPs get
identical semantics operation-for-operation; the ownership rules live in one
component; the service layer is two symmetric command services plus one query
service.**

## Key domain rules (settled with the domain owner)

**The Default Set and exclusions.** Exclusion is a *Default-Set-only*
mechanism (`ac_default_skillset_skill_exclusion`,
`ac_default_skillset_mcp_exclusion`; every read is guarded by `is_default`).
The Default Set can neither be deactivated nor have its shared membership
edited per Bot, so the exclusion row is the Default Set's own per-Bot
*deactivation* mechanism. **An excluded member still belongs to the Set — it
is NOT handed back to direct control.** Re-activating an excluded member
means removing the exclusion row, never calling the skill-level API. (This
supersedes the 2026-08-23 listing spec's decision that exclusion returns a
member to direct control, and the code paths built on it.) Ordinary Sets
never use exclusions: opting out there is removing the membership or
deactivating the Set.

**What the lazy flush does** (the existing
`repair_bot_skillset_installations` bridge, renamed `flush_installations`,
stated plainly). For one Bot, it
walks every Set the Bot has — its ordinary Sets plus the always-active
Default Set — and makes Installation agree with what those Sets say:

- A Set that is active (an ordinary Set with `is_active=1`, or the Default
  Set) means: every member must hold an Installation row. The flush inserts
  the missing rows.
- An excluded Default-Set member is deactivated *by the Set*: it must not
  hold a row, and the flush removes one if present.
- An ordinary Set that is inactive means: its members must not hold rows.
  The flush deletes rows only inactive claims account for.
- Installation rows that no Set explains — capabilities activated *directly*
  through the capability-level API — are never touched in either direction.
  Direct commands own those rows.

"Extended to cover MCP membership" means: the identical procedure runs for
the MCP members of those same Sets (`ac_skill_set_mcp` rows) against
`ac_bot_mcp_installation`, exclusions included. Engine/template *default*
MCPs are platform code config, not membership — they never enter the flush
(see criterion 2 for why).

**One Set per capability.** A capability belongs to at most one Set — R3
below enforces it on every add, the Default Set included (excluded or not).
The flush therefore never has to arbitrate between two Sets' claims; where
historical malformed data does present two, it errs safe and keeps a row an
active Set accounts for rather than uninstalling something live.

**Flush vs runtime projection — two different syncs.** The *flush* is
DB-side only: it reconciles the Installation tables with SkillSet
configuration and never touches a device. The *runtime projection* is
DB→engine: pushing Installation-backed desired state to the Bot's running
engine (symlinks / Pool mappings, MCP details, Passport scope) through the
runtime projector (`BotRuntimeProjector`, renamed from
`BotRuntimeProjectionReconciler` — criterion F.15a). Every command ends with a
synchronous, **best-effort** runtime projection. A committed Installation row
is not compensated merely because a device is offline, a managed source is
missing, or an unmanaged active entry cannot safely be replaced: those outcomes
are returned as `PENDING` / `DEGRADED` Runtime observations. Database,
authorization, ownership, offline/retirement, path-safety and duplicate-name
validation remain fail-closed before the write. The reader's flush never
triggers a projection; read paths that need the runtime updated go through the
projector, which reads via the reader.

## Motivation — the inconsistencies as they exist today

1. **Two duplicated in-memory merges, and they disagree.**
   `SkillRepository.list_bot_active_assets` (runtime projection, Skills-Pool
   convergence) and `SkillSetService.get_active_skills` (symlinks, teclaw
   config-compose) both compute "active skills = active-Set members +
   Installation rows"; only the first applies Default-Set exclusions.

2. **Two overlapping lazy-flush mechanisms.** The insert-only
   `ActiveSkillSetInstallationMaterializer` (ordinary Sets only, no Default
   Set, no exclusions, never deletes) runs before runtime reconciles and
   Service-Bot builds; the full `repair_bot_skillset_installations` bridge
   runs before the public listing. Two algorithms, different answers.

3. **Legacy writers bypass Installation.** `SkillSetActivator` and
   `SkillSetSwitcher` write `ac_skill_set.is_active` and sync symlinks but
   never touch Installation. Remaining callers: `/api/skills/deactivate-all`,
   the deprecated `/api/skills/skillset/current` read, and the dead
   data-init feature (deleted, not migrated — see Out of Scope).

4. **MCP activation is still decided by a merge.** The runtime reconciler
   unions `list_installed_mcps` (Installation) with `collect_bot_active_mcps`
   (a legacy merge re-deriving active-Set MCP membership), so the merge — not
   Installation — is what actually keeps legacy-activated Sets' MCPs alive.

5. **Listing and detail disagree.** The public listing flushes before reading
   `active`; the skill detail reads Installation without flushing.

6. **The activation-ownership rules are scattered and drifting.** "A
   Set-managed skill cannot be directly toggled" exists as two near-copies in
   `LocalSkillStateService`; "a directly-active capability cannot join a Set"
   and "one ordinary Set per capability" live inside the repository commands;
   the MCP direct commands carry a third variant that misses the Default-Set
   half of the rule entirely.

7. **The Default-Set opt-out is dead on the canonical surface.** The control
   plane refuses Default Sets (`SYSTEM_DEFAULT_IMMUTABLE`) on membership
   commands, and the legacy service branches that wrote exclusion rows have
   no remaining callers — so no live command can exclude (or un-exclude) a
   Default-Set member.

8. **The service layer is asymmetric and over-layered.** Set commands live in
   `SkillSetControlPlaneService`, but MCP *direct* activation lives there too
   while skill direct activation lives in `LocalSkillStateService` (misnamed:
   it handles local *and* market/repo skills, and writes through a different
   repository with hand-rolled compensation). `BotSkillAssetService` adds a
   dispatch layer between the routers and the state service.

## User Stories

- As a maintainer, I want activation state decided in exactly one place, and
  written by exactly one component, so drift is structurally impossible.
- As a Bot owner, when I exclude a skill from the Default Set it is inactive
  on every surface, and I can exclude/un-exclude through a live API again.
- As an API caller, the listing, the detail, and the running Bot agree on
  `active` after any single read.
- As a platform engineer, every MCP operation behaves exactly like its skill
  counterpart, so one mental model covers both.
- As a maintainer, the "who controls this capability" rules are readable in
  one file, and the service layer names say what each component does.

## Acceptance Criteria

### A. Single source of truth for reads

1. Every read of "which skills are active for this Bot" answers from
   `ac_bot_skill_installation` **after the lazy flush**; no reader merges
   SkillSet membership in memory. In-scope readers: the runtime projection
   (snapshot and plan), Skills-Pool convergence/recovery/reconcile/
   bridge-repair, symlink-mapping generation (provisioning, DeviceActivated
   listener, propagation, internal sync endpoints), the teclaw config-compose
   collector, the public listing and detail, and the direct-activation
   name-conflict guard.
2. Set-derived MCP activation likewise answers from `ac_bot_mcp_installation`
   after the flush. **Materialization is symmetric for everything that is Set
   membership** — skills and MCPs, ordinary and Default Sets alike. The one
   thing not materialized is the engine/template default-MCP *code config*
   (`_defaults.py` + template/ext context), which is not Set membership and
   has no skill counterpart. It stays a policy input, for cause: it is
   platform-wide (a config change must reach every Bot at once, not via
   per-Bot backfills), it is context-dependent at read time
   (template_type/ext_info), and materialized rows could not be told apart
   from direct activations when an entry leaves the config — that would need
   a provenance column and a config-diff reconciler (considered, rejected as
   over-design). Effective MCPs = `default policy ∪ installed`, computed in
   one place.

### B. One lazy flush

3. Exactly one flush algorithm reconciles Installation with SkillSet
   configuration — the bridge, extended to MCP membership, with the contract
   in *Key domain rules* (including: excluded Default-Set members are
   inactive claims and lose their rows).
4. The insert-only materializer is retired; its call sites use the one flush.
5. The flush is convergent and idempotent, with a read-only fast path when
   Installation already agrees.

### C. One writer

6. After this refactor the activation state (Installation rows, `is_active`,
   membership, exclusions) has **exactly one writer**: the desired-state
   unit-of-work repository, invoked by the two command services. The writers
   removed to get there: `SkillSetActivator`/`SkillSetSwitcher` (serving
   `/api/skills/deactivate-all` and the deprecated `/skillset/current` read —
   both re-pointed at the canonical services) and the dead data-init
   activation step (deleted mechanically, not migrated). Remaining legacy
   metadata/bootstrap writers (`ensure_default_skill_set`, the
   `/admin/init-and-sync` and `/admin/set-default-skills*` content tooling)
   edit shared Set *content*, which the flush treats as upstream
   configuration; migrating them onto the UoW is mechanical follow-up work,
   recorded in Out of Scope.
7. One tolerated outlier: `/api/skillsets/admin/set-skillset-active` flips
   `is_active` via a direct repository write. **It exists only to serve a
   dead feature, and it will be deprecated very soon** — it is env+id
   addressed with no Bot/owner scope, so it cannot express the command shape.
   Until it is deleted, its writes are treated like a manual DB fix: drift
   the lazy flush repairs on the next read. (This tolerance is also why the
   flush must exist on the read side at all.)

### D. Ownership rules in one component, skills ≡ MCPs

8. One component (`CapabilityOwnershipPolicy`) states and decides the three
   rules, identically for skills and MCPs:
   - **R1 — Set-managed, no direct control.** A capability that is a member
     of *any* Set reaching the Bot — the Default Set included, **excluded or
     not** — is activated and deactivated only through Set-level operations
     (activate/deactivate the Set; exclude/un-exclude for Default-Set
     members). Direct activate/deactivate is refused.
   - **R2 — deactivate before joining.** A capability holding a direct
     Installation row cannot be added to a Set.
   - **R3 — one Set per capability.** A capability held by *any* Set —
     ordinary or Default, excluded or not — cannot be added to another Set.
9. Both enforcement sites consume the policy and nothing else re-implements
   it: the direct-activation service and the membership/exclusion commands in
   the UoW repository. Existing error types stay on the wire.
10. **Operation-for-operation parity.** Every MCP operation has semantics
    identical to its skill counterpart — direct activate/deactivate,
    add-to-Set / remove-from-Set, Default-Set exclude/un-exclude, and flush
    treatment — pinned by pairwise tests. In particular the MCP direct
    commands gain the Default-Set half of R1 they miss today.

### E. Restored Default-Set opt-out

11. Set-scoped exclusion commands exist again, on the canonical service, for
    skills and MCPs symmetrically: removing a Default-Set member writes the
    exclusion row plus the Installation delta in one transaction and
    reconciles the runtime (restoring the historical wire semantics of
    `DELETE /{set_id}/skills/{skill_id}` on the Default Set); adding a
    Default-Set member back removes the exclusion row the same way (a
    non-excluded add still answers `SYSTEM_DEFAULT_IMMUTABLE`).

### F. Component architecture and naming

12. Two symmetric command services over the same desired-state aggregate,
    named by scope:
    - **`SkillSetManagementService`** (renamed from
      `SkillSetControlPlaneService`) — everything done *to a SkillSet*:
      CRUD, activate/deactivate/sync/deactivate-all, skill and MCP
      membership, Default-Set exclusions, plus its Set-scoped reads.
    - **`DirectActivationService`** (absorbs `LocalSkillStateService`, which
      is deleted — the name was wrong: it handled local *and* market/repo
      skills) — activate/deactivate one capability (skill **or** MCP)
      directly; legal only when no Set governs it (Policy R1). The MCP
      direct commands move here from the Set service.
13. Both command services write through **one unit-of-work repository per
    aggregate**: `CapabilityDesiredStateRepository` (renamed from
    `SkillSetControlPlaneRepository`). Direct skill activation becomes a UoW
    command exactly like MCP direct already is (deleting
    `SkillInstallationRepository`'s separate session-owning write path and
    `LocalSkillStateService`'s hand-rolled compensation). Within the
    repository, **each table's SQL has exactly one owner** — per-table
    command modules that take the session as a parameter — and the UoW
    composes them in one transaction. (Strict one-*session-owning*-repository
    -per-table is deliberately not the design: it would reintroduce the
    eventual-atomicity bug the UoW exists to prevent.)
14. `BotSkillAssetService` is dissolved: its activate/deactivate dispatch
    layer is removed (routers call `DirectActivationService`), and its reads
    merge with `LocalSkillQueryService` into one **`SkillQueryService`**
    (listing, detail with flush-consistent `active`, content, parameters —
    `replace_parameters` delegating to the existing parameter service —
    and legacy reference resolution).
15. The read model is **`BotCapabilityStateReader`** (flush-then-read; the
    only door to "what is active"), consistent with the `Capability*` naming
    axis. Error class names are left as-is to bound the diff (recorded as
    accepted naming debt).
15a. The DB→engine component is renamed **`BotRuntimeProjector`** (from
    `BotRuntimeProjectionReconciler`; `reconcile()` → `project()`).
    "Reconcile" collided with the flush — the codebase has two
    reconciliations at different boundaries, and the established vocabulary
    is *flush* (DB↔DB) vs *runtime projection* (DB→engine) — so the
    component is named after its verb, pairing with the pure
    `RuntimeProjectionResolver`: the resolver resolves the snapshot, the
    projector applies it to the engine.

### G. Cohesion and dead code

16. Dead code named by the 2026-08-23 listing spec is removed
    (`includes_default_skill_member`, upload-time default-set membership,
    inert `local://` filters), plus legacy `SkillSetService` methods with no
    remaining callers (verified by search: `add_skills_to_set`,
    `remove_skill_from_set`, `add_mcp_to_skill_set`,
    `remove_mcp_from_skill_set`, the `get_active_skills` merge body).
17. Each surviving component has a one-paragraph docstring stating its single
    concern; `core/skill_center/README.md`'s context boundary reflects the
    new component list.

### H. Behavior

18. Externally observable behavior is preserved, except these deliberate
    corrections:
    - Default-Set exclusions are honored by the symlink and config-compose
      paths.
    - Excluded Default-Set members are no longer directly controllable, and
      the flush removes any stray Installation rows for them (supersedes the
      2026-08-23 "left alone in both directions" decision).
    - The Default-Set exclude/un-exclude commands are live again (criterion
      11) — today they are unreachable.
    - Direct MCP activate/deactivate refuses membership in *any* reaching
      Set, Default Set included, excluded or not (parity with skills).
    - Adding a Default-Set member (excluded or not) to an ordinary Set is
      refused — R3 previously covered only ordinary Sets.
    - The runtime projection selects Default Sets with the
      layout-engine-first precedence (`bot_default_engine_types`) the listing
      and commands already use.
    - `deactivate-all` converges to "Default-Set capabilities only" through
      desired state instead of deleting device symlinks imperatively.
    - Reads on flush-covered paths may perform the repair write; repeat reads
      are no-ops.
    - The dead data-init activation step no longer runs at Bot creation (dead
      feature; explicitly no behavior guarantee).

## Out of Scope

- **Bot data-init** (`data_init_service`): dead feature per the domain owner;
  its activation step and activator wiring are deleted mechanically so the
  codebase imports — nothing about it is migrated, tested, or guaranteed.
- Concurrency and durable serialization (`skill_activation_sync_task` skeleton
  stays as it is).
- Backfilling the Installation tables.
- `center://` membership resolution (pre-existing, per the 2026-08-23 spec).
- Migrating the legacy metadata/bootstrap writers (`ensure_default_skill_set`,
  `/admin/init-and-sync`, `/admin/set-default-skills*`, `fix-git-path`) onto
  the UoW — mechanical follow-up; they edit shared Set content, not per-Bot
  activation state.
- Renaming error classes (`SkillSetControlPlane*Error` etc.) — accepted
  naming debt to bound the diff.
- The MCP default policy config and the Default-Set BFF display merges —
  shapes unchanged; only who computes *activation* changes.
- HTTP response schemas, envelopes, authorization, frontend contracts, and
  the device/dispatcher layer.
