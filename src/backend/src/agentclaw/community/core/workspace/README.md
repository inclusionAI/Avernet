# `agentclaw.community.core.workspace`

Workspace path factory + workspace constants — single source of truth for per-bot / per-user paths.

## Context Boundary

```yaml
purpose: "Workspace path factory + workspace constants — single source of truth for per-bot / per-user paths."
provides:
  - "WorkspacePathFactory"
  - "DEFAULT_ENGINE_TYPE, SUPPORTED_ENGINE_TYPES constants"
  - "EngineSandboxProvider Protocol + OpenClaw / ClaudeCode impls (mode-blind, take WorkspaceConfig)"
consumes:
  - "SkillRepoSyncPlugin"
  - "WorkspaceConfig (typed dataclass from agentclaw.community.di.config)"
internal_dependencies:
  - agentclaw.community.di
  - agentclaw.community.log
  - agentclaw.community.plugin_api.skill_repo_sync
  - agentclaw.community.utils
  - agentclaw.community.utils.env_utils
```

### Change impact

Path-layout changes ripple to every consumer (engine, device, skill_center, resources). Versioning the layout is preferable to in-place renames.
