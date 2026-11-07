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
Issuance includes the authorized Provider auth mode as signed `auth_mode` in the
unchanged v2 Token format. Redemption forwards this optional verified claim to
core for comparison with current configuration. Historical Tokens lacking the
claim remain valid. Response selection metadata and HTTP request fields do not change.

Both v1 and v2 tokens are interchangeable between legacy and OpenAPI HTTP
surfaces. V2 tokens remain purpose-bound Provider capabilities. Legacy HTTP
verifies v2 before delegation to preserve unsupported-version errors; the shared
application facade revalidates the capability and routes it to Provider registration.
The core owns persistence, Provider/ref uniqueness, owner edges and delivery binding.
Duplicate scoped refs conflict; no runtime credential is replayed. A valid token
can create different refs. Owner-edge failures can require operator reconciliation.
The HTTP adapter owns query parsing, status codes, envelopes and no-store headers.

## Change impact

Bootstrap injects the scoped core while legacy constructors retain their behavior.
Both HTTP adapters consume optional metadata; v1 responses omit it entirely.
Changes to signed scope, error codes or response projection require matching
registration contract and compatibility tests.

## Tests

- `cargo test -p bcs-app-register`
- `cargo test -p bcs-api-http --test register_routes`

The facade suite includes real HTTP adapter integration with a fake scoped core,
legacy compatibility, mode and credential rejection, and sanitized error mapping.

## Subbot registration

The facade passes the complete `provider_bot_ref` into the shared registration
core, which derives authentication mode from the Provider configuration and
parses the parent/slug only for agentpass-subbots.
Successful scoped responses add auth mode, original parent and Bot slug metadata.
Occupancy validation belongs to registration itself; no precheck API is exposed.


## A2A registration

The explicit register_a2a_bot use case verifies the same scoped v2 token, requires
Gateway mode without a webhook, then reauthorizes Provider management before
calling the injected A2aCardPort. Self-service ordinary registration is insufficient
to bind a remote Agent. Core repeats current admission/credential validation and
commits the full Card with Bot, binding, Human, owner, profile and audit in one
transaction. Fetch never holds a database lock. Bootstrap injects the guarded
fetcher; SDK/HTTP types do not enter this facade. Legacy tokens reject this path.
Both HTTP registration surfaces accept optional agent_card_url and keep existing
response envelopes/no-store headers. Failed writes return no runtime credential.

A2A scoped registration accepts URL or inline JSON Card through the same validator.
An omitted display name defaults from Card.name, and the complete original Card
plus nullable source URL and revision is committed in the lifecycle transaction.
Neither Provider identity nor ownership derives from Card content. See
docs/specs/a2a/minimal-card-registration-v1.md.
