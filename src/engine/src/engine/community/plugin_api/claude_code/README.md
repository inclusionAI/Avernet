# `engine.community.plugin_api.claude_code`

The claude_code engine's **native port** — the shared abstraction both the
concrete impl (`plugins/`) and the ACL adapters (`core/adapters/claude_code/`)
depend on. Classic DIP: `core/adapters -> plugin_api <- plugins/prod`.

## Context Boundary

```yaml
purpose: "claude_code's engine-owned native operation surface (ClaudeCodePlugin), expressed in native shapes (dicts + kernel frames), so core adapters and the prod impl share one abstraction without core<->plugins coupling."
provides:
  - "engine.community.plugin_api.claude_code.ClaudeCodePlugin — aggregate native port Protocol (per-domain ports composed in)"
  - "Per-domain port Protocols: ClaudeCodeChatPort, ClaudeCodeSessionPort, ClaudeCodeMcpPort, ClaudeCodeSkillsPort, ClaudeCodeCronPort, ClaudeCodeModelsPort, ClaudeCodeFilePort, ClaudeCodeCommandsPort, ClaudeCodeRelayPort"
consumes:
  []
internal_dependencies:
  - engine.community.kernel
```

### Change impact

Imports only `engine.community.kernel` (+ stdlib/typing) — never `core`, `plugins`, or
`api`. A change to a port method signature ripples to the plugin implementation,
the adapter that calls it, and the shared skills delivery contract.
`rollback_pool_layout` is additive and is invoked only by explicit
Pool-to-Legacy recovery; old images remain valid for ordinary operation. The port grows one
per-domain Protocol per vertical slice; see
`specs/2026-07-01-engine-claude-code-acl-opensource/` for the full method
catalog and the native-shape / token conventions.
