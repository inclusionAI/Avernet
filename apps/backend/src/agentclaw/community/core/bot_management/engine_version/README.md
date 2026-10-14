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
  - DeviceBindingRepository
  - CollaboratorServiceProtocol
  - DeviceContextResolver
  - EngineReleaseProbe
internal_dependencies:
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.bot_management.services.bot_service
  - agentclaw.community.core.devices.services
  - agentclaw.community.core.engine_runtime
  - agentclaw.community.core.repository.protocols
```

### Change impact

HTTP delivery calls the version Service API. DI injects a frozen deployment
release and the existing BaaS device-list callable (five-second deadline).
Repositories and the device transport are existing external seams. The query
performs no publish-policy resolution, cache mutation, or lifecycle operation.
The injected fixed-probe adapter reads only AGENTCLAW_IMAGE_VERSION and translates
HTTP failures into Detection/CurrentVersion, leaving no HTTP status types in core. Current release and
restart eligibility are independent facts. Missing metadata is legacy;
capability/transport/command errors are never legacy. Multiple instances need
explicit selection. The comparison identity is the complete release branch;
only strict valid RELYYYYMMDD branches are ordered. A newer Bot never receives
a downgrade hint.

The target is a paired platform release, not an online image-registry lookup.
Only OpenClaw and Hermes are inspected. Other engines return not_applicable /
engine_not_supported and restart_hint=none after authorization and draft checks,
before binding lookup, inventory, connection resolution or the fixed probe.
Their comparison remains unknown because no comparison was attempted.

Hints depend only on release facts: older/legacy recommends restart, same/newer
needs none, and failed reads, missing targets or incomparable branches remain
unknown. Provider and image Pin/template policy do not gate this advisory.
The service does not read template policy or change existing restart behavior.
Restricted remains in the response type for compatibility but is not emitted.
Deployment acceptance must keep default images aligned with the paired release
and prevent independent version-marker overrides.

Conformance is exercised at the Service API and signed HTTP seam in
`tests/community/core/engine_version/` and
`tests/community/adapters/http/openapi_v1/test_engine_version.py`.
