# Apply engine

Converges one bot to a **typed desired state**: identity files, resources,
skills, MCP servers, CLI tools, the startup script, engine config. The engine
owns what happens *after* a desired state exists — the materialisers that
write each construct through narrow ports, the orchestrator that walks them
in order and reports, the delivery strategies that bind the engine family's
ports and phases, and the apply run (lock, task, persisted report).

It owns none of what *produces* a desired state. Two compilers emit it
through `DesiredStateCompiler`:

- the **Bot Config Manifest** (`core/bot_config_manifest/compiler/`) compiles
  the bot's stored document: grammar, source resolution, fetch, filing bytes
  in the content store;
- the **Genome Registry** (`core/bot_genome/compile.py`) compiles a revision.

The engine never reads a Manifest document and never fetches: every file in
a desired state is already in the content store, named by digest. That is
what lets a revision be delivered with no Manifest document in between
(RSI-04) and what keeps a grammar change out of the materialisers.

A producer can also subscribe to how its applies end (`completion.py`): a
second map keyed by state kind, beside the compilers. Every terminal path of
an apply publishes its `ApplyOutcome` to the kind's subscriber — a finished
run, a refused compile, an engine exception, a task that could not be
rebuilt, and the task queue giving up on the apply task (its give-up hook) —
and the apply lock is released only once the terminal report is stored and
that publish has landed, so the bot's next apply cannot start before the
subscriber has settled, nor beside a re-run of an apply whose report is still
`RUNNING`. A re-run of
an apply whose report is already terminal applies nothing and only publishes
again (then unlocks), so a failed publish is retried by the queue with the
bot still locked. The one exception is the give-up hook: the queue has
already retired the task, so it unlocks regardless, and a subscriber that
raises there is logged and not retried. The genome subscribes; the Manifest does not.

A desired Center skill is found by name and a shared-repository skill by
path through `SharedSkillPort` (`core/ports`), then claimed Direct like any
skill.

The skills and MCP materialisers speak only for the bot's Direct entries:
what the bot's skill sets supply — an active ordinary set, the Default less
its exclusions, the platform's default MCP servers — is never counted as
current and never removed, for every compiler alike. Declaring a
set-supplied entry is a conflict: before writing anything, the orchestrator
asks each such materialiser (`ChecksSetSupply.set_conflicts`) and, on any
conflict, refuses the whole apply — every declared category aborted, nothing
written. The repository's claims refuse the same entries under their own
lock, so Direct and set supply never share an Installation row.

Extracted from `core/bot_config_manifest/apply/` by
`apps/backend/specs/2026-10-23-apply-engine-desired-state/`, which also
records the resources-root rule (the workspace root minus
`core/workspace/protected_entries`, cleared then written on every
apply that declares resources) and the typed state reference an apply run
starts from.

## Context Boundary

```yaml
purpose: Converge one bot to a typed desired state — the DesiredState model, the materialiser contract and the six materialisers, the orchestrator, the delivery strategies and their narrow ports, and the apply run (lock, task, persisted report) — fed by compilers (Manifest, Genome) and never by a document or a fetch.
provides:
  - DesiredState  # the typed input; one Declaration per construct, None when not declared
  - Declaration
  - EntryFailure
  - ContentRef
  - DesiredScript
  - DesiredIdentityFile
  - DesiredSkill  # LocalSkillPackage | CenterSkillByName | RepoSkillPath
  - DesiredResourceFile
  - DesiredResourceTree
  - DesiredMcpServer  # McpOverride, McpHeader, McpTransport, McpEndpointEnv
  - DesiredCliTool
  - DesiredEngineConfig
  - DesiredStateCompiler  # the compiler contract both producers implement
  - StateRef  # (kind, reference) — what an apply run converges to; each compiler registers its kind
  - ApplyCompletionHandler  # completion.py: a producer's subscriber, told when an apply of its kind ends
  - ApplyOutcome
  - StateCompletionHandlers  # kind → subscriber, the second map beside the compilers
  - CompletionPublisher  # completion.py: hands an ended apply's outcome to its kind's subscriber (run service, unstartable path)
  - Compiled
  - ContentReader  # imported from core/content_store; ContentStoreProtocol inherits it
  - Category  # the construct vocabulary (constructs.py)
  - Section
  - Construct
  - ApplyConstruct
  - kind_of
  - parse_category
  - Materialiser  # resolve → plan → write, per construct (registry.py)
  - Intent
  - ResolveResult
  - CategoryPlan
  - build_materialisers
  - ApplyOrchestrator
  - ApplyContext
  - ApplyReport  # and CategoryResult, EntryResult, EntryOutcome, ApplyStatus, SourceResolution
  - ApplyPhase
  - APPLY_ORDER
  - DeliveryStrategy  # ArcaDelivery, TeclawPlatformDelivery, TeclawDeviceDelivery, DeliveryStrategyFactory
  - MaterialiserPorts
  - TeclawDeliveryMode
  - ApplyRunService
  - ApplyRunServiceProtocol  # run/service_protocol.py; api/apply_run_service.py re-exports it
  - ApplyAccepted
  - ApplyTaskHandler
  - ApplyTaskLifecycle
  - APPLY_TASK_TYPE
  - build_apply_task_payload
  - BotConfigManifestApplyRecord  # the persisted apply row (table keeps its historical name)
  - Capabilities  # which constructs a bot can be told to have (capabilities.py); resolve_capabilities, capabilities_for_bot, CapabilityResolver
  - SourceKind  # and source_matrix: which (construct, source kind) pairs are open (support_matrix.py)
  - legal_identity_types  # and RESERVED_IDENTITY_FILES (identity_types.py)
  - unpack_archive  # UnpackedTree, UnpackError, ARCHIVE_MEMBER_LIMIT, ARCHIVE_UNPACKED_LIMIT — the guarded archive unpack
  - CREATE_PRE_CONTAINER_TRIGGER  # and CREATE_ON_CONTAINER_TRIGGER, ALL_TRIGGERS (triggers.py)
  - CliToolService  # and the cli_tools package it moved with: CliToolDecl, CliToolStore, CliToolContext, the delivery ports, BotCliToolService, BotCliToolServiceProtocol
  - ManagedFilesStore  # and ManagedFilesComposeReader, EngineOwnedComposeReader, PlatformIdentity / PlatformResource / PlatformSkillPackageUpload (managed_files/)
consumes:
  - "ContentStoreProtocol (core.content_store) — the bytes every desired entry names, read by digest"
  - "IdentityFilePort, ResourceFilePort, SkillPackageUploadPort, ActivationPort, SharedSkillPort (core.ports) — the narrow ports the materialisers call"
  - "BotCapabilityStateReaderProtocol, SkillPackageValidator, DirectActivationServiceProtocol (core.skill_center) — the skills and MCP area state and the package gate"
  - "MCPAuthServiceProtocol, MCPConfigServiceProtocol (core.mcp) — the MCP permission and override checks"
  - "BotStartupScriptServiceProtocol (core.bot_startup_script) — the script row"
  - "ResourceFileService, ResourceService (core.services, core.resources) — the resources root and its rows"
  - "DeviceFilesystem (core.devices) — the device road the resource and identity delegates write through"
  - "BotConfigManifestApplyRepositoryProtocol and the lock repository (core.repository) — the persisted apply run"
  - "TaskQueue (core.task_queue) — the apply task"
consumed_by:
  - "core.bot_config_manifest — compiles a stored document into a DesiredState and starts apply runs with MANIFEST_STATE, kind 'manifest'; the creation flow applies its two phases through ApplyRunService"
  - "core.bot_genome — compiles a revision into a DesiredState, starts apply runs with genome_state(promotion_id), kind 'genome', and subscribes to their completion to settle the promotion"
  - "adapters/http/openapi_v1/bots — the apply, last-apply and create-with-manifest routes"
  - "di/modules/apply_engine_module, bot_management_module — binds ports, strategies, compilers and the run service"
  - "plugins/community/agentswarm — reads a bot's last apply"
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.core.bot_management.engines.registry  # the runtime-engine routing policy the resources materialiser addresses workspaces through
  - agentclaw.community.core.bot_startup_script
  - agentclaw.community.core.config_compose.models  # the collector-shaped refs the managed-files reader yields to the teclaw composer
  - agentclaw.community.core.config_compose.protocols  # ManagedFilesReader + PlatformOwnershipReader, the compose seams the managed-files reader inherits
  - agentclaw.community.core.content_store
  - agentclaw.community.core.mcp
  - agentclaw.community.core.ports
  - agentclaw.community.core.repository
  - agentclaw.community.core.resources
  - agentclaw.community.core.services
  - agentclaw.community.core.skill_center
  - agentclaw.community.core.task_queue
  - agentclaw.community.core.workspace
  - agentclaw.community.kernel  # OwnershipCategory (bot_config) for the managed-files reader; lifecycle for the apply task handler
  - agentclaw.community.log
  - agentclaw.community.plugin_api.device_adapter_transport  # the one engine channel core can reach; the ARCA cli_tools port POSTs an install through it
  - agentclaw.community.plugin_api.object_storage  # the bot-data object store the managed-files store and the cli_tools store write into
  - agentclaw.community.utils
```

### Change impact

- A new construct: add the enum member in `constructs.py`, its position in
  `order.py`, a materialiser under `materialisers/`, its entry type in
  `desired_state.py`, and teach both compilers to emit it. A construct with
  no materialiser is an expected state: the orchestrator fails its entries
  with a readable reason and touches nothing.
- A new engine family: a delivery strategy in `delivery.py` with its phase
  table and port bundle, bound in `di/modules/apply_engine_module.py`.
- A new compiler: implement `DesiredStateCompiler` for a new `StateRef`
  type and register it in the compilers map; nothing in the engine changes.
