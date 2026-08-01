# Gateway principal verification (backend half of auth design §7.1)

- Date: 2026-07-30
- Status: implemented, pending review
- Component: `src/backend` (`agentclaw.community`)
- Counterpart: gateway PR #599 (`feat/principal-signer`) — the signing half
- Related: `docs/2026-07-21-auth-design.md` §7.1; the cross-cutting "Real
  caller-identity verifier" row in `src/backend/docs/openapi-v1/README.md`
- **Superseded in part**: how the verifier is *configured* changed in PR #670.
  The signing key is a credential, so it now resolves through `SecretResolver`
  instead of `AVERNET_PRINCIPAL_SIGNING_KEY`, and `aud`/`iss` became constants
  rather than env vars. Everything else below — the wire contract, what gets
  rejected, and every decision in the Decisions section — still holds. See
  **Configuration (as of PR #670)** below for the current contract and the
  migration a deployment needs.

## Problem

The public `/openapi/v1` surface has been definition-complete since PR #494 and
has two categories wired (bots #494, mcp #610), but **it is not callable**:
`require_principal` returns `None` and `resolve_avernet_tenant` returns the
internal tenant, both stubs. Every real request answers 401. The board records
this as "auth workstream (other team)", and it gates DoD items 6 and 7.

The gateway side has landed the identity model it forwards — `PrincipalType` /
`UserPrincipal` / `BotPrincipal` / `AppPrincipal` / `AccessKeyPrincipal`, the
per-identity strategy chains, and the route→requirement table are on `dev`. What
was missing on `dev` is the *forwarding*: `_attach_identities` is an explicit
no-op, because auth design §7.1 forbids a component trusting a bare
`X-Avernet-Principal` header. Gateway PR #599 flips that seam to real signing.

**PR #599 does not need to merge before this work.** Its spec puts the
component-side verifier out of scope ("下游组件仓库"), and the wire contract is
fully specified in it. This spec implements our half against that contract.

## Wire contract consumed

From `plugins/principal_signer/bare/_plugin.py` and `_forward.py` in PR #599:

| Element | Value |
| --- | --- |
| Header | `X-Avernet-Principal` (gateway strips any inbound copy) |
| Token | compact JWS, **HS256**, shared HMAC key, `kid` in the JOSE header |
| `iss` | the gateway's configured issuer, default `gateway` |
| `aud` | the upstream server's name — **`backend`** for us (`servers:` in the gateway's `upstreams.yaml`) |
| `iat`/`exp` | short TTL, default 60s |
| `principals` | a **list** of `Principal.model_dump(mode="json")`, tagged by `type` |

~~Gateway env names, reused verbatim so one secret has one vocabulary:
`AVERNET_PRINCIPAL_SIGNING_KEY`, plus our `AVERNET_PRINCIPAL_AUDIENCE` and
`AVERNET_PRINCIPAL_ISSUER`.~~ **Superseded by PR #670** — see below.

## Configuration (as of PR #670)

The backend no longer reads any `AVERNET_PRINCIPAL_*` variable. Neither does the
gateway: #673 moved its signing side onto its own `SecretResolver` SPI in the
same release. **The two sides now share only a value, not a vocabulary** — each
has its own registry, its own secret name, and its own resolver:

| Side | Secret name | Community lookup |
| --- | --- | --- |
| backend | `SecretNamesConfig.gateway_principal_signing_key` (defaults) | `AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE` |
| gateway | `principal_signer.secret_name` (default `principal_signing_key`) | `AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE` |

Nothing links those two names, and **the two sides fail differently when
unprovisioned** — which side is missing decides what you see:

| Missing | Result |
| --- | --- |
| backend, in `pre`/`prod` | **the backend refuses to boot** — `strict=True`, so the rollout fails loudly. Not silent. |
| backend, elsewhere | the backend boots and answers 401 to every `/openapi/v1` request |
| gateway, any environment | **silent, and the dangerous one.** The gateway keeps a dev fallback (`avernet-dev-signing-key-NOT-FOR-PROD`) and logs a warning, so it does *not* fail — it signs real tokens with the wrong key. The backend boots, looks configured, and rejects every one of them. |

The gateway-side miss is the case worth designing around: nothing on either side
treats it as an error, so it survives a rollout and presents as "auth is broken"
with two healthy-looking services.

| What | Where it comes from now |
| --- | --- |
| signing key | `SecretResolver`, under `SecretNamesConfig.gateway_principal_signing_key` — which **defaults**, so a deployment configures only the value |
| `aud` | a constant, `backend` — the gateway signs it from the upstream server's own name, which it never made configurable |
| `iss` | a constant, `gateway` — matching the **default** of the gateway's `principal_signer.issuer`, which since #673 *is* configurable there |

The `iss` row is a live coupling, not a symmetry: changing the gateway's
`issuer` requires changing the backend constant in the same release, or every
`/openapi/v1` request answers 401.

Per profile, the key's value resolves from: the corp secret store (corp);
`{env_prefix}{NAME}_VALUE` (community, via `CommunitySecretResolver`); or
nothing at all in singlebox/test — that profile has no secret store and ships
no local stand-in, so the public surface denies there.

**Migration.** A deployment that set `AVERNET_PRINCIPAL_SIGNING_KEY` on the
backend must move that value. The secret *name* needs no action — it defaults —
so this is only about provisioning the value where that profile's resolver reads
it (see the table above).

What a missed migration looks like depends on the environment:

- **`pre` / `prod` fail to boot.** `init_principal_verifier_config` is called
  with `strict=True` and raises, so the process never starts. The rollout fails
  visibly rather than deploying a surface that 401s while reporting healthy.
- **local / dev / singlebox keep booting and answer 401** on every
  `/openapi/v1` request. Those environments legitimately have no key —
  singlebox ships no local key on purpose — so failing
  the boot there would brick local development and the singlebox coverage gate.

There is deliberately no env fallback: silently honouring the old variable would
keep a credential in the environment, which is the thing this change exists to
stop, and a fallback that works is a fallback nobody migrates off. Failing —
loudly in strict environments, closed everywhere else — makes the missed step
visible instead of leaving a credential-shaped hole open.

## Solution

`core/gateway_principal/` verifies the token and projects it onto backend DTOs;
`adapters/http/openapi_v1/dependencies.py` becomes the real seam. No handler,
router, or middleware changes — which is exactly what PR #456 promised when it
placed the seams.

1. **Projected DTOs** (`models.py`) mirroring the gateway's wire shape without
   importing gateway types (Rule 7 / §9). Unknown fields are ignored so the
   gateway can add one freely; a renamed or removed required field fails parsing
   and the request is denied.
2. **Verification** (`verifier.py`): pinned `algorithms=["HS256"]`, required
   `exp`/`iat`/`aud`/`iss`, audience and issuer value-checked, 5s fixed clock
   skew, then parse, then tenant integrity.
3. **`VerifiedCaller`** exposing `tenant` and `user_id` — the two things this
   surface scopes by. `user_id` is the attribute `caller_owner_id` already looks
   for, so the swap needs no handler edits.
4. **Seam** (`dependencies.py`): one verification per request, cached on the
   request scope, because both `resolve_avernet_tenant` (called from ASGI
   middleware before routing) and `require_principal` (the route dependency)
   need it.
5. **Config** (`utils/gateway_principal_config.py`): env-driven, process-cached,
   shaped like `utils/env_utils` because the middleware call site is outside DI.

## Decisions

1. **No dev fallback signing key.** The gateway's `bare` signer falls back to a
   committed dev secret with a warning; we deliberately do not mirror it. A
   committed shared secret is a committed credential, and on this side "no key"
   fails safe: every public request answers 401, which is precisely the state
   this replaces. **Single-box cannot set a key at all** — since PR #670 it has
   no local stand-in for a secret store, so it denies unconditionally. The two
   sides matching is a concern only where both are provisioned: a real secret
   store or the environment here, and since gateway #673
   `AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE` there.
2. **Tenant passes through verbatim.** The gateway's tenant id *is* the
   `avernet_tenant` isolation key — no translation table. Consequence worth
   knowing: a gateway tenant must be spelled exactly as the column stores it,
   and because all existing rows are `teamclaw`, a real external tenant sees an
   empty dataset until it has data. That is correct isolation, not a bug.
3. **A wire tenant of `teamclaw` is rejected.** `DEFAULT_AVERNET_TENANT` owns
   every internal row, and the board says it must never be handed to an external
   tenant. No gateway tenant is named this today, so the guard costs nothing;
   without it, one tenant-naming mistake upstream is a total internal data leak.
   Lift it only alongside a designed internal-through-gateway path.
4. **Contradictory tenants across the identity set are rejected.** One request
   cannot belong to two tenants; picking one would invent an answer the gateway
   did not give.
5. **Secrets in the payload are not projected.** The gateway forwards the bot
   session token and the access-key token. This surface only needs to know *who*
   is calling, and the cheapest way to not leak a credential is to never hold
   it.
6. **Owner id is derived only for `user` and `bot`.** See open question 1 — an
   `app` or `access_key` caller gets 401 rather than a guessed owner.
7. **The mapped-error lookup moved out of the `@envelope_errors` decorator** into
   `mapped_error_response`, and the app's catch-all now consults it. Necessary,
   not incidental: the seam raises in a **dependency**, before the handler the
   decorator wraps, so without this an unauthenticated public request would have
   regressed from 401 to 500. One table, two entry points.

## Open questions (for the gateway owner, not resolvable here)

1. **What does an `app` or `access_key` caller own?** Every public handler scopes
   by owner id. `app.owners` is free-text org attribution (`varchar(1024)`,
   plural), and `avernet_access_key_token` has no owner column at all — neither
   yields a per-caller owner. Since the public API's callers are *external
   registered tenants*, this is likely the common case, and it currently 401s.
2. **Route security does not admit those callers anyway.** The gateway's
   `route_security.yaml` marks `/openapi/v1/bots/**` as `user: required`, and the
   `user` chain is `[google]`. An external tenant presenting an access key is
   refused at the gateway before we see it. The table needs a rule for this
   surface.
3. **Key rotation.** `kid` rides in the JOSE header and we ignore it (single
   key). Rotation arrives with the asymmetric `sofa` flavor; verification will
   need a `kid`→key lookup then.

## Out of scope

- Asymmetric `sofa` verification (RS256 + JWKS + `kid` rotation) — §7.1 leaves it
  to that flavor's workstream; only HS256 `bare` is contracted today.
- Replay hardening beyond `exp` + `aud` (`jti` + nonce cache, binding
  `method`/`path`) — the gateway chose not to sign those claims, so there is
  nothing to check.
- The gateway-side route-security and owner-semantics decisions above.
- #556 (bot identity keys collide across tenants) still gates *enabling*
  multi-tenancy; verification landing does not change that.

## Tests

- `tests/community/core/gateway_principal/test_verifier.py` — 29 cases minting
  tokens with the gateway's own claim shape. The forgery half is the point:
  wrong key, `alg: none`, another upstream's audience, another issuer, expired,
  each required claim omitted, unknown `type` tag, renamed contract field,
  unparseable payload, mixed/empty/internal tenant, no key configured.
- `tests/community/adapters/http/openapi_v1/test_principal_seam.py` — the HTTP
  seam: tenant binding through the real `AvernetTenantMiddleware`, one
  verification per request, 401 parity between "no credential" and "bad
  credential", and `test_public_routes_require_principal`, which pins the
  property that makes the tenant fallback safe.
- `tests/community/utils/test_gateway_principal_config.py` — the configuration
  contract (the `SecretResolver` path since PR #670), including that no fallback
  key is invented.
- Existing suites unmodified and green, including `test_bots_endpoints.py` and
  `test_mcp_endpoints.py`, whose `require_principal` overrides keep working
  because `caller_owner_id`'s tolerated shapes did not change.
