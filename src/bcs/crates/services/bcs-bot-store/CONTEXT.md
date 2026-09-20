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

ProviderBotBinding stores a nullable webhook_url. Endpoint writes are scoped by environment, Provider and Bot; failures propagate, successful writes invalidate local binding cache. Other instances observe updates within the existing 30-second TTL. SQLite migration 028 and MySQL migration 027 add the nullable column.

ProviderRegistrationRepoPort has independent Memory/DB implementations. Its
environment-scoped journal reserves immutable Provider/ref identity atomically,
without cache or delivery semantics. DB uniqueness coordinates multiple processes;
errors propagate and completion of a missing reservation fails. The internal
record contains a Bot runtime credential and is not logged or directly exposed.
SQLite 030 / MySQL 029 are additive; shared conformance plus process-concurrency,
restart and failure tests cover the store. Memory mode is process-local.
