# `agentclaw.community.plugins.local`

Local-mode plugin implementations — in-memory / fixture-backed / noop variants for offline dev and tests.

## Context Boundary

```yaml
purpose: "Local-mode plugin implementations — in-memory / fixture-backed / noop variants for offline dev and tests."
provides:
  - "19 Local* and Noop* classes implementing the 23 plugin Protocols"
  - "Shared SQLite ORM models for local persistence"
consumes:
  - "Every plugin Protocol (agentclaw.community.plugin_api.*)"
  - "Some core domain repository protocols (injected for sandbox-resolution etc.)"
internal_dependencies:
  - agentclaw.community.core.access
  - agentclaw.community.core.auth
  - agentclaw.community.core.base
  - agentclaw.community.core.bot_chat
  - agentclaw.community.core.bot_dormant   # SQLite ORM side-effect import for local table creation
  - agentclaw.community.core.economy.governance.contracts.models  # SQLite ORM side-effect import for local table creation
  - agentclaw.community.core.economy.governance.repositories.orm  # SQLite ORM side-effect import for local table creation
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.bot_public
  - agentclaw.community.core.bot_chat
  - agentclaw.community.core.devices
  - agentclaw.community.core.errors
  - agentclaw.community.core.expert_chat
  - agentclaw.community.core.harness
  - agentclaw.community.core.models
  - agentclaw.community.core.operator_context
  - agentclaw.community.core.service_bot
  - agentclaw.community.core.skill_center
  - agentclaw.community.core.task_queue   # SQLite ORM side-effect import for local table creation
  - agentclaw.community.core.workspace
  - agentclaw.community.kernel
  - agentclaw.community.log
  - agentclaw.community.plugin_api.approval_workflow
  - agentclaw.community.plugin_api.auth
  - agentclaw.community.plugin_api.auth_relationship
  - agentclaw.community.plugin_api.base
  - agentclaw.community.plugin_api.bot_publish_approval
  - agentclaw.community.plugin_api.cache
  - agentclaw.community.plugin_api.database
  - agentclaw.community.plugin_api.device_adapter_transport
  - agentclaw.community.plugin_api.device_connection_manager
  - agentclaw.community.core.devices.services.device_filesystem
  - agentclaw.community.plugin_api.device_mcp_sync
  - agentclaw.community.plugin_api.device_sync
  - agentclaw.community.plugin_api.devices
  - agentclaw.community.plugin_api.drm
  - agentclaw.community.plugin_api.engine_ext_client
  - agentclaw.community.plugin_api.health_probe
  - agentclaw.community.plugin_api.http_client
  - agentclaw.community.plugin_api.impl_registry
  - agentclaw.community.plugin_api.mcp_auth
  - agentclaw.community.plugin_api.mcp_center
  - agentclaw.community.plugin_api.notify_sender
  - agentclaw.community.plugin_api.model_api
  - agentclaw.community.plugin_api.models
  - agentclaw.community.plugin_api.object_storage
  - agentclaw.community.plugin_api.outbound_rules
  - agentclaw.community.plugin_api.passport
  - agentclaw.community.plugin_api.sandbox_runtime
  - agentclaw.community.plugin_api.secret_resolver
  - agentclaw.community.plugin_api.skill_center_client
  - agentclaw.community.plugin_api.skill_repo_sync
  - agentclaw.community.plugin_api.skill_scanner
  - agentclaw.community.plugin_api.storage
  - agentclaw.community.plugin_api.token_exchange
  - agentclaw.community.plugin_api.tracer
  - agentclaw.community.plugins.prod.baas_service    # LocalBaasService inherits ProdBaasService to reuse httpx logic, override URL only
  - agentclaw.community.utils.env_utils
```

### Change impact

Local-mode breakage shows up only when running ./scripts/local_setup.sh --local. Adding a new plugin Protocol requires a paired local impl here per Rule 20. Removing the local impl breaks offline dev for the entire feature.
