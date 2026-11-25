# Caller Identity Plugin API v1.1

## Provides

`CallerIdentityPlugin` establishes a subject and issues a credential. ExecutionBound
providers also verify that proof against explicit execution expectations;
IdentityRelay providers explicitly reject execution verification. `proof_type` labels
the protected delivery wire; the label itself is not authentication evidence.

`CallerIdentityPluginFactory` is a static-link extension point. Only composition
roots call `build_caller_identity_plugin`. A linked adapter registers a unique
name through `inventory`; absent, duplicate or invalid selection fails closed.
There is no fallback from an unavailable private provider to JWT.

## Consumes

`bcs-domain::caller_identity_material` owns shared runtime-only trusted identity
envelopes. `bcs-config-api` owns the configuration DTO. Neither this API nor its
implementations depend on `bcs-service-api`. Services consume this API, never a
concrete identity implementation. Service API reexports shared domain data only.

## Contract and trust

This is an in-process trusted Rust plugin contract, not a sandbox or remote wire
protocol. Instances are constructed once at startup and shared as `Arc<dyn
CallerIdentityPlugin>`; implementations must be Send + Sync. Construction failure
refuses startup; runtime errors use the closed, secret-free error enum. External
I/O belongs to adapters, not core. Callers retain cancellation/deadline policy.

Only trusted authentication and BCS-approved run, scope, lineage and isolation
facts may enter issuance. Attempt zero is invalid. `credential_profile()` declares
one of two guarantees (never infer it from a wire label):

- `ExecutionBound` (default, existing JWT behavior): fresh proofs bounded by
  ingress/subject/request expiry and exact scope/run/purpose/isolation verification.
  Missing isolation is exact absence, not a wildcard.
- `IdentityRelay`: upstream identity credentials may be reused and have their own
  expiry. Return the actual credential expiry, never clamp it to imply upstream
  revocation. Issuance must finish within the BCS authorization window. Execution
  constraints remain BCS policy; `verify_execution` must return
  `ExecutionVerificationUnsupported`, never fabricated execution facts. A profile
  unable to issue purpose-restricted delegation credentials must reject that purpose.

Ingress, subject and execution authorization windows still only shrink. Credential
expiry does not authorize a new BCS run or extend a deadline; cancellation does
not promise revocation of an external identity credential. The existing protected
send carries credential expiry and BCS authorization deadlines separately.
Validated bounded upstream machine codes may cross the plugin seam via
`UpstreamRejected`; free-form messages, responses and secrets may not.
Proofs and upstream evidence must never enter persistence, logs or model input.
Routing, membership, delegation authorization and MCP grants remain outside this
interface. The plugin does not add database reads or writes to dispatch.

## Configuration and compatibility

`[caller_identity] plugin = "jwt"` and the existing `[caller_identity.jwt]`
settings remain supported. JWT requires those settings and rejects nonempty
`options`. Other linked plugins can accept their own validated `options` without
supplying JWT keys. The public binary links only JWT. Private implementations
and SDK dependencies live in OCB, not the public workspace.

The Rust trait formerly exported by `bcs-service-api::port` moves here; consumers
must update imports and dependencies. Shared Service API data paths remain
reexported. Token claims, protected-send wire fields and database schema do not
change. Breaking contract changes require a versioned migration and matching
conformance updates.

## Verification

Reusable v1.1 tests live in `bcs-test-support::contract::plugin::caller_identity`.
Each adapter mounts the same issuance/verification/deadline/redaction contract
and adds profile-specific negative tests. Registry tests cover external plugin
selection without JWT configuration, unknown names, duplicates and failed builds.

## v1.1 propagation

Additive Rust trait method defaults to ExecutionBound for existing implementers.
JWT tests keep the original bounded issuance and exact verification requirements.
IdentityRelay conformance requires explicit unsupported execution verification.
The shared runtime token envelope represents actual credential expiry; no DB or
protected-send schema change is made. The private relay implementation must ship
with this API version. Core dispatch continues to enforce BCS-owned deadlines and
lineage, without inferring execution claims from identity-only credentials.
