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

`BotRepoPort::begin_identity_operation` borrows the existing PersistentBotRepo or
MemoryBotRepo. It owns lookup reuse, serialization and storage updates; BotCore
owns the connection decision. PersistentBotRepo still uses its original memory
maps and SQL DB. MemoryBotRepo is the alternative memory/local-file registry.
The operation creates no second registry and does not copy the registry maps.

The request scope keeps token-candidate lookup separate from the locked Bot ID
lookup. Persistent state is NotLoaded, Loaded(Some(identity)), or Loaded(None).
Only a successful read can produce Loaded; errors propagate without recording a
miss. Neither token misses nor ID misses are written into the shared Bot maps.
Raw memory snapshots include heartbeat time, active-connection and deletion facts,
including expired temporary state; ordinary read visibility is unchanged.

Admission, persistence writers, heartbeat renewal and disconnect share a per-Bot
lock. The global Bot/token maps are held only for memory operations. A cold
token lookup carries its full row into admission; a process-local write epoch
forces a fresh read if a local identity mutation overlapped that lookup. This is
single-process serialization, not cross-instance or direct-SQL exclusion.

Core supplies the chosen update while the scope still owns the Bot lock. Token
replacement compares the loaded persistent token/deletion state before memory
attachment; a failed comparison or persistence write cannot attach memory. The
consuming update or scope Drop releases the lock, including cancellation. No DB
transaction or global map lock spans Core decisions. Successful reconnects reuse
loaded capabilities without writing them back. No background cleanup or global
negative cache is introduced. Store tests cover lookup reuse, failures, lock
lifetime, index maintenance and conditional writes; Core tests cover policy and
end-to-end SQL budgets.
