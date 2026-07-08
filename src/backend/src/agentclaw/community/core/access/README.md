# `agentclaw.community.core.access`

Access-control domain — ACL policies, user info, permission evaluation.

## Context Boundary

```yaml
purpose: "Access-control domain — ACL policies, user info, permission evaluation."
provides:
  - "PolicyService"
  - "UserService"
  - "admin_scopes (config-driven privileged-user allow-lists)"
  - "Access SQLAlchemy models (ac_access_control_policy, ac_user_info)"
consumes:
  - "DatabasePlugin (via SQLAlchemy session)"
internal_dependencies:
  - agentclaw.community.core.config
  - agentclaw.community.log
  - agentclaw.community.plugin_api.models
  - agentclaw.community.utils.env_utils
```

### Change impact

Schema changes here require migrations (currently SQLite-only locally). Policy evaluation is invoked on most authenticated routes — a bug yields false allow/deny across the app.
