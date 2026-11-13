# bcs-edge-permission-store Context

## Provides

- The SQL authority implementation (`DbBotAuthorityStore`) for the bot
  authority management edges (bot-authority spec §5/§6/§7): owner/manager
  edge reads and the registration/lifecycle composition steps, manager
  single-item edits and trusted full-set sync, trusted ownership sync
  (owner replace, previous owner fully removed), ownership transfer
  lifecycle (request/refresh/accept/reject/cancel with one pending row
  per `(env, bot_id, target_owner_actor_id)`), the paged reads behind
  the twelve `/api/v1/collaboration/` authority routes, and the
  `bot_authority_changes` permission-scope audit ledger.
- One transaction per authority change, serialized on the Bot row:
  bot lock → owner guard → count guards → Human-existence guards →
  exact diff writes → post-state guard. Bounded retry (≤3) only for
  deterministic local rollbacks; `Timeout` maps to the pinned
  `bot_authority_tx_outcome_uncertain` and is never retried. Audit
  writes stay ≤99 rows per statement. Owner normalization excludes the
  owner from every manager operand; no path fabricates rows.
- `DbEdgeGrantStore` for the runtime edge-grant domain (grants,
  revocation, friend dual-write reads), carrying the new
  `(from_id, to_id, env, grant_kind, grant_ref_id)` unique key and the
  one-approved-owner-per-Bot uniqueness from migration 033/034.
- A MySQL conformance surface for the authority contracts
  (`tests/authority_mysql.rs`, executed explicitly by CI with an
  injected fresh `BCS_TEST_MYSQL_URL`); SQLite runs the same harnesses
  by default.

## Consumes

- `bcs-service-api` port/repository contracts (`port::repo`) and domain
  types (`bcs-domain` edge_permission).
- DB plugin contracts (`bcs-db-api`); both SQL flavors through dialect
  helpers, never dialect-specific parsing in callers.

## Allowed dependencies

- `service-api/*`, `bcs-domain`, plugin API crates, utility crates.

## Forbidden dependencies

- `bootstrap/bcs`, `adapters/*`, transport/framework crates, and any
  raw environment/catalog access outside constructor parameters.

## Batch read cost

`roles_for` deduplicates requested (Human actor, Bot) pairs and reads those exact
pairs in chunks of 64 (at most 128 identity parameters plus the bound env per
statement), not one query per user and not the Cartesian product. Returned
management rows are bounded by two per pair. Bot state/owner completeness and
Human liveness remain chunked at 128 distinct ids; legacy creator reads touch
only version-0 Bots when fallback is enabled. For initialized Bots, round trips
are `2*ceil(B/128) + ceil(P/64) + ceil(H/128)` for B Bots, H Humans and P distinct
pairs; 100 users viewing one initialized Bot therefore use 5 SQL reads, not
400 scalar-role reads. Duplicate inputs preserve output order without more reads.
Storage/integrity failures still fail the whole batch. No positive cache, write
or transaction is added. MySQL uses the existing binary identity predicates;
SQLite query/row counts do not establish MySQL EXPLAIN or lock/contention results.

### Candidate owner projection

`ownerships_for` deduplicates exact input Bot IDs before bounded queries and
restores input order and multiplicity in its result. Missing/deleted/version-0
Bots project `None` (or the legacy creator under the migration shim); corrupt
initialized owner sets and storage failures still error the batch. Returned
owner rows are not deduplicated, so actual integrity failures remain observable.

For B distinct IDs the normal path uses `2*ceil(B/128)` statements (states and
owner actors); the legacy shim adds `ceil(L/128)` creator reads for L version-0
IDs. Empty input performs no reads. A healthy, fully-owned 100-item candidate
page therefore adds two statements, or three with legacy creators; zero-owner
cases add the re-check below. In a healthy initialized state, these return at
most B state rows plus B owner rows, not B rows in total.

A retirement can commit its tombstone and owner revocation between the state and
owner SELECTs. For U initialized IDs with no returned owner row, one liveness
re-check per 128-ID chunk (`ceil(U/128)` extra statements) distinguishes deleted
Bots (`None`) from still-live corrupt owner state (whole-batch error). Healthy
pages add no statements; the re-check starts no transaction and takes no lock.
This reasoning uses the existing single-primary, per-statement read visibility;
routing these reads to a lagged replica would require a shared snapshot instead.

MySQL state/creator/owner projection reads use an indexable raw identity `IN`
prefilter plus a binary residual predicate. The residual preserves byte-exact
case and trailing-space matching on retained collations. Each statement binds
at most 128 distinct IDs twice (257 parameters including env); SQLite binds each
ID once (129 including env). Query-count bounds alone do not establish scanned
row bounds. The opt-in `authority_ownerships_mysql` test runs the full migration
chain, captures generated statements, checks identity index key parts on a
20,000-Bot dataset and checks current/legacy collation behavior. No new cache,
write, lock, transaction, schema, or migration is added to the read path.

## Runtime ownership

Ownership sync guards both the target's and previous owner's approved manager
counts, including zero, under the Bot row lock. This adds at most two bounded
count queries per transaction; owner and Bot liveness pre-read query counts
stay unchanged. The command still uses at most four transaction attempts and
the existing ten-second transaction deadline. A manager grant racing pre-read
forces a fresh attempt; retirement is classified before owner-integrity errors.
Owner identity validation finishes before any write, and ownership audit states
use JSON serialization while preserving the existing field order.

Authority writes are the only permission-scope writers here; audit rows
append with a monotonic per-mutation sequence. Reads never audit.
Store-level error surfaces re-desensitize (stable codes), exposing no
SQL text. The environment is resolved once at construction.

## Tests

- `cargo test -p bcs-edge-permission-store`
- Candidate projection MySQL gate: `cargo test -p bcs-edge-permission-store
  --test authority_ownerships_mysql -- --ignored` (fresh empty
  `BCS_TEST_MYSQL_URL`; separate database from other MySQL gates).
- Plan §6 MySQL gate: `cargo test -p bcs-edge-permission-store
  --test authority_mysql authority_mysql_conformance -- --ignored
  --exact --nocapture` (needs `BCS_TEST_MYSQL_URL`, fresh empty DB).

Authority role lookup excludes Human management targets before owner/fallback
resolution, in both scalar and batched Bot-state queries. Actor kind is an
additional predicate on the existing statements; query counts and chunk bounds
are unchanged. A Human row with created_by is never a legacy-owned physical Bot.

## Request reads and source layout

`PermissionRequestRepoPort::list_inbox` and `list_sent` return `ServiceResult`.
SQL errors and invalid persisted rows fail the complete list; implementations
must not return empty/partial success. Connect listing and pending lookups
propagate these errors, including through legacy/V1 friendship facades.

SQL implementations are separated into `edge_grants.rs`, `profiles.rs`,
`requests.rs` and `bot_config.rs`; shared row/statement mapping is in
`mapping.rs`. `memory.rs` implements the same repo ports, with conformance
coverage for both SQL and memory stores. The result-type change does not add
queries or change ordering: one SELECT per inbox/outbox, two for All. Pending
connect checks still scan the target's pending inbox (existing behavior), so
this change does not claim bounded pagination or improved high-fan-in cost.

## Friendship cutover read authority

Both legacy and V1 friend facades read edge-owned friendship membership and
`permission_requests`. Legacy friend-request rows are not backfilled, read as
a fallback or required for bootstrap readiness. The current schema/field widths
are sufficient; no cutover migration is required. The legacy unpaged list
reuses the existing fallible, Bot-filtered edge-page query in batches of 100,
not the older unpaged N+1 profile reader. See [list access cost and semantics](../bcs-friend/CONTEXT.md#edge-owned-compatibility-list-access-cost)
and [rollout](../../../migrations/README.md#legacy-friend-request-cutover).
