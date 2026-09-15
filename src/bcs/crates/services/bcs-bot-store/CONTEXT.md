# bcs-bot-store Context

## Provides

Bot/Provider repository implementations own SQL and process-local caching.
DbProviderStore caches binding presence (including negative results), Provider
records and by-kind credentials with bounded 30-second TTL caches. Successful
mutations invalidate the affected key before returning; entry identity fences
stale loader refill. Same-key concurrent reads share a loader. Authentication
by secret remains a direct repository read. Errors never become negative hits.

## Consumes

Service API repository contracts, DB/cache plugin contracts and config context.
No worker policy, network delivery adapter or concrete metrics dependency.

## Runtime ownership

Caches live with the selected shared store, not individual workers. Keys include
environment. Credential values are memory-only, never logged or included in
cache statistics; statistics contain only fixed outcome totals. Direct DB writes
have TTL-delayed visibility. Single-instance invalidation is not distributed coherence.

## Change impact and tests

Provider administration and Bot resolution share the same cached store; disable,
insert and metadata update invalidate after successful DB writes. Provider repo
contracts and SQLite tests verify mutation visibility; cache tests cover negative
hits, errors, bounds, singleflight and invalidation during an old load.

## Streaming identity admission

`BotRepoPort::connect_streaming` owns one complete identity decision for both
PersistentBotRepo and MemoryBotRepo. It preserves token-first resolution, protects
unexpired temporary and durable identities, refuses soft-deleted identities, and
reclaims expired temporary state only after a successful persistent lookup finds
no identity and there is no active connection. Streaming expiry is heartbeat age
greater than 300 seconds; ordinary read visibility is unchanged.

Admission, persistence writers, heartbeat renewal and disconnect share a per-Bot
lock. The global Bot/token maps are held only for memory operations. A cold
token lookup carries its full row into admission; a process-local write epoch
forces a fresh read if a local identity mutation overlapped that lookup. This is
single-process serialization, not cross-instance or direct-SQL exclusion.

Successful reconnects reuse loaded capabilities without writing them back. MOCK
promotion uses a conditional token update before memory attachment; read/write
failures remain errors. SQL results and misses live only for the current request.
No background cleanup or new negative cache is introduced. Tests cover identity
states, SQL budgets, failure paths, concurrent claims and deletion during lookup.
