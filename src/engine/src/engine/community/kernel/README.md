# `engine.community.kernel`

Bottom layer: primitives shared by `core` internals and `plugins`. Because
`plugins` must not import `core`, anything both sides need lives here.

## Context Boundary

```yaml
purpose: "Foundational primitives shared by core internals and plugin transport (e.g. wire frames); the bottom layer, importing nothing internal."
provides:
  - "engine.community.kernel.frames — wire-protocol envelope types (RequestFrame, ResponseFrame, EventFrame, ErrorShape, StateVersion, ErrorCodes) + protocol constants"
consumes:
  []
internal_dependencies:
  []
```

### Change impact

`kernel/` imports nothing under `engine.*` (enforced by the "kernel is the
bottom layer" contract, which since F2 also forbids `engine.community.config` /
`engine.community.shared`), so it is safe for every layer to depend on. The first
inhabitant (F2) is `frames` — moved down from `engine.community.core.protocol.frames`
because engine transport (in `plugins`) produces it and `core` consumes it; a
change to a frame shape then ripples to both sides.
