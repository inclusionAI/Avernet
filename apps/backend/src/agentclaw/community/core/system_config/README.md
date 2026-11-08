# `agentclaw.community.core.system_config`

System-level configuration — runtime feature flags, env-driven knobs.

## Context Boundary

```yaml
purpose: "System-level configuration — runtime feature flags, env-driven knobs."
provides:
  - "SystemConfigService"
  - "SystemConfig SQLAlchemy models"
consumes:
  - DwsRolloutIdentity
internal_dependencies:
  - agentclaw.community.core.repository.protocols.config    # repository contracts consumed by this module
  - agentclaw.community.core.base    # declarative Base for the system_config-owned ORM models
  - agentclaw.community.log
  - agentclaw.community.plugin_api.dws_identity
```

### Change impact

Config-key churn breaks downstream readers silently. Adding a new flag without a sensible default risks crashing on startup in environments that have not shipped the config row.

DWS rollout uses the composition-selected `DwsRolloutIdentity` v1 policy only
for allowlist comparison. The default is exact opaque-ID matching. Policies
must not rewrite stored configuration, signed principals or Bot owner checks.
The public eligibility, command verification and runtime eligibility routes
share this policy; invalid/empty comparison keys never grant access.
