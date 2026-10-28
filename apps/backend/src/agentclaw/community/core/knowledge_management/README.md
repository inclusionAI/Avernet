# Knowledge management

This module enriches a bounded provider catalog/grant page with public workspace
and Bot metadata. It does not store knowledge assets, canonical ACLs or grants.
Bot enablement remains owned by the existing bot_knowledge module.

The configured space_tenant identifies the current public space dataset; because
legacy spaces are not tenant-column scoped, reads outside that configured tenant
fail before SQL. Grant platform/environment and composite Bot identity are checked
before enrichment. Compatibility wire names such as kgbase_status describe the
existing browser contract, not a vendor-specific engine dependency.

## Context Boundary

```yaml
purpose: Provider-neutral workspace knowledge management and bounded display aggregation.
provides:
  - KnowledgeManagementService
  - KnowledgeSourceRepository
  - KnowledgeManagementConfig
consumes:
  - KnowledgeCatalog
  - DatabasePlugin
  - SpaceKnowledgeSync
internal_dependencies:
  - agentclaw.community.core.knowledge_management
  - agentclaw.community.core.bot_knowledge
  - agentclaw.community.core.spaces
  - agentclaw.community.plugin_api
  - agentclaw.community.kernel
  - agentclaw.community.utils
```

### Change impact
Management browser clients and external knowledge providers consume this contract.
Configuration is disabled by default. Providers retain native authorization,
content construction and schedule execution. No schema or publication change.
