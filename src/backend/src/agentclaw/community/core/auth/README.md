# `agentclaw.community.core.auth`

Authentication domain — user identity model (`BuserviceUser`). FastAPI dependencies live in `api/auth/` (Rule 7).

## Context Boundary

```yaml
purpose: "Authentication domain — user identity model (BuserviceUser). FastAPI dependencies live in api/auth/ (Rule 7)."
provides:
  - "BuserviceUser"
consumes:
  - "AuthPlugin (used by api/auth/dependencies.py)"
internal_dependencies:
  - agentclaw.community.plugin_api.auth
```

### Change impact

Adding fields to BuserviceUser ripples through every endpoint that injects get_current_user. Renaming or removing a field is a breaking change for all api/* routes.
