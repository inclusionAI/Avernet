# Task panel discovery

`GET /api/v1/collaboration/tasks/manifest` exposes the authenticated host
asset descriptor. `POST` on the same path requires a signed-in user and
creates or fully replaces the manifest for the server environment. There is no
OpenAPI mirror and no task-execution behavior here.
The service reads `category=task`, `config_key=panel` from system-config using the
server environment on each request; a missing or malformed value fails closed.
The task-owned writer validates the complete schema and asset URL before any
storage write, creates the category if absent, and rejects storage failures.
Its request is `{schema_version: 1, bundles: [{name: "taskPanel", type: "url",
url: "https://<asset-host>/<release>/index.umd.js"}]}`; unknown fields are rejected.
Both methods return the same manifest envelope; invalid writes return 422,
storage failures return 503, and successful responses use `Cache-Control: no-store`.
The task module reuses the existing configuration service without modifying its
HTTP interfaces, permissions or storage contract.
Existing boolean task settings are unchanged.

## Context Boundary

```yaml
purpose: Validate, save and discover environment-scoped task panel frontend assets.
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
