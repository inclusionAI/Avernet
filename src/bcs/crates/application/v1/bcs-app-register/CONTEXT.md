# bcs-app-register Context

## Provides

- The OpenAPI V1 `RegisterService` application facade for Human token issuance
  and anonymous registration authenticated solely by the supplied register token.
- Legacy v1 issuance and ordinary upstream registration, preserving the existing
  response fields and onboarding-error behavior.
- Provider-scoped v2 issuance, signed mode validation and public registration
  metadata projection, with sanitized application errors.

## Consumes

- `bcs-service-api::application::v1` registration DTOs and error contracts.
- `BotManagementService` and `BotOnboardingService` for legacy registration.
- `ProviderRegistrationCoreService` for Provider authorization, redemption
  reauthorization and all scoped registration mutations.
- The separate legacy v1 and Provider v2 codecs in `bcs-domain`.

## Allowed dependencies

- Service API and domain contracts, async traits and tracing.
- HTTP adapter and serialization utilities as development dependencies for
  tests that exercise the real facade through the registration routes.

## Forbidden dependencies

- Concrete core services, repositories, plugins and bootstrap in runtime code.
- HTTP framework types, direct storage access and environment reads in runtime code.

## Configuration

Bootstrap supplies the signing secret and services. The unchanged `new`
constructor leaves Provider registration disabled; `with_provider_registration`
enables it with an injected core contract. Token lifetime remains six hours.
Provider eligibility and endpoint policy belong to the core and its configuration.

## Runtime ownership

The facade validates names, verifies v1 first, and only tries v2 after
`UnsupportedVersion`. Legacy tokens cannot request Provider or gateway fields.
Scoped owner and Provider identities come only from the signed token; the selected
mode must be in its allowlist and `provider_bot_ref` must be nonblank.

Only v1 tokens are interchangeable with legacy registration. V2 tokens are
purpose-bound Provider capabilities and legacy registration rejects them.
The core owns persistence, Provider/ref uniqueness, owner edges and delivery binding.
Duplicate scoped refs conflict; no runtime credential is replayed. A valid token
can create different refs. Owner-edge failures can require operator reconciliation.
The HTTP adapter owns query parsing, status codes, envelopes and no-store headers.

## Change impact

Bootstrap injects the scoped core while legacy constructors retain their behavior.
The OpenAPI adapter consumes optional metadata; v1 responses omit it entirely.
Changes to signed scope, error codes or response projection require matching
registration contract and compatibility tests.

## Tests

- `cargo test -p bcs-app-register`
- `cargo test -p bcs-api-http --test register_routes`

The facade suite includes real HTTP adapter integration with a fake scoped core,
legacy compatibility, mode and credential rejection, and sanitized error mapping.
