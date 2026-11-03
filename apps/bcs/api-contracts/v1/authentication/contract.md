# V1 API Authentication Contract

Status: active (V1 API auth plugin chain, 2026-11). This document pins the
wire-level contract of the configurable `/openapi/v1/auth/*` facade and the
authentication sources behind the V1 protected routes. The YAML surface is
in [openapi/auth.yaml](../openapi/auth.yaml) — the assertions that keep this
document honest live in `scripts/test_api_auth_contract.py`. Implementation
contexts: `crates/application/v1/bcs-app-auth`, `crates/plugins/bcs-auth-oauth`,
the `bcs-api-http` delivery adapter, and the bootstrap composition root.

Three sources of authority, in reading order:

1. The checked-in OpenAPI YAML (machine-validated).
2. This document (human-pinned semantics the YAML schema cannot express).
3. The design spec `docs/superpowers/specs/2026-11-02-v1-api-auth-plugin-chain-design.md`.

## 1. Envelope and error mapping

The V1 JSON envelope is unchanged: `{code, message, data, request_id}`;
errors carry `data.error_code`. Status families:

| Status | Envelope `code` | `error_code` | Producing conditions |
| --- | --- | --- | --- |
| 400 | 40000 | `invalid_state` / `invalid_login_state` / `missing_code` | Callback without (or with a broken/duplicated/foreign) browser challenge or pending state, or without an authorization code |
| 401 | 40100 | `unauthenticated` | No source matched, or the presented session is invalid/expired/revoked/superseded — INCLUDING a lost refresh rotation race (never a 500) |
| 403 | 40300 | `forbidden` | A verified identity is rejected by source policy on the protected surface (final — the chain is never re-entered) |
| 403 | 40300 | `forbidden` | Pinned current behavior: an ordinary cookie-authenticated write on any OTHER protected route whose browser Origin fails the per-source CSRF gate also surfaces as plain 403 `forbidden` — only the login-family writes `/auth/refresh` and cookie-bearing `/auth/logout` use the distinct `browser_origin_untrusted` code below |
| 403 | 40300 | `browser_origin_untrusted` | Cookie-authenticated unsafe write (`/auth/refresh`, cookie-bearing `/auth/logout`) with a missing, malformed, or untrusted Origin |
| 404 | 40400 | `auth_not_configured` / `provider_not_found` / `provider_not_enabled` | No OAuth source configured, or the callback path names a provider outside the enabled chain |
| 409 | 40900 | `login_session_conflict` | A concurrent login replaced this identity's session instance between the revision read and the atomic install; the browser restarts the login batch |
| 500 | 50000 | `internal_error` | Internal translation or contract violation (sanitized message) |
| 502 | 50200 | `bot_identity_unavailable` (bot registry path) or `token_exchange_failed` / `userinfo_request_failed` (third-party callback failures) | The pre-existing Bot identity store fault class is UNCHANGED; third-party credential exchange failure is a 502, not 503 |
| 503 | 50300 | `auth_dependency_unavailable` | A strict-auth backing dependency (session identity store, pending login store, verifier store) is unreachable. Never folded into a denial, an Ok(None), or an unconditional write |

Two 5xx classes are deliberately distinct: the strict authentication
dependency outage is **503 / 50300 / `auth_dependency_unavailable`**, while
the long-standing signed-Bot registry outage keeps its **502
`bot_identity_unavailable`** mapping. The YAML response objects are
`shared.yaml#/AuthDependencyUnavailableResponse` and the per-operation
`502` entries.

`logout` attains `200` only after the revoke committed (or there was no
cookie at all — the idempotent boundary). A persistence failure is 503,
never a 200 with a cleared cookie: best-effort logout is prohibited.

## 2. Authentication sources and precedence

Sources are configured under `[api.auth]` (see the [config
contract](#5-configuration-contract)); the legacy `[auth]` section keeps its
own frozen semantics. The chain executes strictly in configured order:

| Source result | Chain behavior |
| --- | --- |
| NotApplicable (no credentials for this source) | try the next source |
| Authenticated | STOP; this source's identity is the caller; lower sources never run and cannot add claims |
| InvalidCredential | 401 immediately — no fallback |
| Forbidden | 403 immediately — no fallback |
| DependencyUnavailable | 503 immediately — no fallback |
| Internal | 500, sanitized |
| All NotApplicable | 401 |

Consequences that are contract, not implementation detail:

- first-success short-circuits; a request may carry credentials for several
  sources, but once one authenticates the rest are ignored — they neither
  strengthen identity nor can trigger a failure;
- a failing enabled source is never rescued by a later source (no
  fallback-on-error), and a credential for a source that is NOT in the chain
  simply does not participate: it neither authenticates nor fails the
  request — a valid cookie for an unlisted provider is only ever seen as
  "no credentials" unless a chained source accepts it;
- source and identity are never merged: OAuth never turns a Bot into a
  Human, and accounts never merge on email or name equality;
- the verification context records which registered source and which
  credential kind actually authenticated the request; CSRF and cookie
  rules read this actual context, never credential presence heuristics.

## 3. Session instance model (sid/revision) and cookie contract

Every OAuth session is one **session instance** of the `(user_id, provider,
env)` scope: a random `session_id`, a monotonically increasing `revision`,
and the SHA-256 hash of the current credential. The browser holds an HS256
JWT whose REQUIRED claims are:

- `sub` — internal BCS user id;
- `src` — the instance name of the OAuth provider that established the login;
- `env` — environment part of the session scope address;
- `session_id` — the session instance id (max 64 chars);
- `revision` — CAS revision the token was signed for;
- `iat`, `exp` — unix seconds; `exp` is enforced inclusively;
- `name` — OPTIONAL display snapshot; verification joins the live identity row.

Legacy tokens without `env`/`session_id`/`revision` are rejected by the
strict verifier and by the refresh/revoke paths — the 2026-11 security
upgrade invalidates them; users must re-login. External JWT-only verifiers
of `bcs_session` (offline signature+expiry decoding) necessarily lose all
session-instance visibility — revocation, rotation, and revision are
invisible to pure JWT validation, so the server-side strict verify (which
owns the stored hash/revision) is authoritative. Only the SHA-256 of the
JWT is stored (64 hex chars). Storage conformance:

- **MySQL deployments MUST run a strict SQL mode.** The token hash column is
  `VARCHAR(64)` — exactly 64 chars with zero headroom. A non-strict mode
  silently truncates and thereby accepts forged hashes; that deployment is
  unsupported.
- Session ids must stay within `VARCHAR(64)` (128-bit random hex or
  dash-less UUIDs fit).

Lifecycle writes are compare-and-swap and atomic per instance:

- `install_login_session` — new login installs a NEW session instance
  (replaces any prior active instance); a lost race surfaces as 409
  `login_session_conflict`;
- `rotate_session` (refresh) — matches scope + session_id +
  expected revision + expected hash; a lost race is 401
  (`unauthenticated`), never an unconditional rewrite; the same-second
  refresh still yields a DIFFERENT token (revision changes the claims);
- `revoke_session` (logout) — revokes BY session instance, killing the
  rotated descendant too; a stale logout of an already-replaced instance is
  an idempotent success and cannot touch a newer login.

Cookies:

- Session cookie: `bcs_session = <jwt>; HttpOnly; Secure (https transport);
  SameSite=Lax; Path=/`.
- Login challenge: the callback family prefixing every URL discovery with a
  browser proof. `GET /auth/url` sets `__Host-bcs_oauth_login = <nonce>;
  Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=300` on production HTTPS.
  Plain-HTTP local development uses the separate, non-`__Host-` name
  `bcs_oauth_login` — production never downgrades, and EXACTLY ONE name is
  accepted per transport (the OTHER mode's cookie never satisfies the
  binding, so a hostile-subdomain `Domain`-scoped cookie planted over
  production HTTPS cannot be replayed).
- The completing `302` carries TWO separate `Set-Cookie` headers — install
  `bcs_session`, clear the challenge (Max-Age=0) — appended, never a merged
  comma form; a matched-but-failed completion still clears the challenge and
  never installs a session cookie.
- The nonce never appears in URLs, JSON, query parameters, or logs; login
  responses (and every auth-flow response) carry `Cache-Control: no-store`.
- The in-memory pending-login store is bounded to 4,096 provider states.
  Each issue/consume reclaims up to 64 expired batches; consumed batches also
  leave the expiry index immediately. A full store rejects issuance with
  503 `auth_dependency_unavailable`, no new challenge cookie and no eviction
  of other live logins.
- One outstanding batch per browser: re-issuing the URL list rotates the
  nonce and invalidates the earlier batch in that browser; a callback from
  another browser is rejected WITHOUT consuming the initiating browser's
  pending state.

## 4. Shared-writer safety and deployment constraints

- The unconditional `update_token` write path of the pre-2026-11 facade is
  REMOVED. All writers — legacy `/auth/*` and V1 `/openapi/v1/auth/*` — use
  the same strict engine, so the legacy entry cannot bypass browser binding
  or atomic session semantics (legacy keeps its response shapes, not its
  old safety weaknesses).
- Mixed-version rollout of old and new binaries against one shared session
  store is NOT a supported upgrade path: old binaries that still write
  unconditionally can resurrect revoked sessions. Upgrade = drain old
  writers → migrate schema → deploy the fixed writers → re-login.
- Multi-instance deployments: OAuth login requires either an atomically
  shared pending-login state store, or explicit single-instance/sticky
  routing. Without one of them the OAuth source must stay disabled.
- Rollback: removing `[api.auth]` returns to the compatibility assembly,
  but never to unconditional identity/session writes; the schema (sid/revision
  columns) and the session JWT contract persist. Pre-upgrade sid-less
  sessions are invalidated in either direction — re-login is the recovery.
- The identity model stays: `ensure_identity` per
  `(provider, external_user_id, env)`; deleting a session never deletes the
  identity row or user assets.

## 5. Configuration contract

`[api.auth]` is the V1 chain. Absent `[api.auth]` = compatibility assembly
(current Gateway-protected surface; legacy login entry choices unchanged —
but on the SAME hardened lifecycles). In this compatibility mode, V1 OAuth
refresh/logout use the finite exact `cors.allowed_origins` subset, just like
the legacy entry: wildcard/null entries grant no trust, and no exact origins
means cookie writes are denied. Malformed exact origins fail validation before
routes are published. Explicit `[api.auth]` continues to use only
its own `trusted_browser_origins`, never the CORS fallback. Present `[api.auth]`
fully defines
the chain: `chain` (required, ordered, unique, non-empty), plus
`public_base_url` and `session_signing_key_secret` (required with any OAuth
source), `session_idle_timeout_minutes` (default 30, positive, expanded once
to seconds), `success_redirect_path` (in-site relative path only, default
`/`), `trusted_browser_origins` (required with any cookie-kind source,
forbidden otherwise: exact `scheme://host[:port]` matches, no wildcards/paths/
userinfo). Callbacks are always `{public_base_url}/callback/{provider}`,
never appended `/auth`, never inferred from Host/Forwarded headers.

Per-source tables under `[api.auth.<name>]`:

- `gateway` — `issuers` (default `["gateway", "backend"]`), `audience`
  (default `bcs`), `key_id` (default `bare`), required `signing_key_secret`;
- `alipay` — required `client_id`, `private_key_secret`,
  `alipay_public_key_secret` (RSA material verified at startup);
- `github` / `google` / `wechat` — required `client_id`,
  `client_secret_secret`.

Secret fields are logical references into the secret backend (a key NAME),
never literal material. Two hard rules that operators keep tripping on:

1. **The provider table being present does not enable anything.** A provider
   joins the surface only by appearing in `chain`. A present-but-not-chained
   table is structurally validated (and its secrets are NOT read, no
   client is initialized) and otherwise inert.
2. **The local/mock plugins never join this chain implicitly.** `[api.auth]`
   selects only registered named sources; the legacy `local`/legacy-mock
   choice of `[auth]` is not inherited, and there is no production Mock
   source. Test identities come from injected fakes in tests, not from
   config.

Full samples (gateway-only, OAuth-only, mixed) live in
`configs/bcs-config-example.toml`; the local profile keeps its commented
gateway-only shape in `configs/bcs-config-local.toml`.

## 6. Route coverage

The composite verifier covers the V1 protected collaboration routes (public
OpenAPI and internal API), the Bot Authority router, the connection-token
route, and `/auth/user`. Manifest, public registration, invitation claim, and
public draft reads keep their existing dedicated trusts. The legacy browser
Human chain (`/collaboration/user/**`) stays on its own verifier and is NOT
switched to this chain. With no OAuth source configured, the login family
answers `auth_not_configured` 404 while `/user` still resolves Humans through
the remaining sources.

## 7. Compatibility notes (supersedes 2026-08-28 facade spec parts)

The 2026-08-28 OpenAPI Auth Facade design documented (a) missing-gateway →
cookie fallback, and (b) best-effort logout; both are removed by the
2026-11 security upgrade: the cookie path was never implemented as
described, and best-effort persistence violates the repository-wide rule
that failed writes must surface as errors. Historical statements in that
spec and in provider/migration notes that presumed unconditional
`update_token` session writes are version-bound: they describe pre-upgrade
binaries. See also
[../gateway-principal/contract.md](../gateway-principal/contract.md) for
the typed gateway principal that remains the first-class production source.
