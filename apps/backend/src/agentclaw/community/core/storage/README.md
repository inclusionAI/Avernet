# `agentclaw.community.core.storage`

Storage-path utilities — central path-resolution for OSS/NAS/local storage roots.

## Context Boundary

```yaml
purpose: "Storage-path utilities — central path-resolution for OSS/NAS/local storage roots."
provides:
  - "StoragePath helpers"
consumes:
  - "core.config"
internal_dependencies:
  - agentclaw.community.core.config
```

### Change impact

Path-resolution bugs are silent — they manifest as file-not-found in unrelated callers. Treat path-computation as a stable contract.
