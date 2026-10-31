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

Ordinary Bot retirement rejects Human targets in the repository, not just the
application hook. SQL checks actor_kind again under the existing Bot row lock;
Memory checks it under the shared authority write lock. This API does not delete
Humans, so registration/transfer cannot race it into orphaning an owner. No new
Human lifecycle API, query, migration or owner scan is introduced. Authority
role/fallback reads accept only physical Bot targets (scalar and batch).


## Caller configuration storage seam

`PersistentBotRepo` implements the P1.1 caller-configuration read/sync on the
existing `bcs_bots.bot_info` JSON column — no schema migration. Reads treat a
missing `$.caller` as "never configured" and surface `caller:null`, non-object
roots and malformed snapshots as a closed sanitized storage error, never as a
disable. Sync resolves the §10.7.2 revision matrix in storage order
(Apply/Unchanged/Stale/Conflict), writes with a binary CAS over the original
`bot_info` text (CAST on MySQL, binary TEXT compare on SQLite), preserves all
sibling fields, and after a lost CAS re-reads exactly once — a still-writable
decision becomes Conflict, never a blind overwrite. `MemoryBotRepo` keeps the
port's explicit fail-closed defaults. Conformance:
`tests/conformance_caller_configuration.rs` (real SQLite + contended fake).

## Provider caller capabilities read

`DbProviderStore` implements the P1.4 authoritative caller-capability read on
`bcs_providers.config.$.caller_capabilities` as a direct, cache-bypassing
SQLite/MySQL query: missing provider is `ProviderNotFound`, a disabled
provider is a closed "unavailable" error (never "undeclared"), a missing or
empty config is a legacy never-declared row, and `caller_capabilities:null`,
non-object roots and malformed declarations are closed sanitized errors that
never echo raw config. Conformance:
`tests/provider_caller_capabilities_contract.rs` (real SQLite).

The caller-configuration seam is also provider-scoped: the read JOINs the
canonical `bcs_provider_bot_bindings` (non-deleted bot, enabled binding,
exact provider) and the conditional write re-asserts the same ownership
inside its WHERE clause, so Bots of other providers, unbound Bots and
disabled bindings are out of scope without ever being reported
"unconfigured".

## A2A Card storage

`DbA2aBotStore` implements `A2aBotRepoPort` using only raw `agent_card`,
nullable `agent_card_url` and `agent_card_revision` on `bcs_bots` after MySQL 038 /
SQLite 039. One explicit read returns one Card; one CAS commits all three fields
and updates the generic Bot audit time. Writes fence environment, Provider/ref,
active Gateway mode and revision; failures propagate. Routes and credential
references are derived, never duplicated in this store.
Refresh reads at most 16 URL-backed snapshots in one bot_uuid keyset query, not
one ID query plus per-Card lookups. Availability projects only interfaces for at
most 256 IDs. No persistent refresh/error state or additional cache exists.
See docs/specs/a2a/minimal-card-registration-v1.md for access costs and limits.

Full Card queries select only Bot identity and Card payload in batches of 256.
Discovery evaluates Card names/descriptions and skill IDs/names/descriptions/tags
in SQL before selecting matching payloads. A nonmatching A2A identity remains
distinguishable from a legacy Bot, preventing stale capability fallback. Search
count and pagination share the same predicate. SQL flavor is explicitly injected;
SQLite uses JSON extraction and MySQL uses JSON_SEARCH with a literal escape
character and bound values. See the
[MySQL JSON search contract](https://dev.mysql.com/doc/refman/8.0/en/json-search-functions.html).
Provider database implementations and their unit tests remain in provider.rs;
the existing Provider behavior remains covered by its suite.


A2A strict registration extends the existing ownership composite with a checked
Card UPDATE inside the same transaction, after the new Bot/binding and before
ownership/profile/audit steps. Identity, credential scope and initial revision
are checked before writing. It adds one single-row statement, no network or new
schema. Failure of Card or a later ownership step rolls back every lifecycle row.


Provider-admin A2A admission uses the separate
`create_provider_admin_a2a_bot_with_ownership` operation. Its ownership builder
requires a Provider actor exactly matching the Bot's Provider; the old Human
registration operations still reject Provider actors. Both paths reuse the
same SQL transaction/guard statements, differing only in verified audit identity.
No credential reaches audit rows; no post-commit owner/profile/Card write is
introduced. A rollback includes Bot, binding, Card, Human/owner and default profile.
