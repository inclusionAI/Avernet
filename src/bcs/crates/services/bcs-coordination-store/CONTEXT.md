# bcs-coordination-store Context

## Provides

`CoordinationIntentPort` backed by the existing `CachePlugin`: reference payload
reads, atomic claims and immutable execution receipts.

## Consumes

Authenticated run context from `bcs-service-api` and the cache instance selected
by bootstrap. MCP producers use the same cache and documented UTF-8 JSON keys.

## Allowed dependencies

- `bcs-service-api` and `bcs-cache-api`
- Serialization, UUID and async runtime libraries

## Forbidden dependencies

Concrete cache vendors, HTTP clients, provider policy, task dispatch, bootstrap,
direct environment reads and company SDKs.

## Runtime ownership

This store owns keys, serialization, bounded read/receipt retries and TTLs.
Task services retain authorization and execution. Claims are never reclaimed
or retried to grant execution after an uncertain acknowledgement.

## Tests

`cargo test -p bcs-coordination-store` runs the shared port conformance harness,
cross-instance concurrency, cache failures, long payloads and receipt checks.
