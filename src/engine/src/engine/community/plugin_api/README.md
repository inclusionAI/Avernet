# `engine.community.plugin_api`

Per-engine plugin Protocol declarations — the shared abstraction both the
`core/adapters/` ACL and the `plugins/` impls depend on.

## Context Boundary

```yaml
purpose: "Per-engine plugin Protocols (OpenClawPlugin, …) — the native-shaped interface each engine's ACL adapter delegates to."
provides:
  - "(none yet — per-engine ports land in F2)"
consumes:
  []
internal_dependencies:
  - engine.community.kernel
```

### Change impact

Empty skeleton in F1. Once populated (F2+), changing a port Protocol's
signature breaks that engine's `plugins/{prod,local}/<engine>/` impl and its
`core/adapters/<engine>/` adapter — and nothing else, since the port is the
only contract crossing the core↔plugins boundary. This package imports neither
`engine.community.core` nor `engine.plugins`; that is what keeps the graph acyclic.
