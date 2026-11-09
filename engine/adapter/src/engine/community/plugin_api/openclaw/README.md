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

### Session creation result

`session_create` performs one `sessions.patch` RPC. It keeps the legacy request
parameters and adds `canonical_session_id` from the acknowledged `key`, plus
`resolved_model` from `resolved.modelProvider` / `resolved.model`. Both are
required; an incomplete acknowledgement is an upstream error, not a reason to
list sessions or retry creation.

The core adapter exposes these two values as
`ext_info.openclaw_creation.{session_id,model}` on `POST /api/sessions`. Existing
`id`, `title`, `model`, and timestamp fields retain their legacy meanings. No
store path, transcript ID, or raw gateway payload is exposed.

The Backend OpenAPI create route consumes this metadata to return the canonical
session ID and effective model directly, without list/history readback or sleeps.
For older Adapters without this metadata (or an incomplete metadata response),
Backend keeps the legacy best-effort list reconciliation: at most three reads,
with delays of 0, 0.5, and 1.5 seconds, on the same Bot, stage, and device instance.
It preserves the creation timestamps and requested title when merging a match.
If reconciliation fails or finds no match, the original successful creation
response is returned; creation is never retried. Incomplete metadata is logged
without exposing response contents. Updated Adapters always take the fast path.
Other engines and the friend-caller path are unchanged. The local plugin supplies
the same contract using its stored session key and local model.
