# `agentclaw.community.core.gateway_principal`

Verifies the gateway's signed `X-Avernet-Principal` token and projects it onto
backend DTOs — the component half of auth design §7.1.

The gateway resolves every identity a request carries into its own `Principal`
discriminated union and forwards the set as a short-lived HS256 JWT. This module
earns the right to believe that header: signature, `aud` (this component), `iss`,
`exp`, then a parse onto local models, then a check that the whole identity set
agrees on one non-empty tenant. Any failure is total — there is no partial trust
and no fallback.

`signer.py` is the same contract read backwards: when this component has to call
a second one *as the caller* — the friend-approval decision applying to BCN — the
inbound token cannot be relayed, because the gateway addresses each token to one
upstream and BCN refuses ours. So a verified token is **re-addressed**: same
identities, same lifetime, new `iss`/`aud`/`kid`. Verification comes first and is
not optional there — a function that signed an unverified payload would be a
token-minting oracle for every component that trusts the shared key.

Nothing here reads a framework, a header, the environment, or a secret store
(Rule 7). The HTTP seam lives in `adapters/http/openapi_v1/dependencies.py`; the
config — including resolving the shared signing key through `SecretResolver` —
in `utils/gateway_principal_config.py`.

## Context Boundary

```yaml
purpose: Verify the gateway-signed forwarded principal, project it onto backend caller DTOs, and re-address a verified token to a second upstream.
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
  - PrincipalSignerConfig
  - VerifiedCaller
  - verify_principal_token
  - decode_principal_token
  - caller_from_claims
  - resign_principal_token
  - key_fingerprint
  - is_weak_signing_key
  - MIN_SIGNING_KEY_BYTES
  - PrincipalVerificationError
consumes:
  - "Gateway PrincipalSigner (the signing half of the contract, in src/gateway)"
  - "BCS/BCN GatewayPrincipalTokenVerifier (the target of a re-addressed token, in src/bcs)"
internal_dependencies:
  - agentclaw.community.utils
```

### Change impact

This module defines what the backend believes about who is calling the public
`/openapi/v1` surface, so a change here changes the trust boundary itself.
Loosening a verification step (accepting another algorithm, skipping the audience
check) is a security change, not a refactor, and needs to be argued in the PR
rather than noted.

**Changed 2026-08-05 — the internal tenant is routable.** Verification used to
refuse any token whose machine principal named `DEFAULT_AVERNET_TENANT`
(`teamclaw`), because no gateway tenant was registered under that name and
honouring the claim would have scoped an arbitrary external caller to every
pre-existing internal row. A `teamclaw` tenant now exists on the gateway as a
deliberate first-party path onto `/openapi/v1`, so the claim is honoured like any
other tenant's and the guard is gone. What still holds the boundary: the token is
HS256-signed with the gateway's shared key, so the tenant is the gateway's
assertion and not the caller's; a `tenant` on a `user` entry is still dropped as
an unknown field; and a set naming two tenants is still refused. The load-bearing
assumption is therefore narrower than before — that the gateway registers
`teamclaw` only to first-party apps. If that stops being true, this guard is what
has to come back.

**Added 2026-09-01 — this module now signs, not only verifies.** `signer.py`
re-addresses a verified token so the backend can call BCN as the caller
(`core/work_orders/callbacks.py`). Three properties hold the boundary and must
survive any change to it: the original is fully verified — signature, `aud`,
`exp`, *and* the identity-set admission — before anything is signed; the copy
carries the original's `principals` and `exp` unchanged, so it names the same
caller and dies at the same moment; and the target's `iss`/`aud`/`kid` come from
config rather than from the inbound token. Relaxing the first turns this
component into a token-minting oracle for everything that trusts the shared key.
BCN's requirements are pinned in `utils/gateway_principal_config.py` and
published in `src/bcs/api-contracts/v1/gateway-principal/contract.md`; the two
must move together.

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
