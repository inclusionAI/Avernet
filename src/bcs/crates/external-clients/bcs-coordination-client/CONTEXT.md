# bcs-coordination-client Context

## Provides

`CoordinationIntentPort` over an authenticated, configured HTTP resolver API.
Bounded reads, single claim attempts, immutable finish retries, and reference
payload validation. Credentials and endpoints never come from tool output.

## Consumes

Coordination references and authenticated run context from `bcs-service-api`.
Endpoint and bearer credential injected by bootstrap.

## Allowed dependencies

- `bcs-service-api`
- HTTP, serialization and async runtime libraries

## Forbidden dependencies

Database/cache implementations, provider policy, task dispatch, bootstrap,
direct environment reads and company SDKs.

## Runtime ownership

The remote store owns payload and claim retention. This adapter owns only HTTP
exchanges. Task services own authorization, execution and its result. No claim
reclamation or redispatch after an uncertain exchange.

## Tests

`cargo test -p bcs-coordination-client`
