# bcs-bot-store Context

## Provides

Provider slug is optional and unique within environment, including disabled rows.
Memory and SQL share the slug conformance harness. SQL discovery performs one
indexed, environment-scoped SELECT with LIMIT 1 and no cache, credentials or writes.
Metadata updates atomically include slug/name/config; duplicate writes conflict
and preserve the previous values. SQLite 033 / MySQL 032 add the nullable column
and unique `(env, slug)` index without rewriting historical migrations.

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

## AgentPass subbot queries

`query_agent_bots` and `lookup_bot_token` return authoritative identity records
with joined Provider/binding state. Exact Actor/token reads return at most one
row; implicit ordinary selection returns at most two candidates; discovery reads
at most 101 rows. SQL predicates isolate environment and deleted actors. Memory
storage persists a credential-free affiliation marker and fails closed when
Provider metadata is missing after restart. Existing columns and uniqueness
constraints are reused; no migration or additional credential cache is added.

A freshly connected ordinary WS Bot can authenticate before onboarding persists
its row. After the indexed token read misses, only a matching in-memory pending
credential can trigger one extra bounded existence query (Bot and binding keys,
including tombstones). Any persisted identity blocks this fallback, so stale
memory tokens cannot bypass revocation, deletion or Provider state. Unknown
tokens incur no extra query; no writes or new cache are introduced.
## Provider collaboration scope
Bot Provider authorization uses a bounded fresh environment-scoped LEFT JOIN (maximum 256 actors per read); no policy cache or group policy table is introduced. Provider metadata writes atomically reject relaxation of same_owner, including stale ordinary PATCH config.
Provider tightening to same_owner atomically rejects related public groups through current environment-scoped membership. Memory implementations receive a Group membership projection and weak Bot-metadata reference; their policy gate serializes Group writes against policy updates. Failed tightening leaves all Provider metadata unchanged.
Affiliation into a same_owner Provider rejects current public membership before writing Bot metadata or gateway projection. SQL attachment locks Provider then Bot and checks membership inside the transaction; public Group writes also lock unbound Bot rows. Memory attachment uses the existing policy gate. A fresh preflight port supports registration before capability merging; the write always rechecks.
## Bot authority

Bot authority state (bot-authority spec, plan Tasks 3–4) joins the SAME
atomic shared state for Memory (`MemoryBotState`) and the SQL chain: the
owner/manager edge projection, `bcs_bots.ownership_version`, the
`bot_ownership_transfers` current-round row (one pending per
`(env, bot_id, target_owner_actor_id)`) and the `bot_authority_changes`
permission-scope audit. Registration commits the owner identity, the owner
edge, `ownership_version` and the default runtime profile in one command
(`register_with_ownership` → `CommittedBotRegistration`); owner transfer
acceptance moves owner/manager edges and bumps the version inside one
transaction, restoring the ex-owner as a plain manager. Manager sync is a
set replacement in one transaction (input ≤1,000 deduped known Humans,
stock cap 10,000, bounded retry for deterministic local rollbacks only);
the owner is never a sync operand. Provider retirement (three branches),
cleanup-pending retry and retire audits share the same atomic command
boundary; a failed post-commit cleanup returns
`retirement_cleanup_pending` and stays retriable without new audit rows.
The authority read-side same-record pin (profile port, memory mode) holds
for both dialects.
