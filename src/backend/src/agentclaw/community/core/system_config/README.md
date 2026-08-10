# `agentclaw.community.core.system_config`

System-level configuration — runtime feature flags, env-driven knobs.

## Context Boundary

```yaml
purpose: "System-level configuration — runtime feature flags, env-driven knobs."
provides:
  - "SystemConfigService"
  - "SystemConfig SQLAlchemy models"
consumes:
  []
internal_dependencies:
  - agentclaw.community.core.repository.protocols.config    # repository contracts consumed by this module
  - agentclaw.community.core.base    # declarative Base for the system_config-owned ORM models
  - agentclaw.community.log
```

### Change impact

Config-key churn breaks downstream readers silently. Adding a new flag without a sensible default risks crashing on startup in environments that have not shipped the config row.
