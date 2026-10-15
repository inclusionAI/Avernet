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
`api`. A change to a port method signature ripples to the plugin implementation,
the adapter that calls it, and the shared skills delivery contract.
`rollback_pool_layout` is additive and is invoked only by explicit
Pool-to-Legacy recovery; old images remain valid for ordinary operation. The port grows one
per-domain Protocol per vertical slice in Groups C (gateway) and D (local-infra);
see `specs/2026-05-31-engine-arch-f2-openclaw-acl/port-design-notes.md` for the
full method catalog and the native-shape / token conventions.

### File-address compatibility

The concrete OpenClaw file port accepts the existing pre/prod storage-view
addresses and, additively, the simulator address form
`/home/admin/.aidesktop/aidesktop_dev/bolt_data/<owner>/<bot>/openclaw/<relative>`.
Both resolve to `<relative>` inside `/home/admin/.openclaw` in the selected Bot
container. Read, upload, remove, recursive removal, and directory listing share
this conversion. Owner/Bot segments describe the legacy address; they do not
select another container or replace transport authentication.

The existing pre/prod grammar, singlebox workspace-root handling, and direct-path
passthrough are unchanged. This compatibility mapping does not mount storage,
copy files, select a Skills layout, or make old Engine images understand the new
address form; simulator Bots must load an image containing the change.
