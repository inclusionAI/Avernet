# `engine.community.plugin_api`

Per-engine plugin Protocol declarations — the shared abstraction both the
`core/adapters/` ACL and the `plugins/` impls depend on.

## Context Boundary

```yaml
purpose: "Per-engine plugin Protocols (OpenClawPlugin, …) — the native-shaped interface each engine's ACL adapter delegates to."
provides:
  - "OpenClawPlugin and its per-domain native port Protocols"
  - "ClaudeCodePlugin and its per-domain native port Protocols"
consumes:
  []
internal_dependencies:
  - engine.community.kernel
```

### Change impact

Changing a port Protocol's signature affects that engine's plugin
implementation, its `core/adapters/<engine>/` adapter, and the shared skills
HTTP delivery surface. Skills Pool rollback is additive: new Backend and new
Engine use `rollback_pool_layout`; old images remain compatible until an
operator explicitly requests rollback. This package imports neither
`engine.community.core` nor `engine.plugins`, preserving the acyclic graph.
