# bcs-caller-identity-jwt Context

## Provides

Default JWT `CallerIdentityPlugin` (spec `2026-09-16-caller-identity-session-container`
§10.13.4): EdDSA-only JOSE profile with a `kid`-addressed trusted public-key
set (verifiers never hold the signing key), a domain-separated HMAC pseudonym
subject derived from the verified principal with key material independent of
the signing keys, and proofs whose expiry is the earliest of the
ingress/subject/request deadlines and the configured TTL, floored to the
JWT second grid. Establishment, issuance and verification all read the
explicit caller-provided clock; the closed error set never echoes secrets.
Token metadata Debug is redacted; subject proofs use a distinct audience and
token type so a subject proof can never be replayed as an execution proof.
The plugin decides identity material only — never routing, MCP grants or
derivation eligibility.

## Does not provide

Any external I/O, private identity exchange, persistence, routing or MCP grants.
The adapter implements `bcs-caller-identity-api`, not a Service API port. Its
`jwt` inventory factory validates typed JWT configuration. Bootstrap links this
crate and selects it through the generic Plugin API registry; no concrete
selection logic belongs to core. Signing and verification behavior is unchanged.

## Tests

`tests/jwt_profile_conformance.rs`: 17 deterministic conformance cases
(pseudonym stability/isolation, earliest-deadline expiry and second-grid
flooring, tamper, unknown kid, untrusted key, key rotation, HS256/`none`
algorithm confusion, audience/purpose/scope/run-attribute mismatches,
exact binding semantics, attempt-zero and malformed issuance rejection,
subject-proof confusion rejection, Debug redaction, fail-closed config).
Ed25519 key material in tests is generated fixture data, not production
material.
The suite also mounts the shared Caller Identity Plugin API v1 contract.
