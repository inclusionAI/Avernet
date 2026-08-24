# Installation Is the Single Source of Truth for a Bot's Active Capabilities

## Summary

One fact decides what a Bot actively runs: the Installation tables
(`ac_bot_skill_installation`, `ac_bot_mcp_installation`). SkillSet activation
is *configuration*; it must be reflected into Installation — eagerly by the
canonical commands (already true), and lazily on read everywhere else, because
the tables are not backfilled. Today that rule is only half-implemented: some
readers still merge SkillSet membership in memory to decide the active state,
two of those merges disagree with each other, two lazy-flush mechanisms
overlap, legacy write paths flip `is_active` without touching Installation,
and the activation-ownership rules ("a Set-managed skill cannot be directly
toggled", "an active skill cannot join a Set") are scattered across four
places. This refactor finishes the cutover: **every reader answers from
Installation after one shared lazy flush; every activation writer writes
Installation; the ownership rules live in one component; each concern lives in
exactly one place.**

## Key domain rules (confirmed against current code)

These two rules anchor the whole design; both were verified in the codebase.

**The Default Set and exclusions.** Exclusion is a *Default-Set-only*
mechanism. The tables are literally `ac_default_skillset_skill_exclusion` and
`ac_default_skillset_mcp_exclusion`, and every read of them is guarded by
`is_default`. It exists because the Default Set can neither be deactivated
(`SYSTEM_DEFAULT_IMMUTABLE`) nor have its shared membership edited per Bot —
so the per-Bot opt-out from a Default-Set member is an exclusion row. Ordinary
Sets never use exclusions: opting out there means removing the membership or
deactivating the Set. An excluded Default-Set member returns to *direct*
control (the owner may activate/deactivate it individually).

**What the lazy flush does** (the existing
`repair_bot_skillset_installations` bridge, stated plainly). For one Bot, it
walks every Set the Bot has — its ordinary Sets plus the always-active
Default Set — and makes Installation agree with what those Sets say:

- A Set that is active (an ordinary Set with `is_active=1`, or the Default
  Set) means: every member skill must hold an Installation row. The flush
  inserts the missing rows. For the Default Set only, members the owner
  excluded are skipped.
- An ordinary Set that is inactive means: its member skills must *not* hold
  Installation rows. The flush deletes rows that only inactive Sets account
  for. If some active Set also provides the same skill, the active claim wins
  and the row stays.
- Installation rows that no Set explains — skills the user activated
  *directly* through the skill-level API — are never touched in either
  direction. Direct activate/deactivate commands own those rows.

"Extended to cover MCP membership" means: the identical procedure runs for
the MCP members of those same Sets (`ac_skill_set_mcp` rows) against
`ac_bot_mcp_installation`. Activating a Set gives its MCPs rows; an inactive
Set's MCPs lose theirs; directly-installed MCPs are untouched; Default-Set
MCP exclusions are honored. (Engine/template *default* MCPs are policy, not
membership — they never enter the flush.)

## Motivation — the inconsistencies as they exist today

1. **Two duplicated in-memory merges, and they disagree.**
   `SkillRepository.list_bot_active_assets` (feeds the runtime projection and
   every Skills-Pool convergence path) and `SkillSetService.get_active_skills`
   (feeds symlink generation and the teclaw config-compose collector) both
   compute "active skills = active-Set members + Installation rows". The first
   applies Default-Set exclusions; the second does not. The same Bot can be
   projected differently depending on which path ran last.

2. **Two overlapping lazy-flush mechanisms.**
   `ActiveSkillSetInstallationMaterializer` / `ensure_active_skillset_installations`
   (insert-only, ordinary Sets only, ignores the Default Set and exclusions)
   runs before runtime reconciles and Service-Bot builds, while
   `repair_bot_skillset_installations` (full bridge: Default Set, exclusions,
   inserts *and* deletes, one transaction) runs before the public listing.
   Two algorithms for one job, with different answers.

3. **Legacy writers bypass Installation.** `SkillSetActivator` and
   `SkillSetSwitcher` write `ac_skill_set.is_active` and sync symlinks but
   never touch Installation. Their remaining callers are the internal
   `/api/skills/deactivate-all` endpoint, the deprecated
   `/api/skills/skillset/current` read, and the dead data-init feature (see
   Out of Scope — deleted, not migrated).

4. **MCP activation is still decided by a merge.** The runtime reconciler
   unions `list_installed_mcps` (Installation) with
   `collect_bot_active_mcps` (a legacy merge that re-derives active-Set MCP
   membership plus engine defaults). The merge — not Installation — is what
   actually keeps legacy-activated Sets' MCPs alive, so Installation is not
   yet authoritative for MCPs.

5. **Listing and detail disagree.** `GET /openapi/v1/bots/{bot_id}/skills`
   flushes before reading `active`; `GET .../skills/{skill_id}` reads
   Installation without flushing. The same skill can be `active` in the list
   and inactive in its detail until something else flushes.

6. **The activation-ownership rules are scattered.** "A Set-managed skill
   cannot be directly activated/deactivated" lives in
   `LocalSkillStateService._reject_skill_set_member` *and*
   `_require_no_normal_skill_set_membership` (two near-copies); "a
   directly-active capability cannot join a Set" and "one ordinary Set per
   capability" live inside the repository commands (`add_skill`, `add_mcp`);
   the MCP direct commands carry a third variant that *misses the Default-Set
   half of the rule*. Four places, three phrasings, one gap.

## User Stories

- As a maintainer, I want activation state decided in exactly one place, so I
  never have to keep two merge implementations in sync again.
- As a Bot owner, when I exclude a skill from the Default Set, it must stop
  being projected on *every* surface — symlinks and teclaw compose included.
- As an API caller, the listing, the skill detail, and the running Bot must
  agree on `active` after any single read.
- As a platform engineer, MCPs a SkillSet brings to a Bot must follow the same
  Installation fact as skills, so one reconcile path serves both.
- As a maintainer, I want the "who controls this capability" rules readable in
  one file and enforced from that one place, so a new command path cannot
  forget one of them.

## Acceptance Criteria

### A. Single source of truth for reads

1. Every read of "which skills are active for this Bot" answers from
   `ac_bot_skill_installation` **after the lazy flush**; no reader merges
   SkillSet membership in memory to produce the answer. In-scope readers:
   - the runtime projection (reconciler snapshot and plan),
   - Skills-Pool convergence / recovery / reconcile / bridge-repair services,
   - symlink-mapping generation (`get_symlink_mappings` without an explicit
     `desired_skills` argument: Bot provisioning, the DeviceActivated
     listener, propagation, the internal sync endpoints),
   - the teclaw config-compose skill collector,
   - the public listing (already true) and the public skill detail (`active`),
   - the direct-activation runtime-name-conflict guard.
2. Set-derived MCP activation likewise answers from `ac_bot_mcp_installation`
   after the flush. Engine/template default MCPs (minus exclusions) remain a
   *policy* input — never materialized into Installation — and the effective
   MCP set is exactly `default policy ∪ installed`, computed in one place.

### B. One lazy flush

3. Exactly one flush algorithm reconciles Installation with SkillSet desired
   state for one Bot — the existing bridge (`repair_bot_skillset_installations`),
   extended to MCP membership, with the contract stated in *Key domain rules*
   above.
4. The insert-only materializer is retired; its call sites (runtime
   reconcile, Service-Bot build stage) use the one flush.
5. The flush is convergent and idempotent, with a read-only fast path when
   Installation already agrees (unchanged from today's repair).

### C. Writers

6. The remaining live Set-activation writers go through the canonical control
   plane: `/api/skills/deactivate-all` is reimplemented as a canonical
   command; the deprecated `/api/skills/skillset/current` read stops using
   the switcher. `SkillSetActivator`, `SkillSetSwitcher`, their factories,
   Service-API protocols, and DI wiring are deleted. The dead data-init
   activation step is deleted with them (mechanically, so the codebase still
   imports — see Out of Scope).
7. One tolerated outlier: `/api/skillsets/admin/set-skillset-active`, a
   pre-release ops-only data-fix endpoint (already marked deprecated in code)
   that flips `is_active` directly via the repository. It is env+id
   addressed, with no Bot/owner scope, so it cannot express the control-plane
   command shape — and it is slated for deletion. Rather than teach a dying
   admin tool the full command path, its writes are treated like a manual DB
   fix: legacy drift the lazy flush repairs on the next read. This tolerance
   is exactly why the flush must exist on the read side even once all product
   paths write eagerly.

### D. Activation-ownership rules in one component

8. One dedicated component (`CapabilityOwnershipPolicy`) states and decides
   the three rules, identically for skills and MCPs:
   - **R1 — Set-managed, no direct control.** A capability reached by a Set
     of the Bot's is activated/deactivated only through that Set's lifecycle;
     direct activate/deactivate is refused. Default-Set members are
     Set-managed *unless excluded* — exclusion hands them back to direct
     control.
   - **R2 — deactivate before joining.** A capability holding a direct
     Installation row cannot be added to a Set.
   - **R3 — one ordinary Set per capability.** A capability held by one
     ordinary Set cannot be added to another.
9. Both enforcement sites consume the policy and nothing else re-implements
   it: the direct-activation commands (`LocalSkillStateService` for skills,
   the MCP direct commands) and the membership commands (`add_skill` /
   `add_mcp` in the control-plane repository). Existing error types
   (`SkillSetManagedResourceError`, `RESOURCE_DIRECT_ACTIVE`,
   `RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET`, …) are preserved on the wire.
10. Deliberate correction: the MCP direct commands today check only
    *ordinary* membership. They gain the Default-Set half of R1 (minus
    exclusions) — otherwise, once the flush covers MCPs, a direct deactivate
    of a Default-Set MCP would report success that the next flush silently
    undoes, the same defect the skill side already fixed.

### E. Cohesion and dead code

11. Dead code named by the 2026-08-23 listing spec is removed:
    `installation_compatibility.includes_default_skill_member`, the upload
    service's `_ensure_default_set` / `_ensure_default_set_membership`, and
    the inert `local://` Default-Set filters. Legacy `SkillSetService`
    methods with no remaining callers after this change (verified by search,
    e.g. `add_skills_to_set`, `remove_skill_from_set`, `add_mcp_to_skill_set`,
    `remove_mcp_from_skill_set`, `get_active_skills`'s merge body) are
    removed.
12. Each surviving component has a one-paragraph docstring stating its single
    concern, and `core/skill_center/README.md`'s context boundary reflects
    the new component list.

### F. Behavior

13. Externally observable behavior is preserved, except these deliberate
    corrections:
    - Default-Set exclusions are now honored by the symlink and
      config-compose paths (completing the 2026-08-23 decision).
    - The runtime projection selects Default Sets with the same
      layout-engine-first precedence the listing and control plane already
      use (`bot_default_engine_types`), instead of the persisted engine only.
    - Direct MCP activate/deactivate refuses Default-Set-managed MCPs unless
      excluded (criterion 10).
    - `deactivate-all` converges to "Default-Set capabilities only" through
      desired state instead of deleting device symlinks imperatively.
    - Reads on flush-covered paths may now perform the repair write; repeat
      reads are no-ops (criterion 5).
    - The dead data-init activation step no longer runs at Bot creation
      (dead feature; explicitly no behavior guarantee).

## Out of Scope

- **Bot data-init** (`data_init_service`). Dead feature per the domain owner,
  to be deprecated; it is deliberately *not* migrated to the control plane.
  Its `_activate_and_sync_skill_sets` step and activator wiring are deleted
  mechanically (so the module still imports); no behavior of data-init is
  designed for, tested, or guaranteed here.
- Concurrency and durable serialization. The compensating-restore model and
  the `skill_activation_sync_task` skeleton stay exactly as they are.
- Backfilling the Installation tables.
- `center://` membership resolution (`skill_uuid` is never populated by
  membership writers — pre-existing, per the 2026-08-23 spec).
- The MCP default *policy* (engine/template defaults, `_defaults.py`) and the
  Default-Set BFF projections (`get_set_mcp_servers` display merge) — their
  shapes are unchanged; only who computes *activation* changes.
- HTTP response schemas, envelopes, authorization, and frontend contracts.
- The device/dispatcher layer (`DeviceSync`/`DeviceFilesystem` routing).
