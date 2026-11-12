# Task panel discovery

Only `GET /api/v1/collaboration/tasks/manifest` exposes the authenticated host
asset descriptor. There is no OpenAPI mirror and no task-execution behavior here.
The service reads `category=task`, `config_key=panel` from system-config using the
server environment on each request; a missing or malformed value fails closed.
Administrators use the existing config category/set endpoints to create the
category and update the value. The task panel writer validates the schema and
requires operator permission; existing boolean task settings are unchanged.

## Context Boundary

```yaml
purpose: Validate and discover environment-scoped task panel frontend assets.
provides:
  - TaskPanelManifestService
  - TaskPanelManifestServiceProtocol
  - PanelManifestConfig
consumes:
  - SystemConfigServiceProtocol
internal_dependencies:
  - agentclaw.community.core.errors
  - agentclaw.community.core.system_config
```

### Change impact

Production builds of both hosts discover taskPanel through this single API.
Local development loads the local UMD/CSS directly and does not request it.
A configuration update takes effect on the next page load without host rebuild
or service restart. The shared UI remains an independently published frontend
package; no task execution, reporting, state transition or Relay contract changes.
