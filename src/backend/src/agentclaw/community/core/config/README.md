# `agentclaw.community.core.config`

Configuration loading — runtime-mode-agnostic config parsing for the backend.

## Context Boundary

```yaml
purpose: "Configuration loading — runtime-mode-agnostic config parsing for the backend."
provides:
  - "Config loader entry points"
consumes:
  - "Device models (for cross-validation)"
internal_dependencies:
  - agentclaw.community.core.devices
  - agentclaw.community.utils
```

### Change impact

Config keys are referenced widely; renaming a key without an alias breaks startup. Most config consumers don't currently use a schema, so silent breakage on rename is possible.
