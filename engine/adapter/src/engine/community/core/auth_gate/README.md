# `engine.community.core.auth_gate`

The auth-gate **domain facade** — re-exports the `AuthGateService` port and
`VerifyResult` from `plugin_api/auth_gate/` for `core`/`api` consumers (the WS
transport gate). Keeps upper layers depending on a `core`-level name while the
actual port lives in `plugin_api` (so leaf plugins avoid a `plugins ↛ core` edge).

## Context Boundary

```yaml
purpose: "core-layer entry point for auth-gating: exposes AuthGateService / VerifyResult to api and transport, delegating the abstraction to plugin_api.auth_gate."
provides:
  - "engine.community.core.auth_gate.AuthGateService"
  - "engine.community.core.auth_gate.VerifyResult"
consumes:
  - "engine.community.plugin_api.auth_gate.AuthGateService"
internal_dependencies:
  - engine.community.plugin_api.auth_gate
```

### Change impact

Thin layer over `plugin_api.auth_gate`; changes here are mostly re-export surface.
Consumers: `api/transport/ws_server.py` (the per-connection gate) and the DI wiring
that injects the resolved `AuthGateService`.
