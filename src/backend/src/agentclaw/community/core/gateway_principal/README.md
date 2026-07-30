# `agentclaw.community.core.gateway_principal`

Verifies the gateway's signed `X-Avernet-Principal` token and projects it onto
backend DTOs — the component half of auth design §7.1.

The gateway resolves every identity a request carries into its own `Principal`
discriminated union and forwards the set as a short-lived HS256 JWT. This module
earns the right to believe that header: signature, `aud` (this component), `iss`,
`exp`, then a parse onto local models, then a check that the whole identity set
agrees on one tenant which is not the internal one. Any failure is total — there
is no partial trust and no fallback.

Nothing here reads a framework, a header, or the environment (Rule 7). The HTTP
seam lives in `adapters/http/openapi_v1/dependencies.py`; the environment-driven
config in `utils/gateway_principal_config.py`.

## Context Boundary

```yaml
purpose: Verify the gateway-signed forwarded principal and project it onto backend caller DTOs.
provides:
  - GatewayPrincipal
  - UserPrincipal
  - BotPrincipal
  - AppPrincipal
  - AccessKeyPrincipal
  - GatewayUser
  - GatewayBot
  - GatewayApp
  - GatewayAccessKey
  - PrincipalType
  - PrincipalVerifierConfig
  - VerifiedCaller
  - verify_principal_token
  - PrincipalVerificationError
consumes:
  - "Gateway PrincipalSigner (the signing half of the contract, in src/gateway)"
internal_dependencies:
  - agentclaw.community.utils
```

### Change impact

This module defines what the backend believes about who is calling the public
`/openapi/v1` surface, so a change here changes the trust boundary itself.
Loosening a verification step (accepting another algorithm, skipping the audience
check, allowing the internal tenant off the wire) is a security change, not a
refactor, and needs to be argued in the PR rather than noted.

The DTOs mirror the gateway's `spi/authn/_models.py` wire shape. Unknown fields
are ignored, so the gateway can add one freely; a **rename or removal** on its
side fails parsing here and every public request answers `401` until the models
follow. That coupling is the point — it fails loudly and safely rather than
silently mis-reading an identity — but it means the gateway's model changes are
breaking changes for this module, and the two must be released with that in mind.

Every consumer reaches this through `require_principal`, so handlers do not
notice changes to the DTOs unless `VerifiedCaller.user_id` or `.tenant` changes
meaning. Those two properties are the load-bearing surface: `user_id` scopes data
to the caller and `tenant` scopes it to their tenant, and getting either wrong is
a cross-tenant data leak rather than a bug.
