# bcs-edge-permission-store Context

## Provides

- The SQL authority implementation (`DbBotAuthorityStore`) for the bot
  authority management edges (bot-authority spec §5/§6/§7): owner/manager
  edge reads and the registration/lifecycle composition steps, manager
  single-item edits and trusted full-set sync, ownership transfer
  lifecycle (request/refresh/accept/reject/cancel with one pending row
  per `(env, bot_id, target_owner_actor_id)`), the paged reads behind
  the eleven `/api/v1/collaboration/` authority routes, and the
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

## Runtime ownership

Authority writes are the only permission-scope writers here; audit rows
append with a monotonic per-mutation sequence. Reads never audit.
Store-level error surfaces re-desensitize (stable codes), exposing no
SQL text. The environment is resolved once at construction.

## Tests

- `cargo test -p bcs-edge-permission-store`
- Plan §6 MySQL gate: `cargo test -p bcs-edge-permission-store
  --test authority_mysql authority_mysql_conformance -- --ignored
  --exact --nocapture` (needs `BCS_TEST_MYSQL_URL`, fresh empty DB).

Authority role lookup excludes Human management targets before owner/fallback
resolution, in both scalar and batched Bot-state queries. Actor kind is an
additional predicate on the existing statements; query counts and chunk bounds
are unchanged. A Human row with created_by is never a legacy-owned physical Bot.
