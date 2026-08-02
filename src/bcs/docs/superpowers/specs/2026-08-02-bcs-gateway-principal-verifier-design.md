# BCS Gateway Principal Verifier Design

- **Date:** 2026-08-02
- **Status:** Approved (brainstorm)
- **Scope:** Gateway authentication and Principal parsing for the BCS V1 HTTP boundary

## Context

Gateway authenticates the upstream caller, resolves every identity present on
the request, and forwards the resulting Principal set in a short-lived JWT in
`X-Avernet-Principal`. The community Gateway signer currently uses HS256 with a
shared secret and emits `kid`, `iss`, `aud`, `iat`, `exp`, and `principals`.

BCS V1 currently has 27 implemented HTTP operations, but the production
bootstrap does not mount `bcs-api-http`. Its route tests inject a test-only
`PrincipalVerifier`, and every V1 Application command currently carries one
closed `Principal::{Human, Bot}`. That single-actor type cannot represent the
Gateway identity set: a valid request can contain User, Bot, App, and AccessKey
at the same time.

This design prepares the trusted Gateway authentication boundary without
inventing an Actor-selection rule. It deliberately keeps V1 production
unreachable until a later design decides how each use case selects a Human or
Bot Actor from a verified caller.

## Goals

1. Verify the current Gateway HS256 Principal token without changing Gateway
   behavior or relying on mTLS.
2. Parse all four Gateway Principal kinds and preserve their coexistence.
3. Convert the wire payload into a transport-neutral, secret-free
   `AuthenticatedCaller` owned by the V1 Application contract.
4. Fail closed on malformed, forged, expired, mis-scoped, or internally
   contradictory identity sets.
5. Establish shared Gateway-provider and BCS-consumer contract fixtures and
   tests.
6. Keep the verifier isolated inside the versioned HTTP adapter so it can be
   integrated later without leaking JWT concepts into Application services.

## Non-goals

- Do not modify the current 27 V1 Application commands.
- Do not replace or rename the current single `Principal::{Human, Bot}`.
- Do not define the Actor-selection policy for User+Bot callers.
- Do not change route handlers, the current test `PrincipalVerifier`, or HTTP
  middleware.
- Do not add bootstrap/config wiring, resolve a production secret, or mount the
  V1 router.
- Do not change Gateway signing behavior.
- Do not reuse the Bot token or AccessKey token forwarded inside the JWT.
- Do not add scopes that Gateway does not provide.

## Chosen approach

Use the Rust `jsonwebtoken` crate inside
`bcs-api-http::v1::gateway_principal`. The library handles compact-JWT parsing
and signature verification; BCS pins HS256 and performs its own BCS-specific
header, time, Principal-set, and tenant validation.

The alternatives are rejected as follows:

- A manual implementation using `base64`, `hmac`, and `sha2` would duplicate
  security-sensitive JWT machinery.
- Extending `bcs-jwt` would couple the V1 HTTP adapter to a concrete service and
  mix the unrelated BCS session-token claims (`sub/src/iat/exp`) with the
  Gateway Principal protocol.

## Module and dependency boundaries

```text
bcs-service-api
  `-- application/v1/identity.rs
        transport-neutral authenticated identity contract

bcs-api-http
  `-- src/v1/gateway_principal/
        |-- mod.rs
        |-- wire.rs
        `-- verifier.rs
              Gateway JWT wire parsing and verification
```

Compile-time dependencies remain:

```text
bcs-api-http --> bcs-service-api
```

`bcs-service-api` must not depend on Axum, HTTP, JWT, `jsonwebtoken`, bootstrap,
or concrete services. `bcs-api-http` must not depend on bootstrap, legacy
`bcs-http`, `bcs-jwt`, or concrete V1 Application implementations.

No `bcs-config-api` or bootstrap change belongs to this batch. The verifier
constructor receives already-resolved trust material; a later integration
batch will define how bootstrap resolves and injects it.

## Internal authenticated identity contract

`bcs-service-api::application::v1::identity` defines the following conceptual
types:

```rust
pub struct AuthenticatedCaller {
    pub tenant: String,
    pub user: Option<AuthenticatedUserIdentity>,
    pub bot: Option<AuthenticatedBotIdentity>,
    pub app: Option<AuthenticatedAppIdentity>,
    pub access_key: Option<AuthenticatedAccessKeyIdentity>,
}

pub struct AuthenticatedUserIdentity {
    pub id: String,
    pub username: String,
    pub display_name: Option<String>,
    pub full_name: Option<String>,
}

pub struct AuthenticatedBotIdentity {
    pub bot_uuid: String,
    pub owner_id: String,
    pub app_id: i64,
    pub agent_code: String,
}

pub struct AuthenticatedAppIdentity {
    pub app_id: i64,
    pub app_name: String,
    pub owners: String,
    pub app_type: String,
}

pub struct AuthenticatedAccessKeyIdentity {
    pub access_key: String,
    pub expire_at: time::OffsetDateTime,
}
```

The caller stores one normalized tenant after verification. Nested tenant
copies are not retained because accepting contradictory tenants is forbidden.
The four optional identities may coexist, but at least one must be present.

These types contain no raw JWT, Gateway signing metadata, Bot token,
AccessKey token, HTTP type, or scopes. App and AccessKey are authenticated
calling context; neither becomes a BCS business Actor in this design.

## External Gateway token contract

The future HTTP integration consumes exactly one non-empty raw compact JWT from
`X-Avernet-Principal`; it does not use a `Bearer` prefix. Header extraction and
401 mapping are explicitly deferred from this batch, but the token contract is
fixed here so the verifier can be implemented and tested independently.

The JOSE header must contain:

```json
{
  "alg": "HS256",
  "typ": "JWT",
  "kid": "bare"
}
```

The claims payload must contain:

```json
{
  "iss": "gateway",
  "aud": "bcs",
  "iat": 1785657600,
  "exp": 1785657660,
  "principals": [
    {
      "type": "user",
      "tenant": "tenant-a",
      "subject": {
        "id": "user-1",
        "username": "alice"
      }
    }
  ]
}
```

Initial trust values are therefore:

- algorithm: `HS256`, pinned in code;
- token type: `JWT`;
- key ID: `bare`;
- issuer: `gateway`;
- audience: `bcs`;
- clock-skew allowance: 5 seconds.

The verifier receives the expected issuer, audience, and key ID as explicit
runtime trust inputs so later key rotation and deployment wiring do not require
the verifier to read environment variables. The accepted algorithm and clock
skew are not configurable. Construction rejects an empty signing key, issuer,
audience, or key ID before any token can be verified.

## Gateway wire projection

`wire.rs` privately models the Gateway discriminated union:

- `user`: outer `tenant` plus `subject`;
- `bot`: outer `tenant` plus nested `bot`;
- `app`: outer `tenant` plus nested `app`;
- `access_key`: outer `tenant` plus nested `access_key`.

Unknown fields within a known Principal are ignored so Gateway can add fields
compatibly. An unknown Principal discriminator is rejected. Required known
fields cannot be renamed or removed without failing closed.

The private Bot and AccessKey projections intentionally omit `bot.token` and
`access_key.access_key_token`. Serde ignores these wire fields without storing
them in a Rust object. BCS also does not require them to be present, allowing
Gateway to stop forwarding those secrets in a later compatible change.

## Verification pipeline

`GatewayPrincipalTokenVerifier` receives a token string and resolved trust
material. It does not receive `HeaderMap`, read environment variables, access a
secret store, select an Actor, or return an HTTP response.

Validation runs in this order:

1. Reject an empty token. Empty trust material has already failed verifier
   construction.
2. Decode the JOSE header.
3. Require `alg=HS256`, `typ=JWT`, and the expected `kid`.
4. Verify the signature with a `jsonwebtoken::DecodingKey` created from the
   shared HMAC secret.
5. Require `iss`, `aud`, `iat`, `exp`, and `principals`.
6. Require the configured issuer and the exact string audience `bcs`.
7. Require integer NumericDate values and `iat < exp`.
8. Reject `iat > now + 5 seconds`.
9. Reject when `now >= exp + 5 seconds`.
10. Validate and normalize the complete Principal set.
11. Return `AuthenticatedCaller` only after every check succeeds.

The public production entrypoint obtains current Unix time from the system
clock. Verification delegates to an internal `verify_at(token, now)` path so
unit tests can exercise time boundaries deterministically. The verifier keeps
`jsonwebtoken` signature verification and algorithm pinning enabled, but turns
off that library's wall-clock expiration check and applies the time rules above
inside `verify_at`; otherwise fixed-time contract tests would still depend on
the machine clock.

## Principal-set validation

- `principals` must contain at least one entry.
- Each of User, Bot, App, and AccessKey may occur at most once.
- Principal ordering has no semantic meaning.
- Every outer tenant must be non-blank and identical.
- `bot.bot.tenant` and `app.app.tenant` must equal the outer tenant.
- `user.subject.tenant_id` may be null; when present it must be non-blank and
  equal the outer tenant.
- Stable identity fields used by BCS must be non-blank after trimming. These
  include User ID and username, Bot UUID, Bot owner ID, Bot agent code, and
  AccessKey ID.
- AccessKey `expire_at` must parse as RFC 3339 into `OffsetDateTime`.
- BCS does not independently re-authenticate the underlying AccessKey from
  `expire_at`; Gateway authenticates that credential, while the Principal JWT
  `exp` controls this request's authentication lifetime.

BCS accepts User-only, Bot-only, User+Bot, and all other valid combinations. It
does not impose Backend's user-required admission rule.

## Errors and secret handling

The verifier returns a closed error classification such as:

```rust
pub enum GatewayPrincipalVerifierBuildError {
    EmptySigningKey,
    InvalidTrustConfiguration,
}

pub enum GatewayPrincipalVerificationError {
    EmptyToken,
    InvalidHeader,
    UnsupportedAlgorithm,
    InvalidTokenType,
    InvalidKeyId,
    InvalidSignature,
    InvalidClaims,
    InvalidPrincipalSet,
}
```

Construction errors are separate from request-token errors so later bootstrap
can fail startup on invalid trust configuration. The error contract is
diagnostic, not a wrapper around raw
`jsonwebtoken`/Serde errors. It must never include the JWT, signing key, raw
payload, Bot token, or AccessKey token. HTTP 401 mapping is deferred until the
middleware integration batch.

The verifier and its decoding key must not derive or implement a `Debug`
representation that exposes key material.

## Contract fixtures

Add the canonical BCS V1 Gateway Principal contract under:

```text
src/bcs/api-contracts/v1/gateway-principal/
  |-- contract.md
  `-- principal-set.json
```

The fixture contains one example of all four Principal kinds coexisting.
Clearly labeled synthetic marker values occupy the Bot-token and
AccessKey-token fields solely to prove that BCS discards them. The fixture
contains no real credential, signing key, or reusable long-lived JWT.

Gateway provider tests load the fixture and prove that current Gateway model
serialization and signer claims match it. BCS consumer tests load the same
fixture, sign it with an explicitly test-only HMAC key at test time, verify it,
and assert the normalized `AuthenticatedCaller`. This pins the cross-language
payload contract without committing a bearer token.

## Test strategy

### Successful verification

- User-only caller.
- Bot-only caller.
- User+Bot caller.
- User+Bot+App+AccessKey caller from the shared fixture.
- Principal order does not affect the normalized result.
- Unknown fields on known types are ignored.
- Forwarded secret fields are absent from the returned identity and any
  serialization/debug output.
- Time skew within 5 seconds is accepted.

### Rejection cases

- Empty or malformed compact token.
- Missing required claim.
- Unsigned token or algorithm other than HS256.
- Wrong `typ`, `kid`, issuer, audience, or signing key.
- Expired token, future-issued token beyond skew, or `iat >= exp`.
- Missing, non-array, or empty Principal set.
- Unknown or duplicate Principal kind.
- Blank stable identity.
- Mixed outer tenants or nested/outer tenant contradiction.
- Invalid AccessKey timestamp.

### Boundary and regression tests

- `bcs-service-api::application::v1` remains transport-agnostic.
- `bcs-api-http` continues to depend only on Application service contracts and
  HTTP/serialization/security utility crates.
- Existing V1 route tests and the test-only single-Principal verifier continue
  to pass unchanged.
- Production bootstrap still has no `bcs-api-http` mount after this batch.

Focused verification commands are:

```text
cargo test --package bcs-service-api
cargo test --package bcs-api-http
cargo check --package bcs-service-api --all-targets
cargo check --package bcs-api-http --all-targets
Gateway Principal provider contract pytest
BCS boundary/architecture tests
```

## Staging and follow-up

This batch ends with a verified, tested authentication component that is not
on the live request path. That is intentional rather than an incomplete
security fallback.

A separate design must next define:

1. how each V1 use case selects an `ActorPrincipal` when User and Bot coexist;
2. how all 27 commands carry the complete `AuthenticatedCaller` without losing
   App and AccessKey context;
3. bootstrap trust configuration and one-time secret resolution;
4. middleware extraction, uniform 401 mapping, and V1 router mounting;
5. end-to-end Gateway-to-BCS coverage.

No production mount may occur before those decisions and migrations are
complete.
