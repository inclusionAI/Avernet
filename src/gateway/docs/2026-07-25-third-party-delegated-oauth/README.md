# Third-Party Delegated Access via OAuth (Authorization Code + PKCE)

**Status:** Draft / for review
**Date:** 2026-07-25
**Component:** `src/gateway` (the gateway hosts the auth surface; public host `https://teamclawgw-pre.alipay.com`)
**Scope:** How a third-party server acts **on behalf of one of our end users** without ever holding a first-party session token.
**Related:** `src/gateway/docs/2026-07-21-auth-design.md` — this note concretizes that doc's **§15 "Mode C" (OAuth authorization-code, "Login with Avernet")**, which is earmarked but deferred.

> 中文版见 [`README.zh-CN.md`](./README.zh-CN.md)。

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
- Explicit, revocable **user consent**; tenant + user + app + scopes are **claims inside the token**, not separate tokens.
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
aud: teamclaw-openapi
sub: <our user id / staffId>     # the delegated human, verified once at consent
tnt: <tenant id>                 # a CLAIM — not a separate token
azp: <client_id>                 # the acting third-party app
org: <developer_org_id>          # for resource-ownership anchoring
scope: "bots:chat bots:read"     # exactly what the user consented to
exp / iat / jti
```

Everything the current two/three tokens carried is now a claim inside one credential.

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

## 10. Downstream "act as the user" credential

When the request reaches the runtime and must call BaaS/MCP **as Alice**, reuse the backend seam `CallerIdentityService.exchange_caller_identity()` (`src/backend/src/agentclaw/community/core/caller_identity/service.py:328`). The recorded consent from Step 2 **is** the pre-authorization; the minted caller credential is installed into the runtime via `runtime_updater.update_caller_identity(...)` and is **never returned to the partner**.

One signature change is required: `exchange_caller_identity` currently takes `iam_token: str`. In the OAuth path we do not hold Alice's live `IAM_TOKEN`, so `CallerTokenProviderProtocol` needs an overload that mints from `(service_credential, subject_id, tenant, grant_ref)` instead of a forwarded user token. Whether BUService can issue such a delegated credential without the user's live token is the key external dependency — see §12.

## 11. Migration from the current two-token baas path

| Today (`open_api/dependencies.py`) | Target |
| --- | --- |
| `Bearer <api_key>` → app + tenant | Client is an **OAuth client**; app + tenant + user + scopes ride in the access token |
| `IAM_TOKEN` cookie → user (every call) | User verified **once** at consent; access token carries `sub` |
| `policy.allowed_bots` fail-closed whitelist | Keep as gateway coarse-grained scope / resource whitelist |
| (no consent, no revocation) | Explicit consent record; revocable; refresh-token rotation |

The re-home is additive: the `app_key` (pure app / opaque on-behalf-of) path from auth-design.md is unchanged; this note only adds the **delegated-user** path.

## 12. Open questions to resolve first

1. **Does Alipay IAM already act as an OAuth/OIDC authorization server for third-party apps** (an "open platform" capability)? This is the config-vs-build fork:
   - **Yes** → register the client in IAM; IAM runs `/authorize` + consent + `/token`; the gateway only **validates** IAM-issued tokens. Minimal build.
   - **No** → the gateway builds the thin `/authorize` + `/token` endpoints described here, using the `iam.alipay.com` redirect solely for the human-login step.
2. **Token format** — signed JWT (stateless validation, aligns with §7.1 signing seam) vs opaque + introspection (easier revocation). Recommendation: JWT access token + server-side refresh/consent state.
3. **Delegated-credential mint** — can BUService issue an "act as subject" credential from `(service credential + subject + recorded consent)` without the user's live token (RFC 8693-style token exchange / on-behalf-of)? If not, store a redeemable delegation grant at consent time; the user-token dependency stays entirely inside our trust boundary. (auth-design.md §15 flags the same sender-constrained-token question.)
4. **Consent granularity & lifetime** — per-scope consent, consent expiry, and the re-consent trigger when a client requests new scopes.

## 13. Incremental delivery

1. **MVP:** implement `/authorize` + `/token` for `authorization_code + PKCE` only (skip implicit / client-credentials / device). Issue JWT access tokens signed with the gateway Principal key pair. Wire the `oauth_bearer` strategy. This alone removes `IAM_TOKEN` from the third-party surface.
2. Add refresh-token rotation + `/revoke` + a consent-management UI (users can view/revoke apps).
3. Adapt `CallerTokenProviderProtocol` for the tokenless mint, retiring the last `iam_token` dependency in the delegated path.

## 14. Glossary

- **Authentication** — "who is this human?" Owned by IAM/BUService. Unchanged.
- **Authorization** — "does this user allow this app to act for them, and here is a token proving it." The thin new layer.
- **Authorization code** — short-lived, single-use value carried in the browser redirect; exchanged server-to-server for tokens.
- **Access token** — short-lived bearer credential used on API calls; carries tenant + user + app + scopes as claims.
- **Refresh token** — long-lived, rotating credential used server-to-server to mint new access tokens without user interaction.
- **PKCE** — `code_challenge` / `code_verifier` (S256) binding a code to the flow's initiator.
- **`DelegatedPrincipal`** — gateway principal carrying both the acting app and the verified end-user subject (auth-design.md §15).
