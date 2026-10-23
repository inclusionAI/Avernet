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

Extracted from `core/bot_config_manifest/apply/` by
`apps/backend/specs/2026-10-23-apply-engine-desired-state/`, which also
records the resources-root rule (`data/`, cleared then written on every
apply that declares resources) and the typed state reference an apply run
starts from.

## Context Boundary

```yaml
purpose: Converge one bot to a typed desired state — the DesiredState model, the materialiser contract and the six materialisers, the orchestrator, the delivery strategies and their narrow ports, and the apply run (lock, task, persisted report) — fed by compilers (Manifest, Genome) and never by a document or a fetch.
provides:
  - DesiredState  # the typed input; one Declaration per construct, None when not declared
  - Declaration
  - EntryFailure
  - StoredContent
  - DesiredScript
  - DesiredIdentityFile
  - DesiredSkill  # LocalSkillPackage | CenterSkillVersion | RepoSkillPath
  - DesiredResourceFile
  - DesiredResourceTree
  - DesiredMcpServer  # McpOverride, McpHeader, McpTransport, McpEndpointEnv
  - DesiredCliTool
  - DesiredEngineConfig
  - RESOURCES_ROOT  # data/ — the one directory resources live under
  - DesiredStateCompiler  # the compiler contract both producers implement
  - StateRef  # ManifestStateRef | GenomeStateRef — what an apply run converges to
  - Compiled
  - ContentReader  # the engine's read port; ContentStoreProtocol satisfies it
  - ManifestCategory  # the construct vocabulary (constructs.py)
  - ManifestSection
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
  - ApplyRunServiceProtocol  # api/apply_run_service.py re-exports it
  - ApplyAccepted
  - ApplyTaskHandler
  - ApplyTaskLifecycle
  - APPLY_TASK_TYPE
  - build_apply_task_payload
  - BotConfigManifestApplyRecord  # the persisted apply row (table keeps its historical name)
  - CliToolService  # and the cli_tools package it moved with
consumes:
  - "ContentStoreProtocol (core.content_store) — the bytes every desired entry names, read by digest"
  - "IdentityFilePort, ResourceFilePort, SkillPackageUploadPort, ActivationPort (core.ports) — the narrow write ports the materialisers call"
  - "BotCapabilityStateReaderProtocol, SkillPackageValidator, DirectActivationServiceProtocol (core.skill_center) — the skills and MCP area state and the package gate"
  - "MCPAuthServiceProtocol, MCPConfigServiceProtocol (core.mcp) — the MCP permission and override checks"
  - "BotStartupScriptServiceProtocol (core.bot_startup_script) — the script row"
  - "ResourceFileService, ResourceService (core.services, core.resources) — the resources root and its rows"
  - "DeviceFilesystem (core.devices) — the device road the resource and identity delegates write through"
  - "BotConfigManifestApplyRepositoryProtocol and the lock repository (core.repository) — the persisted apply run"
  - "TaskQueue (core.task_queue) — the apply task"
consumed_by:
  - "core.bot_config_manifest — compiles a stored document into a DesiredState and starts apply runs with ManifestStateRef; the creation flow applies its two phases through ApplyRunService"
  - "core.bot_genome — compiles a revision into a DesiredState (RSI-04 starts apply runs with GenomeStateRef)"
  - "adapters/http/openapi_v1/bots — the apply, last-apply and create-with-manifest routes"
  - "di/modules/apply_engine_module, bot_management_module — binds ports, strategies, compilers and the run service"
  - "plugins/community/agentswarm — reads a bot's last apply"
internal_dependencies:
  - agentclaw.community.api
  - agentclaw.community.core.base
  - agentclaw.community.core.bot_startup_script
  - agentclaw.community.core.content_store
  - agentclaw.community.core.devices
  - agentclaw.community.core.mcp
  - agentclaw.community.core.ports
  - agentclaw.community.core.repository
  - agentclaw.community.core.resources
  - agentclaw.community.core.services
  - agentclaw.community.core.skill_center
  - agentclaw.community.core.storage
  - agentclaw.community.core.task_queue
  - agentclaw.community.core.workspace
  - agentclaw.community.log
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
