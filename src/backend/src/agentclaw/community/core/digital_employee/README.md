# Digital Employee

## Context Boundary

```yaml
purpose: Bind service Bot metadata to digital employees and govern capability changes.
provides:
  - DigitalEmployeeEvent
  - DigitalEmployeeCatalogProtocol
  - DigitalEmployeeServiceProtocol
  - DigitalEmployeePublicationProtocol
consumes:
  - DigitalEmployeePlatformPlugin
  - ExecutionIdentityServiceProtocol
internal_dependencies:
  - agentclaw.community.core.digital_employee
  - agentclaw.community.core.engine_runtime
  - agentclaw.community.core.workspace
  - agentclaw.community.core.mcp
  - agentclaw.community.kernel
  - agentclaw.community.core.skill_center
  - agentclaw.community.core.repository
  - agentclaw.community.core.execution_identity
  - agentclaw.community.plugin_api
  - agentclaw.community.utils
```

### Change impact

The employee association belongs to the Bot metadata, independent of publication
state. External platform identity, credentials, HTTP, and messaging are owned by
enterprise adapters. Messages are validated before mutations and approval results
must identify the exact employee, Bot, request, and publication task.

The authenticated OpenAPI catalog uses OPEN admission with the platform envelope.
Historical membership is recovered from original publication artifacts; complete
scan packages use exact content and refreshed signed URLs. Missing artifacts fail
explicitly. Draft effective MCPs include policy defaults and Skill dependencies.
