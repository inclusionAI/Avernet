# `agentclaw.community.core.skill_center`

Skill Center domain — Bot capability desired state (Skills and MCPs), market
sync, repository sync, skill auth, propagation logging.

## Context Boundary

```yaml
purpose: "Skill Center domain — Bot capability desired state (Skills and MCPs), market sync, repository sync, skill auth, propagation logging."
provides:
  - "SkillSetService"
  - "MarketSyncService"
  - "RepositoryCatalogService"
  - "GitSyncService"
  - "SkillAuthService"
  - "CurrentRuntimeLayoutProbeService"
  - "SkillQueryService"
  - "LocalSkillUploadService"
  - "SkillPackageValidator"
  - "SkillPackageManifestParserProtocol"
  - "ValidatedSkillPackage"
  - "DraftContentStore"
  - "DraftContentStoreConfig"
  - "DraftRevisionIdentity"
  - "DraftRevisionRef"
  - "OssDraftContentStore"
  - "DirectActivationService"
  - "LocalSkillDeleteService"
  - "BotCapabilityAuthorizationHookProtocol"
  - "SkillSetManagementService"
  - "SpaceSkillGrantService"
  - "SpaceSkillApplicationService"
  - "SpaceSkillOfflineService"
  - "PublishedVersionDraftBuilder"
  - "SpaceSkillApplicationServiceProtocol"
  - "SpaceSkillDraftRepository"
  - "SpaceSkillReadRepository"
  - "SpaceSkillVersionQueryService"
  - "SpaceSkillVersionReadRepository"
  - "SpaceSkillEditorRequestService"
  - "SkillCollaboratorApprovalHandler"
  - "DraftEditLeaseService"
  - "RuntimeProjectionResolver"
  - "resolve_effective_mcp_server_codes"
  - "BotCapabilityStateReader"
  - "SkillVersionResolver"
  - "SkillVersionResolverProtocol"
  - "SkillVersionMaterializer"
  - "SkillVersionMaterializerProtocol"
  - "PublishedMaterializedSkillVersion"
  - "SpaceSkillPublicationService"
  - "SpaceSkillPublicationServiceProtocol"
  - "SkillCenterPublicationGatewayProtocol"
  - "SpaceSkillPublicationTaskHandler"
  - "ACTIVE_SKILL_PUBLICATION_ATTEMPT_STATUSES"
  - "PublicationAttemptRecord"
  - "InstallationBackfillService"
  - "InstallationBackfillServiceProtocol"
  - "BotRuntimeProjector"
  - "BotRuntimeProjectorProtocol"
  - "LocalSkillCleanupWorkModel"
  - "SkillActivationSyncAction"
  - "SkillActivationSyncScope"
  - "SkillActivationSyncTaskHandler"
  - "SkillActivationSyncWork"
  - "SkillParser"
  - "SkillMetadata"
  - "SkillManifestError"
  - "SkillManifestErrorCode"
  - "SkillManifestValidationIssue"
  - "SkillManifestValidationResult"
  - "SkillCenterGatewayService"
  - "SkillCenterReferenceService"
  - "SkillCenterReferenceProcessor"
  - "SkillCenterSyncService"
  - "TrackLatestService"
  - "TrackLatestPublishedVersionListener"
  - "PublicCenterSkillIdentity"
  - "CanonicalCenterVersionStore"
  - "CanonicalCenterVersion"
  - "CanonicalCenterVersionIdentity"
  - "enqueue_skill_activation_sync"
  - "build_skill_activation_sync_payload"
  - "parse_skill_activation_sync_payload"
  - "build_skill_activation_sync_idempotency_key"
consumes:
  - "BotRepository"
  - "BotCollabLogRepositoryProtocol"
  - "CollaboratorServiceProtocol"
  - "Events"
  - "core/mcp services"
  - "CachePlugin"
  - "DeviceAccessor"
  - "DeviceContextResolver"
  - "DeviceAdapterTransport"
  - "MCPCenterPlugin"
  - "ObjectStoragePlugin"
  - "ImmutableObjectStorageCapability"
  - "SecretResolver"
  - "SkillCenterClient"
  - "SkillCenterGateway"
  - "StaffDeptPlugin"
  - "SpaceSkillSourcePlugin"
  - "SkillRepoSyncPlugin"
  - "WorkspacePathFactory"
  - "LocalSkillCleanupRepository"
  - "BotRuntimeProjectorProtocol"
  - "SpaceAccessServiceProtocol"
  - "SpaceSkillRepository"
  - "WorkOrderRepositoryProtocol"
  - "DraftEditLeaseRepository"
  - "SpaceSkillDraftRepository"
  - "SpaceSkillReadRepository"
  - "SkillVersionRepositoryProtocol"
  - "SkillVersionMaterializationRepositoryProtocol"
  - "SpaceSkillPublicationRepositoryProtocol"
  - "HttpClient"
  - "ServiceArtifactLineageReaderProtocol"
internal_dependencies:
  - agentclaw.community.core.bot_config_surface    # BotConfigCoords, the shared config-category address type
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.skill_center    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.space_skill_version # published Space Skill read contract consumed by this module
  - agentclaw.community.core.repository.protocols.skill_center_types # query projection types consumed by this module
  - agentclaw.community.core.repository.protocols.space_skill_publication # Publication aggregate persistence contract
  - agentclaw.community.core.repository.protocols.skill_center_reference
  - agentclaw.community.core.repository.protocols.track_latest
  - agentclaw.community.core.repository.skill_center_reference_types
  - agentclaw.community.core.repository.track_latest_types
  - agentclaw.community.core.repository.protocols.work_orders
  - agentclaw.community.core.repository.protocols.space_skill_offline
  - agentclaw.community.core.repository.space_skill_offline_types
  - agentclaw.community.core.service_bot.service_artifact_lineage_reader_protocol
  - agentclaw.community.core.work_orders
  - agentclaw.community.core.repository.protocols.skill_installation
  - agentclaw.community.core.repository.protocols.skills_pool    # Skills Pool repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.capability_desired_state
  - agentclaw.community.core.repository.capability_desired_state_types
  - agentclaw.community.core.repository.protocols.identity    # per-Bot MCP execution identity, read for the Passport scope
  - agentclaw.community.core.access
  - agentclaw.community.core.base
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.config
  - agentclaw.community.core.config_compose
  - agentclaw.community.core.devices
  - agentclaw.community.core.errors    # transport-free DomainError base for SkillSet control-plane errors
  - agentclaw.community.core.events
  - agentclaw.community.core.mcp
  - agentclaw.community.core.models
  - agentclaw.community.core.spaces.services
  - agentclaw.community.core.spaces.errors
  - agentclaw.community.core.spaces.models
  - agentclaw.community.core.spaces.protocols
  - agentclaw.community.core.skills_pool
  - agentclaw.community.core.task_queue    # durable enqueue for Bot-level activation sync
  - agentclaw.community.core.workspace
  - agentclaw.community.di.modules
  - agentclaw.community.di.runtime_mode
  - agentclaw.community.kernel
  - agentclaw.community.log
  - agentclaw.community.plugin_api.cache
  - agentclaw.community.plugin_api.http_client
  - agentclaw.community.plugin_api.local_skill_cleanup
  - agentclaw.community.plugin_api.models
  - agentclaw.community.plugin_api.device_adapter_transport
  - agentclaw.community.plugin_api.devices
  - agentclaw.community.plugin_api.device_sync_dispatcher
  - agentclaw.community.plugin_api.mcp_center
  - agentclaw.community.plugin_api.mcp_auth
  - agentclaw.community.plugin_api.passport
  - agentclaw.community.plugin_api.object_storage
  - agentclaw.community.plugin_api.secret_resolver
  - agentclaw.community.plugin_api.skill_center_client
  - agentclaw.community.plugin_api.skill_center_gateway
  - agentclaw.community.plugin_api.skill_repo_sync
  - agentclaw.community.plugin_api.skill_scanner
  - agentclaw.community.plugin_api.space_skill_source
  - agentclaw.community.plugin_api.staff_dept
  - agentclaw.community.utils
  - agentclaw.community.utils.avernet_tenant
  - agentclaw.community.utils.env_utils
```

### Change impact

`SpaceSkillEditorRequestService` is an additional consumer of the existing
`StaffDeptPlugin` profile lookup: newly created Skill editor-request approval
notifications persist the applicant as `「花名」(工号)` when the directory returns
a nickname. The production composition supplies the corporate directory
implementation; community and local implementations, a missing nickname, and
`StaffProfileLookupError` all degrade to the previous work-number-only display
without blocking the approval request. This is write-time presentation data
only: public APIs, list reads, and already persisted notifications remain
unchanged. Contract coverage verifies lookup and fallback, while repository
coverage verifies the exact persisted notification text.

`SkillPackageValidator` is the pure package boundary shared by Local upload and
future Draft/materialization workflows. It owns safe relative paths, archive
limits, wrapper normalization, the single `SKILL.md` rule, manifest validation,
ignored platform metadata, and deterministic canonical ZIP generation. Its
default ZIP/directory entry points require frontmatter; only the existing Local
upload lifecycle calls the explicit legacy-compatible ZIP entry point. The
`ValidatedSkillPackage` value does not authorize a Bot, write a content store,
mutate desired state, or project Runtime; those lifecycle effects remain in
their owning application services.

`CanonicalCenterVersionStore` keeps the exact Runtime file tree under
`skills-center/<skill_uuid>/<sc_version_number>/` free of control objects. Its
write intent and integrity manifest live under the derived sibling
`skills-center-control/` prefix, which Engine Runtime must never mount or copy.
Those objects protect immutable writes and validate completeness; they are not
a publication state. Only `ac_skill_version.status=PUBLISHED`, owned by
`SkillVersionMaterializer` after exact download/hash, strict package validation,
scope-owned metadata, MCP dependency and Store verification all succeed, expresses
domain readiness. SC Public and Team/Space exact downloads trust the MCP facts
already established by SkillCenter publication: both consume upstream
`mcpServices` and skip a second remote Scanner pass. Local/Repo scan flows remain
owned by their existing services and are not changed by this trust boundary.
Publication and SC Reference producers consume the public
Materializer Service API; Runtime reads consume only PUBLISHED Versions through
`SkillVersionResolver`.

`SpaceSkillPublicationService` freezes the current immutable Draft Revision and
persists its `frozen_draft_locator` on the durable Attempt before enqueueing;
the worker never re-reads mutable `Skill.zip_url` as its input. Its worker is the only owner of
the one-shot SC submit/status state machine: it records `sc_post_started_at`
before the external call, never submits the same Attempt twice, and moves an
uncertain response to `RESULT_UNKNOWN`. Once SC identifies an exact Version it
delegates exclusively to `SkillVersionMaterializerProtocol`; retries keep the
same `skill_version_id`. The returned `PublishedMaterializedSkillVersion` is
also the unified at-least-once Published event seam. Track Latest, Offline,
Reference and Artifact consumers remain outside the Publication transaction.

`DraftContentStore` persists one canonical ZIP per immutable Draft revision.
Its business reference is `draft://<skill_uuid>/v<target>/<revision_id>`; only
the OSS adapter knows the configured physical object prefix. The Store owns no
Draft status, TTL, READY marker, database command, Publication behavior, or
Runtime projection. OSS writes use atomic create-if-absent, then compare exact
canonical bytes for idempotency; storage read failures remain distinct from a
missing revision.

Capability activation is the highest-throughput flow in production. Changes here can break every chat session in flight. Coordinate with the propagation log schema before changing repository protocols. Changes to `SkillMetadataParserProtocol`, `SkillMetadata`, or stable manifest error codes affect Local folder upload immediately and the shared fixtures consumed by Git import, Draft validation and publication validation; coordinate those consumers before changing fields, limits or codes. List/detail/market readers must continue consuming parser-derived projections rather than inventing a second name or description source.

`SkillCenterGatewayService` is a typed consumer of the independent SC adapter
boundary. It accepts already-resolved Team requests and does not modify
`ac_skill`, create Versions, select an Attempt result, retry publication, or
materialize runtime content. It also preserves catalogue metadata, tag trees,
SC publish diagnostics (including lossless raw standard/security reports),
non-paged versions, and exact download facts without turning any of them into
HTTP presentation DTOs. A status lookup uses the globally unique `skill_code`
and returns SC's current version; the future Publication application service
compares that response to its persisted Attempt rather than making the Gateway
caller supply a Team or expected version.
It rejects response identity drift across Team, Skill, page, and exact version;
retry, Attempt, persistence, and materialization decisions remain above it.
Public version/download reads use an explicit scope and verify public visibility
before crossing the exact-version boundary.

The catalogue Gateway, Space Publication, SC Public Reference, materialized-only
Sync, and Track Latest application modules now consume the typed seams above.
`TrackLatestPublishedVersionListener` is the single required EventBus bridge
from the unified at-least-once PUBLISHED event to durable fanout. Publication
still owns no Track Latest policy or task type, and Reference/Sync still own no
Publication Attempt state.

SC Public Sync's distributed cache lease is a best-effort batch coordinator,
not a transactional fencing token. If renewal is lost while one exact,
idempotent materialization is already running, that item may complete; Sync
observes the loss at the next item boundary, stops subsequent items, and reports
the stable coordinator error. A later periodic/manual pass converges the
materialized-only set again.

### One writer, one flush, one reader, one rule book

Installation (`ac_bot_skill_installation` / `ac_bot_mcp_installation`) is the
single source of truth for a Bot's active capabilities, and four seams keep it
that way:

- **One writer.** Each Installation/exclusion table's SQL lives in exactly one
  command module under
  `core/repository/implementations/skill_center/tables/`; only the
  `CapabilityDesiredStateRepository` unit of work composes them. An
  architecture test (`test_installation_table_write_ownership.py`) fails any
  other module that writes the models.
- **One flush.** `flush_installations` is the only reconciliation from Set
  configuration into Installation, and every read-side consumer runs it first
  (details below).
- **One reader.** `BotCapabilityStateReader` answers every "what is active on
  this Bot" question — it flushes, then reads Installation alone.
  `SkillQueryService` (listing/detail/content/parameters) and
  `DirectActivationService.list_installed_mcps` answer through it.
- **One rule book.** `policies/capability_ownership.py` owns the ownership
  rules: R1 a Set-held capability (Default included, excluded or not) refuses
  direct control; R2 a directly-active capability refuses joining a Set; R3 a
  capability lives in at most one Set. Engine/template Default MCPs are a
  separate platform policy input rather than Set membership; they likewise
  refuse Direct control and change only through Default exclusion/un-exclusion.
  Command services consult these policies before and inside the write
  transaction; nothing else re-derives those decisions.

Writes go through two command services, one per scope, with identical shape —
authorize, mutate desired state in one UoW transaction, then best-effort
project the runtime halves declared by `ProjectionScope`. A committed Desired
State is never reverted merely because Runtime observation is unavailable or
degraded; the command returns a structured `runtime_projection` result instead:
`SkillSetManagementService` for Set-scoped
mutations (Default-Set edits become per-Bot exclusion rows) and
`DirectActivationService` for Set-free single-capability activation, Skills
and MCPs alike.

`ac_bot_skill_installation` materializes the current active Desired State with
identity `(tenant, env, owner_id, bot_id, skill_id)`. Installation tables were
never globally backfilled, so every read-side consumer runs the lazy flush
first: `CapabilityDesiredStateRepository.flush_installations` atomically makes
Installation agree with Set configuration for one exact Bot — inserting active
(and non-excluded Default) members' rows, Skills and MCPs alike, and deleting
rows only inactive claims account for. It runs before a complete runtime
projection, before a new Service Bot Artifact build, and before flush-fronted
reads such as `GET /openapi/v1/bots/{bot_id}/skills` (see
`specs/2026-08-23-openapi-v1-bot-skill-listing/` and
`specs/2026-08-24-installation-single-source-of-truth/`). It writes only the
difference, in one transaction, after the caller's Bot access has been checked,
and never touches runtime.

`InstallationBackfillService` runs that same flush deliberately for one named
Bot, behind the Bearer-token
`POST /api/internal/skill-center/installations/backfill/bot` endpoint. The lazy
flush converges a Bot only when something reads it, which is enough for per-Bot
commands but not for configuration that reaches many Bots at once and has no
per-Bot write to ride on: platform Default-Set content edited through the
`/api/skillsets/admin/*` tooling, an `is_active` flipped straight on the row, or
a `center://` membership resolving to a newly published version. The endpoint is
the tool a backfill invokes for each affected Bot; selecting the Bots and pacing
the calls stays with whoever drives it. It is DB-side only, exactly like the
flush it runs, so a Bot converged this way still needs a runtime projection
before its engine sees the change.

MCP Direct activation and ordinary SkillSet MCP membership share the same
active-only desired-state and best-effort projection boundary as Skills. The MCP
catalogue, user configuration, and permission grant remain separate facts;
the control plane consults the MCP authorization Service API before any MCP
membership or Direct-installation write.

Engine/template Default MCPs never become Installation provenance: Direct
commands reject them, while exclusion removes any legacy Direct row left by an
older process so the installed union cannot bypass policy. Runtime projection
resolves template Default MCP context strictly; a provider failure produces a
pending Runtime observation rather than becoming an empty Default policy.

`RuntimeProjectionResolver` is the only source of Projector snapshots.
`resolve_skills` produces the complete Local/Repo/Center Skill half for a
Skill-only command; `resolve` additionally receives Installation, System
Default MCP and CLI facts and produces the complete Skill/MCP/CLI projection.
The two results have distinct types, so an Engine Adapter cannot interpret an
unresolved Non-Skill half as an empty final state. A whole-artifact runtime's
existing ConfigComposer still rebuilds its persisted MCP artifact at delivery;
CLI authorization remains an overwrite-style Passport projection.

Direct activation and canonical SkillSet mutations commit desired state in the
repository transaction and then project the declared runtime projection
best-effort. A device outage, a missing managed source, or an unmanaged active
entry that cannot safely be replaced is reported as `PENDING` / `DEGRADED`; it
does not restore a committed Installation row.
They do not acquire `SkillsPoolEditGuard`: Pool editing is a file-corpus and
layout-migration concern, retained only by Local package upload/replacement/
deletion and Pool cutover/rollback paths. Phase 1 intentionally has no
cache-backed cross-command Bot mutation fence; durable serialization is
deferred to the task-queue design.

`skill_activation_sync_task.py` is the enqueue half of that durable design:
`skill_center.activation_sync`, one task type shared by every
activation-shaped operation, discriminated by the payload's `action_type` and
deduped on the Bot — `(env, entity_id, bot_id)` — so at most one
synchronization per Bot is ever live. A second operation arriving mid-sync
joins the live task and gets `created=False`; that is only correct while the
handler reconciles against desired state read from the database, so the
handler that consumes these rows must not replay `action_args` as its
desired-state write. `SkillActivationSyncTaskHandler` is a skeleton: it owns
the registry key and the payload validation, and its `_run` seam reports
`Fail` until the body lands. It is not registered, and no call site enqueues,
so the control plane's inline mutate-then-reconcile path is unchanged.

Phase 1 does not run a global Local Installation backfill. Normal
SkillSet/Direct commands continue to maintain Installation synchronously; the
lazy flush reconciles the rows that pre-date the new command path, treating a
Default-Set exclusion as that Set's per-Bot deactivation of the member.

Local Skill replacement is defined only for an existing complete package at the
stable layout-owned `skills-local/<skill-name>` locator. It stages and verifies
the new package, backs up the old package, and publishes the replacement back to
that same locator; the Skill ID, `git_path`, desired active state, membership,
and Installation identity do not change. Staging and rollback directories are
temporary implementation details and are removed before success is returned.
If publication, metadata persistence, audit persistence, or temporary-package
cleanup fails, the old canonical package and metadata are restored before the
request fails. A Runtime projection failure is instead returned as `PENDING` /
`DEGRADED` with the new package and metadata retained. A non-canonical locator or a metadata row
whose authoritative package is missing fails closed and is repaired outside the
upload path.

Public Local Skill deletion first persists a non-purgeable `preparing` record,
then promotes it to `repair_required` before copying and verifying package
bytes in a unique Bot-scoped quarantine. Its one transaction rechecks active
custom SkillSet references, removes the default-set exclusion, all SkillSet
associations, and the Skill row, and makes the retained cleanup work
purgeable. Bot-scoped SkillSet activation takes the same edit lease, so it
cannot publish a stale association while deletion is in flight. If the
transaction fails, the package is restored from quarantine before the request
fails. A post-commit purge failure retains the same durable cleanup work; it
never recreates the deleted Skill.

If a device reports source deletion failure after a partial delete and the
authoritative package cannot be verified repaired, the complete quarantine is
retained as `repair_required` cleanup work. It is deliberately excluded from
ordinary obsolete-byte purge retries until package repair is resolved.
Before a later deletion of that same Local Skill starts, it reacquires the
serialized edit lease and restores any such quarantine to the authoritative
locator; only after that succeeds can the deletion retry. If restoration leaves
a redundant quarantine, ordinary pending cleanup may purge that duplicate.
