# `engine.community.plugin_api.auth_gate`

The **auth-gate port** — the `AuthGateService` Protocol and its `VerifyResult`
DTO. This is the shared abstraction the WS transport calls into; the corp
zero-check impl (`plugins/prod/auth_gate/`) and the community no-op
(`plugins/community/auth_gate/`) both satisfy it.

## Context Boundary

```yaml
purpose: "Token-verification port (AuthGateService.verify -> VerifyResult) so the WS transport gates connections without coupling to a concrete auth backend (corp zero-check vs community no-op)."
provides:
  - "engine.community.plugin_api.auth_gate.AuthGateService — verification port Protocol"
  - "engine.community.plugin_api.auth_gate.VerifyResult — verification outcome DTO"
consumes:
  []
internal_dependencies:
  []
```

### Change impact

A pure port: imports only its own `models` (+ stdlib/typing). Both profile impls
(`plugins/prod/auth_gate/zero_check_impl.py`, `plugins/community/auth_gate/`) and
the DI wiring (`di/modules/infrastructure/{corp,community}/auth_gate.py`) satisfy
or bind it. `VerifyResult` lives here (not `core`) so the leaf plugins stay
`plugins ↛ core` compliant.
