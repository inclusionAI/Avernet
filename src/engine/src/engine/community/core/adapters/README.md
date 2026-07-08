# `engine.community.core.adapters`

The Anti-Corruption Layer. Per-engine adapters implement the granular core
`*Service` protocols by converting DTOs to/from the engine's native shape and
delegating to the engine's `engine.community.plugin_api` port.

## Context Boundary

```yaml
purpose: "Per-engine ACL adapters that implement core *Service protocols by translating to/from an engine.community.plugin_api port, and guard capability."
provides:
  - "(none yet — adapters land in F2)"
consumes:
  - "SessionService, ChatService, …"   # core *Service protocols implemented
  - "<Engine>PortPlugin"               # engine.community.plugin_api port delegated to
internal_dependencies:
  - engine.community.core
  - engine.community.plugin_api
  - engine.community.kernel
```

### Change impact

Empty skeleton in F1. This is the **only** part of `core` that knows about a
specific engine's port. It imports the port *abstraction* from
`engine.community.plugin_api`, never the concrete from `engine.plugins` (`engine.community.di`
injects that) — so the "core does not import plugins" contract holds for all of
`core`, including here. Capability surface for an engine is determined here: a
capability exists only if its adapter wires a native method for it.
