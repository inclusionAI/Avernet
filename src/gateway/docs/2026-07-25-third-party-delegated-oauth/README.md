# Third-Party Delegated Access via OAuth (Authorization Code + PKCE)

**Status:** Draft / for review
**Date:** 2026-07-25
**Component:** `src/gateway` (the gateway hosts the auth surface; public host `https://teamclawgw-pre.alipay.com`)
**Scope:** How a third-party server acts **on behalf of one of our end users** without ever holding a first-party session token.
**Related:** `src/gateway/docs/2026-07-21-auth-design.md` — this note concretizes that doc's **§15 "Mode C" (OAuth authorization-code, "Login with Avernet")**, which is earmarked but deferred.

> 中文版见 [`README.zh-CN.md`](./README.zh-CN.md)。
> **System flow** (corp + community sequence diagrams, consent model, review agenda): [`SYSTEM-FLOW.md`](./SYSTEM-FLOW.md).

---

## 1. Background & the problem we are fixing

Our OpenAPI surface (`/openapi/v1/*`, fronted by the gateway) is opened to third-party developers, whose **servers** call us. Today a third-party call carries **two tokens**:

1. `Authorization: Bearer <api_key>` — validated by baas, yields the app plus its **tenant** (`APIKeyRecord.tenant`).
2. `IAM_TOKEN` cookie — resolved through IAM/BUService to identify the **end user**.

See the current implementation in `src/baas/src/secbaas/community/adapters/web/routers/open_api/dependencies.py` (`get_api_key_from_header`, `get_iam_token_from_cookie`, `get_bot_chat_context`).

**Why this is a smell — not just "one token too many":**

- `IAM_TOKEN` is a **first-party session credential** (the same cookie minted when a human logs into our own web app via `iam.alipay.com`). Requiring a *third-party server* to obtain and forward it means that third party must somehow hold our users' live sessions. That is the classic **confused-deputy / token-passthrough** anti-pattern: a first-party credential leaks outside our trust boundary.
- The end user is re-resolved against BUService **on every single call**, instead of being verified once.
- There is no consent record and no way for a user to see or **revoke** a third party's access.

This is not inevitable. The right shape is a single, limited, revocable token — standard **3-legged OAuth 2.0**.

## 2. Goals & non-goals

**Goals**

- A third-party server ends up presenting **exactly one** credential per API call (`Authorization: Bearer <access_token>`), never `IAM_TOKEN`.
- The real human is authenticated **once**, interactively, through the existing `iam.alipay.com` login (IAM/BUService stays the source of truth).
- Explicit, revocable **user consent** (revocation latency bounded — see §8.3); tenant + user + app + scopes are **claims inside the token**, not separate tokens.
- Resource ownership stays anchored to the app's `developer_org_id` / `tenant`, so a borrowed user handle can never reach another org's data.

**Non-goals**

- We are **not** building a new account/identity system. Authentication ("who is this human") remains IAM/BUService.
- Pure machine-to-machine calls (no end user) and "act on behalf of the partner's *own* user" (opaque handle) are already covered by the existing `app_key` design (auth-design.md §5–§6); this note is only about **acting for one of *our* users**.

## 3. Core idea

Today the user is resolved **on every call** by forwarding `IAM_TOKEN`. Instead, resolve the user **once, at consent time**, and from then on let a minted **access token carry that identity**. IAM/BUService validates the human exactly once — in a browser — not on each machine-to-machine call. After that the wire carries one bearer token and nothing else.

This is OAuth 2.0 **Authorization Code + PKCE** ("Login with Avernet / teamclaw"), with the gateway acting as (or fronting) the authorization server.

## 4. Actors & domains

| Actor | In this design | Real host |
| --- | --- | --- |
| Third-party server ("client") | Wants to act for its user Alice | (partner-owned) |
| **Gateway auth surface** | Hosts `/authorize` + `/token`, issues our tokens | `https://teamclawgw-pre.alipay.com` |
| **IAM login** | Where the human actually logs in | `iam.alipay.com` |
| **BUService** | Resolves an IAM session → our user identity | (internal) |
| Backend (agentclaw) | Consumes the token; mints downstream caller creds | `agentclaw-prod.alipay.com` |

Key point: the browser redirect to `iam.alipay.com` that we **already** perform for our own frontend **is** the "authenticate the human" step. This design reuses it — it does not replace it.

## 5. End-to-end flow

```
FIRST TIME (linking — happens once per user, in a browser):

  client ──redirect Alice's browser──▶ teamclawgw-pre.alipay.com/authorize
                                          │
             STEP 1 (identity): IAM cookie present on our domain?
                 no  ──redirect──▶ iam.alipay.com  ──login──▶ back (cookie now set)
                 yes ──▶ resolve cookie via BUService ──▶ we now know it's Alice
                                          │
             STEP 2 (consent — our DB, keyed on user+client+scopes):
                 already consented? ─ yes ─▶ skip the screen
                                    └─ no  ─▶ show consent screen ─▶ Alice clicks Allow ─▶ save
                                          │
             STEP 3: redirect browser back to client with a one-time CODE
                                          │
  client SERVER ──POST /token (code + client_secret + PKCE verifier)──▶ gateway
  gateway ──▶ returns  { access_token (short-lived), refresh_token (long-lived) }
  client stores both tokens next to Alice's account


STEADY STATE (every API call afterward — no browser, no IAM cookie):

  client SERVER ──▶ /openapi/v1/...   Authorization: Bearer <access_token>
  gateway validates the token ──▶ "Alice, tenant X, scopes Y" ──▶ forward


TOKEN REFRESH (silently, when the access token expires ~15 min):

  client SERVER ──▶ /token (grant_type=refresh_token) ──▶ new access_token
```

- The client decides whether to start linking by checking **its own** storage ("do I already have a token for Alice?"), never by inspecting our cookie (browsers scope cookies per domain — the client cannot see ours).
- `/authorize` is hit **only** at first linking or re-linking (refresh expired, consent revoked, or new scopes needed) — **not** per API call.

## 6. The `/authorize` decision tree

Identity **before** consent — you cannot ask "did Alice consent?" until you know it is Alice.

```
STEP 1 — WHO is this? (identity)
    IAM cookie present on teamclawgw-pre.alipay.com?
       no  → 302 to iam.alipay.com → returns with cookie → continue
       yes → BUService resolves cookie → subject = Alice

STEP 2 — Did Alice already allow THIS client for THESE scopes? (our DB)
       yes → skip consent screen
       no  → render consent → Alice approves → persist grant(user, client, scopes)

STEP 3 — mint a fresh one-time authorization code, redirect to client's registered redirect_uri
```

Remembered consent only lets us **skip the screen** — a fresh code (and therefore a fresh token pair) is still minted on every trip through `/authorize`.

## 7. Why the browser carries a one-time code, not the token

The redirect back to the client carries a short-lived, single-use **authorization code**, not the access token. The client's **server** then exchanges that code for the real token over a direct back-channel call, presenting its `client_secret` and the PKCE `code_verifier`.

If the token itself rode in the browser URL it would leak into browser history, server logs, and `Referer` headers. A code is useless to a thief without the client secret and self-destructs after one use. **PKCE** (`code_challenge`/`code_verifier`, S256) binds the code to the party that started the flow, closing interception attacks.

## 8. Access token contents

Model it as a signed JWT so the gateway can validate statelessly (this is the same asymmetric-signing seam the existing design wants in §7.1):

```
iss: teamclaw-authz
aud: teamclaw-openapi             # CLIENT-facing token audience (see §8.1 — two credentials)
sub: <our user id / staffId>     # the delegated human — consent + attribution/audit ONLY, never a data-access anchor
tnt: <tenant id>                 # the CLIENT's tenant (the app's developer_org); the end user's OWN tenant is never consulted
azp: <client_id>                 # the acting third-party app
org: <developer_org_id>          # the resource-ownership anchor
scope: "bots:chat bots:read"     # exactly what the user consented to
exp / iat / jti
```

Everything the current two/three tokens carried is now a claim inside one credential.

### 8.1 Two distinct credentials — do not conflate

Two credentials live in this system and must never be conflated:

- the **OAuth access token** — client-facing, short-lived (~15 min), `aud: teamclaw-openapi`, validated only at the gateway edge;
- the **forwarded (internal) Principal** — internal, seconds-lived, `aud: <specific backend>`, per auth-design.md §7.1, gateway-signed and verified by the backend.

The backend never sees or verifies the OAuth access token; it only ever verifies a **gateway-signed Principal** (§9). This keeps the OAuth token from crossing into the backend's trust boundary, and keeps backend verification uniform across every strategy.

### 8.2 What `tnt` and `sub` mean (and don't)

`tnt` is the **client's** tenant (the app's `developer_org`). The end user's own tenant is **never** consulted, and `sub` is **not** a resource-ownership anchor — it exists for **consent and attribution/audit**. This is what makes cross-tenant delegation impossible by construction (the §2 goal): a delegated call can only ever touch data in the **client's** tenant, anchored to `org`/`tnt`.

Concretely, "act on behalf of our end user" here means **"act within the client's tenant, with a verified, consented human on record"** — it does **not** grant the client access to the user's *own* cross-tenant data. A later reader must not assume `sub` scopes data access.

### 8.3 Revocation model & latency

A pure stateless JWT can't be rejected before its `exp` (the gateway validates by signature alone), so "revocable" and "stateless validation" are in genuine tension. Three options:

- **Opaque token + introspection** — instant revocation, but a store lookup on every API call (gateway stateful on the hot path).
- **JWT + a cheap revocation check (recommended)** — keep signature validation, but also consult a per-`(user, client)` "min-valid-`iat`" watermark (or a small `jti` denylist), cached with a short TTL → near-instant revocation, mostly stateless.
- **Pure JWT, no check** — revoke kills refresh + consent only; an already-issued access token lives until `exp` (≤15 min), so "revoke" effectively means "within 15 min."

Recommend the middle option. Whichever is chosen, **state the resulting revocation latency** so "revocable" (§2, §11) does not over-promise.

## 9. Gateway validation → `DelegatedPrincipal`

This is the deferred `oauth_bearer` strategy in auth-design.md (§5 strategy table, §15). It slots into the existing strategy machinery (`gateway/community/plugins/authn/`, `spi/authn/`):

```python
# gateway/community/plugins/authn/oauth_bearer/_strategy.py  (sketch)
class OAuthBearerStrategy(AuthStrategy):
    name = "oauth_bearer"

    async def build(self, creds, params):
        tok = _bearer(creds.headers.get("authorization"))
        if not tok:
            return None                                # not applicable → next OR branch
        claims = await self._verifier.verify(tok)      # JWKS/introspection; invalid → AuthError
        return DelegatedPrincipal(
            tenant=claims.tnt,
            app=ThirdPartyApp(client_id=claims.azp, developer_org_id=claims.org, ...),
            subject=AuthenticatedUser(id=claims.sub, tenant_id=claims.tnt),
            scopes=frozenset(claims.scope.split()),
        )
```

The runner then enforces `required_scopes ⊆ granted_scopes` exactly as for the other strategies. `DelegatedPrincipal` is the §15 discriminated-union member that carries **both** app and verified subject — a shape `AppPrincipal.on_behalf_of_opaque` (an *unverified* handle) cannot express.

**Then re-sign — do not forward the OAuth token.** `oauth_bearer` validating the access token and building `DelegatedPrincipal` is only the gateway-edge step. The gateway then **re-signs that `DelegatedPrincipal` as the short-lived internal Principal of auth-design.md §7.1** (`aud: <specific backend>`, seconds-lived) and forwards *that*; the backend verifies the gateway-signed Principal and **never** the client's OAuth access token (see §8.1). From the backend's view every strategy (`first_party_user` / `app_key` / `oauth_bearer`) collapses to "verify one gateway-signed Principal."

### 9.1 Placement within the config-driven forwarder (PR #420)

`/authorize`, `/token`, `/revoke`, and the consent UI are gateway-**local** endpoints — they are *not* under `/openapi/v1/<domain>` and must **not** be handled by the config-driven forwarding catch-all. That catch-all routes everything under the version base to a domain's upstream and denies unknown domains, so these routes must be registered **ahead of / excluded from** it (the same treatment as `/health` and `/docs`), or they'd be rejected as an unknown domain.

Two more alignment points with PR #420:

- `oauth_bearer` is registered in `route_security.yaml` as one **strategy alternative** among others (e.g. `[oauth_bearer, app_key]`), consistent with "the gateway owns auth-strategy selection." The re-signed `DelegatedPrincipal` (§9) is what the backend's per-route dependency consumes for authorization.
- This design turns the gateway into a **stateful authorization server** (consent/grant DB, refresh state, consent UI) sitting alongside the otherwise-stateless proxy. Keep that authz state a **bounded, separable** component so the forwarding hot path stays stateless.

## 10. Downstream credential — act *within the client's tenant, attributed to* the user

When the request reaches the runtime and must call BaaS/MCP, it acts **within the client's tenant, attributed to Alice** (per §8.2 — never with Alice's own tenant or personal permissions). Reuse the backend seam `CallerIdentityService.exchange_caller_identity()` (`src/backend/src/agentclaw/community/core/caller_identity/service.py:328`). The recorded consent from Step 2 **is** the pre-authorization; the minted caller credential is installed into the runtime via `runtime_updater.update_caller_identity(...)` and is **never returned to the partner**. The minted credential must stay anchored to the client's `org`/`tnt` — it must **not** silently reintroduce the user's tenant or permissions.

One signature change is required: `exchange_caller_identity` currently takes `iam_token: str`. In the OAuth path we do not hold Alice's live `IAM_TOKEN`, so `CallerTokenProviderProtocol` needs an overload that mints from `(service_credential, subject_id, tenant, grant_ref)` instead of a forwarded user token. Whether BUService can issue such a delegated credential without the user's live token is the key external dependency — see §12.

## 11. Migration from the current two-token baas path

| Today (`open_api/dependencies.py`) | Target |
| --- | --- |
| `Bearer <api_key>` → app + tenant | Client is an **OAuth client**; app + tenant + user + scopes ride in the access token |
| `IAM_TOKEN` cookie → user (every call) | User verified **once** at consent; access token carries `sub` |
| `policy.allowed_bots` fail-closed whitelist | Keep as gateway coarse-grained scope / resource whitelist |
| (no consent, no revocation) | Explicit consent record; revocable (bounded latency — see §8.3); refresh-token rotation **with reuse detection** (§13) |

The re-home is additive: the `app_key` (pure app / opaque on-behalf-of) path from auth-design.md is unchanged; this note only adds the **delegated-user** path.

## 12. Open questions to resolve first

1. **Does Alipay IAM / antbuservice already act as an OAuth/OIDC authorization server** that can issue teamclaw-audience, teamclaw-consented tokens (more than SSO/login)? This is the config-vs-build (build-weight) fork; the provider's capability **isn't knowable from this repo** and needs the IdP owners:
   - **Yes** → register the client in IAM; IAM runs `/authorize` + consent + `/token`; the gateway only **validates** IAM-issued tokens. Lighter build.
   - **No / unconfirmed** → the gateway builds the `/authorize` + `/token` endpoints itself, using the `iam.alipay.com` redirect solely for the human-login step. **This is the working design** (always buildable by us).
2. **Token format** — signed JWT (stateless validation, aligns with §7.1 signing seam) vs opaque + introspection (easier revocation). Recommendation: JWT access token + server-side refresh/consent state.
3. **Delegated-credential mint** — can BUService issue an "act as subject" credential from `(service credential + subject + recorded consent)` without the user's live token (RFC 8693-style token exchange / on-behalf-of)? If not, store a redeemable delegation grant at consent time; the user-token dependency stays entirely inside our trust boundary. (auth-design.md §15 flags the same sender-constrained-token question.)
4. **Consent granularity & lifetime** — per-scope consent, consent expiry, and the re-consent trigger when a client requests new scopes.
5. **Scope taxonomy is a hard prerequisite.** Per-scope consent, the token's `scope` claim, and the runner's `required ⊆ granted` check all depend on a defined **scope vocabulary** (the valid scope strings + what each grants). That taxonomy is currently deferred in auth-design.md and the gateway-v1 spec; it must land **before/with** this design — you cannot render a meaningful consent screen or enforce scopes without it.

## 13. Incremental delivery

1. **MVP:** implement `/authorize` + `/token` for `authorization_code + PKCE` only (skip implicit / client-credentials / device). Issue JWT access tokens signed with the gateway Principal key pair. Wire the `oauth_bearer` strategy. This alone removes `IAM_TOKEN` from the third-party surface.
2. Add refresh-token rotation **with reuse detection** + `/revoke` + a consent-management UI (users can view/revoke apps). Reuse detection is what makes rotation worth doing: since each rotated refresh token is single-use, a replay of an already-consumed refresh token means two parties hold it (one was stolen) → **revoke the whole token family** for that grant (all refresh + access) and force re-authorization (OAuth Security BCP). Without it, a stolen refresh token works quietly for its full lifetime.
3. Adapt `CallerTokenProviderProtocol` for the tokenless mint, retiring the last `iam_token` dependency in the delegated path.

## 14. Glossary

- **Authentication** — "who is this human?" Owned by IAM/BUService. Unchanged.
- **Authorization** — "does this user allow this app to act for them, and here is a token proving it." The thin new layer.
- **Authorization code** — short-lived, single-use value carried in the browser redirect; exchanged server-to-server for tokens.
- **Access token** — short-lived, client-facing bearer credential used on API calls; carries tenant + user + app + scopes as claims. Validated only at the gateway edge (§8.1).
- **Forwarded (internal) Principal** — the short-lived, gateway-signed credential (`aud: <specific backend>`, auth-design.md §7.1) that the backend actually verifies; distinct from the client-facing access token (§8.1, §9).
- **Refresh token** — long-lived, rotating credential used server-to-server to mint new access tokens without user interaction; rotation is paired with reuse detection (§13).
- **PKCE** — `code_challenge` / `code_verifier` (S256) binding a code to the flow's initiator.
- **`DelegatedPrincipal`** — gateway principal carrying both the acting app and the verified end-user subject (auth-design.md §15).
