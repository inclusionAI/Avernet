# Product domain vocabulary

This glossary defines shared domain language for product design, contracts,
code, data migrations and reviews. Keep stable concepts and semantic boundaries
here; put module-specific behavior and implementation details in the owning
module. Do not use a glossary term as evidence of a specific deployment.

## Skills

**Skill**: A reusable capability with a stable identity across versions, managed by users and referenced by downstream consumers. Avoid using it for a single database row, package or version.

**Skill Version**: An immutable published content snapshot belonging to exactly one Skill. Versions of one Skill are successive versions, not separate Skills. Avoid: Skill, version record.

**Version Ordinal**: A positive, monotonically increasing number ordering successful publications of one Skill, starting at 1 and displayed as `Vn`. Abandoned, rejected or technically failed drafts do not consume it. It is neither the version row primary key nor the external version key. Avoid: decimal version, semantic version, external version key.

**Target Version Ordinal**: The next published-version candidate fixed when an active draft is created, allowing a stable `Vn` display. It is not a published version before success and may be reused after an unsuccessful draft is abandoned. Avoid: version ID, published version.

**Skill Draft**: The current mutable working copy used for initial publication or an upgrade. At most one active draft per Skill exists in an environment. It becomes an immutable published version only after publication succeeds. Avoid: draft version, in-development version.

**Publication Attempt**: A persistent record of one execution of automated standards checks, security scanning and external publication for frozen content. Resends with the same idempotency key and automatic retries of transient failures belong to the same attempt; an explicit retry after terminal failure creates a new attempt, not multiple business versions. Avoid: manual review, Skill Version, Skill status.

**Publication Snapshot**: The immutable content and hash frozen from a draft when publication starts. Automated checks, security scanning, external publication and the resulting Skill Version must use this same snapshot. Avoid: mutable draft, published version.

**Published Skill Version**: A version available for downstream selection. Published describes availability, not whether it is currently executing. Avoid: running Skill, active Skill.

**Skill Upgrade**: Creating and publishing a successor version of an existing Skill without changing its identity. Avoid: cloning a Skill, creating a new Skill.

**Skill Source**: A channel from which content is acquired, such as upload, Git snapshot or external market. It is independent of ownership and collaboration scope. Avoid: Space, ownership scope.

**Source Snapshot**: An immutable snapshot acquired and stored from an upload or Git repository. A manual Git update follows the same branch as the initial import and replaces the draft only after complete download and validation; published versions remain unchanged. Avoid: live Git sync, repository mirror.

**Skill Content Metadata**: The name and description in `SKILL.md` frontmatter. The current draft's `SKILL.md` is their sole source of truth; a published version stores a frozen snapshot. Database list fields and search indexes are projections, not independent write sources. Avoid: independent basic information, primary-table name, card description.

**Historical Skill Identity**: An identity previously assigned to a Skill, whose references, grants, audit records or external artifacts may remain even if the visible Skill record is gone. Avoid: unused UUID, deleted row.

## Spaces and collaboration

**Space**: The boundary for Skill ownership and collaboration, not a content source. Avoid: repository, source, Skill Center team.

**Skill Space Ownership**: The single fact associating a managed Skill with a Space. Personal and Team Spaces use the same relationship; at most one ownership relationship exists per Skill per environment. Legacy Skills not yet migrated may temporarily lack one. Avoid: ordinary many-to-many binding, Skill type, `scope_type`.

**Personal Space**: A Space centered on one user's ownership and collaboration boundary. Avoid: private Skill, local Skill.

**Team Space**: A Space with a shared team collaboration boundary. Avoid: shared Skill, Skill Center team.

**Cross-space Skill Reference**: A read-only relationship allowing another Space to use a Skill without changing ownership. Copying content creates a new Skill identity and is not a cross-space reference. Avoid: multiple ownership, shared ownership.

**Space Membership**: A user's role in a Space; membership alone does not confer management or editing rights over an individual Skill. Avoid: Skill grant, Skill member.

**Space Administrator**: A member with Space membership governance authority. The creator becomes the first administrator at creation; creation is an audit fact, not a permanent privilege. Administrators do not automatically manage or edit every Skill. Avoid: Space creator, Skill manager, owner of every Skill.

**Skill Grant**: Skill-level permission granted independently of Space Membership. Only `OWNER` and `MANAGER` roles are supported; ordinary Space members have no Skill Grant. Avoid: Space role, member.

**Skill Owner**: The person ultimately responsible for a Skill and its Managers. Its creator is the default Owner; normally each Skill has exactly one Owner, and ownership must be transferred before leaving the owning Space. This is distinct from Space Administrator. Avoid: Skill manager, Space owner, team owner.

**Skill Manager**: A person granted operational rights by the Skill Owner. A Skill can have multiple Managers who may edit and perform allowed operations, but cannot manage Managers or transfer ownership. Avoid: Skill Editor, Skill member, ordinary Space member.

**Edit Lease**: A time-limited exclusive right to edit a Team Space Skill draft. It coordinates concurrent editing and prevents stale writes after takeover; it is neither permanent permission nor a substitute for Skill Grants. Personal Space Skills do not use it. Avoid: Skill permission, permanent lock.

## Bot consumption

**Skill Type**: The content-management and delivery category: local, Repo or Space Skill. It does not specify direct installation versus Skill Set activation. Avoid: activation source, installation method, Skill scope.

**Skill Reference**: A stable reference target consumable by a Bot. Space Skills use OCB Skill Identity; external market Skills use source system plus external stable identifier. It contains no fixed version. Avoid: version reference, local Skill copy.

**Skill Set**: A named optional collection of Skill References that a Bot can activate together. It is neither mandatory for consumption nor an ownership boundary. Avoid: capability set, Skill, Space.

**Skill Set Membership**: The relationship placing a Skill Reference in a Skill Set. It does not mean a Bot activated or obtained the Skill. Avoid: direct installation, Bot Skill Binding.

**Skill Set Activation**: A Bot's choice to enable a Skill Set; its members become one source of the Bot's Effective Skill Set. Avoid: individual Skill activation, direct installation.

**Direct Skill Installation**: A Bot enabling a Skill Reference without a Skill Set. Local, Repo and Space Skills can all be installed directly. Avoid: default Skill Set, membership, Bot-local Skill.

**Effective Skill Set**: The desired union of enabled direct installations and activated Skill Set members, deduplicated by Skill identity. It does not prove successful runtime delivery. Avoid: single Skill Set, observed runtime state, Default SkillSet.

**Bot Skill Binding**: A Bot's continuing dependency on a market or Space Skill Reference through direct installation or an activated set. It tracks the latest eligible published version; the Bot cannot edit the referenced content. Avoid: copied Skill, Skill ownership.

**Bot-local Skill**: A Skill asset uploaded directly to one Bot without a continuing reference to a managed Skill and its versions. It belongs only to that Bot and is consumed through Direct Skill Installation. Avoid: direct installation, Space Skill, Bot Skill Binding.

**Package Deletion**: Removing the complete content root of a Bot-local Skill in one runtime operation. It does not imply an atomic transaction between metadata and runtime files. Avoid: per-file deletion, cross-storage atomic deletion.

**Track Latest**: The fixed rule resolving market/Space Skill Bot bindings and draft runtimes to the latest eligible published version. It does not modify a published Service Bot Artifact. Avoid: automatic copying.

**Binding Resolution**: The target Skill version resolved for a Bot binding in draft state and the observable result of whether it reached the draft container. It is not a user-selected fixed-version policy. Avoid: Bot binding version, publication status.

**Bot Draft Runtime**: Dependency resolution and preview-container state for a Bot's editable configuration. Skill upgrades advance its target version and trigger container updates without modifying published Service Bot Artifacts. Avoid: published Bot, production artifact.

**Service Bot Artifact**: An immutable runtime artifact produced by a service Bot publication from its draft. It freezes at least each dependency's `skill_uuid + external version key`; deployment to multiple instances, restart and rollback do not resolve latest again or participate in later draft pushes. Avoid: current Bot configuration, dynamic latest reference.

**Skills Pool Admission Policy**: An environment-level policy governing the first Pool layout claim by an unclaimed Bot. Rules cover exact Bot, Owner + Engine, Environment + Engine and exact Bot exclusions; it does not execute migration, recovery or rollback. Avoid: Batch, acceptance batch, migration status.

**Engine Admission Switch**: An operational fuse evaluated by Engine before first claim, ahead of all allow rules. Disabling it only blocks new claims for that Engine; it does not cancel, pause or roll back existing claims. Avoid: stopping running Bots, automatic rollback, Batch close.

**Policy Revision**: An immutable identifier for an atomic Skills Pool Admission Policy change. Writes use the previous revision for CAS. First claim freezes the matched revision and admission reason for observation, not as a later rollout gate. Avoid: Accepted Batch, Promotion Ready.

**Runtime Skill Dependency**: A specific Skill Version resolved from a Skill Reference and recorded in a Service Bot Artifact. It is a deterministic publication input, distinct from the control-plane Track Latest binding policy. Avoid: Bot Skill Binding, fixed-version policy.

## Skills runtime layout

**Pool-native Bot Layout**: A Bot runtime layout that uses Pool as its sole Skill content authority from creation and never undergoes a Legacy-to-Pool cutover. Avoid: new Bot cutover, empty Legacy migration.

**Legacy Layout Migration**: Forward migration to Pool of an existing Bot created with Legacy as its Skill content authority. It applies to existing Legacy Bots, not Pool-native Bots. Avoid: Pool-native initialization, new Bot cutover.

## Lifecycle

**Bot Recycling**: A personal Bot Owner's action to stop platform-managed runtime resources and move the Bot to `RECYCLED`. It differs from activity-based dormant governance and currently applies only to personal Bots with platform-managed resources, not desktop, service or Teclaw Bots. Avoid: deletion, restart, dormant scan.

**Bot Reactivation**: An asynchronous Owner operation restoring a `RECYCLED` personal Bot. After acceptance, the caller must query status until `ACTIVE` and runtime ready, or failure. Avoid: restart, license-only activation, synchronous activation.

**Dormant Governance**: A platform process deciding warnings and recycling based on activity, warning cool-down and protection policies. It respects Bot and Owner protection lists; Owner-initiated recycling is outside this process and is not blocked by those protections. Avoid: Owner-initiated or single-Bot operational recycling.

**Skill Creation Cancellation**: Deleting a pure draft Skill that has never submitted publication and has no publication attempts, external mappings, published versions or historical references. This is the only ordinary-flow case allowing physical removal of Skill identity. Avoid: published Skill deletion, retirement.

**Skill Identity Tombstone**: The minimum identity record retained after a Skill has publication attempts, external mappings, published versions or historical references, even after retirement. It preserves references, audit and UUID non-reuse, not availability. Avoid: available Skill, historical version.

**Historical Skill Version**: A published version no longer latest because a successor was published, whose content and exact version addressing remain available for existing Service Bot Artifacts. It is not withdrawn or deleted. Avoid: withdrawn or deleted version.

**Version Withdrawal**: Stopping downstream selection of one published version while retaining the Skill and history. It must be blocked while any active Bot Binding or Service Bot Artifact references that version. Avoid: Skill deletion, ambiguous withdrawal.

**Skill Retirement**: Ending availability of an entire Skill and preventing new downstream selections. It differs from deleting records and from withdrawing one version. Avoid: deletion, ambiguous withdrawal.

## Market and favourites

**Market Resource**: A discoverable market Skill entry, potentially from Skill Center, a Git-synchronized market or an OCB Space. It is a discovery projection, not necessarily an OCB-managed Skill entity. Avoid: Skill primary table, Space Skill.

**Space Favourite**: A Space's shortcut relationship to a Market Resource. It does not copy content, transfer ownership or automatically give the Skill to a Bot. Avoid: installation, binding, import.

## External publication

**Skill Center**: An external system for publishing and distributing Skill content, not the authority for OCB Space Membership or Skill Grants. Avoid: Space, capability set.

**External Skill Mapping**: An explicit relationship between an OCB Skill and its Skill Center identity; it cannot be inferred from the name. Avoid: Skill name, source locator.

**External Version Key**: An immutable string used by Skill Center or Skill Repo to address a published version, such as `1.0.0`. It connects through External Skill Mapping to an OCB Skill Version; it neither orders OCB versions nor serves as their identity. Avoid: Version Ordinal, displayed version, latest.

## Manifest MCP configuration

**Bot MCP Override**: A Manifest-declared connection override for one Bot and MCP. It stores only overridden fields, without copying user configuration or MCP Center metadata. Avoid: user MCP configuration, final runtime configuration.

**Effective MCP Configuration**: Runtime connection configuration resolved for one Bot and MCP from the Bot override, user configuration, MCP Center metadata and platform rules. Avoid: raw Manifest, MCP installation relationship.

**Manifest Direct claim**: A Bot-level direct claim produced by Manifest Apply from an explicit Skill or MCP. Ordinary SkillSet membership is removed; Default/platform supply is suppressed by exclusion, with Direct state expressed together with Installation. This state is inferred from existing facts, not persistent provenance. Avoid: managed-by-Manifest, continuing Manifest ownership.

## Backend runtime vocabulary

**Runtime binding**: The existing association selected for a trusted Bot request, distinct from a Skill binding. Resolution returns a binding identity and source; it does not provision or select a physical device. See the [runtime-binding boundary](apps/backend/src/agentclaw/community/core/runtime_binding/README.md).

**Runtime stage**: The draft, verify or online target defined by the owning API and [engine stage contract](apps/backend/src/agentclaw/community/core/engine_runtime/stage.py). Stage is separate from deployment environment.

**Runtime device target**: A provider-specific destination within a resolved runtime. For Cron, a device UUID can select one instance; a Bot ID or binding ID alone is not that UUID. See the [Cron contract](apps/backend/src/agentclaw/community/core/cron/README.md).
