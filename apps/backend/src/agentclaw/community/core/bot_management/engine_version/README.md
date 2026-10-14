# Engine release inspection

## Context Boundary

```yaml
purpose: Read one authorized workspace Engine release and advise about its paired platform release without writes.
provides:
  - EngineVersionServiceProtocol
  - EngineVersionService
  - EngineVersionConfig
  - EngineVersionResult
consumes:
  - BotRepository
  - TemplateRepository
  - DeviceBindingRepository
  - CollaboratorServiceProtocol
  - DeviceContextResolver
  - DeviceAdapterTransport
internal_dependencies:
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.bot_management.services.bot_service
  - agentclaw.community.core.devices.services
  - agentclaw.community.core.engine_runtime
  - agentclaw.community.core.repository.protocols
  - agentclaw.community.core.service_bot.services.arca_image_pin
  - agentclaw.community.plugin_api.device_adapter_transport
```

### Change impact

HTTP delivery calls the version Service API. DI injects a frozen deployment
release and the existing BaaS device-list callable (five-second deadline).
Repositories and the device transport are existing external seams. The query
performs no publish-policy resolution, cache mutation, or lifecycle operation.
The fixed bash call reads only AGENTCLAW_IMAGE_VERSION. Current release and
restart eligibility are independent facts. Missing metadata is legacy;
capability/transport/command errors are never legacy. Multiple instances need
explicit selection. The comparison identity is the complete release branch;
only strict valid RELYYYYMMDD branches are ordered. A newer Bot never receives
a downgrade hint. A configured template version override invalidates identity.

The target is a paired platform release, not an online image-registry lookup.
Deployment acceptance must keep default templates aligned with that release
and prevent independent environment overrides. Explicit image overrides are
restricted; personal ext Pins persist while service-draft restarts clear them.
Teclaw's existing restart path is unsupported, so a version difference is
returned with a restricted hint. Runtime variants without the probe endpoint
return read_failed; this does not claim cross-provider deployment acceptance.

Conformance is exercised at the Service API and signed HTTP seam in
`tests/community/core/engine_version/` and
`tests/community/adapters/http/openapi_v1/test_engine_version.py`.
