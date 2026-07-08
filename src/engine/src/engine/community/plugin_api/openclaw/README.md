# `engine.community.plugin_api.openclaw`

The OpenClaw engine's **native port** — the shared abstraction both the concrete
impl (`plugins/prod/openclaw/`) and the ACL adapters (`core/adapters/openclaw/`)
depend on. Classic DIP: `core/adapters → plugin_api ← plugins/prod`.

## Context Boundary

```yaml
purpose: "OpenClaw's engine-owned native operation surface (OpenClawPlugin), expressed in native shapes (dicts + kernel frames), so core adapters and the prod impl share one abstraction without core<->plugins coupling."
provides:
  - "engine.community.plugin_api.openclaw.OpenClawPlugin — aggregate native port Protocol (per-domain ports composed in)"
  - "engine.community.plugin_api.openclaw.EngineToken — per-call routing key (str | None), the AuthContext stand-in"
consumes:
  []
internal_dependencies:
  - engine.community.kernel
```

### Change impact

Imports only `engine.community.kernel` (+ stdlib/typing) — never `core`, `plugins`, or
`api`. A change to a port method signature ripples to exactly two places: the
prod impl that satisfies it and the adapter that calls it. The port grows one
per-domain Protocol per vertical slice in Groups C (gateway) and D (local-infra);
see `specs/2026-05-31-engine-arch-f2-openclaw-acl/port-design-notes.md` for the
full method catalog and the native-shape / token conventions.
