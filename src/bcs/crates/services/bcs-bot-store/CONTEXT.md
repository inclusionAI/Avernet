# bcs-bot-store Context

## Provides

`find_agent_registration` reads current-environment nondeleted Bot rows directly,
independent of cache, heartbeat and enabled state. One query returns at most two
rows; ambiguity is a conflict and read/decoding failures remain errors. Bot
Provider affiliation is authoritative, with legacy binding metadata used only
when both affiliation columns are null. Name/summary and unaffiliated Provider
fields may be null. No writes, tokens, cache population or schema changes occur.
SQLite conformance verifies isolation, errors and bounded query/row counts.
The dedicated agent_code takes precedence; historical JSON agent_code is used
only when that column is null. Both sources participate in conflict detection.
The legacy branch can examine unbackfilled rows; LIMIT bounds rows returned,
not total database work. No automatic backfill or migration is introduced.

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

BotProviderRepoPort has MemoryBotProviderStore and DbProviderStore implementations.
Provider identity, connection mode and webhook override belong to bcs_bots, with
environment-scoped Provider/ref uniqueness including tombstones. SQL Bot creation,
gateway binding projection, webhook updates and soft deletion use DB transactions;
upstream never writes a binding. Errors propagate; runtime credentials are not
cached in a second journal. Memory metadata is process-local.

ProviderBindingProjection selects legacy binding reads or strict Bot-mode reads,
independently of gateway dual writes. Affiliation alone is not a delivery binding.
SQLite 030 / MySQL 029 add nullable migration-state columns and a unique index.
Bot Provider metadata has no separate timestamps or version field. Binding
response timestamps stay on the legacy binding; Bot-mode projections read them
from the matching binding without changing the direction/metadata authority.
Mutations retain business-state checks, uniqueness and gateway transactionality;
explicit webhook updates do not require an old version. Repeated affiliation does
not rewrite callback state. No new explicit locks are added to the existing
single-binding lock, and Provider updates never lock all affiliated Bots.
Historical data correction is handled by a separate reviewed work order, not a
dedicated store method or executable. Normal registration, mutation and gateway
projection writes are unchanged. No separate registration table is created or
queried. Upstream migrations remain
unchanged; this PR's unused drafts were consolidated before its first deployment.
See docs/provider-bot-storage-migration.md for rollout limitations.
