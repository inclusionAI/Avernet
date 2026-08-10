# Backend — Repository Consolidation into `core/repository/`

## Summary

Every repository implementation in the backend currently lives in the plugin
layer (`agentclaw/community/plugins/`), and every repository Protocol lives
somewhere inside the domain package it serves, under nine different filename
conventions. No implementation names its Protocol as a base class, so the
contract-to-implementation link exists only inside a DI `binder.bind()` call and
in prose.

This feature moves all 43 repository implementation modules (44 classes) and all
46 repository Protocols into one package —
`agentclaw/community/core/repository/`, split into `protocols/` and
`implementations/` and grouped into 11 domain subdirectories — and makes the contract enforceable at runtime: every
Protocol member becomes `@abstractmethod`, every implementation declares its
Protocol(s) as a base, and an implementation that omits a member fails at
construction with a `TypeError` naming the missing member.

36 of those modules come from the plugin layer, which is where the misplacement
this feature corrects actually lives. The remaining 7 already sit inside `core/`
but under four more competing conventions; they are pulled in so that the new
package is the *single* answer rather than the most common one.

No repository body changes behaviour: same queries, same return shapes, same
error cases.

## Motivation

### Repositories are not plugins

`plugin_api/README.md` and Rule 20 of `AGENTS.md` define the plugin layer as the
kernel's outbound interface to *swappable* capabilities, and require every
plugin contract to carry paired local and prod implementations. Repositories
fail that test on both counts:

- Each has exactly one implementation. The DI binding is a single unconditional
  `binder.bind(Protocol, to=Impl, scope=singleton)` in a profile-agnostic domain
  module (`di/modules/<domain>_module.py`) — not a per-profile column. The
  infrastructure columns under `di/modules/infrastructure/{community,test,singlebox}/`
  cover plugin_api concerns only (cache, database, identity, secret, tracer, …);
  none of them re-binds a repository.
- The only per-profile difference is the `DatabasePlugin` injected into the
  constructor — one layer below, where the swap genuinely belongs.

The placement is a fossil. Several repository docstrings still record the
collapse of raw-SQL/ORM twins into one body, e.g. `plugins/quality_repository.py`:

> Unified ORM repo (one body, ZDAS + SQLite). `@inject` ctor takes the bound
> `DatabasePlugin`; prod vs test differ only by which `DatabasePlugin` is bound.

`plugins/README.md` still describes the directory as a skeleton whose files
"contain empty class stubs" — it has never been updated to acknowledge that 36
repository bodies moved in.

### Three costs today

1. **The contract link is invisible to tooling.** `class BotRepository:` in
   `plugins/bot_repository.py` has no base class. IDE "go to implementation" on
   the `BotRepository` Protocol finds nothing. This is what triggered the work.

2. **Contract drift is undetectable, and has already happened.** There is no
   mypy/pyright in CI, and although 31 of the 39 contracts are decorated
   `@runtime_checkable` (7 are bare `Protocol`s and one is an ABC), no `isinstance` check is ever run against them. The
   guard suite has no conformance test for repository protocols — Rule 25's
   `test_protocol_contracts.py` only discovers Protocols that subclass `Plugin`,
   which none of these do.

   The investigation found live drift: the `SkillRepository` Protocol
   (`core/skill_center/services/repositories.py`) declares
   `add_default_skill_exclusion`, `remove_default_skill_exclusion`, and
   `remove_all_default_skill_exclusions`, and the bound implementation
   (`plugins/skill_repository.py`, `class SkillRepository`) implements none of
   them. All three are duplicated on the sibling `SkillSetRepository` Protocol,
   which *is* implemented (`plugins/skill_repository.py:1927` sits inside
   `class SkillSetRepository`, which starts at line 1157), and every caller
   reaches them through `skill_set_repo`. Any caller that resolved the
   `SkillRepository` Protocol and called one of them would raise
   `AttributeError` in production — exactly the failure mode this feature is
   meant to make impossible.

   **This is not a historical accident that has stopped happening.** The spec
   originally recorded two drifted members. On the `dev` rebase of 2026-08-08,
   `remove_all_default_skill_exclusions` was added to *both* Protocols by
   `feat(backend): integrate REL20260806 bot and Skills updates (#894)` and
   implemented on only one — reproducing the same copy/paste exactly, and taking
   the count from two to three. Nothing in CI noticed. The drift rate is the
   argument for R2: a contract that cannot be checked will keep drifting at
   whatever rate the team edits it.

3. **There is no single correct answer for new code.** Protocols are spread
   across nine filename conventions:

   | Shape | Example |
   | --- | --- |
   | `<domain>/repository/protocol.py` | `core/bot_management/repository/protocol.py` |
   | `<domain>/repository.py` | `core/access/repository.py` |
   | `<domain>/repository/<name>_repository.py` | `core/expert_chat/repository/expert_chat_repository.py` |
   | `<domain>/repository/<name>_repository_protocol.py` | `core/bot_management/repository/template_repository_protocol.py` |
   | `<domain>/repository_protocol.py` | `core/harness/repository_protocol.py` |
   | `<domain>/repositories.py` | `core/quality/repositories.py` |
   | `<domain>/services/repositories.py` | `core/skill_center/services/repositories.py` |
   | inline in a service module | `core/skill_center/services/skill_propagation_service.py` |
   | inline in a domain module | `core/skills_pool/{ports,quarantine,rollout_repository}.py` |

   One Protocol (`LocalSkillCleanupRepository`) is not in `core/` at all — it
   sits in `plugin_api/local_skill_cleanup.py`, though it does not subclass
   `Plugin` and has a single implementation.

   The *implementations* disagree just as badly. Most are in `plugins/`, but
   seven already live in `core/` under four further conventions
   (`core/<domain>/repository/<name>_repository.py`,
   `core/<domain>/repository/{open,product}.py`,
   `core/<domain>/repositories/<name>_repo.py`). The tree even contains a
   written convention that contradicts the dominant practice —
   `core/devices/repository/__init__.py` states "业务 Repository 放在
   `core/<module>/` 内部，不放 `plugin_api/`" while the implementation it
   documents sits in `plugins/`.

## Classification

A candidate is a repository iff it **injects `DatabasePlugin` and does ORM
work** *and* **is independently bound in a DI module**. All 44 non-`__init__`
modules at the top level of `plugins/` were classified against that test, plus
the nine repository-shaped modules already resident in `core/`.

### Plugin-layer repositories — 36 modules, 37 classes (move to `implementations/`)

| Implementation module (`plugins/…`) | Class(es) | Protocol | Protocol source today |
| --- | --- | --- | --- |
| `bot_repository.py` | `BotRepository` | `BotRepository` | `core/bot_management/repository/protocol.py` |
| `bot_restart_lock_repository.py` | `BotRestartLockRepository` | `BotRestartLockRepositoryProtocol` | `core/bot_management/repository/protocol.py` |
| `template_repository.py` | `TemplateRepository` | `TemplateRepository` | `core/bot_management/repository/template_repository_protocol.py` |
| `render_screen_repository.py` | `RenderScreenRepository` | `RenderScreenRepository` | `core/bot_management/render_screen/repositories.py` |
| `bot_collaborator_repository.py` | `CollaboratorRepository` | `CollaboratorRepositoryProtocol` | `core/bot_collaborator/repository/protocol.py` |
| `bot_collab_log_repository.py` | `BotCollabLogRepository` | `BotCollabLogRepositoryProtocol` | `core/bot_collaborator/repository/protocol.py` |
| `bot_collab_lock_repository.py` | `BotCollabLockRepository` | `BotCollabLockRepositoryProtocol` | `core/bot_collaborator/repository/protocol.py` |
| `bot_friend_repository.py` | `BotFriendRepository` | `BotFriendRepositoryProtocol` | `core/bot_public/repository/bot_friend_repository.py` |
| `policy_repository.py` | `PolicyRepository` | `PolicyRepository` | `core/access/repository.py` |
| `caller_identity_repository.py` | `CallerIdentityRepository` | `CallerIdentityRepositoryProtocol` | `core/caller_identity/repository.py` |
| `channel_repository.py` | `ChannelRepository` | `ChannelRepository` | `core/channel/services/repositories.py` |
| `device_repository.py` | `DeviceRepository` | `DeviceBindingRepository` | `core/devices/repository/protocol.py` |
| `oss_to_nas_record_repository.py` | `OssToNasRecordRepository` | `OssToNasRecordRepository` | `core/devices/repository/protocol.py` |
| `expert_chat_repository.py` | `ExpertChatRepository` | `ExpertChatRepository` | `core/expert_chat/repository/expert_chat_repository.py` |
| `expert_chat_instance_repository.py` | `ExpertChatInstanceRepository` | `ExpertChatInstanceRepository` | `core/expert_chat/repository/expert_chat_instance_repository.py` |
| `harness_repository.py` | `HarnessTemplateRepository` | `HarnessTemplateRepository` | `core/harness/repository_protocol.py` |
| `harness_scan_repository.py` | `HarnessScanRecordRepository` | `HarnessScanRecordRepository` | `core/harness/repository_protocol.py` |
| `harness_patch_record_repository.py` | `HarnessPatchRecordRepository` | `HarnessPatchRecordRepository` | `core/harness/repository_protocol.py` |
| `harness_patch_repository.py` | `HarnessPatchRepository` | `HarnessPatchRepository` | `core/harness/repository_protocol.py` |
| `user_mcp_config_repository.py` | `UserMCPConfigRepository` | `UserMCPConfigRepository` | `core/mcp/services/repositories.py` |
| `quality_repository.py` | `QualityTaskRepository` | `QualityTaskRepository` | `core/quality/repositories.py` |
| `resource_repository.py` | `ResourceRepository` | `ResourceRepositoryProtocol` | `core/resources/repository/protocol.py` |
| `bot_publish_repository.py` | `BotPublishRepository` | `BotPublishRepositoryProtocol` | `core/service_bot/repository/bot_publish_repository.py` |
| `publish_operation_repository.py` | `OrmPublishOperationRepository` | `PublishOperationRepository` (ABC) | `core/service_bot/repository/publish_operation_repository.py` |
| `session_resource_repository.py` | `SessionResourceRepository` | `SessionResourceRepositoryProtocol` | `core/session_resources/repository/protocol.py` |
| `skill_repository.py` | `SkillRepository` | `SkillRepository` **and** `SkillsPoolSkillRepositoryProtocol` | `core/skill_center/services/repositories.py`, `core/skills_pool/ports.py` |
| `skill_repository.py` | `SkillSetRepository` | `SkillSetRepository` | `core/skill_center/services/repositories.py` |
| `skill_member_repository.py` | `SkillMemberRepository` | `SkillMemberRepository` | `core/skill_center/services/repositories.py` |
| `skill_category_repository.py` | `SkillCategoryRepository` | `SkillCategoryRepository` | `core/skill_center/services/repositories.py` |
| `skill_center_sync_log_repository.py` | `SkillCenterSyncLogRepository` | `SkillCenterSyncLogRepository` | `core/skill_center/services/skill_center_sync_service.py` |
| `skill_propagation_log_repository.py` | `SkillPropagationLogRepository` | `SkillPropagationLogRepository` | `core/skill_center/services/skill_propagation_service.py` |
| `local_skill_cleanup_repository.py` | `SqlLocalSkillCleanupRepository` | `LocalSkillCleanupRepository` | `plugin_api/local_skill_cleanup.py` |
| `skills_pool_layout_repository.py` | `SkillsPoolLayoutRepository` | `SkillsPoolLayoutRepositoryProtocol` **and** `QuarantineRepositoryProtocol` | `core/skills_pool/repository/protocol.py`, `core/skills_pool/quarantine.py` |
| `skills_pool_rollout_repository.py` | `SkillsPoolRolloutRepository` | `SkillsPoolRolloutRepositoryProtocol` | `core/skills_pool/rollout_repository.py` |
| `config_repository.py` | `ConfigRepository` | `ConfigRepositoryProtocol` | `core/system_config/repository.py` |
| `task_queue_repository.py` | `TaskQueueRepository` | `TaskQueueRepositoryProtocol` | `core/task_queue/repository/protocol.py` |
| `user_list_repository.py` | `UserListRepository` | `UserListRepositoryProtocol` | `core/user_list/repository.py` |

Two implementation classes satisfy two Protocols each, so 37 classes carry 39
Protocols.

### In-core repositories — 7 modules, 7 classes (also move to `implementations/`)

These were never in the plugin layer. Five pass the classification test as
written; two are pulled in by decision (see Decisions below) and need work
beyond a move.

| Implementation module | Class | Protocol | Protocol source today |
| --- | --- | --- | --- |
| `core/common_config/repository/common_config_repository.py` | `CommonConfigRepository` | `CommonConfigRepositoryProtocol` | `core/common_config/repository/protocol.py` |
| `core/economy/governance/repositories/audit_repo.py` | `GovernanceAuditRepository` | `AuditRepositoryProtocol` | `core/economy/governance/domain/protocols.py` |
| `core/economy/governance/repositories/notify_log_repo.py` | `NotifyLogRepository` | `NotifyLogRepositoryProtocol` | `core/economy/governance/domain/protocols.py` |
| `core/economy/governance/repositories/task_record_repo.py` | `TaskRecordRepository` | `TaskRecordRepositoryProtocol` | `core/economy/governance/domain/protocols.py` |
| `core/economy/governance/repositories/whitelist_repo.py` | `GovernanceWhitelistRepository` | `WhitelistRepositoryProtocol` | `core/economy/governance/domain/protocols.py` |
| `core/bot_chat/repository/open.py` | `OpenBotChatRepository` | **none today — must be authored** | — |
| `core/bot_chat/repository/product.py` | `BotChatDbRepository` | **none today — must be authored** | — |

`OpenBotChatRepository` is DI-bound to itself with no Protocol.
`BotChatDbRepository` is not DI-bound at all: it is constructed directly at
three call sites — `core/bot_chat/service.py:327`,
`adapters/http/bot_chat/relation_router.py:61`, and
`adapters/http/bot_chat/otel_router.py:361` — each passing an
already-injected `DatabasePlugin`. Both need a Protocol authored from their
current public surface, and `BotChatDbRepository` needs its three construction
sites converted to injection so it is independently bound like every other
repository.

Totals across both tables: **43 modules, 44 classes, 46 Protocols** (44 existing,
2 authored).

### Not repositories — 9 modules (no Protocol, no `implementations/` entry)

| Module | Why it fails the test | Disposition |
| --- | --- | --- |
| `skills_pool_capability_repository.py` | `SkillsPoolCapabilityRepositoryMixin` — no `DatabasePlugin`, no DI binding; composed into `SkillsPoolLayoutRepository` | relocate beside its composite as a plain module |
| `skills_pool_operational_repository.py` | `SkillsPoolOperationalRepositoryMixin` — same | relocate as a plain module |
| `skills_pool_post_cutover_repository.py` | `SkillsPoolPostCutoverRepositoryMixin` — same | relocate as a plain module |
| `skills_pool_quarantine_repository.py` | `SkillsPoolQuarantineRepositoryMixin` — same | relocate as a plain module |
| `skills_pool_layout_persistence.py` | classless; four pure SQL-expression helpers | relocate as a plain module |
| `skills_pool_cutover_diagnostics.py` | classless; one logging helper | relocate as a plain module |
| `skills_pool_runtime.py` | `SkillsPoolRuntime` — a transport client. Injects `DeviceAdapterTransport`, `DeviceContextResolver`, `CurrentRuntimeLayoutProbeService`; **no `DatabasePlugin`, no persistence at all**. Bound to `SkillsPoolRuntimeProtocol`, which already exists in `core/skills_pool/ports.py`. | relocate as a plain module; its existing Protocol stays in `core/skills_pool/ports.py` |
| `http_client.py` | `HttpxClient` — a genuine plugin. Implements the `HttpClient` plugin_api Protocol and **has a paired implementation** at `plugins/local/http_client.py`, plus a third override in `di/modules/infrastructure/test/http_client.py`. Passes Rule 20. | **stays in `plugins/`, untouched** |
| `core/economy/governance/repositories/task_record_query.py` | `TaskRecordQueryMixin` — no `DatabasePlugin`, no DI binding; composed into `TaskRecordRepository` | relocate beside its composite as a plain module |

The skills_pool mixins and helpers are only reachable from
`skills_pool_layout_repository.py` (and one test); nothing else in the tree
imports them.

**Decision — the mixins stay mixins.** Inlining them into their composite was
considered and rejected: the four mixins plus the composite total ~1,856 lines,
so a merged file would sit ~856 lines over the 1000-line Rule 9 cap and could only
land by adding an allowlist entry — which success criterion 4 forbids. Inlining
therefore is not available until the decomposition in
[#912](https://github.com/inclusionAI/Avernet/issues/912) lands, and that work is
explicitly out of scope here under R8.

The domain grouping in R1 removes the reason the mixins read badly in the first
place. Under a flat layout they would have been four files named
`skills_pool_*_repository.py` sitting in a directory called `implementations/`
while not being repositories. Inside `implementations/skills_pool/` they are named
for the part they are (`layout_capability.py`, `layout_quarantine.py`, …), sort
directly beneath the `layout.py` they compose, and need no disambiguating prefix.

`core/economy/governance/repositories/orm.py` holds four `Base` subclasses
(`GovernanceNotificationOrm`, `AuditLogOrm`, `WhitelistEntryOrm`,
`GovernanceTicketOrm`) that are imported by three `domain/` modules as well as by
the repository bodies. They are domain-owned ORM models, not repository code, so
they do **not** enter `implementations/`; emptying the `repositories/` package
around them means they need a home inside `core/economy/governance/`. The
metadata-registration import in `plugins/local/database.py:160` follows them.

## Requirements

### R1 — One package, grouped by domain

All repository Protocols live under
`agentclaw/community/core/repository/protocols/`; all repository implementations
live under `agentclaw/community/core/repository/implementations/`. Both
directories are new.

Within them, content is grouped into **domain subdirectories** — one module per
domain under `protocols/`, one subdirectory per domain under `implementations/`.
44 implementation modules plus their mixins and helpers in a single directory is
not navigable, and the domain grouping is what makes "where does a new repository
go?" answerable by inspection rather than by reading this spec.

The domain set is a deliberate consolidation, not a copy of the `core/` domain
names: several `core/` domains own exactly one repository, and a subdirectory per
one-file domain would trade a too-flat layout for a too-deep one.

### R2 — Enforceable contract

- Every member of every repository Protocol carries `@abstractmethod`.
- Every implementation declares its Protocol(s) as base class(es).
- Constructing an implementation that omits a Protocol member raises
  `TypeError` at construction time, and the message names the missing member.
- The move must not silently satisfy a contract by inheriting a Protocol's `...`
  body: a member that is not marked abstract would be inherited as a no-op
  returning `None`, which is worse than today's `AttributeError`. Marking every
  member abstract is therefore load-bearing, not cosmetic.

This mechanism was verified against the real toolchain (Python 3.13, `injector`),
including the multiple-Protocol and mixin-plus-Protocol shapes this tree needs:

```
TypeError: Can't instantiate abstract class Bad without an implementation
for abstract method 'put'
```

Existing `@runtime_checkable` decorators are retained where present so structural
`isinstance` checks keep working for test doubles.

### R3 — Contract drift resolved

The `SkillRepository` Protocol's declaration of `add_default_skill_exclusion`,
`remove_default_skill_exclusion`, and `remove_all_default_skill_exclusions` must
be reconciled with its implementation before R2 can hold — under R2 the current
state fails at construction. The resolution must not change behaviour for any
live caller (all of which reach these members through `SkillSetRepository`).

Re-derive this set at implementation time rather than trusting the list above:
it grew from two members to three between the spec being written and the first
`dev` rebase, and it will keep growing until R2 lands.

### R3b — Missing contracts authored

`OpenBotChatRepository` and `BotChatDbRepository` gain Protocols derived from
their current public method surface — no members added, none dropped.
`BotChatDbRepository`'s three direct construction sites are converted to
injection so it is independently DI-bound like every other repository. Both
Protocols then satisfy R2 in full.

This is the one place where the feature changes wiring rather than only moving
files. It changes no query, no return shape, and no error case (R8 still holds);
the repository holds nothing but its `DatabasePlugin`, so per-request
construction and a singleton are behaviourally identical.

### R4 — Non-repositories relocated without contracts

The eight non-plugin modules in the classification table above move without
gaining a Protocol. `plugins/http_client.py` does not move. The governance ORM
models keep their domain ownership rather than entering the repository package.

### R5 — Wiring updated

All DI modules, importers, tests, guard allowlists, module-boundary
declarations, and documentation that name a moved module path are updated in
the same change.

### R6 — Path map artifact

A complete old-module-path → new-module-path map covering every moved module
and every moved class is delivered as a file in this spec directory. It must
cover the ORM models of R7 and the non-repository relocations of R4, not just
the repositories, because the `corp/ocb` distribution imports these paths and
will be updated from this map.

### R7 — Layering violation resolved, not relocated

Five repository bodies import ORM models out of the local-profile plugin
package:

| Importer | Imported | From |
| --- | --- | --- |
| `bot_repository.py` | `DefaultSkillsetMcpExclusion`, `DefaultSkillsetSkillExclusion` | `plugins/local/sqlite_models.py` |
| `skill_repository.py` | `DefaultSkillsetMcpExclusion`, `DefaultSkillsetSkillExclusion` | `plugins/local/sqlite_models.py` |
| `device_repository.py` | `EntityDeviceBinding` | `plugins/local/sqlite_models.py` |
| `policy_repository.py` | `EntityDeviceBinding`, `AcConfigCategory`, `AcConfigItem` | `plugins/local/sqlite_models.py`, `plugins/local/system_config_models.py` |
| `config_repository.py` | `AcConfigCategory`, `AcConfigItem` | `plugins/local/system_config_models.py` |

These are shared declarative models that map real production tables; the
`local/` path is a misnomer that `skill_repository.py:1610-1618` already
acknowledges with a `TODO(repo-unify)`. Today the imports are legal only because
the importers sit outside `core/`. Once inside `core/`, they violate Rule 14 and
would fail two guards. The five model classes must be relocated into `core/` in
the same commit — allowlisting them is not acceptable, because the allowlist
entries would encode the exact debt this feature exists to remove.

The SQLite bootstrap in `plugins/local/database.py` imports both modules purely
to register their tables in the SQLAlchemy metadata; it must follow the models
to their new home.

### R8 — No behaviour change

No repository body changes: same queries, same return shapes, same error cases.
Renames of classes are out of scope. Splitting oversized modules is out of
scope.

### R9 — One atomic commit

The whole change lands as a single commit. `corp/ocb` imports these module
paths, so an incremental rollout leaves it broken between steps; the path map
exists so the `ocb` side can be updated in lockstep and the broken window stays
at one commit.

## Out of scope

- `src/backend/pyproject.toml` is not modified.
- Adding mypy/pyright to CI. R2 makes the contract enforceable at runtime, which
  is what was asked; static checking remains absent.
- Class renames. `DeviceRepository` keeps its name even though its Protocol is
  `DeviceBindingRepository`, and the several `…Repository` / `…RepositoryProtocol`
  spellings are preserved as-is. Flattening the packages already collides some
  short module names; resolving those is a naming decision for the plan, but no
  *class* is renamed.
- Splitting oversized modules. `skill_repository.py` stays 2331 lines; it is
  re-keyed in the allowlist, not decomposed.

## Constraints and collateral

### C0 — Pre-move baselines

Recorded before any file moves, so post-move numbers are comparable:

- `tests/community/architecture/`: **120 passed** — measured on `dev` @ `dd02f82`
  and re-measured unchanged after the 2026-08-08 rebase onto `dev` @ `2de13dc`.
- CI on this spec's own PR (`dev` + Markdown files): **all 7 checks green** —
  Backend / BaaS / Engine / Gateway / BCS unit tests, BCS e2e, and Singlebox
  coverage. Per-module coverage figures are in C4, measured on the rebased branch.
- Aggregate backend coverage **46.79%**; BaaS 48.37%; BCS line/method 41.48%/38.44%.

### C0b — Re-derivation after the 2026-08-08 `dev` rebase

`dev` advanced 16 commits / 234 files / ~15k insertions. The whole classification
was re-derived rather than assumed. **The structural findings all held:**

| Claim | Status after rebase |
| --- | --- |
| 44 non-`__init__` modules at the top level of `plugins/` | unchanged — 4 modified, none added or removed |
| 36 plugin-layer repositories / 8 non-repositories | unchanged, same members |
| 7 in-core repositories | unchanged, same members |
| 46 Protocols | unchanged — members were *added* to existing Protocols, but no new repository Protocol class appeared |
| R7: 5 bodies importing ORM models from `plugins/local/` | unchanged, same 5 files |
| R3b: `BotChatDbRepository` constructed directly at 3 sites | unchanged, same 3 sites |
| 108 test modules importing a moved path; 49 under `tests/community/plugins/` | unchanged |
| architecture suite green | unchanged, 120 passed |

**What did change:**

- The `SkillRepository` drift grew from two members to three (see Motivation).
- `harness`'s `core_min_percent` was re-pinned 41.30 → 40.86, invalidating that
  row of C4's table and supplying the re-pin precedent now cited there.
- Vendor-name exposure grew: **27** of the 36 plugin-layer repository modules now
  name `ZDAS` in a docstring, up from 26. The 7 in-core ones remain vendor-clean.
- Oversized-module pressure increased. `skill_repository.py` 2331 → 2423 lines
  (already allowlisted). `bot_repository.py` 901 → **954**, now 46 lines below the
  1000-line cap and not allowlisted. `skills_pool_layout_repository.py` sits at
  **exactly 1000** — the guard fails at `> 1000`, so a single added line breaks
  it. The move must not add a line to either file; if a relocated import block
  grows one of them, the fix is to shorten it, not to allowlist the file.
- A new guard, `tests/community/framework/test_no_mock_in_endpoint_tests.py`,
  forbids `unittest.mock` / `monkeypatch` under `tests/*/endpoints/`. It does not
  touch the repository tests today, but it is path-keyed: the D3 test relocation
  must not land any repository test under an `endpoints/` directory.

### C1 — Architecture guards (baseline: 120 passed)

The full suite under `tests/community/architecture/` passes today. The relocated
tree will trip at least four guards, each requiring a real fix rather than a
suppression:

1. **`test_no_data_infra_vendor_in_core.py`** — bans the literal `ZDAS` in
   `core/`. 27 of the 36 plugin-layer repository modules name `ZDAS` in a
   docstring (`skill_repository.py` five times); the 7 in-core ones are already
   clean, since the guard passes today. The docstrings must be neutralized to
   capability language; the guard must not be weakened and the allowlist must
   not grow.
2. **`test_core_no_concrete_plugin_imports.py`** (Rule 14) — forbids `core/`
   importing `agentclaw.community.plugins.local.*`. Resolved by R7. Its
   `_ALLOWLIST` is currently empty and must stay empty.
3. **`test_architecture_compliance.py::test_core_layer_does_not_import_plugins_outside_dependencies`**
   — same violations, second guard. Also resolved by R7.
4. **`test_no_oversized_modules.py`** — the allowlist is keyed **by path**, and
   entries are failed as stale when the path no longer exists.
   `plugins/skill_repository.py` (2331 lines) is listed and must be re-keyed.
   `plugins/skills_pool_layout_repository.py` sits at exactly 1000 lines,
   one line under the threshold — the plan must not let a mechanical edit push
   it over.

The full architecture suite must be run against the relocated tree early, before
the wiring is finished — some of these only appear once files are inside `core/`.

### C2 — `arch.rules.md` §8 (resolved by the domain grouping)

§8 ("Directory Organization Matches Architectural Roles") recommends
`core/<domain>/repositories/` — repositories grouped by the domain they serve.

An earlier revision of this spec required a **flat** `core/repository/`, which
contradicted that recommendation and was recorded here as a deliberate accepted
cost. R1 now groups by domain, so the conflict is gone: this change keeps §8's
*grouping* principle while consolidating the *location*, which is what removes the
nine competing conventions.

Two obligations from §8 remain and are met by the plan:

- *"each top-level architectural directory or package has a declared role"* —
  `core/repository/README.md` carries a Context Boundary section, and the package
  joins `BOUNDARY_SIGNIFICANT_MODULES`.
- *"one path does not serve incompatible roles simultaneously"* — `protocols/`
  holds contracts only and `implementations/` bodies only, enforced by the guard
  in Task 13.

### C3 — Module-boundary model

`test_module_boundaries.py` (Rule 22) requires each boundary-significant module's
`README.md` to declare every `agentclaw.*` prefix it imports. Pulling Protocols
out of the domain packages inverts a dependency: every domain that today owns its
Protocol will instead *import* `agentclaw.community.core.repository.protocols.*`,
so every affected domain README gains a new declared dependency. In the other
direction, `core/repository/protocols/` imports back into the domain packages for
models, enums, and error types.

Two consequences the plan must handle:

- The declared-dependency graph gets denser, and the generated
  `docs/arch/generated/dependents.md` view changes accordingly.
- Import cycles are a live risk: a Protocol module importing a domain package
  whose `__init__` imports a service that imports the Protocol module. This must
  be checked, not assumed.

`core/repository` is not currently in `BOUNDARY_SIGNIFICANT_MODULES`. Whether to
add it (and give it a `README.md` with a Context Boundary section) is a plan
decision; §8's "declared role" check argues for yes.

### C4 — Singlebox coverage denominators

`scripts/ci/singlebox_coverage_modules.yaml` declares each module's `core_paths`
as a domain directory (e.g. `src/agentclaw/community/core/devices/`) with
`core_min_percent` thresholds pinned to two decimals. Moving Protocol files
**out** of those directories removes lines from the denominators and shifts every
affected module's percentage. The relocated implementations land in
`core/repository/`, which is in no module's `core_paths`, so they add nothing
back.

Pulling in the in-core repositories (Decision 2) makes this materially worse:
`core/economy/`, `core/bot_chat/`, and `core/common_config/` lose whole
implementation bodies from their denominators, not just Protocol stubs.

**The gate does run in CI** (`.github/workflows/singlebox-coverage.yml`, on every
PR) — it is only the *local* pre-push path that gates it behind
`OCB_PRE_PUSH_RUN_CI=1`. Because this PR is `dev` plus Markdown files, its green
run is a true pre-move baseline. Measured on the rebased branch, `dev` @ `2de13dc`
(run 31291465522):

| Module | `core_min_percent` | measured | headroom | affected by this move? |
| --- | ---: | ---: | ---: | --- |
| `expert_chat` | 63.49 | 65.59 | **+2.10** | yes — 2 Protocol files (172 lines) leave |
| `access` | 42.80 | 45.68 | **+2.88** | yes — `repository.py` (38 lines) leaves |
| `bot_chat` | 67.48 | 71.26 | **+3.78** | yes — **both implementations (1,770 lines) leave** |
| `bot_collaborator` | 53.88 | 60.47 | +6.59 | yes — `repository/protocol.py` (341 lines) leaves |
| `devices` | 43.36 | 52.33 | +8.97 | yes — `repository/protocol.py` + shim (309 lines) leave |
| `harness` | 40.86 | 53.08 | +12.22 | yes — `repository_protocol.py` (279 lines) leaves |
| `bot_dormant` | 54.13 | 54.53 | +0.40 | no |
| `cron` | 41.84 | 48.78 | +6.94 | no |
| `files` | 63.52 | 70.31 | +6.79 | no |
| `auth` | 100.00 | 100.00 | +0.00 | no |

Reading this: a module's percentage moves *up* if the removed file was better
covered than the module average and *down* if it was worse, so the direction is
per-module and cannot be predicted from line counts alone. What the table
establishes is where there is no room to absorb a shift in either direction:

- **`expert_chat` has 2.10 points** — the tightest module this move touches.
- **`access` has 2.88** on a 38-line Protocol file. Small file, small module
  (529 lines total), so the proportional effect is not small.
- **`bot_chat` loses roughly half its `core_paths` tree** (1,770 of 3,565 lines)
  on 3.78 points — by far the largest single perturbation, and a direct
  consequence of Decision 2.
- `bot_dormant` is the tightest module overall at +0.40, but **no repository file
  lives under `core/bot_dormant/`**, so this move cannot disturb it.

**What changed since the first baseline, and why it matters.** The `dd02f82`
baseline (reproduced identically across two CI runs, so it was signal, not
jitter) had `harness` at 41.44 against a 41.30 floor — **+0.14**, which this spec
called the sharpest risk in the change. On the rebased branch `harness` measures
**53.08 against 40.86: +12.22**. It went from the tightest module to the roomiest.

The cause is recorded in the yaml itself. The `41.30 → 40.86` entry closes with:

> Update: singlebox now gains a fixture-backed MCPCenter — TestingMcpModule serves
> CommunityMCPCenter when SINGLEBOX_ACCEPTANCE_MCP_FIXTURE_FILE is set … so the
> helpers are acceptance-coverable again; the floor stays at the last measured
> value until CI re-measures with the fixture.

This run is that re-measurement. The predicted reversal happened, and it is 11.64
points larger than the floor anticipated.

The lesson for Task 16 is not "the risk is gone" — it is that **these numbers have
a short half-life**. Two of the ten modules moved by more than a point in four
days for reasons unrelated to this change (`harness` +11.64, `devices` −1.36).
Task 16 must re-read them from the post-move run rather than diffing against this
table.

Consequence for the plan: re-pinning at least one threshold is probable, not
hypothetical. That is a re-baseline (the denominator changed, the testing did
not), **not** a weakening of the gate, but AGENTS.md forbids weakening checks to
make a change pass and forbids inflating a result by excluding production Core
paths — so any re-pin must be justified file-by-file in the PR, derived from a
fresh run, and must never be achieved by trimming `core_paths`. If a module's
number drops because real coverage was lost rather than because the denominator
moved, the correct answer is to fix the coverage, not the threshold.

### C4a — What actually happened (post-move CI)

Exactly one module breached, and it was the one C4 named as the largest
perturbation: **`bot_chat` measured 65.63% against its 67.48% floor**, having lost
1,770 of its 3,565 `core_paths` lines when both repository bodies moved out.

**It was fixed by restoring the denominator, not by lowering the threshold.**
`core_paths` is a prefix list, so the two moved files were added back to
`bot_chat`'s entry. This is strictly stronger than a re-pin:

- No threshold was weakened; the 67.48% floor stands untouched.
- No production Core path was excluded — one that would otherwise have silently
  dropped out was *retained*.
- It reflects what actually happened: the code and its acceptance flows are
  unchanged, only the module path moved, so the denominator should follow it.

They are listed per-file rather than as the `implementations/chat/` directory,
because that directory also holds the `expert_chat` and `channel` repositories,
which belong to other modules' denominators.

Re-pinning remained available and was not used. The precedent below stands for
the case where a denominator genuinely shrinks — code that leaves the reachable
set — rather than one where it merely moves.

**Not fully restored, and why.** Five other modules (`access`,
`bot_collaborator`, `devices`, `expert_chat`, `harness`) also lost Protocol lines
to the move. None breached — protocol stubs sit near their modules' averages and
every one had headroom. Restoring theirs faithfully would need `protocols/chat.py`
and `protocols/identity.py` split per sub-domain, since each serves several
coverage modules; the domain grouping deliberately does not do that. The residual
denominator drift is recorded here rather than papered over.

**There is an established in-repo precedent for exactly this, and the plan should
imitate it rather than invent a format.** `singlebox_coverage_modules.yaml`
carries the `harness` threshold's history as inline comments — `41.59 → 41.30`,
then `41.30 → 40.86` on the 2026-08-08 rebase. Each entry names the commit that
moved it, identifies the specific functions that left the reachable set, explains
*why* they became structurally uncoverable by acceptance, states the measured
value, and asserts "no production Core path excluded, no test-only call added".
The 40.86 entry even records a pending reversal: singlebox has since gained a
fixture-backed MCPCenter, so "the floor stays at the last measured value until CI
re-measures with the fixture". That is the bar — a re-pin is acceptable when it
is documented to that standard, and not otherwise.

### C5 — Test blast radius

108 test modules import a moved path directly, including 43 of the 49 under
`tests/community/plugins/`. Per Decision 3 they move with the code, so the test
tree mirrors the new layout in the same commit. This is the largest single
contributor to the diff and the main reason R9's one-commit constraint is
demanding rather than merely tidy.

## Decisions

These were raised as open questions and answered before planning.

### D1 — No `corp/ocb` twins (confirmed)

`src/agentclaw/corp/` **does not exist in this repository**. Avernet is the
community-only extraction; the architecture guards themselves branch on
`_CORP_PRESENT = (_SRC_ROOT / "corp").is_dir()`, which is false here. The corp
tree could not be inspected from this checkout, and its contents were not
assumed.

The in-tree evidence pointed one way:

- Every repository is bound once, unconditionally, in a profile-agnostic module.
- The per-profile override mechanism is the infrastructure column
  (`di/modules/infrastructure/<profile>/`), and every column module covers a
  plugin_api concern — none re-binds a repository.
- `di/profile_modules.py` documents the corp and community columns as
  "import-disjoint" and lists their members; no repository appears.
- The unified-body docstrings state directly that prod and test differ only by
  which `DatabasePlugin` is bound.

**Confirmed by the requester: no repository in this set has a corp-side
implementation.** All 43 move. Had any twin existed it would have been a genuine
plugin and excluded.

### D2 — In-core repositories are in scope

All nine repository-shaped modules already inside `core/` join the move, not
just the five that pass the classification test cleanly. `OpenBotChatRepository`
and `BotChatDbRepository` therefore get Protocols authored (R3b), and
`BotChatDbRepository`'s three direct construction sites are converted to
injection.

Cost accepted: roughly 2,600 additional lines of churn in an already-large atomic
commit, and a worse coverage-denominator shift (C4). Benefit: after this change
there is genuinely one place a repository lives, with no "except these seven"
footnote — which is the stated goal.

### D3 — Test files move with the code

The 43 affected modules under `tests/community/plugins/` (and the governance and
bot_chat test modules) relocate to mirror the new layout in the same commit. A
`tests/community/plugins/` tree exercising code that no longer lives in
`plugins/` would recreate the "no single correct answer" problem this feature
exists to fix.

## Success criteria

1. `core/repository/protocols/` holds all 46 repository Protocols; every member
   carries `@abstractmethod`.
2. `core/repository/implementations/` holds all 44 repository classes; each
   declares its Protocol(s) as base(s).
3. A test proves R2's failure mode: a deliberately incomplete subclass of a
   repository Protocol raises `TypeError` at construction naming the missing
   member.
4. `tests/community/architecture/` passes with no new allowlist or exemption
   entry in any guard, and no stale entry left behind.
5. The backend unit suite passes with no behaviour change.
6. No file under `core/repository/` imports `agentclaw.community.plugins.local.*`.
7. `plugins/` retains exactly the genuine plugins: the `local/` and `community/`
   profile packages plus `http_client.py`.
8. The path map covers every moved module and class, and is accurate enough for
   the `corp/ocb` side to be updated from it alone.
