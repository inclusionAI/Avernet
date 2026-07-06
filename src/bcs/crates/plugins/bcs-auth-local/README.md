# bcs-auth-local

Local-dev auth plugin: emits a mock principal from config (no IO). Used when the
chain contains `local` (the debug-build default), so the app runs without real
SSO/OAuth. When `allow_mock_headers` is enabled, `X-Mock-User-Id` and
`X-Mock-Nick-Name` override the configured mock identity for local permission
tests. Also home to `StaticAuthPlugin`, a fixed-identity test double.

## Context Boundary

```yaml
purpose: Local-mock AuthPlugin emitting a configured principal or local mock headers; no IO.
provides:
  - LocalAuthPlugin         # AuthPlugin impl, emits mock_user_id/mock_user_name or X-Mock-* when enabled
  - StaticAuthPlugin        # test double: always returns a preset principal
consumes:
  - AuthPlugin              # implements the bcs-auth-api Plugin API
internal_dependencies:
  - bcs-auth-api
```

### Change impact

Active only when `local` is in the resolved chain (debug default, or explicit
config). Request-header identities are disabled by default and only honored when
`LocalAuthConfig.allow_mock_headers` is true, or the composition root resolves
`BCS_AUTH_MOCK=1` for local tests. The `local` plugin must never be enabled in
production chains. Selection is config-driven in the composition root
(`auth_wiring`).
